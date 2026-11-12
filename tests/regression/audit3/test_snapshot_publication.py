"""Publication controls using genuine solved states and completion variants."""

from __future__ import annotations

from collections.abc import Mapping, MutableMapping
from types import MappingProxyType

from pythonstan.analysis.contracts.identity import (
    CellId,
    ContextId,
    ObjectId,
    ProgramPointId,
    SourceModule,
    StateRevision,
    UnitId,
)
from pythonstan.analysis.contracts.json_data import (
    JsonObject,
    JsonValue,
    require_array,
    require_object,
    validate_json_object,
)
from pythonstan.analysis.contracts.oracle import CandidateAnswer
from pythonstan.analysis.domains.values import Value, ValueOperations
from pythonstan.analysis.engines.abstract.driver import InterproceduralDriver
from pythonstan.analysis.engines.abstract.publication import (
    AccessFact,
    CallFact,
    FunctionPublication,
    ModuleValueRequest,
    PrefixEvent,
    PublicationInput,
    PublicationRuntime,
    PublicationUnit,
    finalize_publication,
    module_value_requests,
    prepare_publication,
    publication_statistics,
)
from pythonstan.analysis.state.flow import (
    BreakFlow,
    ContinueFlow,
    ExceptionValue,
    Flow,
    NormalExprFlow,
    NormalStmtFlow,
    RaiseFlow,
    ReturnFlow,
    SuspendFlow,
)
from pythonstan.analysis.state.handles import StateHandle
from pythonstan.analysis.state.store import HeapObject, Store
from pythonstan.results.output import OutputSpec
from pythonstan.results.projection import project_payload


def _unit(name: str, flows: tuple[Flow, ...], *, is_module: bool = False) -> PublicationUnit:
    return PublicationUnit(
        UnitId(name),
        "subject",
        name,
        ContextId((ProgramPointId("call:17"),)),
        3,
        1,
        is_module,
        "scenario:main" if is_module else None,
        flows,
    )


def _source(
    units: tuple[PublicationUnit, ...],
    *,
    functions: Mapping[ObjectId, FunctionPublication] | None = None,
    queries: tuple[ModuleValueRequest, ...] = (),
    roots: tuple[UnitId, ...] = (),
    calls: tuple[CallFact, ...] = (),
    reads: tuple[AccessFact, ...] = (),
    writes: tuple[AccessFact, ...] = (),
    events: tuple[PrefixEvent, ...] = (),
    exceptions: tuple[str, ...] = (),
) -> PublicationInput:
    return PublicationInput(
        units,
        {} if functions is None else functions,
        queries,
        roots,
        calls,
        reads,
        writes,
        events,
        exceptions,
    )


def _runtime(
    *,
    stats: JsonObject | None = None,
    obligations: frozenset[str] = frozenset(),
    oracle_statistics: JsonObject | None = None,
) -> PublicationRuntime:
    return PublicationRuntime(
        work=11,
        elapsed=0.25,
        pending_units=2,
        stats={} if stats is None else stats,
        obligations=obligations,
        phase_trace=({"phase": "solve", "work": 11},),
        profile="python-core-v1",
        solver_status="converged",
        oracle_statistics=oracle_statistics,
    )


def _publish(source: PublicationInput, runtime: PublicationRuntime | None = None) -> JsonObject:
    prepared = prepare_publication(source, ValueOperations())
    actual_runtime = _runtime() if runtime is None else runtime
    published = finalize_publication(
        prepared,
        actual_runtime,
        published_statistics=publication_statistics(prepared, actual_runtime),
    )
    return validate_json_object(published.snapshot.to_dict())


def test_all_completions_preserve_absent_bottom_and_python_none() -> None:
    state = StateHandle.capture(Store())
    flows: tuple[Flow, ...] = (
        NormalExprFlow(state, Value.bottom()),
        NormalStmtFlow(state),
        ReturnFlow(state, Value.constant(None)),
        RaiseFlow(state, ExceptionValue(Value.constant("bad input"), "ValueError")),
        BreakFlow(state),
        ContinueFlow(state),
        SuspendFlow(state, Value.constant("yielded")),
    )
    result = _publish(_source((_unit("subject.f", flows),)))
    rows = require_array(require_object(result["outcomes"])["subject.f"])
    expression, statement, returned, raised, broken, continued, suspended = map(
        require_object, rows
    )
    assert expression["value_present"] is True
    assert require_object(expression["value"])["bottom"] is True
    for absent in (statement, broken, continued):
        assert absent["value_present"] is False and absent["value"] is None
    assert returned["value_present"] is True
    assert require_object(returned["value"])["constants"] == [None]
    assert raised["exception"] == "ValueError"
    assert require_object(raised["value"])["constants"] == ["bad input"]
    assert require_object(suspended["value"])["constants"] == ["yielded"]
    assert all(require_object(row)["context"] == ["call:17"] for row in rows)
    assert all(require_object(row)["revision"] == 3 for row in rows)
    summary = require_object(require_object(result["returns"])["subject.f"])
    assert summary["constants"] == [None] and summary["normal_exit"] is True
    assert summary["exceptions"] == ["ValueError"] and summary["suspends"] is True


