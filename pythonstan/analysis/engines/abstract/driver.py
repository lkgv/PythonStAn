"""Independent interprocedural equation solver over source-bound Python AST IR.

A unit is a module initializer or finite-context function equation. Calls grow a
shared entry store and subscribe their caller; changes to exit summaries enqueue
all consumers. Recursive calls read the current equation approximation, never a
depth-cap substitute. No PTA, World singleton, target import or target execution
is used by this engine.
"""

from __future__ import annotations

import ast
import hashlib
import time
from collections import defaultdict, deque
from dataclasses import replace
from typing import TYPE_CHECKING
from collections.abc import Iterable, KeysView, Mapping, Sequence

from pythonstan.analysis.contracts import EngineSnapshot, EntryScenario, SourceModule, stable_digest
from pythonstan.analysis.contracts.identity import ObjectId, ProgramPointId, UnitId
from pythonstan.analysis.contracts.private_slots import (
    keyword_default_slot,
    positional_default_slot,
)
from pythonstan.analysis.domains.values import Value, ValueOperations
from pythonstan.analysis.contracts.json_data import JsonValue, require_string
from pythonstan.analysis.contracts.oracle import CandidateOraclePort
from pythonstan.analysis.contracts.observation import ObservationEmitter, ObserverPort
from pythonstan.frontend.source import SourceInput, SourceProgram, normalize_source_modules, parse_sources
from .runtime_options import decode_runtime_options

if TYPE_CHECKING:
    from pythonstan.analysis.composition.guarded_dispatch import GuardedDispatchProvider
    from pythonstan.analysis.composition.plan import EffectiveRegionPlan
    from pythonstan.analysis.composition.region_scheduler import RegionScheduler

