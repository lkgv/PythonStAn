"""Independent interprocedural equation solver over source-bound Python AST IR.

A unit is a module initializer or finite-context function equation. Calls grow a
shared entry store and subscribe their caller; changes to exit summaries enqueue
all consumers. Recursive calls read the current equation approximation, never a
depth-cap substitute. No PTA, World singleton, target import or target execution
is used by this engine.
"""

from __future__ import annotations

import ast
import time
from collections import defaultdict, deque
from collections.abc import Hashable, Iterable, KeysView, Mapping, Sequence
from dataclasses import replace
from typing import TYPE_CHECKING, NamedTuple

from pythonstan.analysis.contracts import EngineSnapshot, EntryScenario, SourceModule, stable_digest
from pythonstan.analysis.contracts.identity import (
    CellId,
    ContextId,
    FrameId,
    IdentityEpoch,
    ObjectId,
    PolicyId,
    ProgramPointId,
    StateRevision,
    UnitId,
)
from pythonstan.analysis.contracts.json_data import (
    JsonObject,
    JsonValue,
    require_array,
    require_int,
    require_object,
    require_string,
    validate_json,
    validate_json_object,
)
from pythonstan.analysis.contracts.observation import ObservationEmitter, ObserverPort
from pythonstan.analysis.contracts.oracle import (
    CandidateOraclePort,
    IncrementalOraclePort,
    OraclePreparedUpdate,
)
from pythonstan.analysis.contracts.private_slots import (
    keyword_default_slot,
    positional_default_slot,
)
from pythonstan.analysis.domains.values import Value, ValueOperations
from pythonstan.frontend.source import (
    SourceInput,
    SourceProgram,
    normalize_source_modules,
    parse_sources,
)

from .runtime_options import decode_runtime_options, integer_option

if TYPE_CHECKING:
    from pythonstan.analysis.composition.guarded_dispatch import GuardedDispatchProvider
    from pythonstan.analysis.composition.plan import EffectiveRegionPlan
    from pythonstan.analysis.composition.region_scheduler import RegionScheduler
    from pythonstan.analysis.composition.summary_context import SummaryApplicationContext

from pythonstan.analysis.state.boundary import HostStateBoundary
from pythonstan.analysis.state.dependencies import SummaryDependency
from pythonstan.analysis.state.flow import (
    ExceptionValue,
    Flow,
    FlowTail,
    NormalExprFlow,
    NormalStmtFlow,
    RaiseFlow,
    ReturnFlow,
    flow_payload,
    is_flow,
    make_completion,
    replace_flow,
    require_payload,
    with_state,
)
from pythonstan.analysis.state.handles import StateHandle
from pythonstan.analysis.state.transform import GraphPayload, walk_graph

from .publication import (
    FunctionPublication,
    PublicationInput,
    PublicationRuntime,
    PublicationUnit,
    finalize_publication,
    module_value_requests,
    prepare_publication,
    publication_statistics,
)
from .state import Frame, FunctionInfo, HeapObject, Store, Unit
from .state_transform import (
    PayloadTransformer,
    payload_signature,
    remap_payload,
    semantic_payload_equal,
)


class RootActivation(NamedTuple):
    scenario: str
    module: str
    key: UnitId
    qualname: str | None
    entry: EntryScenario


def _completion_sort_key(flow: Flow) -> tuple[str, str]:
    return flow.kind, flow.exception or ""


class BudgetStop(Exception):
    pass