def test_module_and_procedure_heap_views_do_not_mix_exit_contents() -> None:
    identity = ObjectId("shared-object")
    module_store = Store(
        cells={CellId("global:subject:box"): Value.ref(identity)},
        heap={identity: HeapObject("dict", {"value": Value.constant("module")})},
    )
    procedure_store = Store(
        heap={identity: HeapObject("dict", {"value": Value.constant("procedure")})}
    )
    result = _publish(
        _source(
            (
                _unit(
                    "subject", (NormalStmtFlow(StateHandle.capture(module_store)),), is_module=True
                ),
                _unit(
                    "subject.f",
                    (ReturnFlow(StateHandle.capture(procedure_store), Value.ref(identity)),),
                ),
            )
        )
    )

    def field_constants(value: JsonValue) -> JsonValue:
        slot = require_object(require_array(require_object(value)["slots"])[0])
        entry = require_object(require_array(slot["entries"])[0])
        return require_object(entry["value"])["constants"]

    assert field_constants(require_object(require_object(result["values"])["subject"])["box"]) == [
        "module"
    ]
    assert field_constants(require_object(result["returns"])["subject.f"]) == ["procedure"]
    assert module_store.heap[identity].fields["value"].exact_constants() == ["module"]
    assert procedure_store.heap[identity].fields["value"].exact_constants() == ["procedure"]


def test_cyclic_dict_retains_reference_and_terminates() -> None:
    identity = ObjectId("cycle")
    store = Store(
        cells={CellId("global:subject:box"): Value.ref(identity)},
        heap={identity: HeapObject("dict", {"self": Value.ref(identity)})},
    )
    result = _publish(
        _source((_unit("subject", (NormalStmtFlow(StateHandle.capture(store)),), is_module=True),))
    )
    box = require_object(require_object(require_object(result["values"])["subject"])["box"])
    assert box["objects"] == ["cycle"]
    slot = require_object(require_array(box["slots"])[0])
    child = require_object(require_array(slot["entries"])[0])
    assert child["key"] == "self"
    child_value = require_object(child["value"])
    assert child_value["objects"] == ["cycle"] and child_value["types"] == ["dict"]
    assert "slots" not in child_value


def test_exact_sequence_shape_requires_bottom_default_and_no_havoc() -> None:
    exact, defaulted, havoc = map(ObjectId, ("exact", "defaulted", "havoc"))
    store = Store(
        cells={
            CellId(f"global:subject:{identity}"): Value.ref(identity)
            for identity in (exact, defaulted, havoc)
        },
        heap={
            exact: HeapObject("list", {0: Value.constant(7)}, metadata={"length": 1}),
            defaulted: HeapObject(
                "list", {0: Value.constant(7)}, default=Value.unknown(), metadata={"length": 1}
            ),
            havoc: HeapObject("list", {0: Value.constant(7)}, metadata={"length": 1}, havoc=True),
        },
    )
    result = _publish(
        _source((_unit("subject", (NormalStmtFlow(StateHandle.capture(store)),), is_module=True),))
    )
    values = require_object(require_object(result["values"])["subject"])
    assert require_object(values["exact"])["items"] == [7]
    assert "items" not in require_object(values["defaulted"])
    assert "items" not in require_object(values["havoc"])


def test_only_explicit_module_exit_queries_materialize_havoc_cells() -> None:
    store = Store(havoc_namespaces=frozenset({"subject"}))
    queries: JsonValue = [
        {"kind": "value", "module": "subject", "symbol": "requested"},
        {"kind": "value", "module": "subject", "symbol": "other_point", "point": "entry"},
        {"kind": "return", "module": "subject", "symbol": "f"},
        "values",
    ]
    result = _publish(
        _source(
            (_unit("subject", (NormalStmtFlow(StateHandle.capture(store)),), is_module=True),),
            queries=module_value_requests(queries),
        )
    )
    values = require_object(require_object(result["values"])["subject"])
    assert set(values) == {"requested"}
    requested = require_object(values["requested"])
    assert requested["top"] is True and requested["may_unbound"] is True
    assert requested["bottom"] is False and not store.cells


