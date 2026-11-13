"""Publish solved state through explicit data inputs rather than a driver port.

Preparation reports ordered warning reasons. The driver records those warnings,
publishes base statistics, and only then asks its oracle for statistics. Final
publication copies the host's post-oracle statistics without recomputing them.
Physical state ownership does not become semantic wire identity.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from types import MappingProxyType
from typing import Literal, assert_never
from weakref import WeakSet

from pythonstan.analysis.contracts.cost_model import visit_json_text
from pythonstan.analysis.contracts.engine import EngineSnapshot
from pythonstan.analysis.contracts.identity import (
    CellId,
    ContextId,
    ObjectId,
    ProgramPointId,
    UnitId,
)
from pythonstan.analysis.contracts.json_data import (
    FrozenJsonObject,
    FrozenJsonValue,
    JsonObject,
    JsonValue,
)
from pythonstan.analysis.contracts.wire import freeze_snapshot
from pythonstan.analysis.domains.values import Value, ValueOperations
from pythonstan.analysis.state.flow import (
    BreakFlow,
    ContinueFlow,
    Flow,
    NormalExprFlow,
    NormalStmtFlow,
    RaiseFlow,
    ReturnFlow,
    SuspendFlow,
)
from pythonstan.analysis.state.store import FunctionInfo, HeapObject, Store, Unit
from pythonstan.analysis.state.transform import walk_graph

type CallFact = tuple[str, str, ProgramPointId]
type AccessFact = tuple[str, str, ProgramPointId]
type PrefixEvent = tuple[str, str]
type HeapView = Mapping[ObjectId, HeapObject]
type MutableHeap = dict[ObjectId, HeapObject]
type PublicationWarning = Literal["unrepresented_suspended_function"]
type Visitor = Callable[[int], None]


@dataclass(frozen=True, slots=True)
class ModuleValueRequest:
    module: str
    symbol: str


def module_value_requests(queries: JsonValue) -> tuple[ModuleValueRequest, ...]:
    """Select the existing module-exit requests without upper result imports."""
    if not isinstance(queries, list):
        return ()
    result: list[ModuleValueRequest] = []
    for query in queries:
        if (
            not isinstance(query, dict)
            or query.get("kind") != "value"
            or query.get("point", "module_exit") != "module_exit"
        ):
            continue
        module, symbol = query.get("module"), query.get("symbol")
        if isinstance(module, str) and module and isinstance(symbol, str) and symbol:
            result.append(ModuleValueRequest(module, symbol))
    return tuple(result)


@dataclass(frozen=True, slots=True)
class PublicationUnit:
    key: UnitId
    module: str
    qualname: str
    context: ContextId
    revision: int
    visits: int
    is_module: bool
    scenario: str | None
    outcomes: tuple[Flow, ...]

    @classmethod
    def capture(
        cls, unit: Unit, scenario: str | None, *, visit: Visitor | None = None
    ) -> PublicationUnit:
        if visit is not None:
            visit(1 + len(unit.outcomes))
        return cls(
            unit.key,
            unit.frame.module,
            unit.frame.qualname,
            unit.frame.context,
            unit.revision,
            unit.visits,
            unit.is_module,
            scenario,
            tuple(unit.outcomes),
        )


@dataclass(frozen=True, slots=True)
class FunctionPublication:
    qualname: str
    is_generator: bool

    @classmethod
    def capture(cls, function: FunctionInfo) -> FunctionPublication:
        return cls(function.qualname, function.is_generator)


@dataclass(frozen=True, slots=True)
class PublicationInput:
    units: tuple[PublicationUnit, ...]
    functions: Mapping[ObjectId, FunctionPublication]
    queries: tuple[ModuleValueRequest, ...]
    roots: tuple[UnitId, ...]
    calls: tuple[CallFact, ...]
    reads: tuple[AccessFact, ...]
    writes: tuple[AccessFact, ...]
    events: tuple[PrefixEvent, ...]
    exceptions: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ValueOutcome:
    kind: Literal["normal", "return", "raise", "suspend"]
    value: Value
    exception: str | None

    def to_json(self, unit: PublicationUnit, kinds: Mapping[ObjectId, str]) -> JsonObject:
        return {
            "kind": self.kind,
            "value": self.value.export(kinds),
            "value_present": True,
            "exception": self.exception,
            "context": list[JsonValue](unit.context),
            "revision": unit.revision,
        }


@dataclass(frozen=True, slots=True)
class NoValueOutcome:
    kind: Literal["normal", "break", "continue"]

    def to_json(self, unit: PublicationUnit, kinds: Mapping[ObjectId, str]) -> JsonObject:
        return {
            "kind": self.kind,
            "value": None,
            "value_present": False,
            "exception": None,
            "context": list[JsonValue](unit.context),
            "revision": unit.revision,
        }


type PublishedOutcome = ValueOutcome | NoValueOutcome


def _outcome(flow: Flow) -> PublishedOutcome:
    match flow:
        case NormalExprFlow(value=value) | ReturnFlow(value=value) | SuspendFlow(value=value):
            return ValueOutcome(flow.kind, value, None)
        case RaiseFlow(exception_value=exception):
            return ValueOutcome("raise", exception.value, exception.family)
        case NormalStmtFlow() | BreakFlow() | ContinueFlow():
            return NoValueOutcome(flow.kind)
        case _:
            assert_never(flow)


@dataclass(slots=True)
class ProcedureExit:
    normal_exit: bool = False
    exceptions: set[str] = field(default_factory=set)
    suspends: bool = False
    body_executed_at_creation: bool | None = None

    def to_json(self) -> JsonObject:
        result: JsonObject = {
            "normal_exit": self.normal_exit,
            "exceptions": list[JsonValue](sorted(self.exceptions)),
            "suspends": self.suspends,
        }
        if self.body_executed_at_creation is not None:
            result["body_executed_at_creation"] = self.body_executed_at_creation
        return result


@dataclass(frozen=True, slots=True)
class SummaryCounts:
    units: int
    analyzed_units: int
    root_scenarios: int
    initialized_roots: int
    summaries: int
    functions: int

    def to_json(self) -> JsonObject:
        return {
            "units": self.units,
            "analyzed_units": self.analyzed_units,
            "root_scenarios": self.root_scenarios,
            "initialized_roots": self.initialized_roots,
            "summaries": self.summaries,
            "functions": self.functions,
        }


@dataclass(frozen=True, slots=True)
class PreparedPublication:
    values: Mapping[str, Mapping[str, Value]]
    returns: Mapping[str, Value]
    outcomes: JsonObject
    module_exits: JsonObject
    kinds: Mapping[ObjectId, str]
    heaps: HeapView
    module_heaps: Mapping[str, MutableHeap]
    procedure_heaps: Mapping[str, MutableHeap]
    procedure_exits: Mapping[str, ProcedureExit]
    counts: SummaryCounts
    calls: tuple[CallFact, ...]
    reads: tuple[AccessFact, ...]
    writes: tuple[AccessFact, ...]
    events: tuple[PrefixEvent, ...]
    exceptions: tuple[str, ...]
    open_obligations: tuple[PublicationWarning, ...]
    heap_scope: Literal["semantic_snapshot", "export_view"] = "semantic_snapshot"


@dataclass(frozen=True, slots=True)
class PublicationRuntime:
    work: int
    elapsed: float
    pending_units: int
    stats: Mapping[str, JsonValue]
    obligations: frozenset[str]
    phase_trace: tuple[JsonObject, ...]
    profile: str
    solver_status: str
    oracle_statistics: JsonObject | None = None


@dataclass(frozen=True, slots=True)
class PublishedSnapshot:
    snapshot: EngineSnapshot
    stats: JsonObject


def _admit_value(value: Value, visit: Visitor | None) -> None:
    if visit is None:
        return
    visit(1)
    for integer in value.ints.consts:
        visit(1 + integer.bit_length() // 8192)
    for bound in (value.ints.lo, value.ints.hi):
        if bound is not None:
            visit(1 + bound.bit_length() // 8192)
    for group in (value.strings.consts, value.strings.prefixes, value.strings.suffixes):
        for text in group:
            visit_json_text(text, visit)
    for prefix, suffix in value.strings.conjunctions:
        visit_json_text(prefix, visit)
        visit_json_text(suffix, visit)
    for group in (value.refs, value.top_sorts):
        for text in group:
            visit_json_text(text, visit)
    visit(len(value.bools) + len(value.floats))
    for relation in value.relations:
        visit(1)
        for text in (relation.point, relation.key_type, relation.key_repr, relation.handler):
            visit_json_text(text, visit)
        if relation.display_name is not None:
            visit_json_text(relation.display_name, visit)


def _heap_view_object(obj: HeapObject, visit: Visitor | None) -> HeapObject:
    """Own only components the published value view can observe.

    Captured stores, frames, ASTs and continuations are execution state. Copying
    them for a data-only heap view adds work without contributing a wire fact.
    Values, identities and field keys are immutable; the projected dictionaries
    and key-presence sets are detached before any merge.
    """
    fields = {}
    for key, value in obj.fields.items():
        if visit is not None:
            visit(2)
            if isinstance(key, str):
                visit_json_text(key, visit)
        fields[key] = value
    missing = set()
    for key in obj.maybe_missing:
        if visit is not None:
            visit(1)
            if isinstance(key, str):
                visit_json_text(key, visit)
        missing.add(key)
    metadata: dict[str, object] = {}
    length = obj.metadata.lookup("length")
    if length is not None:
        metadata["length"] = length
    length_value = obj.metadata.lookup("length_value")
    if length_value is not None:
        metadata["length_value"] = length_value
    summarized = obj.metadata.lookup("summarized_keys", frozenset())
    if summarized:
        if visit is not None:
            visit(len(summarized))
        metadata["summarized_keys"] = summarized
    function = obj.metadata.lookup("function")
    if function is not None:
        metadata["function"] = function
    if visit is not None:
        visit(1 + len(metadata) + len(obj.class_ids))
    return HeapObject(
        obj.kind, fields, obj.default, missing, obj.singleton, obj.class_ids, metadata, obj.havoc
    )


@dataclass(slots=True)
class _HeapViewCache:
    objects: dict[int, tuple[HeapObject, HeapObject]] = field(default_factory=dict)
    merges: dict[tuple[int, int], HeapObject] = field(default_factory=dict)


def _same_heap_view(left: HeapObject, right: HeapObject, visit: Visitor | None) -> bool:
    if visit is not None:
        visit(
            1
            + len(left.fields)
            + len(right.fields)
            + len(left.maybe_missing)
            + len(right.maybe_missing)
        )
    if (
        left.kind != right.kind
        or left.havoc != right.havoc
        or left.singleton != right.singleton
        or left.class_ids != right.class_ids
        or left.fields.keys() != right.fields.keys()
        or left.maybe_missing != right.maybe_missing
    ):
        return False
    pairs = [(left.default, right.default)]
    for key, value in left.fields.items():
        if visit is not None:
            visit(1)
        pairs.append((value, right.fields[key]))
    for first, second in pairs:
        if first is second:
            continue
        _admit_value(first, visit)
        _admit_value(second, visit)
        if first != second:
            return False
    # The projection has exactly these metadata components. No semantic store,
    # frame, continuation or arbitrary component attachment participates.
    if left.metadata.lookup("length") != right.metadata.lookup("length"):
        return False
    first_length, second_length = (
        left.metadata.lookup("length_value"),
        right.metadata.lookup("length_value"),
    )
    if first_length is not second_length:
        if first_length is None or second_length is None:
            return False
        _admit_value(first_length, visit)
        _admit_value(second_length, visit)
        if first_length != second_length:
            return False
    first_keys = left.metadata.lookup("summarized_keys", frozenset())
    second_keys = right.metadata.lookup("summarized_keys", frozenset())
    if visit is not None and first_keys is not second_keys:
        visit(len(first_keys) + len(second_keys))
    return first_keys == second_keys and left.metadata.lookup("function") == right.metadata.lookup(
        "function"
    )


def _merge_heap(
    target: MutableHeap,
    store: Store,
    ops: ValueOperations,
    visit: Visitor | None,
    *,
    export_view: bool = False,
    cache: _HeapViewCache | None = None,
) -> None:
    for identity, obj in store.heap.items():
        if not export_view:
            if visit is not None:
                visit_json_text(identity, visit)
                walk_graph(obj, on_value=lambda value: _admit_value(value, visit), charge=visit)
                if identity in target:
                    walk_graph(
                        target[identity],
                        on_value=lambda value: _admit_value(value, visit),
                        charge=visit,
                    )
            target[identity] = target[identity].join(obj, ops) if identity in target else obj.copy()
            continue
        if visit is not None:
            visit_json_text(identity, visit)
        if visit is not None:
            visit(1)
        cached = None if cache is None else cache.objects.get(id(obj))
        projected = None if cached is None else cached[1]
        if projected is None:
            projected = _heap_view_object(obj, visit)
            if cache is not None:
                if visit is not None:
                    visit(1)
                cache.objects[id(obj)] = (obj, projected)
        previous = target.get(identity)
        if previous is None or previous is projected:
            target[identity] = projected
            continue
        if _same_heap_view(previous, projected, visit):
            continue
        pair = (id(previous), id(projected))
        if visit is not None:
            visit(1)
        merged = None if cache is None else cache.merges.get(pair)
        if merged is None:
            if visit is not None:
                walk_graph(
                    projected, on_value=lambda value: _admit_value(value, visit), charge=visit
                )
                walk_graph(
                    previous, on_value=lambda value: _admit_value(value, visit), charge=visit
                )
            merged = previous.join(projected, ops)
            if cache is not None:
                if visit is not None:
                    visit(1)
                cache.merges[pair] = merged
        target[identity] = merged


def prepare_publication(
    source: PublicationInput,
    ops: ValueOperations,
    *,
    visit: Visitor | None = None,
    export_view: bool = False,
) -> PreparedPublication:
    """Aggregate real solved exits in their existing iteration order."""
    view_cache = _HeapViewCache() if export_view else None
    values: dict[str, dict[str, Value]] = {}
    returns: dict[str, Value] = {}
    outcome_rows: dict[str, list[JsonValue]] = {}
    kinds: dict[ObjectId, str] = {}
    heaps: MutableHeap = {}
    module_heaps: dict[str, MutableHeap] = {}
    procedure_heaps: dict[str, MutableHeap] = {}
    module_exit_rows: dict[str, list[JsonValue]] = {}
    procedure_exits: dict[str, ProcedureExit] = {}
    for unit in source.units:
        if visit is not None:
            visit(1)
        if not unit.is_module:
            returns.setdefault(unit.qualname, Value.bottom())
            procedure_exits.setdefault(unit.qualname, ProcedureExit())
        for flow in unit.outcomes:
            store = flow.store
            for identity, obj in store.heap.items():
                if visit is not None:
                    visit_json_text(identity, visit)
                kinds[identity] = obj.kind
            _merge_heap(heaps, store, ops, visit, export_view=export_view, cache=view_cache)
            if unit.is_module and flow.kind in {"normal", "return"}:
                _merge_heap(
                    module_heaps.setdefault(unit.module, {}),
                    store,
                    ops,
                    visit,
                    export_view=export_view,
                    cache=view_cache,
                )
            elif not unit.is_module and isinstance(flow, ReturnFlow):
                _merge_heap(
                    procedure_heaps.setdefault(unit.qualname, {}),
                    store,
                    ops,
                    visit,
                    export_view=export_view,
                    cache=view_cache,
                )
            # Later heap kinds must not retroactively change earlier outcomes.
            outcome = _outcome(flow)
            if visit is not None:
                visit(1 + len(unit.context))
            if isinstance(outcome, ValueOutcome):
                _admit_value(outcome.value, visit)
            outcome_rows.setdefault(unit.qualname, []).append(outcome.to_json(unit, kinds))
            if unit.is_module:
                if unit.scenario is None:
                    raise ValueError("A module publication requires its entry scenario")
                module_exit_rows.setdefault(unit.module, []).append(
                    {"scenario": unit.scenario, "kind": flow.kind, "exception": flow.exception}
                )
            if unit.is_module and flow.kind in {"normal", "return"}:
                target = values.setdefault(unit.module, {})
                prefix = f"global:{unit.module}:"
                for cell, value in store.cells.items():
                    if visit is not None:
                        visit_json_text(cell, visit)
                        _admit_value(value, visit)
                    if cell.startswith(prefix):
                        symbol = cell[len(prefix) :]
                        target[symbol] = ops.join(target.get(symbol, Value.bottom()), value)
            elif not unit.is_module:
                metadata = procedure_exits[unit.qualname]
                if isinstance(flow, ReturnFlow):
                    returns[unit.qualname] = ops.join(returns[unit.qualname], flow.value)
                    metadata.normal_exit = True
                elif isinstance(flow, RaiseFlow):
                    metadata.exceptions.add(flow.exception)
                elif isinstance(flow, SuspendFlow):
                    metadata.suspends = True
    for query in source.queries:
        if visit is not None:
            visit(1)
        for unit in source.units:
            if not unit.is_module or unit.module != query.module:
                continue
            for flow in unit.outcomes:
                store = flow.store
                if (
                    flow.kind not in {"normal", "return"}
                    or query.module not in store.havoc_namespaces
                ):
                    continue
                bound = store.effective_cell(CellId(f"global:{query.module}:{query.symbol}"))
                target = values.setdefault(query.module, {})
                target[query.symbol] = ops.join(target.get(query.symbol, Value.bottom()), bound)
    warnings: list[PublicationWarning] = []
    for obj in heaps.values():
        if visit is not None:
            visit(1)
        if obj.kind not in {"generator", "coroutine", "async_generator"}:
            continue
        function_id = obj.metadata.lookup("function")
        function = source.functions.get(function_id) if function_id is not None else None
        if function is None:
            warnings.append("unrepresented_suspended_function")
            continue
        returns.setdefault(function.qualname, Value.bottom())
        # Preserve the prior creation-only summary overwrite, not a new fix.
        procedure_exits[function.qualname] = ProcedureExit(
            suspends=function.is_generator, body_executed_at_creation=False
        )
        outcome_rows.setdefault(function.qualname, [])
    units_by_key = {unit.key: unit for unit in source.units}
    counts = SummaryCounts(
        units=len(source.units),
        analyzed_units=sum(unit.visits > 0 for unit in source.units),
        root_scenarios=len(source.roots),
        initialized_roots=sum(
            any(flow.kind in {"normal", "return"} for flow in units_by_key[key].outcomes)
            for key in source.roots
            if key in units_by_key
        ),
        summaries=sum(bool(unit.outcomes) for unit in source.units),
        functions=len(source.functions),
    )
    return PreparedPublication(
        values=values,
        returns=returns,
        outcomes=dict(outcome_rows),
        module_exits=dict(module_exit_rows),
        kinds=kinds,
        heaps=heaps,
        module_heaps=module_heaps,
        procedure_heaps=procedure_heaps,
        procedure_exits=procedure_exits,
        counts=counts,
        calls=source.calls,
        reads=source.reads,
        writes=source.writes,
        events=source.events,
        exceptions=source.exceptions,
        open_obligations=tuple(warnings),
        heap_scope="export_view" if export_view else "semantic_snapshot",
    )


def _same_shape(left: JsonValue, right: JsonValue) -> bool:
    if type(left) is not type(right):
        return False
    if isinstance(left, float) and isinstance(right, float):
        return left.hex() == right.hex()
    if isinstance(left, list) and isinstance(right, list):
        return len(left) == len(right) and all(
            _same_shape(a, b) for a, b in zip(left, right, strict=True)
        )
    if isinstance(left, dict) and isinstance(right, dict):
        return left.keys() == right.keys() and all(
            _same_shape(child, right[key]) for key, child in left.items()
        )
    return left == right


def _exact_shape(
    value: Value,
    heap: HeapView,
    seen: frozenset[ObjectId],
    visit: Visitor | None,
    depth: int,
) -> tuple[bool, JsonValue]:
    _admit_value(value, visit)
    if depth > 48:
        return False, None
    constants = value.exact_constants()
    if constants is not None:
        return (True, constants[0]) if len(constants) == 1 else (False, None)
    if not value.refs or not replace(value, refs=frozenset()).is_bottom():
        return False, None
    result: JsonValue = None
    first = True
    for reference in value.refs:
        if visit is not None:
            visit(1)
        if reference in seen:
            return False, None
        obj = heap.get(reference)
        if obj is None or obj.kind not in {"list", "tuple", "range"}:
            return False, None
        length = obj.metadata.lookup("length")
        length_value = obj.metadata.lookup("length_value")
        if length_value is not None:
            _admit_value(length_value, visit)
            exact_length = length_value.exact_constants()
            if (
                exact_length is None
                or len(exact_length) != 1
                or type(exact_length[0]) is not int
                or exact_length[0] != length
            ):
                return False, None
        if (
            length is None
            or length < 0
            or obj.havoc
            or not obj.default.is_bottom()
            or obj.maybe_missing
            or len(obj.fields) != length
            or obj.metadata.lookup("summarized_keys", frozenset())
        ):
            return False, None
        if visit is not None:
            visit(length)
        items: list[JsonValue] = []
        for index in range(length):
            element = obj.fields.get(index)
            if element is None:
                return False, None
            exact, child = _exact_shape(element, heap, seen | {reference}, visit, depth + 1)
            if not exact:
                return False, None
            items.append(child)
        candidate: JsonValue = {"tuple": items} if obj.kind == "tuple" else items
        if not first and not _same_shape(result, candidate):
            return False, None
        result, first = candidate, False
    return not first, result


def _export_value(
    value: Value,
    kinds: Mapping[ObjectId, str],
    heap: HeapView,
    seen: frozenset[ObjectId] = frozenset(),
    *,
    visit: Visitor | None = None,
) -> JsonObject:
    _admit_value(value, visit)
    result = value.export(kinds)
    shape_exact, shape = _exact_shape(value, heap, seen, visit, 0)
    if shape_exact and isinstance(shape, list):
        result["items"] = shape
    elif shape_exact and isinstance(shape, dict) and "tuple" in shape:
        result["items"] = shape["tuple"]
    elif value.refs:
        result["shape_projection"] = {"complete": False, "omitted_fields": ["items"]}
    slots: list[JsonValue] = []
    for ref in value.refs:
        if visit is not None:
            visit(1)
        if ref in seen or len(seen) >= 48:
            continue
        obj = heap.get(ref)
        if obj is not None and obj.kind == "dict":
            entries: list[JsonValue] = []
            for key, item in obj.fields.items():
                if visit is not None:
                    visit(1)
                    if isinstance(key, str):
                        visit_json_text(key, visit)
                if key is None or isinstance(key, (str, int, bool, float)):
                    entries.append(
                        {
                            "key": key,
                            "value": _export_value(item, kinds, heap, seen | {ref}, visit=visit),
                        }
                    )
            _admit_value(obj.default, visit)
            slots.append(
                {
                    "object": ref,
                    "entries": entries,
                    "default": obj.default.export(kinds),
                    "havoc": obj.havoc,
                }
            )
    if slots:
        result["slots"] = slots
    return result


@dataclass(frozen=True, eq=False)
class _ExportOwnership:
    values: FrozenJsonObject
    returns: FrozenJsonObject
    origin: PreparedPublication | None = None


_EXPORTS: WeakSet[_ExportOwnership] = WeakSet()


@dataclass(frozen=True)
class ExportedPublication:
    values: FrozenJsonObject
    returns: FrozenJsonObject
    _ownership: _ExportOwnership

    def validate(self, *, origin: PreparedPublication | None = None) -> None:
        receipt = self._ownership
        if (
            receipt not in _EXPORTS
            or receipt.values is not self.values
            or receipt.returns is not self.returns
        ):
            raise ValueError("Publication export requires its genuine ownership receipt")
        if origin is not None and receipt.origin is not origin:
            raise ValueError("Publication export receipt belongs to another prepared origin")


def export_publication_values(
    prepared: PreparedPublication, *, visit: Visitor | None = None
) -> ExportedPublication:
    values: JsonObject = {}
    for module, rows in prepared.values.items():
        if not rows:
            continue
        if visit is not None:
            visit_json_text(module, visit)
        selected: JsonObject = {}
        for name, value in rows.items():
            if visit is not None:
                visit_json_text(name, visit)
            selected[name] = _export_value(
                value,
                prepared.kinds,
                prepared.module_heaps.get(module, prepared.heaps),
                visit=visit,
            )
        values[module] = selected
    returns: JsonObject = {}
    for name, value in prepared.returns.items():
        if visit is not None:
            visit_json_text(name, visit)
        row = _export_value(
            value, prepared.kinds, prepared.procedure_heaps.get(name, prepared.heaps), visit=visit
        )
        metadata = prepared.procedure_exits.get(name)
        if metadata is not None:
            if visit is not None:
                visit(1 + len(metadata.exceptions))
            row.update(metadata.to_json())
        returns[name] = row
    frozen_values = freeze_snapshot(values, visit=visit)
    frozen_returns = freeze_snapshot(returns, visit=visit)
    if not isinstance(frozen_values, Mapping) or not isinstance(frozen_returns, Mapping):
        raise TypeError("Exported publication payloads must be objects")
    receipt = _ExportOwnership(frozen_values, frozen_returns, prepared)
    _EXPORTS.add(receipt)
    return ExportedPublication(frozen_values, frozen_returns, receipt)


def publication_statistics(
    prepared: PreparedPublication, runtime: PublicationRuntime
) -> JsonObject:
    """Compute base counters before the actual driver invokes oracle.statistics."""
    stats: JsonObject = dict(runtime.stats)
    stats.update(prepared.counts.to_json())
    previous_pending = stats.get("pending_units", 0)
    if not isinstance(previous_pending, (int, float)):
        raise TypeError("The solver's pending-unit statistic must be numeric")
    stats.update(
        {
            "work": runtime.work,
            "total_work": runtime.work,
            "pending_units": max(previous_pending, runtime.pending_units),
            "elapsed_seconds": runtime.elapsed,
            "pta_work": 0,
        }
    )
    return stats


def finalize_publication(
    prepared: PreparedPublication,
    runtime: PublicationRuntime,
    *,
    published_statistics: Mapping[str, JsonValue],
    visit: Visitor | None = None,
    exported: ExportedPublication | None = None,
) -> PublishedSnapshot:
    """Freeze after warning/base-stat publication, retaining post-oracle changes."""
    exported = export_publication_values(prepared, visit=visit) if exported is None else exported
    exported.validate(origin=prepared)
    if visit is not None:
        # Admission precedes materializing any dynamic header sequences.
        for facts in (prepared.reads, prepared.writes, prepared.calls, prepared.events):
            for fact in facts:
                visit(1)
                for text in fact:
                    visit_json_text(text, visit)
        for text in prepared.exceptions:
            visit_json_text(text, visit)
        for text in runtime.obligations:
            visit_json_text(text, visit)
        for key in published_statistics:
            visit_json_text(key, visit)
    # Do not recompute base fields: the oracle may legitimately update the
    # host's statistics during its own statistics call.
    stats: JsonObject = dict(published_statistics)
    if runtime.oracle_statistics is not None:
        stats["oracle"] = runtime.oracle_statistics
        stats["pta_work"] = runtime.oracle_statistics.get("work", 0)
    effects: JsonObject = {
        "reads": [list[JsonValue](fact) for fact in sorted(prepared.reads, key=str)],
        "writes": [list[JsonValue](fact) for fact in sorted(prepared.writes, key=str)],
        "calls": list[JsonValue](sorted({callee for _, callee, _ in prepared.calls})),
        "exceptions": list[JsonValue](sorted(prepared.exceptions)),
        "prefix_events": [list[JsonValue](event) for event in prepared.events],
        "havoc": [list[JsonValue](fact) for fact in prepared.writes if fact[0] == "unknown"],
    }
    header: dict[str, object] = {
        "engine": "hybrid" if runtime.oracle_statistics is not None else "ai_primary",
        "solver_status": runtime.solver_status,
        "coverage_status": "open" if runtime.obligations else "closed_under_profile",
        "profile": runtime.profile,
        "schema_version": 1,
        "phase_trace": runtime.phase_trace,
        "values": {},
        "module_exits": prepared.module_exits,
        "returns": {},
        "outcomes": prepared.outcomes,
        "calls": tuple(
            {"caller": caller, "callee": callee, "site": site}
            for caller, callee, site in sorted(prepared.calls)
        ),
        "effects": effects,
        "obligations": tuple(
            {"code": code, "status": "open"} for code in sorted(runtime.obligations)
        ),
        "diagnostics": (),
        "stats": stats,
        "provenance": {},
    }
    frozen = freeze_snapshot(header, visit=visit)
    if not isinstance(frozen, Mapping):
        raise TypeError("Publication header must be an object")
    # The checked header and the registry-issued payloads are already owned.
    # Installation validates engine fields without a redundant deep recopy.
    record: dict[str, FrozenJsonValue] = dict(frozen)
    record["values"] = exported.values
    record["returns"] = exported.returns
    snapshot = object.__new__(EngineSnapshot)
    snapshot._install(MappingProxyType(record))
    return PublishedSnapshot(snapshot, stats)
