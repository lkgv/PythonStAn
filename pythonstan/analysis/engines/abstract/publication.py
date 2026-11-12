"""Publish solved state through explicit data inputs rather than a driver port.

Preparation reports ordered warning reasons. The driver records those warnings,
publishes base statistics, and only then asks its oracle for statistics. Final
publication copies the host's post-oracle statistics without recomputing them.
Physical state ownership does not become semantic wire identity.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Literal, assert_never

from pythonstan.analysis.contracts.engine import EngineSnapshot
from pythonstan.analysis.contracts.identity import (
    CellId,
    ContextId,
    ObjectId,
    ProgramPointId,
    UnitId,
)
from pythonstan.analysis.contracts.json_data import JsonObject, JsonValue
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

type CallFact = tuple[str, str, ProgramPointId]
type AccessFact = tuple[str, str, ProgramPointId]
type PrefixEvent = tuple[str, str]
type HeapView = Mapping[ObjectId, HeapObject]
type MutableHeap = dict[ObjectId, HeapObject]
type PublicationWarning = Literal["unrepresented_suspended_function"]


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
    def capture(cls, unit: Unit, scenario: str | None) -> PublicationUnit:
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


def _merge_heap(target: MutableHeap, store: Store, ops: ValueOperations) -> None:
    for identity, obj in store.heap.items():
        target[identity] = target[identity].join(obj, ops) if identity in target else obj.copy()


def prepare_publication(source: PublicationInput, ops: ValueOperations) -> PreparedPublication:
    """Aggregate real solved exits in their existing iteration order."""
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
        if not unit.is_module:
            returns.setdefault(unit.qualname, Value.bottom())
            procedure_exits.setdefault(unit.qualname, ProcedureExit())
        for flow in unit.outcomes:
            store = flow.store
            kinds.update((identity, obj.kind) for identity, obj in store.heap.items())
            _merge_heap(heaps, store, ops)
            if unit.is_module and flow.kind in {"normal", "return"}:
                _merge_heap(module_heaps.setdefault(unit.module, {}), store, ops)
            elif not unit.is_module and isinstance(flow, ReturnFlow):
                _merge_heap(procedure_heaps.setdefault(unit.qualname, {}), store, ops)
            # Later heap kinds must not retroactively change earlier outcomes.
            outcome_rows.setdefault(unit.qualname, []).append(_outcome(flow).to_json(unit, kinds))
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
    )


def _export_value(
    value: Value,
    kinds: Mapping[ObjectId, str],
    heap: HeapView,
    seen: frozenset[ObjectId] = frozenset(),
) -> JsonObject:
    result = value.export(kinds)
    shapes: list[list[JsonValue]] = []
    slots: list[JsonValue] = []
    for ref in value.refs:
        if ref in seen:
            continue
        obj = heap.get(ref)
        if obj is None:
            continue
        length = obj.metadata.lookup("length")
        if (
            obj.kind in {"list", "tuple", "range"}
            and length is not None
            and obj.default.is_bottom()
            and not obj.havoc
        ):
            items: list[JsonValue] = []
            for index in range(length):
                constants = obj.fields.get(index, Value.bottom()).exact_constants()
                if constants is None or len(constants) != 1:
                    break
                items.append(constants[0])
            else:
                shapes.append(items)
        if obj.kind == "dict":
            entries: list[JsonValue] = []
            for key, item in obj.fields.items():
                if key is None or isinstance(key, (str, int, bool, float)):
                    entries.append(
                        {"key": key, "value": _export_value(item, kinds, heap, seen | {ref})}
                    )
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
    if shapes and all(shape == shapes[0] for shape in shapes):
        result["items"] = shapes[0]
    return result


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
) -> PublishedSnapshot:
    """Freeze after warning/base-stat publication, retaining post-oracle changes."""
    values: JsonObject = {
        module: {
            name: _export_value(
                value, prepared.kinds, prepared.module_heaps.get(module, prepared.heaps)
            )
            for name, value in rows.items()
        }
        for module, rows in prepared.values.items()
    }
    returns: JsonObject = {}
    for name, value in prepared.returns.items():
        row = _export_value(
            value, prepared.kinds, prepared.procedure_heaps.get(name, prepared.heaps)
        )
        metadata = prepared.procedure_exits.get(name)
        if metadata is not None:
            row.update(metadata.to_json())
        returns[name] = row
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
    snapshot = EngineSnapshot(
        engine="hybrid" if runtime.oracle_statistics is not None else "ai_primary",
        solver_status=runtime.solver_status,
        coverage_status="open" if runtime.obligations else "closed_under_profile",
        profile=runtime.profile,
        values=values,
        module_exits=prepared.module_exits,
        returns=returns,
        outcomes=prepared.outcomes,
        calls=tuple(
            {"caller": caller, "callee": callee, "site": site}
            for caller, callee, site in sorted(prepared.calls)
        ),
        effects=effects,
        obligations=tuple({"code": code, "status": "open"} for code in sorted(runtime.obligations)),
        stats=stats,
        phase_trace=runtime.phase_trace,
    )
    return PublishedSnapshot(snapshot, stats)