def test_suspended_creation_metadata_and_ordered_warning_multiplicity() -> None:
    function = ObjectId("function:known")
    store = Store(
        heap={
            ObjectId("known"): HeapObject("generator", metadata={"function": function}),
            ObjectId("missing-one"): HeapObject("generator"),
            ObjectId("missing-two"): HeapObject("coroutine"),
        }
    )
    source = _source(
        (_unit("subject", (NormalStmtFlow(StateHandle.capture(store)),), is_module=True),),
        functions={function: FunctionPublication("subject.lazy", True)},
    )
    prepared = prepare_publication(source, ValueOperations())
    assert prepared.open_obligations == ("unrepresented_suspended_function",) * 2
    runtime = _runtime(
        stats={"observation_events": 2}, obligations=frozenset(prepared.open_obligations)
    )
    result = validate_json_object(
        finalize_publication(
            prepared,
            runtime,
            published_statistics=publication_statistics(prepared, runtime),
        ).snapshot.to_dict()
    )
    lazy = require_object(require_object(result["returns"])["subject.lazy"])
    assert lazy["bottom"] is True and lazy["normal_exit"] is False
    assert lazy["suspends"] is True and lazy["body_executed_at_creation"] is False
    assert require_object(result["outcomes"])["subject.lazy"] == []
    assert result["coverage_status"] == "open"
    assert result["obligations"] == [{"code": "unrepresented_suspended_function", "status": "open"}]
    assert require_object(result["stats"])["observation_events"] == 2
    assert not store.obligations


def test_calls_effects_root_multiplicity_and_resource_statistics() -> None:
    module = _unit("subject", (NormalStmtFlow(StateHandle.capture(Store())),), is_module=True)
    first, second = ProgramPointId("source:3"), ProgramPointId("source:9")
    source = _source(
        (module,),
        roots=(module.key, module.key, UnitId("missing")),
        calls=(("caller", "z", second), ("caller", "a", first)),
        reads=(("cell", "global:subject:x", first),),
        writes=(("unknown", "whole-heap", second), ("cell", "global:subject:x", first)),
        events=(("write", "before-raise"), ("raise", "ValueError")),
        exceptions=("ValueError",),
    )
    stats: JsonObject = {"pending_units": 5, "unit_evaluations": 1}
    result = _publish(source, _runtime(stats=stats, oracle_statistics={"work": 17}))
    assert result["engine"] == "hybrid"
    assert result["calls"] == [
        {"caller": "caller", "callee": "a", "site": "source:3"},
        {"caller": "caller", "callee": "z", "site": "source:9"},
    ]
    effects = require_object(result["effects"])
    assert effects["calls"] == ["a", "z"]
    assert effects["reads"] == [["cell", "global:subject:x", "source:3"]]
    assert effects["prefix_events"] == [["write", "before-raise"], ["raise", "ValueError"]]
    assert effects["havoc"] == [["unknown", "whole-heap", "source:9"]]
    assert effects["exceptions"] == ["ValueError"]
    published_stats = require_object(result["stats"])
    assert published_stats["work"] == published_stats["total_work"] == 11
    assert published_stats["root_scenarios"] == 3 and published_stats["initialized_roots"] == 2
    assert published_stats["pending_units"] == 5 and published_stats["pta_work"] == 17
    assert published_stats["elapsed_seconds"] == 0.25
    assert result["phase_trace"] == [{"phase": "solve", "work": 11}]
    assert stats == {"pending_units": 5, "unit_evaluations": 1}


def test_physical_store_revision_and_owner_are_not_wire_identity() -> None:
    left = Store(cells={CellId("global:subject:x"): Value.constant(1)})
    right = Store(
        cells={CellId("global:subject:x"): Value.constant(1)}, state_revision=StateRevision(999)
    )
    first = _publish(
        _source((_unit("subject", (NormalStmtFlow(StateHandle.capture(left)),), is_module=True),))
    )
    second = _publish(
        _source((_unit("subject", (NormalStmtFlow(StateHandle.capture(right)),), is_module=True),))
    )
    assert first == second


