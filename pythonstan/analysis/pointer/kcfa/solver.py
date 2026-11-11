"""Constraint-based pointer analysis solver.

This module implements the core solver for pointer analysis using
constraint-based propagation.
"""

import ast
import logging
import warnings
import time
from collections import deque
from typing import Set, Dict, Any, TYPE_CHECKING, Optional, List, Tuple, Iterable, Union

from pythonstan.ir.ir_statements import (
    IRScope,
    IRFunc,
    IRModule,
    IRClass,
    IRCall,
    IRLoadAttr,
    IRStoreAttr,
    IRLoadSubscr,
    IRStoreSubscr,
    IRAssign,
    IRCopy,
)

from .state import PointerAnalysisState, PointsToSet, Worklist
from .constraints import *
from .variable import Variable, VariableKind, VariableFactory, FieldAccess
from .config import Config
from .heap_model import Field, FieldKind, attr, key, elem, value, wildcard_key
from pythonstan.graph.call_graph import AbstractCallGraph, CallEdge, CallKind
from .ir_translator import IRTranslator
from .context_selector import ContextSelector, AbstractContext
from .context import Ctx, Scope, CallSite, ordered_binding_token
from .class_hierarchy import ClassHierarchyManager
from .builtin_api_handler import BuiltinSummaryManager
from .unknown_tracker import UnknownTracker, UnknownKind
from .object import *
from .object import ObjectFactory
from .solver_interface import ISolverQuery
from .pointer_flow_graph import PointerFlowGraph, PointerFlowEdge, PointerFlowNode, NormalNode, GuardNode, SelectorNode, PointerFlowKind
from .processor import Processor
from .bootstrap import (bind_native_solver_contract, bootstrap_statistics,
                        validate_bootstrap_contract)
from .diagnostics import NativeDiagnostics
from .scheduler import NativeScheduler
from pythonstan.world import World

if TYPE_CHECKING:
    from .debug_monitor import DebugMonitor

__all__ = ["PointerSolver", "SolverQuery"]

logger = logging.getLogger(__name__)


