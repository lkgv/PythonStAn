"""Opt-in, guarded builtin initialization; native stores remain canonical."""

from __future__ import annotations

from collections import Counter, OrderedDict, defaultdict, deque
from collections.abc import Sequence
from dataclasses import fields
from typing import TYPE_CHECKING, NoReturn

from pythonstan.analysis.contracts.json_data import JsonObject

if TYPE_CHECKING:
    from .solver import PointerSolver

from pythonstan.analysis.contracts.work import SharedWorkLedger

from . import points_to_set
from .config import Config
from .constraints import ConstraintManager
from .context import (
    CallStringContext,
    Ctx,
    HybridContext,
    NativeContext,
    ObjectContext,
    ParamContext,
    ReceiverContext,
    Scope,
    SummaryContext,
    TypeContext,
)
from .diagnostics import pure_counter_signature
from .heap_model import HeapModel
from .object import AllocKind, AllocSite, BuiltinFunctionObject
from .pointer_flow_graph import NormalNode, PointerFlowGraph
from .points_to_set import PointsToSet
from .processor import (
    AttributeSemanticsProcessor,
    ClassProtocolProcessor,
    ComposeProcessor,
    ContainerProcessor,
    GeneratorProcessor,
    NormalCallProcessor,
    Processor,
    SuperResolveProcessor,
)
from .scheduler import NativeScheduler
from .state import PointerAnalysisState, Worklist
from .variable import Variable, VariableFactory, VariableKind
from .worklist import ConsumerFairWorklist

_WORK_REMAINING_PROPERTY: object = vars(SharedWorkLedger)["remaining"]
if not isinstance(_WORK_REMAINING_PROPERTY, property):
    raise TypeError("work remaining must be a property")
_WORK_REMAINING_FGET = _WORK_REMAINING_PROPERTY.fget


_LEAVES = (
    GeneratorProcessor,
    AttributeSemanticsProcessor,
    ClassProtocolProcessor,
    NormalCallProcessor,
    ContainerProcessor,
    SuperResolveProcessor,
)
_HOOKS = (
    "handle_pts",
    "handle_new_points_to",
    "handle_new_constraint",
    "handle_constraint",
    "handle_call",
    "handle_allocation",
)
_METHOD_NAMES: dict[type[object], tuple[str, ...]] = {
    PointerAnalysisState: (
        "get_points_to",
        "set_points_to",
        "get_variable",
        "set_variable",
        "_add_points_flow_edge",
        "_add_var_points_flow",
    ),
    PointerFlowGraph: ("add_edge", "propagate", "flow_through_edge"),
    ConstraintManager: ("add", "iter_scoped_by_variable"),
    HeapModel: ("get_variable", "set_variable"),
    VariableFactory: ("make_variable",),
    Worklist: ("add", "pop", "empty"),
    ConsumerFairWorklist: ("add", "add_seed", "pop", "promote", "empty"),
    SharedWorkLedger: ("consume", "snapshot"),
    NativeScheduler: ("select", "pop", "pending"),
    Processor: _HOOKS,
    ComposeProcessor: (*_HOOKS, "_call_processors"),
    **dict.fromkeys(_LEAVES, _HOOKS),
}
_METHODS = {
    kind: tuple((name, getattr(kind, name), getattr(kind, name).__code__) for name in names)
    for kind, names in _METHOD_NAMES.items()
}
_SOLVER_CLASS: type[PointerSolver] | None = None
_SOLVER_METHODS: tuple[tuple[str, object, object], ...] = ()


def bind_native_solver_contract(kind: type[PointerSolver]) -> None:
    """Bind at native solver module initialization, before client overrides."""
    global _SOLVER_CLASS, _SOLVER_METHODS
    if _SOLVER_CLASS is not None:
        if kind is not _SOLVER_CLASS:
            raise RuntimeError("native bootstrap solver contract already bound")
        return
    _SOLVER_CLASS = kind
    names = (
        "_apply_dynamic",
        "_enqueue_points_to",
        "_register_dynamic_constraint",
        "_watch_builtin_variable",
        "_schedule_builtin_dependents",
        "_promote_points_variable",
    )
    _SOLVER_METHODS = tuple(
        (name, getattr(kind, name), getattr(kind, name).__code__) for name in names
    )