def test_outcome_field_projection_preserves_absence_versus_returned_none() -> None:
    state = StateHandle.capture(Store())
    result = _publish(
        _source(
            (
                _unit("subject", (NormalStmtFlow(state),), is_module=True),
                _unit("subject.f", (ReturnFlow(state, Value.constant(None)),)),
            )
        )
    )
    retained = validate_json_object(result)
    projection = project_payload(
        "outcomes", result["outcomes"], OutputSpec(fields=("value",)), result
    )
    selected = require_object(projection.value)
    statement = require_object(require_array(selected["subject"])[0])
    returned = require_object(require_array(selected["subject.f"])[0])
    assert statement["value_present"] is False and statement["value"] is None
    assert returned["value_present"] is True
    assert require_object(returned["value"])["constants"] == [None]
    assert result == retained


class _StatsReadingOracle:
    """A supported candidate-only oracle with a read-only view of actual stats."""

    def __init__(self, statistics: Mapping[str, JsonValue]) -> None:
        self._statistics = statistics

    def candidates(self, cell: CellId, consumer: UnitId | None = None) -> CandidateAnswer[Value]:
        return CandidateAnswer(Value.bottom(), "partial", 0, ("publication-control",), "fixture")

    def statistics(self) -> JsonObject:
        return {
            "work": 0,
            "observed_host_work": self._statistics.get("work", -1),
            "observed_host_units": self._statistics.get("units", -1),
            "observed_host_pta_work": self._statistics.get("pta_work", -1),
        }


class _StatsMutatingOracle:
    """Check that finalization preserves changes made by a real provider call."""

    def __init__(self, statistics: MutableMapping[str, JsonValue]) -> None:
        self._statistics = statistics

    def candidates(self, cell: CellId, consumer: UnitId | None = None) -> CandidateAnswer[Value]:
        return CandidateAnswer(Value.bottom(), "partial", 0, ("publication-control",), "fixture")

    def statistics(self) -> JsonObject:
        observed_work = self._statistics.get("work", -1)
        self._statistics["work"] = 41
        self._statistics["pending_units"] = 17
        self._statistics["provider_note"] = "retained"
        return {"work": 3, "observed_host_work": observed_work}


def test_actual_hybrid_oracle_observes_published_base_statistics() -> None:
    driver = InterproceduralDriver((SourceModule("subject", "subject.py", "x=1\n"),))
    driver.oracle = _StatsReadingOracle(MappingProxyType(driver.stats))
    result = validate_json_object(driver.run().to_dict())
    stats = require_object(result["stats"])
    observed = require_object(stats["oracle"])
    assert result["engine"] == "hybrid"
    assert observed["observed_host_work"] == stats["work"] == driver.work
    assert observed["observed_host_units"] == stats["units"] == len(driver.units)
    assert observed["observed_host_pta_work"] == 0
    assert stats["work"] != -1 and stats["units"] == 1


def test_actual_hybrid_finalization_preserves_provider_statistics_mutations() -> None:
    driver = InterproceduralDriver((SourceModule("subject", "subject.py", "x=1\n"),))
    original_statistics = driver.stats
    driver.oracle = _StatsMutatingOracle(original_statistics)
    result = validate_json_object(driver.run().to_dict())
    stats = require_object(result["stats"])
    assert require_object(stats["oracle"])["observed_host_work"] == driver.work
    assert stats["work"] == 41
    assert stats["pending_units"] == 17
    assert stats["provider_note"] == "retained"
    assert stats["pta_work"] == 3
    assert driver.stats is original_statistics
    assert driver.stats["work"] == 41


def test_actual_driver_records_each_missing_suspended_warning_before_freezing() -> None:
    driver = InterproceduralDriver((SourceModule("subject", "subject.py", "x=1\n"),))
    driver.run()
    unit = next(unit for unit in driver.units.values() if unit.is_module)
    store = Store(
        heap={
            ObjectId("missing-one"): HeapObject("generator"),
            ObjectId("missing-two"): HeapObject("coroutine"),
        }
    )
    unit.outcomes = [NormalStmtFlow(StateHandle.capture(store))]
    before_events = driver.stats.get("observation_events", 0)
    assert isinstance(before_events, int)
    before_work = driver.work
    before_phases = validate_json_object({"phases": driver.phase_trace})
    result = validate_json_object(driver.snapshot(0.25).to_dict())
    assert require_object(result["stats"])["observation_events"] == before_events + 2
    assert result["obligations"] == [{"code": "unrepresented_suspended_function", "status": "open"}]
    assert result["coverage_status"] == "open"
    assert driver.work == before_work
    assert result["phase_trace"] == before_phases["phases"]
    assert driver.stats["work"] == before_work