from .state import Flow, Frame, FunctionInfo, HeapObject, Store, Unit
from .state_transform import (
    PayloadTransformer,
    payload_signature,
    remap_payload,
    semantic_payload_equal,
)


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
        self.modules = program.modules
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
        self.oracle = oracle
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
        self.root_keys = []
        self.phase_trace = []
        self.solver_status = "converged"
        self.max_work = runtime.max_work
        self.max_seconds = runtime.max_seconds
        self.max_memory_mb = runtime.max_memory_mb
        self._last_memory_work = -64
        self._statement_visits = defaultdict(int)
        self._in_reabstract = False
        self._activation_occurrences = defaultdict(int)
        self.context_depth = runtime.context_depth
        self.max_paths = runtime.max_paths
        self.region_engine = None
        self.region_plan: EffectiveRegionPlan | None = None
        self.region_scheduler: RegionScheduler | None = None
        self.summary_provider: GuardedDispatchProvider | None = None
        from .transfer import Transfer

        self.transfer = Transfer(self)

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

    def register_generated(self, node, point):
        self.generated_points[id(node)] = point
        self.generated_nodes[id(node)] = node
        return node

    def _check_limits(self, force: bool = False) -> None:
        if self.started and self.max_seconds and time.monotonic() - self.started > self.max_seconds:
            raise BudgetStop("max_seconds")
        if self.max_memory_mb and (force or self.work - self._last_memory_work >= 64):
            import os

            with open("/proc/self/statm", encoding="ascii") as stream:
                resident = int(stream.read().split()[1]) * os.sysconf("SC_PAGE_SIZE")
            self._last_memory_work = self.work
            self.stats["memory_checks"] += 1
            self.stats["resident_memory_bytes"] = resident
            self.stats["peak_sampled_resident_bytes"] = max(
                resident, self.stats["peak_sampled_resident_bytes"]
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
        self.stats[category] += amount

    def tick(self, node=None):
        key = (self.current, self.point(node) if node is not None else "<statement>")
        self._statement_visits[key] += 1
        if self._statement_visits[key] > int(self.abstract.get("max_stmt_visits", 50)):
            raise BudgetStop("max_stmt_visits")
        self.charge(1, "ast_work")
        if node is not None:
            self.emit(
                "transfer", point=self.point(node), unit=self.current, operation=type(node).__name__
            )

    def allocate_identity(self, identifier: ObjectId) -> ObjectId:
        return self.ops.arena.resolve(identifier)

    def reabstract_addresses(self, mapping, extra_payloads=()):
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
        epoch = self.ops.arena.epoch + 1
        self._in_reabstract = True
        try:
            transform = PayloadTransformer(
                prepared_aliases,
                self.ops,
                epoch=epoch,
                charge=lambda amount: self.charge(amount, "reabstract_prepare_work"),
            )
            payload = transform.visit(
                {
                    "units": self.units,
                    "functions": self.functions,
                    "extras": tuple(extra_payloads),
                    "events": self.events,
                }
            )
            functions = {}
            ambiguous = set()
            for info in payload["functions"].values():
                if info.identifier in functions and not semantic_payload_equal(
                    functions[info.identifier].node, info.node
                ):
                    ambiguous.add(info.identifier)
                functions.setdefault(info.identifier, info)
            if ambiguous:
                seen = set()

                def mark(value):
                    if id(value) in seen:
                        return
                    seen.add(id(value))
                    if isinstance(value, Store):
                        for identifier in ambiguous:
                            if identifier in value.heap:
                                value.heap[identifier].havoc = True
                                value.heap[identifier].default = self.ops.join(
                                    value.heap[identifier].default, Value.unknown()
                                )
                    if isinstance(value, ast.AST) or isinstance(value, Value):
                        return
                    if isinstance(value, dict):
                        for item in value.values():
                            mark(item)
                    elif isinstance(value, (tuple, list, set, frozenset)):
                        for item in value:
                            mark(item)
                    elif hasattr(value, "__dataclass_fields__"):
                        for name in value.__dataclass_fields__:
                            mark(getattr(value, name))

                mark(payload)
            oracle_prepared = None
            provider_prepared = None
            if self.oracle is not None:
                if not hasattr(self.oracle, "prepare_reabstract"):
                    raise ValueError("Oracle lacks full-state reabstraction contract")
                oracle_prepared = self.oracle.prepare_reabstract(
                    prepared_aliases,
                    self.ops,
                    charge=lambda amount: self.charge(amount, "reabstract_oracle_work"),
                )
                if not oracle_prepared.get("work_precharged"):
                    self.charge(oracle_prepared.get("work", 0), "reabstract_oracle_work")
            if self.summary_provider is not None:
                if not hasattr(self.summary_provider, "prepare_state_epoch"):
                    raise ValueError("Summary provider lacks state-epoch invalidation")
                provider_prepared = self.summary_provider.prepare_state_epoch(
                    epoch, charge=lambda amount: self.charge(amount, "reabstract_summary_work")
                )
                if not provider_prepared.get("work_precharged"):
                    self.charge(provider_prepared.get("work", 0), "reabstract_summary_work")
            self.charge(len(prepared_aliases), "reabstract_prepare_work")
            prepared_summaries = self.ops.arena.summaries | set(prepared_aliases.values())
            reads = set()
            writes = set()
            for old, target in ((self.reads, reads), (self.writes, writes)):
                for owner, key, point in old:
                    self.charge(1, "reabstract_prepare_work")
                    target.add((prepared_aliases.get(owner, owner), key, point))
            queue = deque(self.queue)
            queued = set(self.queued)
            newly_queued = []
            for key in payload["units"]:
                self.charge(1, "reabstract_prepare_work")
                if key not in queued:
                    queue.append(key)
                    queued.add(key)
                    newly_queued.append(key)
            self._check_limits(force=True)
            # Publication consists solely of assignments to already prepared
            # objects. External service commit hooks have the same contract.
            self.ops.arena.publish(prepared_aliases, summaries=prepared_summaries)
            self.units = payload["units"]
            self.functions = functions
            self.events = payload["events"]
            self.reads = reads
            self.writes = writes
            self.queue = queue
            self.queued = queued
            if oracle_prepared is not None:
                self.oracle.commit_reabstract(oracle_prepared)
            if provider_prepared is not None:
                self.summary_provider.commit_state_epoch(provider_prepared)
            self.stats["address_reabstract_epochs"] += 1
            self.stats["address_aliases"] = len(prepared_aliases)
            for key in newly_queued:
                self.emit("work_enqueue", unit=key, reason="address_epoch_changed")
            self.emit("state_reabstract", epoch=epoch, aliases=len(prepared_aliases))
            return payload["extras"]
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
        seen = set()
        overflow = set()

        def inspect(value):
            if isinstance(value, Value):
                refs = {operations.arena.resolve(ref) for ref in value.refs}
                if len(refs) > cap:
                    overflow.update(refs)
                return
            if value is None or isinstance(value, (str, int, float, bool, bytes, ast.AST)):
                return
            if id(value) in seen:
                return
            seen.add(id(value))
            if isinstance(value, dict):
                for key, item in value.items():
                    inspect(key)
                    inspect(item)
            elif isinstance(value, (tuple, list, set, frozenset)):
                for item in value:
                    inspect(item)
            elif hasattr(value, "__dataclass_fields__"):
                for name in value.__dataclass_fields__:
                    inspect(getattr(value, name))

        inspect(flows)
        if overflow:
            # A single conservative quotient bounds the overflowing reference
            # component. Concrete keys and all heap values remain represented.
            kinds = {
                obj.kind
                for flow in flows
                for ref in overflow
                if (obj := flow.store.heap.get(ref)) is not None
            }
            target = "summary:addresses:" + (next(iter(kinds)) if len(kinds) == 1 else "mixed")
            mapping = {ref: target for ref in overflow if ref != target}
            if mapping:
                flows = list(self.reabstract_addresses(mapping, flows))
                self.stats["address_capacity_collapses"] += 1
        return flows

    def emit(self, kind, **data):
        event = {"schema_version": 1, "kind": kind, **data}
        self.stats["observation_events"] += 1
        if self.observer is not None:
            try:
                if isinstance(self.observer, ObservationEmitter):
                    self.observer.emit(event)
                else:
                    self.observer(event)
            except Exception:
                self.stats["observation_errors"] += 1

    def enqueue(self, key: UnitId, reason: str) -> None:
        if key not in self.queued:
            self.queue.append(key)
            self.queued.add(key)
            self.emit("work_enqueue", unit=key, reason=reason)

    def open(self, reason, store=None, node=None):
        self.obligations.add(reason)
        if store is not None:
            store.obligations |= frozenset((reason,))
        self.emit("open_obligation", reason=reason, point=self.point(node) if node else None)

    def register_module(self, module, store):
        store.ensure_current(self.ops)
        identifier = self.allocate_identity(f"module:{module}")
        if identifier not in store.heap:
            store.heap[identifier] = HeapObject(
                "module",
                singleton=not self.ops.arena.is_summary(identifier),
                metadata={"module": module},
            )
        return Value.ref(identifier)

    def module_key(self, module: str) -> UnitId:
        return UnitId(f"{self.scenario}:module:{module}")

    def request_module(self, module, store, caller=None, site=None):
        if module not in self.modules:
            return self.transfer.unknown(store, site, "external_module:" + module)
        result = store.copy()
        ref = self.register_module(module, result)
        status = result.initialized.get(module, "maybe" if result.havoc_imports else "unseen")
        if status in ("initialized", "initializing"):
            return [Flow(result, value=ref)]
        key = self.module_key(module)
        entry = result.clear_effects()
        entry.initialized[module] = "initializing"
        for name, descriptor in self.options.get("inputs", {}).get("bindings", {}).items():
            prefix = module + "."
            if name.startswith(prefix):
                entry.cells[f"global:{module}:{name[len(prefix) :]}"] = self.input_value(descriptor)
        frame = Frame(module, module, (), key, is_module=True)
        self._request(key, frame, tuple(self.trees[module].body), entry, True, caller)
        flows = (
            [
                Flow(
                    result.copy(),
                    value=self.ops.join(ref, Value.unknown()) if result.havoc_imports else ref,
                )
            ]
            if status in {"maybe", "partial"}
            else []
        )
        for outcome in self.units[key].outcomes:
            applied = result.apply(outcome, self.ops)
            if outcome.kind in ("normal", "return"):
                applied.initialized[module] = "initialized"
                flows.append(Flow(applied, value=ref))
            else:
                flows.append(Flow(applied, outcome.kind, outcome.value, outcome.exception))
        return flows

    def _request(self, key, frame, body, entry, is_module, caller):
        if key not in self.units:
            self.units[key] = Unit(key, frame, body, entry, is_module=is_module)
            self.unit_scenarios[key] = self.scenario
            self.enqueue(key, "new_entry")
        else:
            unit = self.units[key]
            if not entry.leq(unit.entry, self.ops):
                expanded = unit.entry.join(
                    entry, self.ops, widen=unit.visits > int(self.abstract.get("widen_delay", 2))
                )
                unit.entry = expanded
                self.enqueue(key, "entry_growth")
                self.stats["entry_growth"] += 1
        if caller and caller != key:
            self.units[key].consumers.add(caller)
        elif caller == key:
            self.units[key].consumers.add(caller)
        if caller:
            self.emit(
                "call_dependency", caller=caller, callee=key, revision=self.units[key].revision
            )

    def request_function(self, identifier, args, keywords, store, caller_frame, site):
        store.ensure_current(self.ops)
        identifier = self.ops.arena.resolve(identifier)
        args = tuple(self.ops.normalize(value) for value in args)
        keywords = tuple((name, self.ops.normalize(value)) for name, value in keywords)
        info = self.functions[identifier]
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
        bound = bind_arguments(info.node.args, args, keywords, defaults, kw_defaults)
        if bound.errors:
            self.exceptions.add("TypeError")
            return [Flow(store.copy(), "raise", Value.constant("TypeError"), "TypeError")]
        if self.summary_provider is not None:
            try:
                application = self.summary_provider.try_apply(
                    self, identifier, args, keywords, store.copy(), caller_frame, site
                )
            except BudgetStop:
                raise
            except Exception:
                application = None
                self.open("summary_provider_failure", store, site)
            if application is not None:
                self.calls.add((caller_frame.qualname, info.qualname, self.point(site)))
                self.stats["checked_summary_applications"] += 1
                self.emit(
                    "checked_summary_application", callee=info.qualname, point=self.point(site)
                )
                return application
        context = (
            (*caller_frame.context, self.point(site))[-self.context_depth :]
            if self.context_depth
            else ()
        )
        heap_partition = self.semantic_input_partition(info, args, keywords, defaults, store)
        key = f"{self.scenario}:call:{identifier}@{'|'.join(context)}:heap={heap_partition}"
        closure = (
            tuple(function_object.metadata.lookup("closure", info.closure))
            if function_object is not None
            else info.closure
        )
        activation = None
        frame_identity = key
        if info.is_generator or info.is_async:
            occurrence_key = (self.current, caller_frame.identity, self.point(site), identifier)
            self._activation_occurrences[occurrence_key] += 1
            count = self._activation_occurrences[occurrence_key]
            activation = count if count <= 32 else "many"
            frame_identity = f"{key}:activation={activation}"
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
            objid = self.allocate_identity(f"varargs:{frame.identity}")
            collector = HeapObject(
                "tuple",
                {i: v for i, v in enumerate(bound.varargs)},
                singleton=activation != "many" and not self.ops.arena.is_summary(objid),
                metadata={"length": len(bound.varargs)},
            )
            if objid in entry.heap:
                collector = entry.heap[objid].join(collector, self.ops)
                collector.singleton = False
            entry.heap[objid] = collector
            entry.cells[frame.cell(info.node.args.vararg.arg)] = Value.ref(objid)
        if info.node.args.kwarg:
            objid = self.allocate_identity(f"kwargs:{frame.identity}")
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
            objid = self.allocate_identity(f"{kind}:{frame.identity}:{self.point(site)}")
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
                    "entry": entry,
                    "body": tuple(info.node.body),
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
            return [Flow(result, value=Value.ref(objid))]
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
            context=context,
        )
        if self.oracle is not None:
            try:
                if hasattr(self.oracle, "observe_call"):
                    self.oracle.observe_call(caller_frame.qualname, info.qualname, self.point(site))
            except Exception:
                self.open("oracle_observation_failure", store, site)
        result = []
        for outcome in self.units[key].outcomes:
            applied = store.apply(outcome, self.ops)
            kind = "normal" if outcome.kind == "return" else outcome.kind
            result.append(Flow(applied, kind, outcome.value, outcome.exception))
        return result

    def semantic_input_partition(self, info, args, keywords, defaults, store):
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
            store.cells.get(f"global:{info.module}:{name}", Value.bottom()) for name in names
        )
        pending = [ref for value in values for ref in value.refs]
        seen = set()
        tokens = []
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
                value = obj.metadata[name]
                if type(value) is int:
                    value = value if 0 <= value < 32 else "many"
                elif type(value) is not str:
                    value = "abstract"
                tokens.append((ref.split("@", 1)[0], name, value))
            for value in (*obj.fields.values(), obj.default):
                pending.extend(value.refs)
            container = obj.metadata.lookup("container")
            if isinstance(container, Value):
                pending.extend(container.refs)
        if pending:
            tokens.append(("reachable", "overflow", "many"))
        return stable_digest(sorted(tokens, key=str))[:16]

    def compact(self, flows):
        flows = self.coarsen_flows(flows)
        # Subsumption removes repeated recursive approximations before counting
        # paths; otherwise duplicate return paths can oscillate below the cap.
        maximal = []
        for flow in flows:
            same = lambda other: (
                flow.kind == other.kind
                and flow.exception == other.exception
                and semantic_payload_equal(
                    (flow.continuation, flow.cleanups, flow.resume_target),
                    (other.continuation, other.cleanups, other.resume_target),
                )
            )
            if any(
                same(old)
                and self.ops.leq(flow.value, old.value)
                and flow.store.leq(old.store, self.ops)
                for old in maximal
            ):
                continue
            maximal = [
                old
                for old in maximal
                if not (
                    same(old)
                    and self.ops.leq(old.value, flow.value)
                    and old.store.leq(flow.store, self.ops)
                )
            ]
            maximal.append(flow)
        flows = maximal
        if len(flows) <= self.max_paths:
            return flows
        buckets = {}
        for flow in flows:
            key = (
                flow.kind,
                flow.exception,
                payload_signature((flow.continuation, flow.cleanups, flow.resume_target)),
            )
            if key not in buckets:
                buckets[key] = flow
            else:
                old = buckets[key]
                buckets[key] = replace(
                    flow,
                    store=old.store.join(flow.store, self.ops),
                    value=self.ops.join(old.value, flow.value),
                )
        self.stats["trace_merges"] += len(flows) - len(buckets)
        return list(buckets.values())

    def _outcomes_equal(self, left, right):
        if len(left) != len(right):
            return False
        for a, b in zip(
            sorted(left, key=lambda f: (f.kind, f.exception or "")),
            sorted(right, key=lambda f: (f.kind, f.exception or "")),
        ):
            if not semantic_payload_equal(
                (a.continuation, a.cleanups, a.resume_target),
                (b.continuation, b.cleanups, b.resume_target),
            ):
                return False
            if (
                a.kind != b.kind
                or a.exception != b.exception
                or not self.ops.leq(a.value, b.value)
                or not self.ops.leq(b.value, a.value)
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
                    getattr(
                        self.oracle,
                        "phase_record",
                        {
                            "engine": "andersen",
                            "phase": "incremental_candidate_oracle",
                            "whole_program_prepass": False,
                            "coverage": "partial",
                        },
                    )
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
            for query in self.options.get("queries", ())
            if isinstance(query, Mapping) and query.get("module")
        }
        # Query demand changes scheduling, never the declared root set.
        entries = tuple(
            sorted(
                enumerate(entries),
                key=lambda pair: (
                    getattr(pair[1], "root_module", None) not in query_modules,
                    pair[0],
                ),
            )
        )
        try:
            self._check_limits(force=True)
            if self.oracle is not None and hasattr(self.oracle, "bind"):
                self.oracle.bind(self)
            if self.summary_provider is not None and hasattr(self.summary_provider, "charge_setup"):
                self.summary_provider.charge_setup(self)
            for index, entry in entries:
                module = (
                    getattr(entry, "root_module", getattr(entry, "module", None))
                    if not isinstance(entry, Mapping)
                    else entry.get("root_module", entry.get("module"))
                )
                qualname = (
                    getattr(entry, "entry_qualname", getattr(entry, "qualname", None))
                    if not isinstance(entry, Mapping)
                    else entry.get("entry_qualname", entry.get("qualname"))
                )
                self.scenario = getattr(entry, "scenario_id", "") or f"root-{index}-{module}"
                key = self.module_key(module)
                self.root_keys.append((self.scenario, module, key, qualname, entry))
                self.request_module(module, Store())
            # All scenarios are pending before solving; one initializer cannot
            # silently prevent the remaining declared roots from being queued.
            self._solve()
            for scenario, module, key, qualname, entry in self.root_keys:
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

    def _activate_entry(self, module, qualname, entry):
        key = self.module_key(module)
        unit = self.units.get(key)
        if unit is None:
            return
        short = qualname.split(".")[-1]
        arguments = (
            getattr(entry, "arguments", ())
            if not isinstance(entry, Mapping)
            else entry.get("arguments", ())
        )
        for out in unit.outcomes:
            if out.kind not in ("normal", "return"):
                continue
            callee = out.store.cells.get(f"global:{module}:{short}", Value.bottom())
            args = []
            keywords = []
            if arguments:
                args = [
                    self.input_value(x)
                    for x in arguments
                    if not (isinstance(x, Mapping) and "keyword" in x)
                ]
                keywords = [
                    (x["keyword"], self.input_value(x.get("abstract_value", {"top": True})))
                    for x in arguments
                    if isinstance(x, Mapping) and "keyword" in x
                ]
            for identifier in callee.refs:
                info = self.functions.get(identifier)
                if info:
                    if isinstance(entry, Mapping) and (
                        "arguments" not in entry or entry.get("arguments") is None
                    ):
                        args = [
                            Value.unknown()
                            for _ in (*info.node.args.posonlyargs, *info.node.args.args)
                        ]
                        keywords = [
                            (x.arg, Value.unknown())
                            for x in info.node.args.kwonlyargs
                            if x.arg not in dict(info.kw_defaults)
                        ]
                    fake = info.node
                    self.request_function(identifier, args, keywords, out.store, unit.frame, fake)
                else:
                    self.open("entry_not_callable:" + qualname)
            if callee.is_bottom():
                self.open("entry_not_found:" + qualname)

    def _solve(self):
        while self.queue:
            key = self.queue.popleft()
            self.queued.discard(key)
            unit = self.units[key]
            self.current = key
            self.scenario = self.unit_scenarios[key]
            unit.visits += 1
            self.stats["unit_evaluations"] += 1
            self._activation_occurrences.clear()
            if unit.visits > int(self.abstract.get("max_iterations", 100)):
                raise BudgetStop("unit_iterations")
            self.emit("work_dequeue", unit=key, revision=unit.revision)
            entry = unit.entry.clear_effects()
            flows = self.transfer.block(unit.body, [Flow(entry)], unit.frame)
            # Address quotient publication may replace the host unit graph
            # while this transfer is suspended in a nested call.
            unit = self.units[key]
            normalized = []
            for flow in flows:
                if not unit.is_module and flow.kind == "normal":
                    flow = Flow(flow.store, "return", Value.constant(None))
                if unit.is_module and flow.kind == "normal":
                    flow.store.initialized[unit.frame.module] = "initialized"
                normalized.append(flow)
            normalized = self.compact(normalized)
            if not self._outcomes_equal(unit.outcomes, normalized):
                unit.outcomes = normalized
                unit.revision += 1
                if self.observer is not None:
                    self.emit(
                        "summary_update",
                        unit=key,
                        qualname=unit.frame.qualname,
                        revision=unit.revision,
                        outcomes=[
                            {"kind": f.kind, "value": f.value.export(), "exception": f.exception}
                            for f in normalized
                        ],
                        consumers=sorted(unit.consumers),
                    )
                else:
                    self.emit(
                        "summary_update",
                        unit=key,
                        revision=unit.revision,
                        outcomes=[f.kind for f in normalized],
                    )
                for consumer in unit.consumers:
                    self.enqueue(consumer, "callee_exit_growth")
            self.current = None

    def input_value(self, descriptor):
        if isinstance(descriptor, Value):
            return descriptor
        if isinstance(descriptor, Mapping):
            sort = descriptor.get("type")
            if "constants" in descriptor:
                return self.ops.join_all(Value.constant(x) for x in descriptor["constants"])
            if "value" in descriptor:
                return Value.constant(descriptor["value"])
            if sort == "str":
                return Value(strings=self.ops.string.top())
            if sort == "int":
                return Value(ints=self.ops.integer.top())
            if sort == "bool":
                return Value(bools=frozenset((False, True)))
            if sort == "none":
                return Value.constant(None)
            return Value.unknown()
        return Value.constant(descriptor)

    def snapshot(self, elapsed):
        values = {}
        returns = {}
        outcomes = {}
        kinds = {}
        heaps = {}
        module_heaps = {}
        procedure_heaps = {}
        module_exits = {}
        procedure_meta = {}

        def merge_heap(target, store):
            for objid, obj in store.heap.items():
                target[objid] = target[objid].join(obj, self.ops) if objid in target else obj.copy()

        for unit in self.units.values():
            if not unit.is_module:
                returns.setdefault(unit.frame.qualname, Value.bottom())
                procedure_meta.setdefault(
                    unit.frame.qualname,
                    {"normal_exit": False, "exceptions": set(), "suspends": False},
                )
            for flow in unit.outcomes:
                kinds.update((k, v.kind) for k, v in flow.store.heap.items())
                merge_heap(heaps, flow.store)
                if unit.is_module and flow.kind in {"normal", "return"}:
                    merge_heap(module_heaps.setdefault(unit.frame.module, {}), flow.store)
                elif not unit.is_module and flow.kind == "return":
                    merge_heap(procedure_heaps.setdefault(unit.frame.qualname, {}), flow.store)
                outcomes.setdefault(unit.frame.qualname, []).append(
                    {
                        "kind": flow.kind,
                        "value": flow.value.export(kinds),
                        "exception": flow.exception,
                        "context": list(unit.frame.context),
                        "revision": unit.revision,
                    }
                )
                if unit.is_module:
                    module_exits.setdefault(unit.frame.module, []).append(
                        {
                            "scenario": self.unit_scenarios[unit.key],
                            "kind": flow.kind,
                            "exception": flow.exception,
                        }
                    )
                if unit.is_module and flow.kind in ("normal", "return"):
                    target = values.setdefault(unit.frame.module, {})
                    prefix = f"global:{unit.frame.module}:"
                    for cell, value in flow.store.cells.items():
                        if cell.startswith(prefix):
                            symbol = cell[len(prefix) :]
                            old = target.get(symbol, Value.bottom())
                            target[symbol] = self.ops.join(old, value)
                elif not unit.is_module:
                    meta = procedure_meta[unit.frame.qualname]
                    if flow.kind == "return":
                        returns[unit.frame.qualname] = self.ops.join(
                            returns[unit.frame.qualname], flow.value
                        )
                        meta["normal_exit"] = True
                    if flow.kind == "raise":
                        meta["exceptions"].add(flow.exception)
                    if flow.kind == "suspend":
                        meta["suspends"] = True
        for query in self.options.get("queries", ()):
            if (
                not isinstance(query, Mapping)
                or query.get("kind") != "value"
                or query.get("point", "module_exit") != "module_exit"
            ):
                continue
            module = query.get("module")
            symbol = query.get("symbol")
            if not module or not symbol:
                continue
            for unit in self.units.values():
                if not unit.is_module or unit.frame.module != module:
                    continue
                for flow in unit.outcomes:
                    if (
                        flow.kind not in {"normal", "return"}
                        or module not in flow.store.havoc_namespaces
                    ):
                        continue
                    bound = flow.store.effective_cell(f"global:{module}:{symbol}")
                    values.setdefault(module, {})[symbol] = self.ops.join(
                        values.get(module, {}).get(symbol, Value.bottom()), bound
                    )
        for obj in heaps.values():
            if obj.kind in ("generator", "coroutine", "async_generator"):
                info = self.functions.get(obj.metadata.lookup("function"))
                if info is None:
                    self.open("unrepresented_suspended_function")
                    continue
                returns.setdefault(info.qualname, Value.bottom())
                procedure_meta[info.qualname] = {
                    "normal_exit": False,
                    "exceptions": set(),
                    "suspends": info.is_generator,
                    "body_executed_at_creation": False,
                }
                outcomes.setdefault(info.qualname, [])

        def export(value, seen=frozenset(), heap_view=None):
            heap_view = heaps if heap_view is None else heap_view
            out = value.export(kinds)
            shapes = []
            for ref in value.refs:
                if ref in seen:
                    continue
                obj = heap_view.get(ref)
                if (
                    obj
                    and obj.kind in ("list", "tuple", "range")
                    and "length" in obj.metadata
                    and obj.default.is_bottom()
                    and not obj.havoc
                ):
                    items = []
                    exact = True
                    for index in range(obj.metadata["length"]):
                        item = obj.fields.get(index, Value.bottom())
                        constants = item.exact_constants()
                        if constants is None or len(constants) != 1:
                            exact = False
                            break
                        items.append(constants[0])
                    if exact:
                        shapes.append(items)
                if obj and obj.kind == "dict":
                    out.setdefault("slots", []).append(
                        {
                            "object": ref,
                            "entries": [
                                {"key": key, "value": export(item, seen | {ref}, heap_view)}
                                for key, item in obj.fields.items()
                                if isinstance(key, (str, int, bool, float)) or key is None
                            ],
                            "default": obj.default.export(kinds),
                            "havoc": obj.havoc,
                        }
                    )
            if shapes and all(shape == shapes[0] for shape in shapes):
                out["items"] = shapes[0]
            return out

        return_rows = {}
        for name, value in returns.items():
            row = export(value, heap_view=procedure_heaps.get(name, heaps))
            meta = procedure_meta.get(name, {})
            row.update({key: sorted(v) if isinstance(v, set) else v for key, v in meta.items()})
            return_rows[name] = row
        self.stats.update(
            {
                "work": self.work,
                "total_work": self.work,
                "units": len(self.units),
                "analyzed_units": sum(unit.visits > 0 for unit in self.units.values()),
                "root_scenarios": len(self.root_keys),
                "initialized_roots": sum(
                    any(flow.kind in {"normal", "return"} for flow in self.units[key].outcomes)
                    for _, _, key, _, _ in self.root_keys
                    if key in self.units
                ),
                "summaries": sum(bool(u.outcomes) for u in self.units.values()),
                "functions": len(self.functions),
                "pending_units": max(self.stats.get("pending_units", 0), len(self.queue)),
                "elapsed_seconds": elapsed,
                "pta_work": 0,
            }
        )
        if self.oracle is not None and hasattr(self.oracle, "statistics"):
            self.stats["oracle"] = self.oracle.statistics()
            self.stats["pta_work"] = self.stats["oracle"].get("work", 0)
        return EngineSnapshot(
            **{
                "schema_version": 1,
                "engine": "hybrid" if self.oracle is not None else "ai_primary",
                "solver_status": self.solver_status,
                "coverage_status": "open" if self.obligations else "closed_under_profile",
                "profile": self.options.get("semantic_profile", "python-core-v1"),
                "values": {
                    m: {n: export(v, heap_view=module_heaps.get(m, heaps)) for n, v in rows.items()}
                    for m, rows in values.items()
                },
                "module_exits": module_exits,
                "returns": return_rows,
                "outcomes": outcomes,
                "calls": [{"caller": a, "callee": b, "site": s} for a, b, s in sorted(self.calls)],
                "effects": {
                    "reads": [list(x) for x in sorted(self.reads, key=str)],
                    "writes": [list(x) for x in sorted(self.writes, key=str)],
                    "calls": sorted({b for _, b, _ in self.calls}),
                    "exceptions": sorted(self.exceptions),
                    "prefix_events": [list(x) for x in self.events],
                    "havoc": [list(x) for x in self.writes if x[0] == "unknown"],
                },
                "obligations": [{"code": x, "status": "open"} for x in sorted(self.obligations)],
                "stats": dict(self.stats),
                "phase_trace": self.phase_trace,
            }
        )