def native_context(context: NativeContext | None) -> bool:
    candidate: object = context
    while type(candidate) is SummaryContext:
        candidate = candidate.inner
    return type(candidate) in (
        CallStringContext,
        ObjectContext,
        TypeContext,
        ReceiverContext,
        ParamContext,
        HybridContext,
    )


def pure_builtin_singleton(variable: object, points: object, context: NativeContext | None) -> bool:
    if (
        type(variable) is not Variable
        or variable.kind is not VariableKind.GLOBAL
        or type(points) is not PointsToSet
        or points.classmethods_mask
        or points.instancemethods_mask
        or points.objs_mask <= 0
        or points.objs_mask & (points.objs_mask - 1)
    ):
        return False
    try:
        obj = next(iter(points))
    except (IndexError, StopIteration, TypeError, ValueError):
        return False
    return (
        type(obj) is BuiltinFunctionObject
        and obj.function_name == variable.name
        and obj.context is context
        and type(obj.alloc_site) is AllocSite
        and obj.alloc_site.kind is AllocKind.BUILTIN
        and obj.alloc_site.stmt == "<builtin_function:" + variable.name + ">"
    )


def valid_seed_rows(rows: object, names: Sequence[str], context: NativeContext) -> bool:
    """Do not queue a malformed cache merely because eager admission failed."""
    if type(rows) is not tuple or len(rows) != len(names):
        return False
    for row, name in zip(rows, names, strict=False):
        if (
            type(row) is not tuple
            or len(row) != 2
            or type(row[0]) is not Variable
            or row[0].name != name
            or not pure_builtin_singleton(row[0], row[1], context)
        ):
            return False
    return True


def _methods_match(obj: object, expected: tuple[tuple[str, object, object], ...]) -> bool:
    attributes = vars(obj)
    for name, function, code in expected:
        if name in attributes:
            return False
        actual = getattr(obj, name)
        if (
            getattr(actual, "__func__", actual) is not function
            or getattr(actual, "__code__", None) is not code
        ):
            return False
    return True


def _freeze(value: object) -> tuple[type[object], object]:
    if value is None or type(value) in (str, int, bool, float):
        return (type(value), value)
    if isinstance(value, (tuple, list)) and type(value) in (tuple, list):
        return (type(value), tuple(_freeze(item) for item in value))
    if isinstance(value, dict) and type(value) is dict:
        return (dict, tuple(sorted((key, _freeze(item)) for key, item in value.items())))
    raise TypeError("custom bootstrap configuration value")