class PointerSolver:
    _WORK_KIND_ORDER = NativeScheduler.KIND_ORDER
    def __init__(
        self,
        state: 'PointerAnalysisState',
        config: 'Config',
        processor: 'Processor',
        variable_factory: Optional['VariableFactory'] = None,
        ir_translator: Optional['IRTranslator'] = None,
        context_selector: Optional['ContextSelector'] = None,
        class_hierarchy: Optional['ClassHierarchyManager'] = None,
        builtin_manager: Optional['BuiltinSummaryManager'] = None,
        debug_monitor: 'DebugMonitor' = None,
        intrinsic_provider=None,
    ):
        """Initialize solver.
        
        Args:
            state: Analysis state
            config: Configuration
            ir_translator: IR translator for analyzing called functions
            context_selector: Context selector for call/alloc contexts
            function_registry: Map of function names to IR functions
            class_hierarchy: Class hierarchy manager for MRO
            builtin_manager: Builtin summary manager
            debug_monitor: Optional debug monitor for tracking
        """
        self.state = state
        self.config = config
        self.scheduler = NativeScheduler(config.work_scheduling_policy)
        from pythonstan.analysis.contracts.work import SharedWorkLedger
        self.work_ledger=SharedWorkLedger()
        self.intrinsic_provider=intrinsic_provider
        self.intrinsic_seconds=0.0
        if config.points_work_policy == "fifo" and getattr(state, "_consumer_worklist", None) is not None:
            raise ValueError("points_work_policy is fixed for a configured analysis state")
        if config.points_work_policy == "consumer_fair":
            from .worklist import ConsumerFairWorklist
            if type(state._worklist) is not Worklist:
                raise ValueError("consumer_fair requires the standard native worklist backend")
            queue = ConsumerFairWorklist()
            for initial_scope, node, box in state._worklist.items_list:
                queue.add((initial_scope, node, box.val))
            state._worklist = queue
            state._consumer_worklist = queue
        self.processor = processor
        self.ir_translator = ir_translator
        self.context_selector = context_selector
        from .allocation import AllocationPolicyRegistry
        if context_selector is not None:
            self.state.allocation_policy = AllocationPolicyRegistry(context_selector)
        self._definition_objects = {}
        self.class_hierarchy = class_hierarchy
        self.builtin_manager = builtin_manager
        self.variable_factory = variable_factory or VariableFactory()
        self._unknown_tracker = UnknownTracker(
            track_unknowns=config.track_unknowns,
            log_unknown_details=config.log_unknown_details,
        )
        self._debug_monitor = debug_monitor
        
        # Initialize builtin handler with state
        if self.builtin_manager:
            self.builtin_manager.set_state(state)
        self._constraint_replays = deque()
        self._builtin_dependencies = {}
        self._builtin_object_dependencies = {}
        self._builtin_invocations = {}
        self._builtin_replays = deque()
        self._queued_builtin_replays = set()
        self._unknown_results = {}
        self.state._residual_effects = {}
        self._work_scheduler_cursor = 0
        self._work_iteration_started = False
        self._work_step_observer = None
        self._diagnostics = None
        self._inspector_cold_checkpoint = None
        self._inspector_cold_work_interval = 1024
        self._inspector_cold_seconds = 1.0
        self._inspector_cold_last_work = 0
        self._inspector_cold_last_time = 0.0
        self._inspector_cold_failures = 0
        self._inspector_enabled = False
        # Passive capture may only inspect completed work-item boundaries. No
        # optional inspector package is imported by the analyzer.
        self._inspector_boundary = "initialized"
        self._inspector_step_depth = 0
        self._inspector_table_generation = None
        self._inspector_normal_stop = False
        self._inspector_tainted = False
        # Unlike selected-work statistics, these admission clocks survive a
        # legacy iter() reset. Early publications must remain accountable.
        self._bootstrap_publications = 0
        self._bootstrap_contract_signature = None
        self._bootstrap_invalid = False
        self._reset()
    
    def _reset(self) -> None:
        self._iteration = 0
        self._stats: Dict[str, Any] = {
            "iterations": 0,
            "constraints_applied": 0,
            "converged": 0,
            "iteration_limit_reached": 0,
            "ai_intrinsic_attempts": 0,
            "ai_intrinsic_applied": 0,
            "ai_intrinsic_fallbacks": 0,
            "ai_intrinsic_deferred": 0,
            "builtin_dependency_replays": 0,
            "work_items_static": 0,
            "work_items_constraint_replay": 0,
            "work_items_builtin_replay": 0,
            "work_items_points_to": 0,
            "points_to_noop_work_items": 0,
            "points_to_delta_objects": 0,
            "builtin_replay_notifications": 0,
            "builtin_replay_notifications_coalesced": 0,
            "points_to_enqueue_processed_skips": 0,
            "points_to_enqueue_trimmed_requests": 0,
            "work_scheduling_policy": self.config.work_scheduling_policy,
            "points_work_policy": self.config.points_work_policy,
            "work_scheduler_kind_order": list(self._WORK_KIND_ORDER),
            "work_scheduler_cursor": self._work_scheduler_cursor,
            "work_kind_first_service_iteration": {kind: None for kind in self._WORK_KIND_ORDER},
            "configuration_diagnostics": self.config.configuration_diagnostics(),
        }
        self._modules = set()
        self._canonical_module_objects = None
        self._package_module_bindings = set()
        self._package_binding_version = -1
        self._non_callable_evidence: Dict[Tuple['Ctx[Any]', 'Scope', 'CallConstraint'], Set[str]] = {}
    
    def add_constraint(self, scope: 'Scope', context: 'AbstractContext', constraint: 'Constraint') -> None:
        if isinstance(constraint, (TruthTestConstraint, OperatorProtocolConstraint)):
            trigger = self.state.get_variable(scope, context, constraint.base)
            self._register_dynamic_constraint(scope, trigger, constraint)
            return
        if isinstance(constraint, ResidualEffectConstraint):
            identity = ("source_residual", scope, constraint)
            self.state._residual_effects[identity] = {
                "location": constraint.location, "context": str(scope),
                "reason": constraint.reason, "effects": list(constraint.effects),
            }
            self._unknown_tracker.record(
                UnknownKind.UNSUPPORTED_SEMANTICS, constraint.location,
                constraint.reason, context=str(scope),
            )
            return
        if isinstance(constraint, SpecialMethodLookupConstraint):
            base = self.state.get_variable(scope, context, constraint.base)
            self._register_dynamic_constraint(scope, base, constraint)
            return
        if isinstance(constraint, MappingCopyConstraint):
            source = self.state.get_variable(scope, context, constraint.source)
            self._register_dynamic_constraint(scope, source, constraint)
            return
        if self.processor.handle_new_constraint(self, scope, constraint):
            return

        if isinstance(constraint, CopyConstraint):
            self.state._static_constraints.append((scope, context, constraint))
        elif isinstance(constraint, AllocConstraint):
            self.state._static_constraints.append((scope, context, constraint))
        else:
            if isinstance(constraint, LoadConstraint):
                base = self.state.get_variable(scope, context, constraint.base)
                self._register_dynamic_constraint(scope, base, constraint)
            elif isinstance(constraint, StoreConstraint):
                base = self.state.get_variable(scope, context, constraint.base)
                self._register_dynamic_constraint(scope, base, constraint)
            elif isinstance(constraint, CallConstraint):
                callee = self.state.get_variable(scope, context, constraint.callee)
                self._register_dynamic_constraint(scope, callee, constraint)
            elif isinstance(constraint, InheritanceConstraint):
                base = self.state.get_variable(scope, context, constraint.base)
                self._register_dynamic_constraint(scope, base, constraint)

    def _register_dynamic_constraint(self, scope: 'Scope', trigger: 'Ctx[Any]', constraint: 'Constraint') -> None:
        """Subscribe to future facts and replay facts already seen by a new rule.

        Re-enqueuing the trigger node would lose these facts to delta filtering.
        Replay the constraint itself instead, without recursive rule evaluation.
        """
        if self.state.constraints.add(scope, trigger, constraint):
            if self._diagnostics is not None:
                self._diagnostics.bump("constraints_registered")
            self._promote_points_variable(trigger)
            existing = self.state.get_points_to(trigger)
            if not existing.is_empty():
                self._constraint_replays.append((scope, trigger, constraint, existing))

    def _register_builtin_dependencies(self, scope, context, call, builtin_obj):
        """Re-evaluate snapshot-based builtin models when their inputs grow."""
        invocation = (scope, call, builtin_obj)
        if self._diagnostics is not None and invocation not in self._builtin_invocations:
            self._diagnostics.bump("builtin_invocations_registered")
        self._builtin_invocations[invocation] = (scope, context, call, builtin_obj)
        for argument in (*call.args, *(value for _, value in call.kwargs)):
            variable = self.state.get_variable(scope, context, argument)
            self._watch_builtin_variable(variable, invocation)
            observe_mapping = (
                isinstance(builtin_obj, BuiltinMethodObject) and builtin_obj.method_name in {"update", "items", "keys", "copy"}
            ) or (isinstance(builtin_obj, BuiltinFunctionObject) and builtin_obj.function_name in {"dict", "vars"}) or (
                isinstance(builtin_obj, BuiltinClassObject) and builtin_obj.builtin_name == "dict"
            )
            if observe_mapping:
                for obj in self.state.get_points_to(variable):
                    self._builtin_object_dependencies.setdefault(obj, set()).add(invocation)
        if isinstance(builtin_obj, BuiltinMethodObject) and builtin_obj.method_name in {"items", "keys", "copy"}:
            self._builtin_object_dependencies.setdefault(builtin_obj.receiver, set()).add(invocation)
        return invocation

    def _promote_points_variable(self, variable):
        queue = getattr(self.state, "_consumer_worklist", None)
        if queue is not None:
            node = variable if isinstance(variable, PointerFlowNode) else NormalNode(variable)
            queue.promote(node)

    def _watch_builtin_variable(self, variable, invocation):
        dependencies = self._builtin_dependencies.setdefault(variable, set())
        new_dependency = invocation not in dependencies
        dependencies.add(invocation)
        self._promote_points_variable(variable)
        # Actual eager publication precedes source consumers. Fully declined
        # runs retain the queued registration behavior. A newly installed watch
        # must see past facts once, through the original deduplicated replay
        # queue. Re-registering an invocation must not self-reschedule it.
        if (self.config.builtin_bootstrap_policy == "eager_inert"
                and self._bootstrap_publications > 0
                and new_dependency and self.state.get_points_to(variable)
                and invocation not in self._queued_builtin_replays):
            self._queued_builtin_replays.add(invocation)
            self._builtin_replays.append(invocation)

    def _schedule_builtin_dependents(self, variable):
        invocations = set(self._builtin_dependencies.get(variable, ()))
        if isinstance(variable.content, FieldAccess):
            invocations.update(self._builtin_object_dependencies.get(variable.content.obj, ()))
        for invocation in invocations:
            self._stats["builtin_replay_notifications"] += 1
            if invocation not in self._queued_builtin_replays:
                self._queued_builtin_replays.add(invocation)
                self._builtin_replays.append(invocation)
            else:
                self._stats["builtin_replay_notifications_coalesced"] += 1


    def solve_to_fixpoint(self) -> None:
        logger.info("Starting constraint solving")
        for _ in self:
            # Cold owner-thread checkpoint: the atomic __next__ operation has
            # already returned. No callback runs in stores or eager bootstrap.
            self._run_inspector_cold_checkpoint()
            # Update debug monitor iteration
            if self._debug_monitor:
                self._debug_monitor.set_iteration(self._iteration)
            # Log progress periodically
            log_interval = self.config.debug_log_interval if self.config.enable_debug_monitor else 1000
            if self._iteration % log_interval == 0:
                if logger.isEnabledFor(logging.INFO):
                    logger.info(f"Iter {self._iteration}, WL{len(self.state._worklist)}, objs: {len(self.state._heap.objects)}, "
                                f"CE: {len(self.state.call_graph.edges)}, pCE: {self.state.call_graph.num_plain_edges()}, "
                                f"totC: {len(self.state.constraints)}, "
                                f"C: {len(self.state.constraints.get_by_type(CallConstraint))}, "
                                f"I: {len(self.state.constraints.get_by_type(InheritanceConstraint))}, "
                                f"L: {len(self.state.constraints.get_by_type(LoadConstraint))}, "
                                f"S: {len(self.state.constraints.get_by_type(StoreConstraint))}, "
                                f"LS: {len(self.state.constraints.get_by_type(LoadSubscrConstraint))}, "
                                f"SS: {len(self.state.constraints.get_by_type(StoreSubscrConstraint))}, "
                                f"AR: {len(self.state.constraints.get_by_type(AttrReadConstraint))}, "
                                f"AW: {len(self.state.constraints.get_by_type(AttrWriteConstraint))}, "
                    )
                # Record iteration snapshot
                self._log_solver_state()
        if self._diagnostics is not None:
            self._diagnostics.phase = "solve_completed" if self._stats["converged"] else "solve_stopped"
            self._diagnostics.last_action = None
        self._run_inspector_cold_checkpoint(force=True)
        
        self._stats["iterations"] = self._iteration
        if logger.isEnabledFor(logging.INFO):
            logger.info("Processed %s modules: %s", len(self._modules), self._modules)
            logger.info("Call Constraints: %s", len(self.state.constraints.get_by_type(CallConstraint)))
            abs_nodes = {node.stmt.get_qualname() for node in self.state._call_graph.get_nodes()}
            logger.info("Call graph: %s node: %s edge: %s", self.state._call_graph,
                        len(self.state._call_graph.get_nodes()), self.state._call_graph.get_number_of_edges())
            logger.info("    absolute nodes: %s absolute edges: %s", len(abs_nodes), self.state._call_graph.num_plain_edges())
            logger.info("Pointer flow graph: %s node: %s edge: %s", self.state._pointer_flow_graph,
                        len(self.state._pointer_flow_graph.get_nodes()), len(self.state._pointer_flow_graph.get_edges()))
        if self._stats["converged"]:
            logger.info(f"Converged after {self._iteration} iterations")
        else:
            logger.warning(
                "Stopped before convergence after %s iterations (%s pending items)",
                self._iteration,
                self._stats["pending_work"],
            )
        
    def __iter__(self):
        validate_bootstrap_contract(self)
        # Legacy clients historically start a new statistics/budget window on
        # iter(). Fair continuation cannot renew its budget or cursor that way.
        if self.config.work_scheduling_policy == "legacy" or not self._work_iteration_started:
            self._reset()
            if self._diagnostics is not None:
                self._diagnostics.start_window()
            self._stats["initial_pending_work_by_kind"] = self._pending_work_by_kind()
        self._work_iteration_started = True
        return self

    def set_work_step_observer(self, observer):
        """Install a trusted passive diagnostic, called after an atomic step.

        The observation contains only scalar clocks and queue lengths. It does
        not traverse abstract facts, allocate object IDs or change scheduling.
        Callbacks themselves must not mutate the solver or request more steps.
        """
        if observer is not None and not callable(observer):
            raise ValueError("work step observer must be callable or None")
        validate_bootstrap_contract(self)
        if self._bootstrap_publications and observer is not self._work_step_observer:
            raise ValueError("work step observer must be configured before eager builtin bootstrap")
        self._work_step_observer = observer

    def enable_inspector_diagnostics(self, mode, run_id, selector=None, *,
                                     max_events=256, max_bytes=262144, max_refs=512):
        """Configure the native memory-only port before initialization/work.

        Pure counters neither enable capture boundaries nor install an observer.
        Targeted mode is explicitly outside inert eager admission.
        """
        validate_bootstrap_contract(self)
        if self._work_iteration_started or self._iteration or self._bootstrap_publications:
            raise ValueError("native diagnostics must be configured before bootstrap and solving")
        if self._diagnostics is not None:
            raise ValueError("native diagnostics already configured")
        self._diagnostics = NativeDiagnostics(mode, run_id, selector,
            max_events=max_events, max_bytes=max_bytes, max_refs=max_refs)

    def get_inspector_counters(self):
        """Constant-size scalar observation; never traverse facts or inventory."""
        port = self._diagnostics
        native = {"scope": "current_selected_budget_window", "selected_work_items": self._iteration,
            "bootstrap_publications_run": self._bootstrap_publications,
            "logical_work_items_mixed_scope": self._iteration + self._bootstrap_publications,
            "pending_work_by_kind": self._pending_work_by_kind(),
            "converged": bool(self._stats.get("converged")),
            "iteration_limit_reached": bool(self._stats.get("iteration_limit_reached"))}
        if port is None:
            return {"version": 1, "mode": "off", "run_id": None, "native_window": native,
                "cumulative": None, "window": None, "actual_bootstrap": {
                    "requested": self.config.builtin_bootstrap_policy,
                    "publications": self._bootstrap_publications, "fallbacks": None,
                    "fallback_reasons": None, "decision_coverage": "not_observed"},
                "phase": None, "last_action": None, "health": None,
                "cold_checkpoint_failures": self._inspector_cold_failures}
        counts = dict(port._counts)
        publications, fallbacks = counts["bootstrap_publications"], counts["bootstrap_fallbacks"]
        behavior = ("queued_requested" if self.config.builtin_bootstrap_policy == "queued" else
            "mixed_eager_and_queued" if publications and fallbacks else
            "eager_publications_observed" if publications else "queued_fallback_only" if fallbacks else
            "no_seed_decision_observed")
        counts["logical_work_items"] = counts["selected_work_items"] + publications
        return {"version": 1, "mode": port.mode, "run_id": port.run_id, "native_window": native,
            "cumulative": {"scope": "run_since_diagnostics_configuration",
                           "coverage": "complete" if port._counter_complete else "partial", **counts},
            "window": {"scope": "current_selected_budget_window", "index": port.window,
                       "coverage": "complete" if port._window_valid and port._counter_complete else "partial",
                       **dict(port._window_counts)},
            "actual_bootstrap": {"requested": self.config.builtin_bootstrap_policy,
                "observed_behavior": behavior, "publications": publications, "fallbacks": fallbacks,
                "fallback_reasons": dict(port._fallbacks),
                "decision_coverage": "all_native_seed_attempts_since_configuration" if port._counter_complete
                                     else "partial_native_seed_attempts_since_configuration"},
            "phase": port.phase, "last_action": port.last_action, "health": port.health(),
            "counter_definitions": {
                "selected_work_items": "native scheduler selections before executing the selected operation; completion counted separately",
                "completed_work_items": "selected atomic work operations whose native handler returned normally",
                "selected_by_kind": "selected_static/constraint_replay/builtin_replay/points_to use the same selection boundary",
                "points_enqueue_requests": "calls of solver._enqueue_points_to only; excludes direct state/seed queue additions",
                "points_enqueue_accepted": "successful _enqueue_points_to Worklist.add requests, including merges; not new queue entries",
                "points_enqueue_processed_skips": "enqueue requests whose delta against already processed native node storage is empty",
                "points_enqueue_trimmed": "enqueue requests reduced by already processed native facts",
                "bootstrap_seed_enqueued": "successful builtin seed queue-add requests, including merges; not new entries",
                "points_delta_objects": "fresh object-lane deltas handled by _apply_dynamic, including eager bootstrap; unlike resettable native legacy stats",
                "points_noop_work_items": "_apply_dynamic calls with empty fresh delta; not a general useless-work classification",
                "constraints_registered": "successful dynamic trigger-subscription additions; not all static/canonical constraints",
                "builtin_invocations_registered": "new builtin invocation registry keys; not completed model calls",
                "bootstrap_candidates": "all try_publish_builtin_seed calls, including requested queued policy",
                "bootstrap_eager_attempts": "requested eager seed decisions, either published or declined; not proven eligible seeds",
                "bootstrap_publications": "validated native inert seed publications",
                "bootstrap_fallbacks": "requested eager seed decisions that declined early publication",
                "bootstrap_queued_requested": "seed decisions whose requested policy was queued",
                "logical_work_items": "cumulative selected work plus cumulative eager seed publications"},
            "cold_checkpoint_failures": self._inspector_cold_failures}

    def drain_inspector_events(self):
        if self._diagnostics is None:
            return {"schema_version": 1, "run_id": None, "epoch": None, "selector": None,
                    "events": [], "descriptors": [], "health": {"mode": "off", "status": "disabled"}}
        return self._diagnostics.drain()

    def resolve_inspector_event_ref(self, run_ref):
        return self._diagnostics.resolve(run_ref) if self._diagnostics is not None else None

    def set_inspector_cold_checkpoint(self, callback, *, work_interval=1024, seconds=1.0):
        """Install a cold bridge only for queued/targeted or capture paths."""
        if callback is not None and not callable(callback):
            raise ValueError("cold checkpoint must be callable or None")
        if (type(work_interval) is not int or work_interval < 1
                or type(seconds) not in (int, float) or not 0 < seconds <= 3600):
            raise ValueError("invalid cold checkpoint cadence")
        validate_bootstrap_contract(self)
        if self._bootstrap_publications:
            raise ValueError("cold checkpoint must be configured before eager bootstrap")
        targeted = self._diagnostics is not None and self._diagnostics.mode == "targeted"
        if (callback is not None and self.config.builtin_bootstrap_policy == "eager_inert"
                and not self._inspector_enabled and not targeted):
            raise ValueError("cold checkpoint unsupported for unguarded eager counter mode")
        self._inspector_cold_checkpoint = callback
        self._inspector_cold_work_interval = work_interval
        self._inspector_cold_seconds = seconds
        self._inspector_cold_last_work = self._iteration
        self._inspector_cold_last_time = time.monotonic()

    def _run_inspector_cold_checkpoint(self, force=False):
        callback = self._inspector_cold_checkpoint
        if callback is None:
            return
        work = self._iteration - self._inspector_cold_last_work
        if not force and work < self._inspector_cold_work_interval and self._iteration % 64:
            return
        now = time.monotonic()
        if not force and work < self._inspector_cold_work_interval and now - self._inspector_cold_last_time < self._inspector_cold_seconds:
            return
        self._inspector_cold_last_work, self._inspector_cold_last_time = self._iteration, now
        try:
            callback(self)
        except Exception:
            self._inspector_cold_failures += 1

    def _queues(self):
        return (self.state._static_constraints, self._constraint_replays,
                self._builtin_replays, self.state._worklist)

    def _select_work_kind(self):
        kind,self._work_scheduler_cursor=self.scheduler.select(self._queues(),self._work_scheduler_cursor,policy=self.config.work_scheduling_policy)
        return kind

    def _pop_scheduled(self,queue):
        return self.scheduler.pop(queue,policy=self.config.work_scheduling_policy)

    def _finish_work_step(self, kind, result):
        validate_bootstrap_contract(self)
        first = self._stats["work_kind_first_service_iteration"]
        if first[kind] is None:
            first[kind] = self._iteration
        self._stats["work_scheduler_cursor"] = self._work_scheduler_cursor
        if self._diagnostics is not None:
            self._diagnostics.work_completed()
        if self._work_step_observer is not None:
            self._work_step_observer({"iteration": self._iteration, "kind": kind,
                "cursor": self._work_scheduler_cursor,
                "pending_work_by_kind": self._pending_work_by_kind()})
        return result
    
    def __next__(self) -> 'PointerAnalysisState':
        """Advance one item and publish whether the resulting cut is safe.

        Finalization can recursively request another item. The outermost call
        remains busy until that entire operation has returned. An exception,
        including an interruption, leaves an explicitly unsafe forensic cut.
        """
        validate_bootstrap_contract(self)
        if not self._inspector_enabled:
            return self._next_work_item()
        outermost = self._inspector_step_depth == 0
        if outermost:
            if self._inspector_table_generation is None:
                from . import points_to_set
                self._inspector_table_generation = points_to_set._object_table_generation
            self._inspector_boundary = "running"
            self._inspector_normal_stop = False
        self._inspector_step_depth += 1
        completed = False
        try:
            result = self._next_work_item()
        except StopIteration:
            completed = self._inspector_normal_stop
            if outermost:
                self._inspector_boundary = "stopped" if completed else "aborted_step"
            raise
        else:
            completed = True
            if outermost:
                self._inspector_boundary = "between_work_items"
            return result
        finally:
            self._inspector_step_depth -= 1
            if not completed:
                # There is no rollback protocol for partially applied work.
                # Later successful steps cannot certify the earlier mutation.
                self._inspector_tainted = True
                if outermost:
                    self._inspector_boundary = "aborted_step"

    def enable_inspector_boundaries(self):
        """Opt in before solving, without importing the optional inspector.

        A late request cannot retrospectively certify earlier work boundaries.
        """
        validate_bootstrap_contract(self)
        if self._bootstrap_publications:
            raise ValueError("inspector boundaries must be configured before eager builtin bootstrap")
        if self._inspector_enabled:
            return
        if self._work_iteration_started or self._iteration:
            raise ValueError("inspector boundaries must be enabled before solving")
        self._inspector_enabled = True

    def _next_work_item(self) -> 'PointerAnalysisState':
        validate_bootstrap_contract(self)
        if (self._pending_work_count() > 0) and self._iteration < self.config.max_iterations and self.work_ledger.remaining > 0:
            if not self._work_iteration_started:
                self._stats["initial_pending_work_by_kind"] = self._pending_work_by_kind()
                self._work_iteration_started = True
                if self._diagnostics is not None:
                    self._diagnostics.start_window()
            self._stats["converged"] = 0
            self._stats["iteration_limit_reached"] = 0
            if not self.work_ledger.consume(kind="native_selected"):
                raise RuntimeError("Native work admission invariant failed")
            self._iteration += 1
            kind = self._select_work_kind()
            if self._diagnostics is not None:
                self._diagnostics.work_selected(kind)
            if kind == "static":
                self._stats["work_items_static"] += 1
                scope, ctx, constraint = self._pop_scheduled(self.state._static_constraints)
                result = self._apply_static(scope, ctx, constraint)
                return self._finish_work_step(kind, result)
            elif kind == "constraint_replay":
                self._stats["work_items_constraint_replay"] += 1
                scope, trigger, constraint, pts = self._pop_scheduled(self._constraint_replays)
                self._apply_constraint(scope, trigger, constraint, pts)
                return self._finish_work_step(kind, self.state)
            elif kind == "builtin_replay":
                self._stats["work_items_builtin_replay"] += 1
                invocation = self._pop_scheduled(self._builtin_replays)
                self._queued_builtin_replays.remove(invocation)
                scope, context, call, builtin_obj = self._builtin_invocations[invocation]
                trigger = self.state.get_variable(scope, context, call.callee)
                self._stats["builtin_dependency_replays"] += 1
                self._apply_call(scope, trigger, call, PointsToSet.singleton(builtin_obj))
                return self._finish_work_step(kind, self.state)
            elif kind == "points_to":
                self._stats["work_items_points_to"] += 1
                import time                
                start_time = time.time()
                
                scope, node, pts = self.state._worklist.pop()
                result, num_pts, num_constraint = self._apply_dynamic(scope, node, pts)
                                                        
                time_taken = time.time() - start_time
                if time_taken > 1 and isinstance(node, NormalNode):
                    logger.warning(f"Applying dynamic node {node.var.content} with pts {num_pts} constraints {num_constraint} took {time_taken} seconds")
                    
                return self._finish_work_step(kind, result)
            
        pending_work = self._pending_work_count()
        self._stats["iterations"] = self._iteration
        self._stats["pending_work"] = pending_work
        self._stats["pending_work_by_kind"] = self._pending_work_by_kind()
        self._stats["converged"] = int(pending_work == 0)
        self._stats["iteration_limit_reached"] = int(pending_work != 0)
        if getattr(self.state, "_consumer_worklist", None) is not None:
            self._stats.update({"points_queue_" + key: value for key, value in
                                self.state._consumer_worklist.diagnostics().items()})
        if pending_work:
            self._unknown_tracker.record(
                UnknownKind.SOLVER_LIMIT,
                "solver",
                f"Iteration budget exhausted with {pending_work} pending items; results are incomplete",
            )
        else:
            self._finalize_unknowns_at_fixpoint()
            if self._pending_work_count():
                self._stats["converged"] = 0
                return self.__next__()
        if self._inspector_enabled:
            self._inspector_normal_stop = True
        raise StopIteration

    def _pending_work_count(self) -> int:
        return (len(self.state._worklist) + len(self.state._static_constraints)
                + len(self._constraint_replays) + len(self._builtin_replays))

    def _pending_work_by_kind(self) -> Dict[str, int]:
        """Constant-size queue observation; never enumerate or consume work."""
        return self.scheduler.pending(self._queues())
    
    def _apply_dynamic(self, scope: 'Scope', node: 'PointerFlowNode', pts: 'PointsToSet') -> 'PointerAnalysisState':
        if isinstance(node, NormalNode):
            assert isinstance(node.var, Ctx), f"node.var must be a Ctx, but got {type(node.var)}"
        diff = pts - self.state.get_points_to(node)
        num_pts = len(diff)
        num_constraint = 0
        self._stats["points_to_delta_objects"] += num_pts
        if self._diagnostics is not None:
            self._diagnostics.bump("points_delta_objects", num_pts)
        if num_pts == 0:
            self._stats["points_to_noop_work_items"] += 1
            if self._diagnostics is not None:
                self._diagnostics.bump("points_noop_work_items")
        if not diff.is_empty():
            self.state.set_points_to(node, diff)
            
            # apply the constraints associated with the variable
            if isinstance(node, NormalNode):
                self.processor.handle_pts(self, node.var, scope, diff)                
                self.state.set_points_to(node.var, diff)
                if self._diagnostics is not None:
                    self._diagnostics.output_delta(node.var, diff)
                self._schedule_builtin_dependents(node.var)
                
                constraints = self.state.constraints.iter_scoped_by_variable(node.var)
                num_constraint += len(constraints)
                for constraint_scope, constraint in constraints:
                    self._apply_constraint(constraint_scope, node.var, constraint, diff)
            
            for succ, succ_pts in self.state.pointer_flow_graph.propagate(node, diff):
                succ_scope = succ.var.scope if isinstance(succ, NormalNode) else None
                self._enqueue_points_to(succ_scope, succ, succ_pts)
        return self.state, num_pts, num_constraint

    def _log_solver_state(self):
        """Log periodic snapshot of solver state for debugging."""
        if not self._debug_monitor or not self._debug_monitor.enabled:
            return
        
        self._debug_monitor.record_iteration_snapshot(
            worklist_size=len(self.state._worklist),
            call_edges=self.state._call_graph.num_plain_edges(),
            pfg_edges=len(self.state.pointer_flow_graph.get_edges()),
            num_variables=len(self.state._env),
            num_objects=len(self.state._heap.objects)
        )

    def _apply_static(self, scope: 'Scope', context: 'AbstractContext', constraint: 'Constraint'):
        self._stats["constraints_applied"] += 1
        if isinstance(constraint, AllocConstraint):
            self._apply_alloc(scope, context, constraint)
        elif isinstance(constraint, CopyConstraint):
            self._apply_copy(scope, context, constraint)

    def _apply_constraint(self, scope: 'Scope', variable: Ctx[Any], constraint: 'Constraint', diff: 'PointsToSet') -> bool:  
        self._stats["constraints_applied"] += 1
        if isinstance(constraint, OperatorProtocolConstraint):
            from .processor.type_protocol import apply_operator
            apply_operator(self, scope, constraint, diff)
            return True
        if isinstance(constraint, TruthTestConstraint):
            from .processor.truth_protocol import apply_truth_test
            apply_truth_test(self, scope, constraint, diff)
            return True
        result = self.processor.handle_constraint(self, variable, scope, constraint, diff)
        if not result:
            # Here shoud add supports for Imports
            if isinstance(constraint, SpecialMethodLookupConstraint):
                result = self._apply_special_method_lookup(scope, constraint, diff)
            elif isinstance(constraint, MappingCopyConstraint):
                from .processor.heap_mapping import copy_mapping_fields
                for source in diff:
                    copy_mapping_fields(self.state, scope, source, constraint.target_object,
                                        constraint.excluded_keys, self.config.index_sensitive)
                result = True
            elif isinstance(constraint, LoadConstraint):
                result = self._apply_load(scope, variable, constraint, diff)
            elif isinstance(constraint, StoreConstraint):
                result = self._apply_store(scope, variable, constraint, diff)
            elif isinstance(constraint, CallConstraint):
                result = self._apply_call(scope, variable, constraint, diff)
            elif isinstance(constraint, InheritanceConstraint):
                result = self._apply_inheritance(scope, variable, constraint, diff)
            else:
                result = False
        return result

    def _apply_copy(self, scope: 'Scope', context: 'AbstractContext', c: 'CopyConstraint'):
        """Apply copy constraint: target = source."""
        src = self.state.get_variable(scope, context, c.source)
        tgt = self.state.get_variable(scope, context, c.target)
        self.state._add_var_points_flow(src, tgt)
        
    def _apply_alloc(self, scope: 'Scope', context: 'AbstractContext', c: 'AllocConstraint'):
        """Apply allocation constraint: target = new Object."""

        target = self.state.get_variable(scope, context, c.target)

        orig_obj = self.state._heap.get_obj(scope, context, c.alloc_site)
        if orig_obj is not None:
            if isinstance(orig_obj, (FunctionObject, ClassObject)):
                self._capture_lexical_environment(orig_obj, scope, context, c.alloc_site.stmt)
            # Reusing an abstract allocation does not discharge this binding.
            # Coarse contexts can encounter the same site through a synthetic
            # entry scope and a real invocation with distinct target variables.
            self.handle_new_points_to(target, scope, PointsToSet.singleton(orig_obj))
            return
        
        if self.processor.handle_allocation(self, target, scope, context, c):
            return

        if c.alloc_site.kind == AllocKind.FUNCTION:
            obj = self._alloc_function(scope, context, c)
        
        elif c.alloc_site.kind == AllocKind.METHOD:
            obj = self._alloc_method(scope, context, c)

        elif c.alloc_site.kind == AllocKind.CLASS:
            # complex class translation logic, for processing base classes
            if self.config.debug_inheritance:
                logger.info(f"[ALLOC] Allocating class: {c.target.name}")
            obj = self._alloc_class(scope, context, c)
        
        elif c.alloc_site.kind == AllocKind.MODULE:
            obj = self._alloc_module(scope, context, c)
        
        elif c.alloc_site.kind == AllocKind.CONSTANT:
            obj = self._alloc_constant(scope, context, c)
        
        elif c.alloc_site.kind == AllocKind.UNKNOWN:
            obj = UnknownValue(self.state.select_allocation_context(context, c.alloc_site), c.alloc_site)

        elif c.alloc_site.kind == AllocKind.OBJECT and False:
            # logic for instance allocation is located in _apply_call
            obj = None 
        elif not self.config.index_sensitive:
            obj = AbstractObject(alloc_site=c.alloc_site, context=self.state.select_allocation_context(context, c.alloc_site))
                
        else:
            obj = AbstractObject(alloc_site=c.alloc_site, context=self.state.select_allocation_context(context, c.alloc_site))

        if obj is not None:
            self.state._heap.set_obj(scope, context, c.alloc_site, obj)
            pts = PointsToSet.singleton(obj)
            target = self.state.get_variable(scope, context, c.target)
            
            if self.config.debug_inheritance and c.alloc_site.kind == AllocKind.CLASS:
                logger.info(f"[ALLOC] Adding class object to variable: {c.target.name} = {obj.alloc_site.stmt.name if hasattr(obj.alloc_site.stmt, 'name') else obj}")
                logger.info(f"  Target variable: {target}")
            
            # Debug monitoring: record object allocation
            if self._debug_monitor and self._debug_monitor.enabled and self._debug_monitor.track_object_flow:
                obj_id = f"{c.alloc_site.kind.value}:{id(obj)}"
                location = str(c.alloc_site)
                self._debug_monitor.record_object_allocated(
                    obj_id=obj_id,
                    obj_kind=c.alloc_site.kind.value,
                    location=location,
                    target_var=str(c.target)
                )
            self.state.obj_scope[obj] = scope
            self.handle_new_points_to(target, scope, pts)
    
    def handle_new_points_to(self, target: 'Ctx[Any]', scope: 'Scope', pts: 'PointsToSet') -> None:
        if not self.processor.handle_new_points_to(self, target, scope, pts):
            self._enqueue_points_to(scope, NormalNode(target), pts)

    def _enqueue_points_to(self, scope, node, points):
        """Queue only facts not yet processed at this exact PFG node.

        Do not subtract Ctx-only facts or pending work: those may still require
        processor/edge evaluation. New constraints and PFG edges replay existing
        facts independently of this queue. Producer hooks run before this helper,
        and propagated GuardNode callbacks have already run in propagate().
        """
        if self._diagnostics is not None:
            self._diagnostics.bump("points_enqueue_requests")
        delta = points - self.state.get_points_to(node)
        if delta.is_empty():
            self._stats["points_to_enqueue_processed_skips"] += 1
            if self._diagnostics is not None:
                self._diagnostics.bump("points_enqueue_processed_skips")
            return
        if delta != points:
            self._stats["points_to_enqueue_trimmed_requests"] += 1
            if self._diagnostics is not None:
                self._diagnostics.bump("points_enqueue_trimmed")
        self.state._worklist.add((scope, node, delta))
        if self._diagnostics is not None:
            self._diagnostics.bump("points_enqueue_accepted")
    
    def _alloc_constant(self, scope: 'Scope', context: 'AbstractContext', c: 'AllocConstraint') -> 'ConstantObject':
        stmt: 'IRAssign' = c.alloc_site.stmt
        assert isinstance(stmt, IRAssign), f"alloc_site.stmt must be an IRAssign, but got {type(stmt)}"
        obj = ConstantObject(self.state.select_allocation_context(context, c.alloc_site, identity="canonical"), c.alloc_site, stmt.get_rval().value)
        return obj

    def _infer_free_vars(self, ir_func: 'IRFunc') -> Set[str]:
        ir = self.state.scope_manager.get_ir(ir_func, "ir")
        if ir is None:
            return set()
        loads = set()
        stores = set()
        for stmt in ir:
            loads.update(stmt.get_loads())
            stores.update(stmt.get_stores())
            if isinstance(stmt, IRStoreSubscr):
                subslice = stmt.get_slice()
                if isinstance(subslice, ast.Name):
                    loads.add(subslice.id)
        free_vars = loads - stores - ir_func.get_arg_names()
        free_vars.difference_update(ir_func.get_global_vars())
        free_vars.difference_update(ir_func.get_nonlocal_vars())
        return {name for name in free_vars if name and name.isidentifier()}


    def _resolve_outer_var_kind(self, scope: 'Scope', var_name: str) -> VariableKind:
        stmt = getattr(scope, "stmt", None)
        if isinstance(stmt, IRModule):
            return VariableKind.GLOBAL
        if isinstance(stmt, IRFunc):
            if var_name in stmt.get_global_vars():
                return VariableKind.GLOBAL
            if var_name in stmt.get_nonlocal_vars():
                return VariableKind.NONLOCAL
            if var_name in stmt.get_cell_vars():
                return VariableKind.CELL
            if var_name in stmt.arg_names:
                return VariableKind.LOCAL
        return VariableKind.LOCAL

    @staticmethod
    def is_entrance(ir_func: 'IRFunc') -> bool:
        # return True
        name = ir_func.name
        if name.startswith("test") or name.startswith("__init__") or name.startswith("main"):
            return True
        return False

    def _capture_lexical_environment(self, obj, scope, context, ir):
        """Join lexical environments when finite allocation contexts coincide.

        Existing captured variables remain stable aliases. Persistent edges
        join both directions, preserving captured reads and writes through all
        merged environments, including facts arriving after allocation;
        allocation memoization must not discard an environment after the first.
        """
        defining_scope = scope
        while isinstance(defining_scope.stmt, IRClass) and defining_scope.parent is not None:
            defining_scope = defining_scope.parent
        names = ir.get_cell_vars()
        if not names and isinstance(ir, IRFunc):
            names = self._infer_free_vars(ir)
        cells = {}
        for name in names:
            kind = self._resolve_outer_var_kind(defining_scope, name)
            var = self.variable_factory.make_variable(name, kind)
            cells[name] = self.state.get_variable(defining_scope, defining_scope.context, var)
        globals_ = {
            name: self.state.get_variable(scope, context,
                self.variable_factory.make_variable(name, VariableKind.GLOBAL))
            for name in ir.get_global_vars()
        }
        nonlocals = {
            name: self.state.get_variable(defining_scope, defining_scope.context,
                self.variable_factory.make_variable(name,
                    self._resolve_outer_var_kind(defining_scope, name)))
            for name in ir.get_nonlocal_vars()
        }
        for sources, getter, setter in (
            (cells, self.state.get_cell_vars, self.state.set_cell_vars),
            (globals_, self.state.get_global_vars, self.state.set_global_vars),
            (nonlocals, self.state.get_nonlocal_vars, self.state.set_nonlocal_vars),
        ):
            captured = dict(getter(obj))
            for name, source in sources.items():
                previous = captured.get(name)
                if previous is None:
                    captured[name] = source
                elif previous != source:
                    self.state._add_var_points_flow(source, previous)
                    self.state._add_var_points_flow(previous, source)
            setter(obj, captured)
    
    def _alloc_method(self, scope: 'Scope', context: 'AbstractContext', c: 'AllocConstraint') -> 'MethodObject':
        ir_func = c.alloc_site.stmt
        assert isinstance(ir_func, IRFunc), f"AllocSite to be allocated as function {c.alloc_site} should be IRFunc, {type(ir_func)} got!"

        heap_context = self.state.select_allocation_context(context, c.alloc_site)
        identity = ("method", heap_context, c.alloc_site, scope.obj)
        obj = self._definition_objects.get(identity)
        if obj is None:
            obj = MethodObject(heap_context, c.alloc_site, scope, c.alloc_site.stmt, scope.obj, None)
            self._definition_objects[identity] = obj
        
        self._capture_lexical_environment(obj, scope, context, ir_func)

        # Processing contents at once

        func_obj = obj
        func_ir = ir_func
        call_context = self.context_selector.select_call_context(ir_func, context, scope.obj, None)
        # func_ir = self.function_registry[func_name]
        alloc_site = func_obj.alloc_site
        # callee_scope = Scope.new(func_obj, method_scope.module, call_context, func_ir, method_scope)
        callee_scope = Scope.new(func_obj, scope.module, call_context, func_ir, scope)
        old_scope = self.ir_translator._current_scope
        # old_context = self.ir_translator._current_context        
        self.ir_translator._current_scope = alloc_site.stmt
        # self.ir_translator._current_context = call_context
        
        if self.is_entrance(func_ir):        
            try:
                body_constraints = self.ir_translator.translate_function(func_ir)
            except Exception as e:
                body_constraints = []
            finally:
                self.ir_translator._current_scope = old_scope
                # self.ir_translator._current_context = old_context
            for constraint in body_constraints:
                self.add_constraint(callee_scope, call_context, constraint)
            
        return obj

    def _alloc_function(self, scope: 'Scope', context: 'AbstractContext', c: 'AllocConstraint') -> 'FunctionObject':
        ir_func = c.alloc_site.stmt
        # logger.info(f"alloc function {c}")
        assert isinstance(ir_func, IRFunc), f"AllocSite to be allocated as function {c.alloc_site} should be IRFunc, {type(ir_func)} got!"

        heap_context = self.state.select_allocation_context(context, c.alloc_site)
        identity = ("function", heap_context, c.alloc_site)
        obj = self._definition_objects.get(identity)
        if obj is None:
            obj = FunctionObject(heap_context, c.alloc_site, scope, c.alloc_site.stmt)
            self._definition_objects[identity] = obj
        
        self._capture_lexical_environment(obj, scope, context, ir_func)

        # Processing contents at once
        func_obj = obj
        func_ir = ir_func
        call_context = self.context_selector.select_call_context(ir_func, context, None, None)
        alloc_site = func_obj.alloc_site
        callee_scope = Scope.new(func_obj, scope.module, call_context, func_ir, scope)
        old_scope = self.ir_translator._current_scope
        self.ir_translator._current_scope = alloc_site.stmt
        
        if self.is_entrance(ir_func):
            try:
                body_constraints = self.ir_translator.translate_function(func_ir)
            except Exception as e:
                body_constraints = []
            finally:
                self.ir_translator._current_scope = old_scope
                # self.ir_translator._current_context = old_context

            for constraint in body_constraints:
                self.add_constraint(callee_scope, call_context, constraint)

        return obj
    
    def _alloc_class(self, scope: 'Scope', context: 'AbstractContext', c: 'AllocConstraint') -> 'ClassObject':
        ir_cls = c.alloc_site.stmt
        # logger.info(f"alloc class {c}")
        assert isinstance(ir_cls, IRClass), f"AllocSite to be allocated as class {c.alloc_site} should be IRClass, {type(ir_cls)} got!"

        heap_context = self.state.select_allocation_context(context, c.alloc_site)
        identity = ("class", heap_context, c.alloc_site)
        obj = self._definition_objects.get(identity)
        if obj is None:
            obj = ClassObject(heap_context, c.alloc_site, scope, c.alloc_site.stmt)
            self._definition_objects[identity] = obj
        from .processor.class_protocol import record_class_residual
        record_class_residual(
            self, scope, ir_cls,
            "Class namespace/metaclass relation retained; __prepare__, metaclass selection, "
            "__new__/__init__ class statement hooks, __set_name__ and __init_subclass__ remain open",
        )
        
        cls_context = obj.context
        
        self._capture_lexical_environment(obj, scope, context, ir_cls)

        # resolve the content of module
        ctx_scope = Scope.new(obj, scope.module, cls_context, ir_cls, scope)
        self.state.set_internal_scope(obj, ctx_scope)

        # inner_var = self.state.get_variable(ctx_scope, context, self.variable_factory.make_variable("$class", VariableKind.LOCAL))
        # self.state._worklist.add((scope, NormalNode(inner_var), PointsToSet.singleton(obj)))

        # translate the IRs in the imported module        
        constraints = self.ir_translator.translate_class(ir_cls)
        own_variables = tuple(self.ir_translator.used_variables)
        for constraint in constraints:
            self.add_constraint(ctx_scope, cls_context, constraint)
        
        for inner_var in own_variables:
            ctx_field = self.state.get_field(scope, context, obj, attr(inner_var.name))
            ctx_inner_var = self.state.get_variable(ctx_scope, cls_context, inner_var)
            self.state._add_var_points_flow(ctx_inner_var, ctx_field)
            if self.config.debug_inheritance:
                logger.info(f"[CLASS] Storing field {obj.alloc_site.stmt.name}.{inner_var.name}: {ctx_inner_var} -> {ctx_field}")

        from .processor.construction_hooks import install_class_statement_hooks
        install_class_statement_hooks(self, scope, context, c, obj, ctx_scope,
                                      own_variables)
        return obj

    def _alloc_module(self, scope: 'Scope', context: 'AbstractContext', c: 'AllocConstraint') -> 'ModuleObject':
        assert c.alloc_site.kind == AllocKind.MODULE, "AllocSite kind in module allocation should be Module"
        assert c.alloc_site.stmt is not None, "AllocSite should have IRImport as stmt"

        from pythonstan.ir import IRModule
        module_ir = (c.alloc_site.stmt if isinstance(c.alloc_site.stmt, IRModule) else
                     self.state.scope_manager.module_graph.get_succ_module(scope.module.stmt, c.alloc_site.stmt))
                
        if module_ir is None:
            self._unknown_tracker.record(
                UnknownKind.IMPORT_NOT_FOUND,
                str(c.alloc_site),
                f"Attempting to import unknown module: {c.alloc_site.stmt}",
                context=str(context)
            )
            
            if self.config.verbose:
                logger.warning(f"[UNKNOWN] Module not found at {c.alloc_site}")
            
            unknown_obj = UnknownValue(
                context=scope.context,
                alloc_site=AllocSite(stmt=c.alloc_site.stmt, kind=AllocKind.UNKNOWN),
            )
            return unknown_obj

        self._modules.add(module_ir)
        if self.state.scope_manager.get_ir(module_ir, 'ir') is None:
            self.state._residual_effects[(scope, c, 'unlowered_module')] = {
                'origin': module_ir.get_qualname(), 'context': str(context),
                'reason': 'Import target was registered but not lowered (depth/lazy/source boundary)',
                'effects': ['untranslated_source', 'may_mutate_heap', 'may_invoke_callbacks', 'may_raise'],
            }
        # Modules model one sys.modules identity per source in this analysis,
        # rather than one object per import statement/calling context. The
        # driver already seeded every lowered module (including builtins).
        if self._canonical_module_objects is None:
            self._canonical_module_objects = {
                internal.stmt: obj for obj, internal in self.state._internal_scope.items()
                if isinstance(obj, ModuleObject)
            }
        existing = self._canonical_module_objects.get(module_ir)
        if existing is None:
            module_ctx = self.state.select_allocation_context(context, AllocSite.from_ir_node(module_ir, AllocKind.MODULE), identity="canonical")
            existing = ModuleObject(module_ctx, AllocSite.from_ir_node(module_ir, AllocKind.MODULE), module_ir)
            ctx_scope = Scope.new(existing, None, module_ctx, module_ir, None)
            # Register before translating to close import cycles.
            self.state.set_internal_scope(existing, ctx_scope)
            self._canonical_module_objects[module_ir] = existing
            for constraint in self.ir_translator.translate_module(module_ir, c.alloc_site.stmt):
                self.add_constraint(ctx_scope, module_ctx, constraint)
        self._bind_package_modules()
        return existing

    def _bind_package_modules(self):
        """Expose imported submodules through the shared parent package object."""
        if self._package_binding_version == len(self._canonical_module_objects):
            return
        modules = {ir.get_qualname(): (obj, self.state.get_internal_scope(obj))
                   for ir, obj in self._canonical_module_objects.items()}
        self._package_binding_version = len(modules)
        bound = getattr(self, "_package_module_bindings", None)
        if bound is None:
            bound = self._package_module_bindings = set()
        for name, (obj, internal) in modules.items():
            parent_name, separator, local_name = name.rpartition('.')
            if not separator or parent_name not in modules:
                continue
            parent_obj, parent_scope = modules[parent_name]
            identity = (parent_obj, obj)
            if identity in bound:
                continue
            bound.add(identity)
            self.state.register_module_export(parent_scope, local_name)
            variable = self.state.get_variable(parent_scope, parent_scope.context,
                                               Variable(local_name, VariableKind.GLOBAL))
            self.handle_new_points_to(variable, parent_scope, PointsToSet.singleton(obj))

    def _apply_inheritance(self, scope: 'Scope', variable: 'Ctx', c: 'InheritanceConstraint', pts: 'PointsToSet'):
        """Apply inheritance constraint: resolve field from base class.
        
        For each base class object in pts, get the field and create PFG edge
        to the selector node. This allows parent class fields to flow to
        the inheritance target (used by ClassObject and SuperObject).
        """
        from .object import ClassObject
        for base_obj in pts:
            # For class inheritance, use the base class's internal scope
            if isinstance(base_obj, ClassObject):
                base_internal_scope = self.state.get_internal_scope(base_obj)
                field_access = self.state.get_field(base_internal_scope, base_obj.context, base_obj, c.field)
                if self.config.debug_inheritance:
                    logger.info(f"[INHERIT] Applying inheritance: {base_obj.alloc_site.stmt.name if hasattr(base_obj.alloc_site.stmt, 'name') else base_obj}.{c.field} -> selector")
                    logger.info(f"  Base internal scope: {base_internal_scope.stmt.get_qualname() if hasattr(base_internal_scope.stmt, 'get_qualname') else base_internal_scope.stmt}")
                    logger.info(f"  Field access: {field_access}")
                    logger.info(f"  Field pts: {self.state.get_points_to(field_access)}")
            else:
                field_access = self.state.get_field(scope, scope.context, base_obj, c.field)
            edge = PointerFlowEdge(NormalNode(field_access), c.target, PointerFlowKind.NORMAL)
            # Register the edge with the selector node with its inheritance index
            c.target.add_edge(edge, c.index)
            self.state._add_points_flow_edge(edge)
    
    def _apply_special_method_lookup(self, scope, constraint, objects):
        target = self.state.get_variable(scope, scope.context, constraint.target)
        for receiver in objects:
            if isinstance(receiver, InstanceObject):
                cls = receiver.class_obj
                field = self.state.get_field(scope, scope.context, cls, attr(constraint.method))
                # This edge persists when class/method facts arrive later.
                self.state._add_var_points_flow(field, target)
            else:
                self.add_constraint(scope, scope.context, ResidualEffectConstraint(
                    location=f"{scope.stmt.get_qualname()}:{constraint.method}",
                    reason="implicit special-method lookup for a non-instance receiver is unresolved",
                    effects=("may_invoke_callbacks", "may_mutate_heap", "may_raise"),
                ))
        return True

    def _apply_load(self, scope: 'Scope', variable: 'Ctx', c: 'LoadConstraint', pts: 'PointsToSet'):
        """Apply load constraint: target = base.field or target = base[index]."""
        context = scope.context
        target_var = self.state.get_variable(scope, context, c.target)
        
        for base_obj in pts:
            if base_obj.kind == AllocKind.UNKNOWN:
                self.handle_new_points_to(target_var, scope, PointsToSet.singleton(base_obj))
                continue
            if isinstance(base_obj, InstanceObject) and c.field == attr("__dict__"):
                from .processor.heap_mapping import instance_namespace_view
                view = instance_namespace_view(self.state, scope, base_obj)
                self.handle_new_points_to(target_var, scope, PointsToSet.singleton(view))
                self.state._residual_effects[(scope, c, "instance_namespace")] = {
                    "origin": str(c), "context": str(context),
                    "reason": "Own-namespace may view leaves slots, custom __dict__, replacement and deletion open",
                    "effects": ["namespace_presence", "custom_descriptor", "deletion", "may_raise"],
                }
            # Special handling for module imports: from module import name
            # Instead of using field access, directly copy from module's variable
            if isinstance(base_obj, ModuleObject) and c.field and c.field.kind.name == 'ATTR':
                # Get the module's internal scope
                module_scope = self.state.get_internal_scope(base_obj)
                if module_scope:
                    # Create variable in module scope for the imported name
                    field_var = self.state.get_field(module_scope, module_scope.context,
                                                     base_obj, c.field)
                    self.state._add_var_points_flow(field_var, target_var)
                    continue
            
            # type.__new__ is a class allocator, not ordinary instance __new__.
            from .processor.class_protocol import is_type_builtin
            if c.field == attr("__new__") and is_type_builtin(base_obj):
                method_obj = BuiltinMethodObject(
                    context=context, alloc_site=AllocSite("<builtin_method:type.__new__>", AllocKind.BUILTIN),
                    method_name="__new__", receiver=base_obj, receiver_var=c.base,
                )
                self.handle_new_points_to(target_var, scope, PointsToSet.singleton(method_obj))
                continue

            # Special handling for builtin methods on container objects
            if c.field and c.field.kind == FieldKind.ATTRIBUTE and c.field.name:
                builtin_type = None
                if isinstance(base_obj, ListObject):
                    builtin_type = "list"
                elif isinstance(base_obj, DictObject):
                    builtin_type = "dict"
                elif isinstance(base_obj, TupleObject):
                    builtin_type = "tuple"
                elif isinstance(base_obj, SetObject):
                    builtin_type = "set"
                elif isinstance(base_obj, ConstantObject):
                    if isinstance(base_obj.value, str):
                        builtin_type = "str"
                    elif isinstance(base_obj.value, bytes):
                        builtin_type = "bytes"
                    elif isinstance(base_obj.value, bytearray):
                        builtin_type = "bytearray"
                elif isinstance(base_obj, BuiltinInstanceObject):
                    builtin_type = base_obj.builtin_type
                elif isinstance(base_obj, GeneratorObject):
                    builtin_type = "generator"
                elif isinstance(base_obj, AsyncGeneratorObject):
                    builtin_type = "async_generator"
                elif isinstance(base_obj, CoroutineObject):
                    builtin_type = "coroutine"

                if builtin_type:
                    builtin_methods = self.state._get_builtin_methods_for_type(builtin_type)
                    if c.field.name in builtin_methods:
                        method_alloc = AllocSite(
                            stmt=f"<builtin_method:{c.field.name}>",
                            kind=AllocKind.BUILTIN,
                        )
                        method_obj = BuiltinMethodObject(
                            context=context,
                            alloc_site=method_alloc,
                            method_name=c.field.name,
                            receiver=base_obj,
                            receiver_var=c.base,
                        )
                        self.state._worklist.add((scope, NormalNode(target_var), PointsToSet.singleton(method_obj)))
                        continue
            
            # Default behavior: use field access for classes, instances, etc.
            field_access = self.state.get_field(scope, context, base_obj, c.field)
            self.state._add_var_points_flow(field_access, target_var)
            if c.field and c.field.kind == FieldKind.KEY:
                if isinstance(base_obj, DictObject):
                    wildcard = self.state.get_field(scope, context, base_obj, wildcard_key())
                    self.state._add_var_points_flow(wildcard, target_var)
                elif c.index is not None and not (
                    isinstance(base_obj, MappingItemObject)
                    and c.field.key_type == "number" and c.field.name in {"0", "1"}
                ):
                    aggregate = self.state.get_field(scope, context, base_obj, elem())
                    self.state._add_var_points_flow(aggregate, target_var)
    
    def _apply_store(self, scope: 'Scope', variable: 'Ctx', c: 'StoreConstraint', pts: 'PointsToSet'):
        """Apply store constraint: base.field = source or base[index] = source."""
        context = scope.context
        source_var = self.state.get_variable(scope, context, c.source)

        for base_obj in pts:
            field_access = self.state.get_write_field(scope, context, base_obj, c.field)
            self.state._add_var_points_flow(source_var, field_access)
            if isinstance(base_obj, ModuleObject) and c.field and c.field.kind == FieldKind.ATTRIBUTE:
                module_scope = self.state.get_internal_scope(base_obj)
                if module_scope is not None:
                    self.state.register_module_export(module_scope, c.field.name)
                    exported = self.state.get_variable(module_scope, module_scope.context,
                                                       Variable(c.field.name, VariableKind.GLOBAL))
                    self.state._add_var_points_flow(source_var, exported)
            if isinstance(base_obj, DictObject) and c.field:
                if c.field.kind == FieldKind.KEY:
                    aggregate = self.state.get_field(scope, context, base_obj, elem())
                    self.state._add_var_points_flow(source_var, aggregate)
                elif c.field.kind == FieldKind.ELEMENT and not c.summary_only:
                    wildcard = self.state.get_field(scope, context, base_obj, wildcard_key())
                    self.state._add_var_points_flow(source_var, wildcard)
                elif c.field.kind == FieldKind.WILDCARD_KEY:
                    aggregate = self.state.get_field(scope, context, base_obj, elem())
                    self.state._add_var_points_flow(source_var, aggregate)
    
    def _apply_call(self, scope: 'Scope', variable: 'Ctx', c: 'CallConstraint', pts: 'PointsToSet') -> bool:
        """Apply call constraint: target = callee(args...)."""
        # logger.info(f"Applying call constraint: {c.call_site} -> {pts}")
        
        # Debug monitoring: record call constraint processing
        if self._debug_monitor and self._debug_monitor.enabled:
            self._debug_monitor.record_call_constraint_processed(
                call_site=str(c.call_site),
                callee_var=str(variable),
                callee_pts_size=len(pts)
            )
        
        # Check for empty callee
        if len(pts) == 0:
            if self._debug_monitor and self._debug_monitor.enabled:
                self._debug_monitor.record_call_failed(
                    call_site=str(c.call_site),
                    reason="empty_callee",
                    details="Callee points-to set is empty"
                )
            return False
         
        changed = False
        for callee_obj in pts:
            if callee_obj.kind == AllocKind.UNKNOWN:
                self._mark_unknown_call(scope, c, "unknown callable value")
                self._unknown_tracker.record(
                    UnknownKind.CALLEE_UNMODELED, str(c.call_site),
                    "Call target includes an unresolved value", context=str(variable),
                )
                changed = True
                continue
            # logger.info(f"Handling function call: {c.call_site} -> {callee_obj.alloc_site.stmt}\n    {type(callee_obj)} {type(callee_obj.alloc_site.stmt)}")
            if self.processor.handle_call(self, variable, scope, c, callee_obj):
                changed = True                    
            # TODO add the __callable__ magic method
            else:
                self._record_non_callable_evidence(scope, variable, c, callee_obj)
                if self._is_callable_for_constraint(c, callee_obj):
                    self._mark_unknown_call(scope, c, "unmodeled callable target")
                    self._unknown_tracker.record(
                        UnknownKind.CALLEE_UNMODELED,
                        str(c.call_site),
                        f"No call model handled target {callee_obj} for '{c.callee.name}'",
                        context=f"{variable}: {callee_obj}",
                    )
                
                # Debug monitoring: record non-callable
                if self._debug_monitor and self._debug_monitor.enabled:
                    self._debug_monitor.record_call_failed(
                        call_site=str(c.call_site),
                        reason="non_callable",
                        details=f"Object kind: {callee_obj.kind.value}"
                    )
                
                if self.config.verbose:
                    logger.warning(f"[UNKNOWN] Non-callable at {c.call_site}: {callee_obj}")
        return changed

    def _record_non_callable_evidence(
        self,
        scope: 'Scope',
        variable: 'Ctx[Any]',
        call: 'CallConstraint',
        callee_obj: 'AbstractObject'
    ) -> None:
        """Collect non-callable evidence during solving without reporting."""
        if not self.config.track_unknowns:
            return
        key = (variable, scope, call)
        observed = self._non_callable_evidence.setdefault(key, set())
        observed.add(callee_obj.kind.value)

    def _mark_unknown_call(self, scope, call, reason):
        """Publish real state havoc and wildcard events for an arbitrary call."""
        identity = (scope, call)
        self.state._residual_effects[identity] = {
            "origin": str(call.call_site), "reason": reason,
            "context": str(scope.context),
            "return_target": call.target.name if call.target else None,
            "effects": ["unknown_return", "may_mutate_heap", "may_invoke_callbacks", "may_raise"],
        }
        obj = self._unknown_results.get(identity)
        if obj is None:
            if self.config.verbose:
                logger.warning("[UNKNOWN] %s at %s", reason, call.call_site)
            site = AllocSite(call.call_site.statement, AllocKind.UNKNOWN)
            obj = UnknownValue(self.state.select_allocation_context(scope.context, site), site)
            self._unknown_results[identity] = obj
            self.state._heap.set_obj(scope, scope.context, site, obj)
            self.state.obj_scope[obj] = scope
            # Side effects occur whether or not the return is consumed.
            self.state.apply_unknown_effects(scope, obj, str(call.call_site))
        if call.target is not None:
            target = self.state.get_variable(scope, scope.context, call.target)
            self.handle_new_points_to(target, scope, PointsToSet.singleton(obj))

    def _is_callable_for_constraint(self, call: 'CallConstraint', callee_obj: 'AbstractObject') -> bool:
        """Side-effect-free check for callability consistent with call handling."""
        if isinstance(
            callee_obj,
            (
                MethodObject,
                FunctionObject,
                BuiltinObject,
                BuiltinFunctionObject,
                BuiltinMethodObject,
                BuiltinClassObject,
                BuiltinInstanceObject,
            ),
        ) or callee_obj.kind == AllocKind.BUILTIN:
            return True
        if isinstance(callee_obj, ClassObject):
            return True
        callee_name = getattr(call.callee, "name", str(call.callee))
        if callee_name.startswith("$call@"):
            return False
        return True

    @staticmethod
    def _is_internal_helper_callee_name(callee_name: str) -> bool:
        """Identify internal helper calls that are speculative/fallback-safe."""
        return callee_name.startswith(
            (
                "$getattribute@",
                "$getattr@",
                "$setattr@",
                "$descriptor_",
                "$new@",
                "$bound_init@",
            )
        )

    def _finalize_unknowns_at_fixpoint(self) -> None:
        """Scan unresolved constraints after convergence to report true unknowns."""

        # A missing optional protocol hook is expected. Required calls, including
        # generated __call__ dispatches, must remain visible for every module.
        for trigger_var, _scope, constraint in self.state.constraints.iter_scoped():
            if not isinstance(constraint, CallConstraint):
                continue
            pts = self.state.get_points_to(trigger_var)
            if not pts.is_empty():
                continue
            callee_name = getattr(constraint.callee, "name", str(constraint.callee))
            if self._is_internal_helper_callee_name(callee_name):
                continue
            self._mark_unknown_call(_scope, constraint, "callee unresolved at fixpoint")
            
            self._unknown_tracker.record(
                UnknownKind.CALLEE_EMPTY,
                str(constraint.call_site),
                f"Callee '{callee_name}' unresolved at fixpoint (points-to empty)",
                context=str(trigger_var)
            )
            

        # Non-callable calls: only report if no callable targets at fixpoint.
        for (trigger_var, _scope, constraint), _kinds in self._non_callable_evidence.items():
            pts = self.state.get_points_to(trigger_var)
            if pts.is_empty():
                continue
            if any(self._is_callable_for_constraint(constraint, obj) for obj in pts):
                continue
            callee_name = getattr(constraint.callee, "name", str(constraint.callee))
            if self._is_internal_helper_callee_name(callee_name):
                continue
            
            kind_list = ", ".join(sorted({obj.kind.value for obj in pts}))
            self._unknown_tracker.record(
                UnknownKind.CALLEE_NON_CALLABLE,
                str(constraint.call_site),
                f"No callable targets at fixpoint for '{callee_name}' (kinds: {kind_list})",
                context=str(trigger_var)
            )

 
    def _handle_class_instantiation(self, scope: 'Scope', context: 'AbstractContext', call: 'CallConstraint', class_obj: 'AbstractObject') -> bool:
        """Handle class instantiation: call __new__, then __init__ conditionally."""
        # logger.info(f"Handling class instantiation: {call.call_site} -> {class_obj.alloc_site.stmt}")

        if call.semantic_partition is not None:
            self.add_constraint(scope, context, ResidualEffectConstraint(
                str(call.call_site),
                "Class-valued keyword protocol callable retains conservative constructor flows; "
                "key/result partitioning through metaclass/new/init helpers is unproved",
                ("keyword_key_result_correlation", "class_protocol_partition", "may_raise")))

        instance_alloc = AllocSite(call.call_site.statement, AllocKind.INSTANCE)
        if self.context_selector:
            alloc_context = self.state.select_allocation_context(context, instance_alloc, class_obj)
        else:
            alloc_context = context
        instance_obj = InstanceObject(alloc_context, instance_alloc, class_obj)

        cls_scope = self.state.get_internal_scope(class_obj)
        params = ordered_binding_token(
            [instance_obj] + [self.state.get_variable(scope, context, arg) for arg in call.args],
            [(k, self.state.get_variable(scope, context, arg)) for k, arg in call.kwargs],
        )

        instance_parent = cls_scope.parent
        assert instance_parent, f"{cls_scope} : {class_obj} has no parent"
        instance_ctx = self.context_selector.select_call_context(call.call_site, context, instance_obj, params)
        instance_scope = Scope.new(instance_obj, instance_parent.module, instance_ctx, class_obj.alloc_site.stmt, instance_parent)
        self.state.set_internal_scope(instance_obj, instance_scope)

        # TAC may assign the same synthetic line/column to distinct calls.
        # Private helper slots must include statement identity, not location alone.
        helper_site = f"{call.call_site.short_id()}@{id(call.call_site.statement)}@{id(class_obj)}"

        # Seed __new__ result with a fresh instance as a conservative default
        new_result_var = self.variable_factory.make_variable(f"$new_result@{helper_site}")
        ctx_new_result_var = self.state.get_variable(scope, context, new_result_var)
        self.handle_new_points_to(ctx_new_result_var, scope, PointsToSet.singleton(instance_obj))

        # Load and call C.__new__(C, *args, **kwargs)
        cls_var = self.variable_factory.make_variable(f"$class@{helper_site}")
        ctx_cls_var = self.state.get_variable(scope, context, cls_var)
        self.handle_new_points_to(ctx_cls_var, scope, PointsToSet.singleton(class_obj))

        new_callee_var = self.variable_factory.make_variable(f"$new@{helper_site}")
        self.add_constraint(
            scope,
            context,
            LoadConstraint(
                base=cls_var,
                field=attr("__new__"),
                target=new_callee_var,
            ),
        )
        new_args = (cls_var,) + call.args
        self.add_constraint(
            scope,
            context,
            CallConstraint(
                callee=new_callee_var,
                args=new_args,
                kwargs=call.kwargs,
                target=new_result_var,
                call_site=call.call_site,
            ),
        )

        # Flow __new__ result to call target
        if call.target:
            target_var = self.state.get_variable(scope, context, call.target)
            self.state._add_var_points_flow(ctx_new_result_var, target_var)

        # Call __init__ only for instance-like results
        init_base_var = self.variable_factory.make_variable(f"$init_base@{helper_site}")
        ctx_init_base_var = self.state.get_variable(scope, context, init_base_var)

        guard = GuardNode(
            lambda edge, pts: PointsToSet.from_objects(
                [obj for obj in pts if isinstance(obj, InstanceObject)]
            )
        )
        self.state._add_points_flow_edge(
            PointerFlowEdge(NormalNode(ctx_new_result_var), guard, PointerFlowKind.NORMAL)
        )
        self.state._add_points_flow_edge(
            PointerFlowEdge(guard, NormalNode(ctx_init_base_var), PointerFlowKind.NORMAL)
        )

        init_callee_var = self.variable_factory.make_variable(f"$bound_init@{helper_site}")
        self.add_constraint(
            scope,
            context,
            LoadConstraint(
                base=init_base_var,
                field=attr("__init__"),
                target=init_callee_var,
            ),
        )
        self.add_constraint(
            scope,
            context,
            CallConstraint(init_callee_var, call.args, call.kwargs, None, call.call_site),
        )

        return True
    
    def _handle_builtin_call(self, scope: 'Scope', context: 'AbstractContext', call: 'CallConstraint', builtin_obj: 'AbstractObject') -> bool:
        """Handle builtin call: use builtin API handler to generate constraints.
        
        This method delegates to the BuiltinAPIHandler which creates appropriate
        constraints and PFG edges for builtin operations.
        """
        if not self.builtin_manager:
            logger.debug("Cannot handle builtin call: no builtin manager")
            return False

        # Get the builtin API handler
        handler = self.builtin_manager.get_handler()
        if not handler:
            logger.debug("Builtin handler not initialized")
            return False
        
        # Delegate to handler to generate constraints
        try:
            constraints = handler.handle_builtin_call(scope, context, call, builtin_obj)
            
            # Add all generated constraints to the solver
            for constraint in constraints:
                self.add_constraint(scope, context, constraint)
            
            return len(constraints) > 0
        finally:
            pass        
        return False
    
    def set_global_work_limit(self,limit):
        if self.work_ledger.used or self._work_iteration_started:
            raise ValueError("Global work budget must be fixed before execution")
        from pythonstan.analysis.contracts.work import SharedWorkLedger
        self.work_ledger=SharedWorkLedger(limit)

    def set_intrinsic_provider(self,provider):
        if self._work_iteration_started or self.work_ledger.used:
            raise ValueError("Intrinsic provider must be injected before execution")
        if provider is not None and (not callable(getattr(provider,"supports",None))
                                     or not callable(getattr(provider,"apply",None))):
            raise TypeError("Intrinsic provider requires supports/apply")
        self.intrinsic_provider=provider

    def _try_ai_intrinsic(self,scope,context,call,builtin_obj):
        provider=self.intrinsic_provider
        if not self.config.enable_ai_intrinsics or provider is None or not provider.supports(builtin_obj):
            return False
        started=time.perf_counter()
        try:
            return provider.apply(self,scope,context,call,builtin_obj,self.work_ledger)
        finally:
            self.intrinsic_seconds+=time.perf_counter()-started

    def begin_intrinsic(self,scope,context,call,builtin):
        return self._register_builtin_dependencies(scope,context,call,builtin)

    def intrinsic_points(self,scope,context,variable):
        return self.state.get_points_to(self.state.get_variable(scope,context,variable))

    def make_intrinsic_adapter(self):
        from .pta_query_adapter import PtaQueryAdapter
        return PtaQueryAdapter(self.state,self.class_hierarchy,self.context_selector)

    def intrinsic_budget_options(self):
        return dict(self.config.ai_budget)

    def intrinsic_counter(self,event):
        if event not in {"attempts","applied","deferred"}:
            raise ValueError("Unknown intrinsic counter")
        self._stats["ai_intrinsic_"+event]+=1

    def intrinsic_fallback(self,scope,call,reason):
        self._record_ai_fallback(scope,call,reason)

    def watch_intrinsic_read(self,invocation,variable):
        self._watch_builtin_variable(variable,invocation)

    def watch_intrinsic_object(self,invocation,obj):
        self._builtin_object_dependencies.setdefault(obj,set()).add(invocation)

    def watch_intrinsic_field(self,invocation,scope,context,obj,field):
        self._watch_builtin_variable(self.state.get_field(scope,context,obj,field),invocation)

    def publish_intrinsic_return(self,scope,context,target,objects):
        variable=self.state.get_variable(scope,context,target)
        self.handle_new_points_to(variable,scope,PointsToSet.from_objects(objects))

    def publish_intrinsic_write(self,scope,context,obj,field,objects):
        variable=self.state.get_write_field(scope,context,obj,field)
        self.handle_new_points_to(variable,scope,PointsToSet.from_objects(objects))

    def _record_ai_fallback(self, scope, call, reason):
        self._stats["ai_intrinsic_fallbacks"] += 1
        self._unknown_tracker.record(
            UnknownKind.AI_FALLBACK, str(call.call_site), reason, context=str(scope),
        )
    
    def query(self) -> ISolverQuery:
        return SolverQuery(
            self.state,
            self._stats,
            self._unknown_tracker,
            class_hierarchy=self.class_hierarchy,
            solver=self,
        )


# Legacy import surface delegates to the single query implementation.
from .query import SolverQuery


# Capture the native entry points once, before an application can install
# custom hooks. The optional shortcut refuses changed publication contracts.
bind_native_solver_contract(PointerSolver)