class InterproceduralDriver:
    def __init__(
        self,
        modules: Mapping[str, SourceInput] | Iterable[SourceInput],
        options: Mapping[str, object] | None = None,
        oracle: CandidateOraclePort[Value] | None = None,
        observer: ObserverPort | None = None,
        program: SourceProgram | None = None,
    ) -> None:
        runtime = decode_runtime_options(options)
        self.options = runtime.options
        self.abstract = runtime.abstract
        self.resources = runtime.resources
        if program is not None and normalize_source_modules(modules) != program.modules:
            raise ValueError("pre-parsed SourceProgram input identity mismatch")
        program = parse_sources(modules) if program is None else program
        self.modules: Mapping[str, SourceModule] = program.modules
        self.trees = program.trees
        self.nodes = program.nodes
        self.node_modules = program.node_modules
        self.generated_points: dict[int, ProgramPointId] = {}
        self.generated_nodes: dict[int, ast.AST] = {}
        self.source_digest = stable_digest(
            {name: source.digest for name, source in sorted(self.modules.items())}
        )
        self.policy_digest = stable_digest(self.options)
        self.model_digest = "pythonstan-builtins-v1"
        self.future_annotations = frozenset(
            module
            for module, tree in self.trees.items()
            if any(
                isinstance(node, ast.ImportFrom)
                and node.module == "__future__"
                and any(alias.name == "annotations" for alias in node.names)
                for node in tree.body
            )
        )
        self.ops = ValueOperations(runtime.value_policy)
        self._oracle: CandidateOraclePort[Value] | None = None
        self._incremental_oracle: (
            IncrementalOraclePort[Value, ValueOperations, OraclePreparedUpdate] | None
        ) = None
        self._oracle_phase: JsonObject | None = None
        self.install_oracle(oracle)
        self.observer = observer
        self.units: dict[UnitId, Unit] = {}
        self.unit_scenarios: dict[UnitId, str] = {}
        self.functions: dict[ObjectId, FunctionInfo] = {}
        self.queue: deque[UnitId] = deque()
        self.queued: set[UnitId] = set()
        self.current: UnitId | None = None
        self.scenario = ""
        self.reads: set[tuple[str, str, ProgramPointId]] = set()
        self.writes: set[tuple[str, str, ProgramPointId]] = set()
        self.calls: set[tuple[str, str, ProgramPointId]] = set()
        self.exceptions: set[str] = set()
        self.obligations: set[str] = set()
        self.events: list[tuple[str, str]] = []
        self.work = 0
        self.started: float = 0.0
        self.stats: defaultdict[str, JsonValue] = defaultdict(int)
        self.root_keys: list[RootActivation] = []
        self.phase_trace: list[JsonObject] = []
        self.solver_status = "converged"
        self.max_work = runtime.max_work
        self.max_seconds = runtime.max_seconds
        self.max_memory_mb = runtime.max_memory_mb
        self._last_memory_work = -64
        self._statement_visits: defaultdict[tuple[UnitId | None, ProgramPointId], int] = (
            defaultdict(int)
        )
        self._in_reabstract = False
        self._activation_occurrences: defaultdict[
            tuple[UnitId | None, FrameId, ProgramPointId, ObjectId], int
        ] = defaultdict(int)
        self.context_depth = runtime.context_depth
        self.max_paths = runtime.max_paths
        self.region_engine = None
        self.region_plan: EffectiveRegionPlan | None = None
        self.region_scheduler: RegionScheduler | None = None
        self.summary_provider: GuardedDispatchProvider | None = None
        self._summary_host_marker = object()
        from .transfer import Transfer

        self.transfer = Transfer(self)

    @property
    def oracle(self) -> CandidateOraclePort[Value] | None:
        return self._oracle

    @oracle.setter
    def oracle(self, candidate: CandidateOraclePort[Value] | None) -> None:
        self.install_oracle(candidate)

    @property
    def incremental_oracle(
        self,
    ) -> IncrementalOraclePort[Value, ValueOperations, OraclePreparedUpdate] | None:
        return self._incremental_oracle

    def install_oracle(
        self,
        candidate: CandidateOraclePort[Value] | None,
        *,
        incremental: IncrementalOraclePort[Value, ValueOperations, OraclePreparedUpdate]
        | None = None,
        phase_record: JsonObject | None = None,
    ) -> None:
        """Install an explicitly supplied lifecycle channel for the same producer.

        Candidate-only replacements require no publication or epoch hooks.
        Binding still occurs at run start after host construction completes.
        """
        if incremental is not None and incremental is not candidate:
            raise ValueError(
                "Oracle lifecycle channel must belong to the installed candidate producer"
            )
        self._oracle = candidate
        self._incremental_oracle = incremental
        self._oracle_phase = None if phase_record is None else validate_json_object(phase_record)

    def publish_oracle_cell(self, cell: CellId, value: Value, origin: str = "ai") -> bool:
        return (
            False
            if self._incremental_oracle is None
            else self._incremental_oracle.publish_cell(cell, value, origin)
        )

    def publish_oracle_field(
        self, objid: ObjectId, key: Hashable, value: Value, origin: str = "ai"
    ) -> bool:
        return (
            False
            if self._incremental_oracle is None
            else self._incremental_oracle.publish_field(objid, key, value, origin)
        )

    def _summary_context(self) -> SummaryApplicationContext:
        """Expose only the real checked-summary capabilities for this host."""
        from pythonstan.analysis.composition.summary_context import (
            SummaryApplicationContext,
            SummaryHostId,
        )

        return SummaryApplicationContext(
            host_token=SummaryHostId(self._summary_host_marker),
            functions=self.functions,
            modules=self.modules,
            trees=self.trees,
            policy_digest=self.policy_digest,
            model_digest=self.model_digest,
            scenario=self.scenario,
            current_epoch=lambda: self.ops.arena.epoch,
            point=self.point,
            remaining_work=lambda: self.max_work - self.work,
            charge=self.charge,
            record_call=lambda caller, callee, point: self.calls.add((caller, callee, point)),
            record_read=lambda owner, key, point: self.reads.add((owner, key, point)),
            record_write=lambda owner, key, point: self.writes.add((owner, key, point)),
            record_exception=self.exceptions.add,
        )

    @property
    def semantic_profile(self) -> str:
        return require_string(self.options.get("semantic_profile", "python-core-v1"))

    @property
    def unit_ids(self) -> KeysView[UnitId]:
        return self.units.keys()

    def point(self, node: ast.AST | None) -> ProgramPointId:
        return self.generated_points.get(
            id(node), self.nodes.get(id(node), ProgramPointId(f"generated:{type(node).__name__}"))
        )

    def register_generated[NodeT: ast.AST](self, node: NodeT, point: ProgramPointId | str) -> NodeT:
        self.generated_points[id(node)] = ProgramPointId(point)
        self.generated_nodes[id(node)] = node
        return node

    def increment_stat(self, name: str, amount: int = 1) -> None:
        self.stats[name] = require_int(self.stats[name]) + amount

    def abstract_integer(self, name: str, default: int) -> int:
        return integer_option(self.abstract, name, default)

    def _check_limits(self, force: bool = False) -> None:
        if self.started and self.max_seconds and time.monotonic() - self.started > self.max_seconds:
            raise BudgetStop("max_seconds")
        if self.max_memory_mb and (force or self.work - self._last_memory_work >= 64):
            import os

            with open("/proc/self/statm", encoding="ascii") as stream:
                resident = int(stream.read().split()[1]) * os.sysconf("SC_PAGE_SIZE")
            self._last_memory_work = self.work
            self.increment_stat("memory_checks", 1)
            self.stats["resident_memory_bytes"] = resident
            self.stats["peak_sampled_resident_bytes"] = max(
                resident, require_int(self.stats["peak_sampled_resident_bytes"])
            )
            if resident > self.max_memory_mb * 1024 * 1024:
                raise BudgetStop("max_memory_mb")

    def charge(self, amount: int, category: str) -> None:
        if amount < 0:
            raise ValueError("negative work charge")
        self._check_limits()
        if amount > self.max_work - self.work:
            raise BudgetStop("max_work")
        self.work += amount
        self.increment_stat(category, amount)

    def tick(self, node: ast.AST | None = None) -> None:
        key = (
            self.current,
            self.point(node) if node is not None else ProgramPointId("<statement>"),
        )
        self._statement_visits[key] += 1
        if self._statement_visits[key] > self.abstract_integer("max_stmt_visits", 50):
            raise BudgetStop("max_stmt_visits")
        self.charge(1, "ast_work")
        if node is not None:
            self.emit(
                "transfer", point=self.point(node), unit=self.current, operation=type(node).__name__
            )

    def allocate_identity(self, identifier: ObjectId) -> ObjectId:
        return self.ops.arena.resolve(identifier)

    def reabstract_addresses(
        self, mapping: Mapping[ObjectId, ObjectId], extra_payloads: Sequence[GraphPayload] = ()
    ) -> tuple[GraphPayload, ...]:
        """Prepare the entire host quotient, then publish one alias epoch.

        Every preparation visit is charged before its clone is made. Failed
        time/work/memory preparation publishes neither aliases nor new roots.
        Current local stores outside the host are normalized lazily by epoch.
        """
        if self._in_reabstract:
            raise RuntimeError("Nested address quotient")
        self.charge(len(self.ops.arena.aliases) + len(mapping), "reabstract_alias_prepare_work")
        prepared_aliases = self.ops.arena.prepare(mapping)
        if prepared_aliases == self.ops.arena.aliases:
            return remap_payload(
                tuple(extra_payloads), prepared_aliases, self.ops, epoch=self.ops.arena.epoch
            )
        epoch = IdentityEpoch(self.ops.arena.epoch + 1)
        self._in_reabstract = True
        try:
            transform = PayloadTransformer(
                prepared_aliases,
                self.ops,
                epoch=epoch,
                charge=lambda amount: self.charge(amount, "reabstract_prepare_work"),
            )
            payload = transform.visit(
                HostStateBoundary(self.units, self.functions, tuple(extra_payloads), self.events)
            )
            functions: dict[ObjectId, FunctionInfo] = {}
            ambiguous: set[ObjectId] = set()
            for info in payload.functions.values():
                if info.identifier in functions and not semantic_payload_equal(
                    functions[info.identifier].node, info.node
                ):
                    ambiguous.add(info.identifier)
                functions.setdefault(info.identifier, info)
            if ambiguous:

                def mark(store: Store) -> None:
                    for identifier in ambiguous:
                        if identifier in store.heap:
                            store.heap[identifier].havoc = True
                            store.heap[identifier].default = self.ops.join(
                                store.heap[identifier].default, Value.unknown()
                            )

                walk_graph(payload, on_store=mark)
            oracle_prepared = None
            provider_prepared = None
            if self.incremental_oracle is not None:
                oracle_prepared = self.incremental_oracle.prepare_reabstract(
                    prepared_aliases,
                    self.ops,
                    charge=lambda amount: self.charge(amount, "reabstract_oracle_work"),
                )
                if not oracle_prepared.work_precharged:
                    self.charge(oracle_prepared.work, "reabstract_oracle_work")
            if self.summary_provider is not None:
                provider_prepared = self.summary_provider.prepare_state_epoch(
                    epoch, charge=lambda amount: self.charge(amount, "reabstract_summary_work")
                )
                if not provider_prepared.work_precharged:
                    self.charge(provider_prepared.work, "reabstract_summary_work")
            self.charge(len(prepared_aliases), "reabstract_prepare_work")
            prepared_summaries = self.ops.arena.summaries | set(prepared_aliases.values())
            reads: set[tuple[str, str, ProgramPointId]] = set()
            writes: set[tuple[str, str, ProgramPointId]] = set()
            for old, target in ((self.reads, reads), (self.writes, writes)):
                for owner, key, point in old:
                    self.charge(1, "reabstract_prepare_work")
                    target.add((prepared_aliases.get(ObjectId(owner), ObjectId(owner)), key, point))
            queue = deque(self.queue)
            queued = set(self.queued)
            newly_queued = []
            for key in payload.units:
                self.charge(1, "reabstract_prepare_work")
                if key not in queued:
                    queue.append(key)
                    queued.add(key)
                    newly_queued.append(key)
            self._check_limits(force=True)
            # Publication consists solely of assignments to already prepared
            # objects. External service commit hooks have the same contract.
            if oracle_prepared is not None:
                oracle_prepared.validate()
            self.ops.arena.publish(prepared_aliases, summaries=prepared_summaries)
            self.units = payload.units
            self.functions = functions
            self.events = payload.events
            self.reads = reads
            self.writes = writes
            self.queue = queue
            self.queued = queued
            if oracle_prepared is not None:
                oracle_prepared.commit()
            if provider_prepared is not None and self.summary_provider is not None:
                self.summary_provider.commit_state_epoch(provider_prepared)
            self.increment_stat("address_reabstract_epochs", 1)
            self.stats["address_aliases"] = len(prepared_aliases)
            for key in newly_queued:
                self.emit("work_enqueue", unit=key, reason="address_epoch_changed")
            self.emit("state_reabstract", epoch=epoch, aliases=len(prepared_aliases))
            return payload.extras
        finally:
            self._in_reabstract = False

    def coarsen_flows(
        self, flows: Sequence[Flow], operations: ValueOperations | None = None
    ) -> list[Flow]:
        flows = list(flows)
        operations = operations or self.ops
        aliases = operations.arena.aliases
        if aliases:
            flows = list(
                remap_payload(tuple(flows), aliases, operations, epoch=operations.arena.epoch)
            )
        cap = int(operations.policy.max_addr_set_size)
        overflow: set[ObjectId] = set()

        def inspect(value: Value) -> None:
            refs = {operations.arena.resolve(ref) for ref in value.refs}
            if len(refs) > cap:
                overflow.update(refs)

        walk_graph(tuple(flows), on_value=inspect)
        if overflow:
            # A single conservative quotient bounds the overflowing reference
            # component. Concrete keys and all heap values remain represented.
            kinds = {
                obj.kind
                for flow in flows
                for ref in overflow
                if (obj := flow.store.heap.get(ref)) is not None
            }
            target = ObjectId(
                "summary:addresses:" + (next(iter(kinds)) if len(kinds) == 1 else "mixed")
            )
            mapping = {ref: target for ref in overflow if ref != target}
            if mapping:
                transformed = self.reabstract_addresses(mapping, flows)
                flows = []
                for flow in transformed:
                    if not is_flow(flow):
                        raise TypeError("Address transformation did not preserve a completion")
                    flows.append(flow)
                self.increment_stat("address_capacity_collapses", 1)
        return flows

    def emit(self, kind: str, **data: JsonValue) -> None:
        event: dict[str, object] = {"schema_version": 1, "kind": kind, **data}
        self.increment_stat("observation_events", 1)
        if self.observer is not None:
            try:
                if isinstance(self.observer, ObservationEmitter):
                    self.observer.emit(event)
                else:
                    self.observer(event)
            except Exception:
                self.increment_stat("observation_errors", 1)

    def enqueue(self, key: UnitId, reason: str) -> None:
        if key not in self.queued:
            self.queue.append(key)
            self.queued.add(key)
            self.emit("work_enqueue", unit=key, reason=reason)

    def open(self, reason: str, store: Store | None = None, node: ast.AST | None = None) -> None:
        self.obligations.add(reason)
        if store is not None:
            store.obligations |= frozenset((reason,))
        self.emit("open_obligation", reason=reason, point=self.point(node) if node else None)

    def register_module(self, module: str, store: Store) -> Value:
        store.ensure_current(self.ops)
        identifier = self.allocate_identity(ObjectId(f"module:{module}"))
        if identifier not in store.heap:
            store.heap[identifier] = HeapObject(
                "module",
                singleton=not self.ops.arena.is_summary(identifier),
                metadata={"module": module},
            )
        return Value.ref(identifier)

    def module_key(self, module: str) -> UnitId:
        return UnitId(f"{self.scenario}:module:{module}")

    def request_module(
        self, module: str, store: Store, caller: UnitId | None = None, site: ast.AST | None = None
    ) -> list[Flow]:
        if module not in self.modules:
            return self.transfer.unknown(store, site, "external_module:" + module)
        result = store.copy()
        ref = self.register_module(module, result)
        status = result.initialized.get(module, "maybe" if result.havoc_imports else "unseen")
        if status in ("initialized", "initializing"):
            return [NormalExprFlow(StateHandle.capture(result), ref)]
        key = self.module_key(module)
        entry = result.clear_effects()
        entry.initialized[module] = "initializing"
        for name, descriptor in require_object(
            require_object(self.options.get("inputs", {})).get("bindings", {})
        ).items():
            prefix = module + "."
            if name.startswith(prefix):
                entry.cells[CellId(f"global:{module}:{name[len(prefix) :]}")] = self.input_value(
                    descriptor
                )
        frame = Frame(module, module, ContextId(()), FrameId(key), is_module=True)
        self._request(key, frame, tuple(self.trees[module].body), entry, True, caller)
        flows: list[Flow] = (
            [
                NormalExprFlow(
                    StateHandle.capture(result.copy()),
                    self.ops.join(ref, Value.unknown()) if result.havoc_imports else ref,
                )
            ]
            if status in {"maybe", "partial"}
            else []
        )
        for outcome in self.units[key].outcomes:
            applied = result.apply(outcome, self.ops)
            if outcome.kind in ("normal", "return"):
                applied.initialized[module] = "initialized"
                flows.append(NormalExprFlow(StateHandle.capture(applied), ref))
            else:
                flows.append(
                    make_completion(
                        StateHandle.capture(applied),
                        outcome.kind,
                        flow_payload(outcome),
                        outcome.exception,
                        FlowTail(),
                    )
                )
        return flows

    def _request(
        self,
        key: UnitId,
        frame: Frame,
        body: tuple[ast.stmt, ...],
        entry: Store,
        is_module: bool,
        caller: UnitId | None,
    ) -> None:
        if key not in self.units:
            self.units[key] = Unit(key, frame, body, entry, is_module=is_module)
            self.unit_scenarios[key] = self.scenario
            self.enqueue(key, "new_entry")
        else:
            unit = self.units[key]
            if not entry.leq(unit.entry, self.ops):
                expanded = unit.entry.join(
                    entry, self.ops, widen=unit.visits > self.abstract_integer("widen_delay", 2)
                )
                unit.entry = expanded
                self.enqueue(key, "entry_growth")
                self.increment_stat("entry_growth", 1)
        if caller:
            self.units[key].consumers.add(caller)
            self.units[caller].dependencies[key] = SummaryDependency(
                key,
                StateRevision(self.units[key].revision),
                self.source_digest,
                PolicyId(self.policy_digest),
                self.ops.arena.epoch,
            )
            self.emit(
                "call_dependency", caller=caller, callee=key, revision=self.units[key].revision
            )

    def request_function(
        self,
        identifier: ObjectId,
        args: Sequence[Value],
        keywords: Sequence[tuple[str, Value]],
        store: Store,
        caller_frame: Frame,
        site: ast.AST | None,
    ) -> list[Flow]:
        store.ensure_current(self.ops)
        identifier = self.ops.arena.resolve(identifier)
        args = tuple(self.ops.normalize(value) for value in args)
        keywords = tuple((name, self.ops.normalize(value)) for name, value in keywords)
        info = self.functions[identifier]
        from pythonstan.analysis.contracts.arguments import CallArguments
        from pythonstan.analysis.semantics.binding import bind_arguments

        function_object = store.heap.get(identifier)
        defaults = info.defaults
        kw_defaults = info.kw_defaults
        if function_object is not None and function_object.havoc:
            return self.transfer.unknown(store, site, "havoc_function_metadata")
        if function_object is not None:
            defaults = tuple(
                function_object.fields.get(positional_default_slot(i), value)
                for i, value in enumerate(info.defaults)
            )
            kw_defaults = tuple(
                (key, function_object.fields.get(keyword_default_slot(key), value))
                for key, value in info.kw_defaults
            )
            if function_object.havoc:
                defaults = tuple(self.ops.join(value, Value.unknown()) for value in defaults)
                kw_defaults = tuple(
                    (key, self.ops.join(value, Value.unknown())) for key, value in kw_defaults
                )
        bound = bind_arguments(
            info.node.args, CallArguments(tuple(args), tuple(keywords)), defaults, kw_defaults
        )
        if bound.errors:
            self.exceptions.add("TypeError")
            return [
                RaiseFlow(
                    StateHandle.capture(store.copy()),
                    ExceptionValue(Value.constant("TypeError"), "TypeError"),
                )
            ]
        if self.summary_provider is not None:
            try:
                application = self.summary_provider.try_apply(
                    self._summary_context(),
                    identifier,
                    args,
                    keywords,
                    store.copy(),
                    caller_frame,
                    site,
                )
            except BudgetStop:
                raise
            except Exception:
                application = None
                self.open("summary_provider_failure", store, site)
            if application is not None:
                self.calls.add((caller_frame.qualname, info.qualname, self.point(site)))
                self.increment_stat("checked_summary_applications", 1)
                self.emit(
                    "checked_summary_application", callee=info.qualname, point=self.point(site)
                )
                return application
        if self.context_depth:
            context_points: tuple[ProgramPointId, ...] = (*caller_frame.context, self.point(site))
            context = ContextId(context_points[-self.context_depth :])
        else:
            context = ContextId(())
        heap_partition = self.semantic_input_partition(info, args, keywords, defaults, store)
        key = UnitId(f"{self.scenario}:call:{identifier}@{'|'.join(context)}:heap={heap_partition}")
        closure = (
            tuple(function_object.metadata.lookup("closure", info.closure))
            if function_object is not None
            else info.closure
        )
        activation = None
        frame_identity = FrameId(key)
        if info.is_generator or info.is_async:
            occurrence_key = (self.current, caller_frame.identity, self.point(site), identifier)
            self._activation_occurrences[occurrence_key] += 1
            count = self._activation_occurrences[occurrence_key]
            activation = count if count <= 32 else "many"
            frame_identity = FrameId(f"{key}:activation={activation}")
        frame = Frame(
            info.module,
            info.qualname,
            context,
            frame_identity,
            info.locals,
            closure,
            info.globals,
            info.nonlocals,
        )
        if info.is_generator or info.is_async:
            # A lazy frame owns local bindings, not a creation-time copy of all
            # globals or every older generator. Referenced objects stay in the
            # returned caller heap and are read live when execution resumes.
            entry = Store(
                cells={cell: store.effective_cell(cell) for _, cell in closure},
                identity_epoch=self.ops.arena.epoch,
            )
        else:
            entry = store.clear_effects()
        for name in frame.locals:
            entry.cells[frame.cell(name)] = Value(unbound=True)
        for name, value in bound.bindings:
            entry.cells[frame.cell(name)] = value
        if info.node.args.vararg:
            objid = self.allocate_identity(ObjectId(f"varargs:{frame.identity}"))
            collector = HeapObject(
                "tuple",
                dict(enumerate(bound.varargs)),
                singleton=activation != "many" and not self.ops.arena.is_summary(objid),
                metadata={"length": len(bound.varargs)},
            )
            if objid in entry.heap:
                collector = entry.heap[objid].join(collector, self.ops)
                collector.singleton = False
            entry.heap[objid] = collector
            entry.cells[frame.cell(info.node.args.vararg.arg)] = Value.ref(objid)
        if info.node.args.kwarg:
            objid = self.allocate_identity(ObjectId(f"kwargs:{frame.identity}"))
            collector = HeapObject(
                "dict",
                dict(bound.kwargs),
                singleton=activation != "many" and not self.ops.arena.is_summary(objid),
                metadata={"length": len(bound.kwargs)},
            )
            if objid in entry.heap:
                collector = entry.heap[objid].join(collector, self.ops)
                collector.singleton = False
            entry.heap[objid] = collector
            entry.cells[frame.cell(info.node.args.kwarg.arg)] = Value.ref(objid)
        if info.is_generator or info.is_async:
            kind = (
                "async_generator"
                if info.is_async and info.is_generator
                else "coroutine"
                if info.is_async
                else "generator"
            )
            objid = self.allocate_identity(ObjectId(f"{kind}:{frame.identity}:{self.point(site)}"))
            result = store.copy()
            for bound_id, bound_object in entry.heap.items():
                result.heap[bound_id] = bound_object.copy()
            # Argument collector containers are caller-heap objects as well;
            # the suspended entry only needs their reference-valued locals.
            entry.heap = {}
            created = HeapObject(
                kind,
                singleton=activation != "many" and not self.ops.arena.is_summary(objid),
                metadata={
                    "function": identifier,
                    "frame": frame,
                    "entry": StateHandle.capture(entry),
                    "body": tuple(info.node.body)
                    if not isinstance(info.node, ast.Lambda)
                    else (ast.Return(info.node.body),),
                    "stage": "created",
                    "index": 0,
                },
            )
            old = result.heap.get(objid)
            if old is not None:
                created = old.join(created, self.ops)
                created.singleton = False
                created.havoc = True
            if activation == "many":
                created.havoc = True
                self.open("suspended_activation_summary", result, site)
            result.heap[objid] = created
            self.calls.add((caller_frame.qualname, info.qualname, self.point(site)))
            return [NormalExprFlow(StateHandle.capture(result), Value.ref(objid))]
        body = (
            (ast.Return(info.node.body),)
            if isinstance(info.node, ast.Lambda)
            else tuple(info.node.body)
        )
        self._request(key, frame, body, entry, False, self.current)
        self.calls.add((caller_frame.qualname, info.qualname, self.point(site)))
        self.emit(
            "call",
            caller=caller_frame.qualname,
            callee=info.qualname,
            point=self.point(site),
            context=list[JsonValue](context),
        )
        if self.incremental_oracle is not None:
            try:
                self.incremental_oracle.observe_call(
                    caller_frame.qualname, info.qualname, self.point(site)
                )
            except Exception:
                self.open("oracle_observation_failure", store, site)
        returned_flows: list[Flow] = []
        for outcome in self.units[key].outcomes:
            applied = store.apply(outcome, self.ops)
            kind = "normal" if outcome.kind == "return" else outcome.kind
            returned_flows.append(
                make_completion(
                    StateHandle.capture(applied),
                    kind,
                    flow_payload(outcome),
                    outcome.exception,
                    FlowTail(),
                )
            )
        return returned_flows

    def semantic_input_partition(
        self,
        info: FunctionInfo,
        args: Sequence[Value],
        keywords: Sequence[tuple[str, Value]],
        defaults: Sequence[Value],
        store: Store,
    ) -> str:
        """Finite observable heap-state inputs distinguish reusable equations.

        Exact iterator positions/list lengths 0..31 and finite stages form a
        source-independent finite alphabet. Larger/changing states share a
        coarse equation whose metadata join is explicit havoc, never a stale
        exact representative. No full caller/context is nested in the token.
        """
        values = [*args, *[value for _, value in keywords], *defaults]
        function_object = store.heap.get(info.identifier)
        closure = (
            tuple(function_object.metadata.lookup("closure", info.closure))
            if function_object
            else info.closure
        )
        values.extend(store.cells.get(cell, Value.bottom()) for _, cell in closure)
        names = {
            node.id
            for node in ast.walk(info.node)
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)
        } - set(info.locals)
        values.extend(
            store.cells.get(CellId(f"global:{info.module}:{name}"), Value.bottom())
            for name in names
        )
        pending = [ref for value in values for ref in value.refs]
        seen: set[ObjectId] = set()
        tokens: list[tuple[str, str | int, str | int | tuple[str, ...]]] = []
        # Two zero signs have observably different str/repr behavior but Python
        # frozensets cannot retain both. This finite two-atom input partition
        # keeps separate equations rather than silently forgetting a sign.
        import struct

        for index, value in enumerate(values):
            if value.floats and all(item == 0.0 for item in value.floats):
                tokens.append(
                    (
                        "scalar-input",
                        index,
                        tuple(sorted(struct.pack("!d", item).hex() for item in value.floats)),
                    )
                )
        while pending and len(seen) < 64:
            ref = pending.pop()
            if ref in seen:
                continue
            seen.add(ref)
            obj = store.heap.get(ref)
            if obj is None:
                continue
            for name in ("index", "length", "stage", "resume_count"):
                if name not in obj.metadata:
                    continue
                raw = obj.metadata[name]
                token: str | int
                if isinstance(raw, int) and not isinstance(raw, bool):
                    token = raw if 0 <= raw < 32 else "many"
                elif isinstance(raw, str):
                    token = raw
                else:
                    token = "abstract"
                tokens.append((ref.split("@", 1)[0], name, token))
            for value in (*obj.fields.values(), obj.default):
                pending.extend(value.refs)
            container = obj.metadata.lookup("container")
            if isinstance(container, Value):
                pending.extend(container.refs)
        if pending:
            tokens.append(("reachable", "overflow", "many"))
        return stable_digest(sorted(tokens, key=str))[:16]

    def _payload_leq(self, left: Flow, right: Flow) -> bool:
        a, b = flow_payload(left), flow_payload(right)
        return a is b if a is None or b is None else self.ops.leq(a, b)

    def compact(self, flows: Sequence[Flow]) -> list[Flow]:
        current = [
            with_state(flow, StateHandle.capture(flow.store)) for flow in self.coarsen_flows(flows)
        ]
        maximal: list[Flow] = []
        for flow in current:

            def same(other: Flow, reference: Flow = flow) -> bool:
                return (
                    type(reference) is type(other)
                    and reference.exception == other.exception
                    and semantic_payload_equal(
                        (reference.continuation, reference.cleanups, reference.resume_target),
                        (other.continuation, other.cleanups, other.resume_target),
                    )
                )

            if any(
                same(old) and self._payload_leq(flow, old) and flow.store.leq(old.store, self.ops)
                for old in maximal
            ):
                continue
            maximal = [
                old
                for old in maximal
                if not (
                    same(old)
                    and self._payload_leq(old, flow)
                    and old.store.leq(flow.store, self.ops)
                )
            ]
            maximal.append(flow)
        if len(maximal) <= self.max_paths:
            return maximal
        buckets: dict[tuple[type[object], str | None, object], Flow] = {}
        for flow in maximal:
            key = (
                type(flow),
                flow.exception,
                payload_signature((flow.continuation, flow.cleanups, flow.resume_target)),
            )
            if key not in buckets:
                buckets[key] = flow
            else:
                old = buckets[key]
                a, b = flow_payload(old), flow_payload(flow)
                if a is None or b is None:
                    if a is not b:
                        raise AssertionError(
                            "One completion variant carried inconsistent payload presence"
                        )
                    payload = None
                else:
                    payload = self.ops.join(a, b)
                buckets[key] = replace_flow(
                    flow, store=old.store.join(flow.store, self.ops), value=payload
                )
        self.increment_stat("trace_merges", len(maximal) - len(buckets))
        return list(buckets.values())

    def _outcomes_equal(self, left: Sequence[Flow], right: Sequence[Flow]) -> bool:
        if len(left) != len(right):
            return False
        for a, b in zip(
            sorted(left, key=_completion_sort_key),
            sorted(right, key=_completion_sort_key),
            strict=True,
        ):
            if not semantic_payload_equal(
                (a.continuation, a.cleanups, a.resume_target),
                (b.continuation, b.cleanups, b.resume_target),
            ):
                return False
            if (
                type(a) is not type(b)
                or a.exception != b.exception
                or not self._payload_leq(a, b)
                or not self._payload_leq(b, a)
                or not a.store.leq(b.store, self.ops)
                or not b.store.leq(a.store, self.ops)
            ):
                return False
        return True

    def run(self, entries: Sequence[EntryScenario] | None = None) -> EngineSnapshot:
        self.started = time.monotonic()
        if self.region_scheduler is not None:
            self.region_scheduler.validate(self)
        if self.oracle is not None:
            self.phase_trace.append(
                dict(
                    self._oracle_phase
                    or {
                        "engine": "andersen",
                        "phase": "incremental_candidate_oracle",
                        "whole_program_prepass": False,
                        "coverage": "partial",
                    }
                )
            )
        self.phase_trace.append(
            {"engine": "abstract", "phase": "interprocedural_fixed_point", "pta_started": False}
        )
        entries = tuple((EntryScenario(m) for m in self.modules) if entries is None else entries)
        if not entries:
            return EngineSnapshot(
                engine="ai_primary",
                solver_status="failed",
                coverage_status="unsupported",
                obligations=("no_entry",),
                phase_trace=tuple(self.phase_trace),
            )
        query_modules = {
            query.get("module")
            for query in require_array(self.options.get("queries", []))
            if isinstance(query, dict) and query.get("module")
        }
        if any(not isinstance(entry, EntryScenario) for entry in entries):
            raise TypeError("The driver consumes resolved EntryScenario records")

        def demand_priority(pair: tuple[int, EntryScenario]) -> tuple[bool, int]:
            index, entry = pair
            return entry.root_module not in query_modules, index

        ordered_entries = sorted(enumerate(entries), key=demand_priority)
        try:
            self._check_limits(force=True)
            if self.incremental_oracle is not None:
                self.incremental_oracle.bind(self)
            if self.summary_provider is not None:
                self.summary_provider.charge_setup(self._summary_context())
            for index, entry in ordered_entries:
                module, qualname = entry.root_module, entry.entry_qualname
                self.scenario = entry.scenario_id or f"root-{index}-{module}"
                key = self.module_key(module)
                self.root_keys.append(RootActivation(self.scenario, module, key, qualname, entry))
                self.request_module(module, Store())
            # All scenarios are pending before solving; one initializer cannot
            # silently prevent the remaining declared roots from being queued.
            self._solve()
            for scenario, module, _key, qualname, entry in self.root_keys:
                if qualname:
                    self.scenario = scenario
                    self._activate_entry(module, qualname, entry)
            self._solve()
        except BudgetStop as exc:
            self.solver_status = "budget_exhausted"
            self.open("budget:" + str(exc))
            self.stats["pending_units"] = len(self.queue) + 1
        elapsed = time.monotonic() - self.started
        return self.snapshot(elapsed)

    def _activate_entry(self, module: str, qualname: str, entry: EntryScenario) -> None:
        key = self.module_key(module)
        unit = self.units.get(key)
        if unit is None:
            return
        short = qualname.split(".")[-1]
        arguments = entry.arguments
        for out in unit.outcomes:
            if out.kind not in ("normal", "return"):
                continue
            callee = out.store.cells.get(CellId(f"global:{module}:{short}"), Value.bottom())
            args = []
            keywords = []
            if arguments:
                args = [
                    self.input_value(x)
                    for x in arguments
                    if not (isinstance(x, Mapping) and "keyword" in x)
                ]
                keywords = [
                    (
                        require_string(validate_json(x["keyword"])),
                        self.input_value(x.get("abstract_value", {"top": True})),
                    )
                    for x in arguments
                    if isinstance(x, Mapping) and "keyword" in x
                ]
            for identifier in callee.refs:
                info = self.functions.get(identifier)
                if info:
                    fake = info.node
                    self.request_function(identifier, args, keywords, out.store, unit.frame, fake)
                else:
                    self.open("entry_not_callable:" + qualname)
            if callee.is_bottom():
                self.open("entry_not_found:" + qualname)

    def _solve(self) -> None:
        while self.queue:
            key = self.queue.popleft()
            self.queued.discard(key)
            unit = self.units[key]
            self.current = key
            self.scenario = self.unit_scenarios[key]
            unit.visits += 1
            self.increment_stat("unit_evaluations", 1)
            self._activation_occurrences.clear()
            if unit.visits > self.abstract_integer("max_iterations", 100):
                raise BudgetStop("unit_iterations")
            self.emit("work_dequeue", unit=key, revision=unit.revision)
            entry = unit.entry.clear_effects()
            flows = self.transfer.block(
                unit.body, [NormalStmtFlow(StateHandle.capture(entry))], unit.frame
            )
            # Address quotient publication may replace the host unit graph
            # while this transfer is suspended in a nested call.
            unit = self.units[key]
            normalized = []
            for flow in flows:
                if not unit.is_module and flow.kind == "normal":
                    flow = ReturnFlow(StateHandle.capture(flow.store), Value.constant(None))
                if unit.is_module and flow.kind == "normal":
                    flow.store.initialized[unit.frame.module] = "initialized"
                normalized.append(flow)
            normalized = self.compact(normalized)
            if not self._outcomes_equal(unit.outcomes, normalized):
                unit.outcomes = normalized
                unit.revision = StateRevision(unit.revision + 1)
                if self.observer is not None:
                    self.emit(
                        "summary_update",
                        unit=key,
                        qualname=unit.frame.qualname,
                        revision=unit.revision,
                        outcomes=[
                            {
                                "kind": f.kind,
                                "value": None
                                if flow_payload(f) is None
                                else require_payload(f).export(),
                                "value_present": flow_payload(f) is not None,
                                "exception": f.exception,
                            }
                            for f in normalized
                        ],
                        consumers=list[JsonValue](sorted(unit.consumers)),
                    )
                else:
                    self.emit(
                        "summary_update",
                        unit=key,
                        revision=unit.revision,
                        outcomes=[f.kind for f in normalized],
                    )
                for consumer in unit.consumers:
                    dependency = self.units[consumer].dependencies.get(key)
                    if dependency is None or dependency.needs_refresh(
                        revision=StateRevision(unit.revision),
                        input_scope=self.source_digest,
                        policy=PolicyId(self.policy_digest),
                        epoch=self.ops.arena.epoch,
                    ):
                        self.enqueue(consumer, "callee_exit_growth")
            self.current = None

    def input_value(self, descriptor: object) -> Value:
        """Validate the abstract-input wire before constructing runtime values."""
        if isinstance(descriptor, Value):
            return descriptor
        checked = validate_json(descriptor)
        if isinstance(checked, dict):
            sort = checked.get("type")
            if "constants" in checked:
                return self.ops.join_all(
                    Value.constant(item) for item in require_array(checked["constants"])
                )
            if "value" in checked:
                return Value.constant(checked["value"])
            if sort == "str":
                return Value(strings=self.ops.string.top())
            if sort == "int":
                return Value(ints=self.ops.integer.top())
            if sort == "bool":
                return Value(bools=frozenset((False, True)))
            if sort == "none":
                return Value.constant(None)
            return Value.unknown()
        return Value.constant(checked)

    def snapshot(self, elapsed: float) -> EngineSnapshot:
        source = PublicationInput(
            units=tuple(
                PublicationUnit.capture(unit, self.unit_scenarios.get(unit.key))
                for unit in self.units.values()
            ),
            functions={
                key: FunctionPublication.capture(info) for key, info in self.functions.items()
            },
            queries=module_value_requests(self.options.get("queries", [])),
            roots=tuple(key for _, _, key, _, _ in self.root_keys),
            calls=tuple(self.calls),
            reads=tuple(self.reads),
            writes=tuple(self.writes),
            events=tuple(self.events),
            exceptions=tuple(self.exceptions),
        )
        prepared = prepare_publication(source, self.ops)
        for warning in prepared.open_obligations:
            self.open(warning)
        runtime = PublicationRuntime(
            work=self.work,
            elapsed=elapsed,
            pending_units=len(self.queue),
            stats=self.stats,
            obligations=frozenset(self.obligations),
            phase_trace=tuple(self.phase_trace),
            profile=self.semantic_profile,
            solver_status=self.solver_status,
        )
        self.stats.update(publication_statistics(prepared, runtime))
        oracle_statistics = self.oracle.statistics() if self.oracle is not None else None
        runtime = replace(
            runtime,
            stats=self.stats,
            obligations=frozenset(self.obligations),
            phase_trace=tuple(self.phase_trace),
            oracle_statistics=oracle_statistics,
        )
        published = finalize_publication(prepared, runtime, published_statistics=self.stats)
        self.stats.update(published.stats)
        return published.snapshot