def _signature(solver: PointerSolver) -> tuple[object, ...] | None:
    if (
        _SOLVER_CLASS is None
        or type(solver) is not _SOLVER_CLASS
        or type(solver.config) is not Config
        or type(solver.state) is not PointerAnalysisState
        or not _methods_match(solver, _SOLVER_METHODS)
    ):
        return None
    state = solver.state
    ledger = solver.work_ledger
    if (
        type(ledger) is not SharedWorkLedger
        or type(solver.scheduler) is not NativeScheduler
        or type(solver.scheduler.policy) is not str
        or type(vars(SharedWorkLedger)["remaining"]) is not property
        or vars(SharedWorkLedger)["remaining"].fget is not _WORK_REMAINING_FGET
        or ledger.limit is not None
        and (type(ledger.limit) is not int or ledger.limit < 0)
        or type(ledger.used) is not int
        or ledger.used < 0
        or type(ledger.by_kind) is not Counter
        or type(ledger.denials) is not Counter
    ):
        return None
    allowed_work = {
        "native_selected",
        "bootstrap_publication",
        "input_publication",
        "intrinsic_bridge",
        "abstract_transfer",
    }
    if any(
        len(counts) > len(allowed_work)
        or any(
            type(k) is not str or k not in allowed_work or type(v) is not int or v < 0
            for k, v in counts.items()
        )
        for counts in (ledger.by_kind, ledger.denials)
    ):
        return None
    port = solver._diagnostics
    diagnostic_signature = pure_counter_signature(port) if port is not None else None
    if port is not None and diagnostic_signature is None:
        return None
    if (
        solver._debug_monitor is not None
        or state._debug_monitor is not None
        or state.pointer_flow_graph._debug_monitor is not None
        or solver._work_step_observer is not None
        or solver._inspector_enabled
        or solver._inspector_step_depth
        or solver._inspector_tainted
        or solver._inspector_cold_checkpoint is not None
    ):
        return None
    objects: list[object] = [
        state,
        state.pointer_flow_graph,
        state.constraints,
        state._heap,
        state._variable_factory,
        solver.variable_factory,
        state._worklist,
        solver.scheduler,
        ledger,
    ]
    if type(solver.processor) is Processor:
        processors: list[Processor] = [solver.processor]
        processor_container = None
    elif type(solver.processor) is ComposeProcessor:
        if type(solver.processor.processors) is not list:
            return None
        processor_container = solver.processor.processors
        processors = [solver.processor, *solver.processor.processors]
        if tuple(type(processor) for processor in processors[1:]) != _LEAVES:
            return None
    else:
        return None
    objects.extend(processors)
    for obj in objects:
        expected = _METHODS.get(type(obj))
        if expected is None or not _methods_match(obj, expected):
            return None
    # All admitted publication leaves inherit the actual native no-op method.
    leaves = processors[1:] if type(solver.processor) is ComposeProcessor else processors
    if any(
        getattr(processor.handle_pts, "__func__", None) is not _METHODS[Processor][0][1]
        for processor in leaves
    ):
        return None
    graph, constraints, queue = state.pointer_flow_graph, state.constraints, state._worklist
    # Native methods can still dispatch user callbacks through dict subclasses.
    # Seal directly accessed storage types and identities, without traversing
    # any growing fact/edge inventory or keeping a second copy of its contents.
    storage: list[tuple[object, type[object]]] = [
        (state._env, dict),
        (graph.succs, dict),
        (graph.preds, dict),
        (graph.nodes, set),
        (graph.edges, set),
        (graph._edge_activation_counts, dict),
        (graph._edge_object_flow, dict),
        (constraints._constraints, set),
        (constraints._by_variable, defaultdict),
        (constraints._by_type, defaultdict),
        (queue.items_dict, dict),
        (state._heap.heap, dict),
        (state._static_constraints, deque),
        (solver._constraint_replays, deque),
        (solver._builtin_dependencies, dict),
        (solver._builtin_object_dependencies, dict),
        (solver._builtin_invocations, dict),
        (solver._builtin_replays, deque),
        (solver._queued_builtin_replays, set),
        (ledger.by_kind, Counter),
        (ledger.denials, Counter),
    ]
    if type(queue) is Worklist:
        storage.append((queue.items_list, deque))
    elif type(queue) is ConsumerFairWorklist:
        storage.extend(((queue._hot, OrderedDict), (queue._cold, OrderedDict)))
    if (
        any(type(value) is not kind for value, kind in storage)
        or not isinstance(constraints._by_variable, defaultdict)
        or not isinstance(constraints._by_type, defaultdict)
        or constraints._by_variable.default_factory is not list
        or constraints._by_type.default_factory is not list
        or type(solver._stats) is not dict
    ):
        return None
    try:
        config = tuple(
            (field.name, _freeze(getattr(solver.config, field.name))) for field in fields(Config)
        )
        precision = tuple(
            _freeze(getattr(processor, "index_sensitive", None)) for processor in processors
        )
        quota = _freeze(queue.HOT_QUOTA) if type(queue) is ConsumerFairWorklist else None
    except (TypeError, ValueError):
        return None
    consumer = state._consumer_worklist
    if (type(state._worklist) is ConsumerFairWorklist and consumer is not state._worklist) or (
        type(state._worklist) is Worklist and consumer is not None
    ):
        return None
    return (
        id(solver.config),
        config,
        tuple(id(obj) for obj in objects),
        id(processor_container),
        precision,
        quota,
        tuple(id(value) for value, kind in storage),
        points_to_set._object_table_generation,
        diagnostic_signature,
        ledger.limit,
    )


def _refuse(solver: PointerSolver, reason: str) -> NoReturn:
    solver._bootstrap_invalid = True
    solver._inspector_tainted = True
    solver._stats["converged"] = 0
    raise RuntimeError("bootstrap contract refused: " + reason)


def validate_bootstrap_contract(solver: PointerSolver) -> None:
    if getattr(solver, "_bootstrap_invalid", False):
        _refuse(solver, "sticky taint")
    expected = getattr(solver, "_bootstrap_contract_signature", None)
    if expected is not None and _signature(solver) != expected:
        _refuse(solver, "native component, configuration, hook or arena mutation")


def _seed_decision(solver: PointerSolver, reason: str) -> None:
    if solver._diagnostics is not None:
        solver._diagnostics.seed_decision(reason)


def try_publish_builtin_seed(
    solver: PointerSolver, scope: Scope, variable: Ctx[Variable], points: PointsToSet
) -> bool:
    if solver.config.builtin_bootstrap_policy != "eager_inert":
        if solver._diagnostics is not None:
            _seed_decision(solver, "queued_requested")
        return False
    if solver._work_iteration_started or solver._iteration:
        # Preserve late-call validation and the cheap no-contract decline.
        validate_bootstrap_contract(solver)
        _seed_decision(solver, "after_work_started")
        return False
    if getattr(solver, "_bootstrap_invalid", False):
        _refuse(solver, "sticky taint")
    expected = getattr(solver, "_bootstrap_contract_signature", None)
    signature = _signature(solver)
    if expected is not None and signature != expected:
        _refuse(solver, "native component, configuration, hook or arena mutation")
    if signature is None:
        reason = (
            "targeted_diagnostics"
            if solver._diagnostics is not None and solver._diagnostics.mode == "targeted"
            else "inspector_boundaries"
            if solver._inspector_enabled
            else "cold_checkpoint"
            if solver._inspector_cold_checkpoint is not None
            else "work_step_observer"
            if solver._work_step_observer is not None
            else "debug_monitor"
            if solver._debug_monitor is not None
            else "native_contract_not_admitted"
        )
        _seed_decision(solver, reason)
        return False
    if (
        type(scope) is not Scope
        or type(variable) is not Ctx
        or variable.scope is not scope
        or variable.context is not scope.context
        or not native_context(variable.context)
        or not pure_builtin_singleton(variable.content, points, variable.context)
    ):
        _seed_decision(solver, "invalid_seed_shape")
        return False
    state = solver.state
    node = NormalNode(variable)
    successors = state.pointer_flow_graph.succs.get(node)
    subscriptions = state.constraints._by_variable.get(variable)
    watches = solver._builtin_dependencies.get(variable)
    if (
        (successors is not None and type(successors) is not set)
        or (subscriptions is not None and type(subscriptions) is not list)
        or (watches is not None and type(watches) is not set)
    ):
        _seed_decision(solver, "custom_seed_storage")
        return False
    if (
        not state.get_points_to(node).is_empty()
        or not state.get_points_to(variable).is_empty()
        or node in state._worklist.items_dict
        or successors
        or subscriptions
        or watches
    ):
        _seed_decision(solver, "existing_facts_or_consumers")
        return False
    if not solver.work_ledger.consume(kind="bootstrap_publication"):
        _seed_decision(solver, "global_work_budget")
        return False
    previous_delta = solver._stats["points_to_delta_objects"]
    try:
        _, num_points, num_constraints = solver._apply_dynamic(scope, node, points)
        if (
            num_points != 1
            or num_constraints != 0
            or state.get_points_to(node) != points
            or state.get_points_to(variable) != points
            or _signature(solver) != signature
        ):
            _refuse(solver, "non-inert publication")
    except BaseException:
        solver._bootstrap_invalid = True
        solver._inspector_tainted = True
        solver._stats["converged"] = 0
        raise
    # This is initialization work, not selected point-queue work. The original
    # transfer still ran; its separate count survives a later selected reset.
    solver._stats["points_to_delta_objects"] = previous_delta
    solver._bootstrap_publications += 1
    solver._bootstrap_contract_signature = signature
    _seed_decision(solver, "published")
    return True


def bootstrap_statistics(solver: PointerSolver) -> JsonObject:
    selected = solver._iteration
    publications = solver._bootstrap_publications
    return {
        "builtin_bootstrap_policy": solver.config.builtin_bootstrap_policy,
        "bootstrap_publications": publications,
        "selected_work_items": selected,
        "logical_work_items": selected + publications,
        "bootstrap_contract_invalid": solver._bootstrap_invalid,
    }
