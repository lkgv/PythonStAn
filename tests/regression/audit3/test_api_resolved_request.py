"""The actual public request path retains one checked representation."""

from __future__ import annotations

import json
import sys
import time
from collections.abc import Mapping
from pathlib import Path

import pytest

from pythonstan import File, Project, Source, analyze
from pythonstan.analysis.contracts.json_data import JsonValue, require_object, validate_json
from pythonstan.config import ConfigurationError, resolve_config
from pythonstan.inputs import InputError, snapshot_target
from pythonstan.request import resolve_request
from pythonstan.results.output import OutputSpec
from pythonstan.results.queries import QuerySpec, ValueQuery, parse_query
from pythonstan.session import AnalysisSession
from tests.regression.audit3.api_consumer_typeguard import check_consumers


def test_mapping_output_and_object_select_the_same_retained_answer() -> None:
    query = ValueQuery("subject", "x")
    source = Source("x = 7", module="subject")
    mapped = analyze(
        source, config={"engine": "ai_primary"}, queries=[query], output={"views": ["values"]}
    )
    recorded = analyze(
        source,
        config={"engine": "ai_primary"},
        queries=[query],
        output=OutputSpec(views=("values",)),
    )
    assert mapped.to_dict()["answers"] == recorded.to_dict()["answers"]
    assert mapped.output == recorded.output


@pytest.mark.parametrize(
    "output",
    [{"unexpected": True}, {"views": "values"}, {"max_items": True}, {"format": "xml"}],
)
def test_invalid_output_is_a_configuration_error(output: Mapping[str, object]) -> None:
    with pytest.raises(ConfigurationError):
        analyze(Source("x=1"), output=output)


@pytest.mark.parametrize(
    "query",
    [
        {"kind": "value", "module": "subject", "symbol": "x", "unexpected": True},
        {"kind": "return", "module": "subject", "symbol": "f", "unexpected": True},
        {"kind": "value", "module": "subject"},
        {"kind": "property", "property": "guarded_dispatch_return_relation", "unexpected": True},
        {"kind": "calls", "unexpected": True},
    ],
)
def test_all_query_variants_reject_invalid_fields(query: Mapping[str, object]) -> None:
    with pytest.raises(ConfigurationError):
        analyze(Source("x=1", module="subject"), queries=[query])


def test_resolved_request_owns_immutable_output_and_queries() -> None:
    views = ["values"]
    query: dict[str, object] = {"kind": "value", "module": "subject", "symbol": "x"}
    request = resolve_request(
        Source("x=1", module="subject"), output={"views": views}, queries=[query]
    )
    views.append("calls")
    query["symbol"] = "changed"
    assert request.output.views == ("values",)
    assert request.queries == (ValueQuery("subject", "x"),)
    assert request.queries is request.config.queries
    assert request.output is request.config.output


def test_session_construction_does_not_prepare_sources(monkeypatch: pytest.MonkeyPatch) -> None:
    import pythonstan.session_preparation as session_module

    request = resolve_request(Source("x=1"), config={"engine": "ai_primary"})
    calls: list[str] = []

    def forbidden_prepare(*args: object, **kwargs: object) -> None:
        calls.append("snapshot")
        raise AssertionError("source preparation happened during construction")

    monkeypatch.setattr(session_module, "snapshot_target", forbidden_prepare)
    with AnalysisSession(request):
        assert calls == []


def test_session_rejects_unresolved_request_at_runtime() -> None:
    with pytest.raises((TypeError, ConfigurationError)):
        # Deliberate dynamic misuse for the API01 runtime-negative guard.
        AnalysisSession({"output": {"views": ["values"]}})  # type: ignore[arg-type]


def test_serialization_does_not_renormalize_resolved_query(monkeypatch: pytest.MonkeyPatch) -> None:
    import pythonstan.results.queries as queries_module

    calls: list[object] = []

    def counted(raw: object) -> QuerySpec:
        calls.append(raw)
        return parse_query(raw)

    monkeypatch.setattr(queries_module, "parse_query", counted)
    result = analyze(
        Source("x=1", module="subject"),
        config={"engine": "ai_primary"},
        queries=[ValueQuery("subject", "x")],
    )
    assert len(calls) == 1
    result.serialize()
    assert len(calls) == 1


def test_results_remain_detached_after_second_run_and_session_close() -> None:
    request = resolve_request(
        Source("x=1", module="subject"), config={"engine": "ai_primary"}, queries=["values"]
    )
    with AnalysisSession(request) as session:
        first = session.execute()
    before = first.raw_snapshot()
    analyze(Source("x=2", module="subject"), config={"engine": "ai_primary"}, queries=["values"])
    assert first.raw_snapshot() == before
    assert first.query(ValueQuery("subject", "x")).status == "answered"


def test_json_boundary_rejects_cycle_but_accepts_shared_acyclic_inputs() -> None:
    cyclic: list[object] = []
    cyclic.append(cyclic)
    with pytest.raises(ValueError, match="cycle"):
        validate_json(cyclic)
    shared = [1]
    assert validate_json([shared, shared]) == [[1], [1]]


def test_json_boundary_reports_excessive_nesting_without_recursion_error() -> None:
    nested: JsonValue = 1
    for _ in range(130):
        nested = [nested]
    with pytest.raises(ValueError, match="nesting depth"):
        validate_json(nested)


def test_memory_source_never_inherits_current_directory_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "pythonstan.json").write_text('{"unknown": true}')
    monkeypatch.chdir(tmp_path)
    request = resolve_request(Source("x=1"), config=None)
    assert request.config.config_file is None


def test_actual_session_signature_rejects_raw_request_with_normal_imports(tmp_path: Path) -> None:
    repository = Path(__file__).resolve().parents[3]
    results = check_consumers(repository=repository, workspace=tmp_path, python=sys.executable)
    assert results["positive"]["status"] == "passed"
    assert results["positive"]["consumer_diagnostics"] == 0
    assert results["negative"]["status"] == "passed"
    assert results["negative"]["consumer_diagnostics"] == 1


def test_unscoped_event_projection_retains_events_with_optional_source_points() -> None:
    result = analyze(
        Source("x=1"),
        config={"engine": "ai_primary"},
        queries=["events"],
        output={"views": ["events"]},
    )
    before = result.raw_snapshot()
    retained = result.query("events").value
    assert isinstance(retained, dict)
    events = retained["events"]
    assert isinstance(events, list)
    assert any(isinstance(event, dict) and event["point"] is None for event in events)
    serialized: object = json.loads(result.serialize())
    assert isinstance(serialized, dict)
    assert serialized["answers"]["events"]["value"]["events"] == events
    assert result.raw_snapshot() == before


def test_frozen_rerun_rejects_changed_memory_source() -> None:
    source = Source("x=1", module="subject")
    snapshot = snapshot_target(source, resolve_config(source, {"engine": "ai_primary"}))
    changed = resolve_request(Source("x=2", module="subject"), config={"engine": "ai_primary"})
    with pytest.raises(InputError, match="Memory source"):
        AnalysisSession.from_snapshot(changed, snapshot)


def test_frozen_rerun_rejects_changed_declared_entry() -> None:
    source = Source("def f():\n return 1\ndef g():\n return 2\n", module="subject")
    initial = resolve_config(source, {"engine": "ai_primary", "inputs": {"entries": ["subject:f"]}})
    snapshot = snapshot_target(source, initial)
    changed = resolve_request(
        source, config={"engine": "ai_primary", "inputs": {"entries": ["subject:g"]}}
    )
    with pytest.raises(InputError, match="Requested entries"):
        AnalysisSession.from_snapshot(changed, snapshot)


@pytest.mark.parametrize("selector", ["source_roots", "exclusions"])
def test_frozen_rerun_rejects_changed_inventory_selectors(selector: str, tmp_path: Path) -> None:
    source = Source("x=1", module="subject")
    snapshot = snapshot_target(source, resolve_config(source, {"engine": "ai_primary"}))
    value = [str(tmp_path)] if selector == "source_roots" else ["omitted.py"]
    changed = resolve_request(source, config={"engine": "ai_primary", "inputs": {selector: value}})
    with pytest.raises(InputError, match="changed source roots or exclusions"):
        AnalysisSession.from_snapshot(changed, snapshot)


def test_frozen_rerun_uses_retained_file_bytes_after_file_deletion(tmp_path: Path) -> None:
    path = tmp_path / "subject.py"
    path.write_text("x=9")
    target = File(path)
    initial = resolve_config(target, {"engine": "ai_primary"})
    snapshot = snapshot_target(target, initial)
    path.unlink()
    request = resolve_request(target, config=initial, queries=["values"])
    with AnalysisSession.from_snapshot(request, snapshot) as session:
        result = session.execute()
    assert require_object(result.query(ValueQuery("subject", "x")).value)["constants"] == [9]
    assert result.raw_snapshot()["source_digest"] == snapshot.source_digest


def test_frozen_rerun_accepts_explicit_new_abstract_global_binding() -> None:
    source = Source("result=key", module="subject")
    initial = resolve_config(
        source, {"engine": "ai_primary", "inputs": {"bindings": {"subject.key": 1}}}
    )
    snapshot = snapshot_target(source, initial)
    request = resolve_request(
        source,
        config={"engine": "ai_primary", "inputs": {"bindings": {"subject.key": 2}}},
        queries=["values"],
    )
    with AnalysisSession.from_snapshot(request, snapshot) as session:
        result = session.execute()
    assert require_object(result.query(ValueQuery("subject", "result")).value)["constants"] == [2]


def test_frozen_rerun_rejects_changed_explicit_file_module(tmp_path: Path) -> None:
    path = tmp_path / "source.py"
    path.write_text("x=1")
    target = File(path, module="subject")
    snapshot = snapshot_target(target, resolve_config(target, {"engine": "ai_primary"}))
    changed = resolve_request(File(path, module="different"), config={"engine": "ai_primary"})
    with pytest.raises(InputError, match="File module"):
        AnalysisSession.from_snapshot(changed, snapshot)


@pytest.mark.parametrize("new_module", [None, ""])
def test_frozen_rerun_rejects_removing_explicit_file_module(
    tmp_path: Path,
    new_module: str | None,
) -> None:
    path = tmp_path / "source.py"
    path.write_text("x=4")
    target = File(path, module="renamed_subject")
    initial = resolve_config(target, {"engine": "ai_primary"})
    snapshot = snapshot_target(target, initial)
    changed = resolve_request(File(path, module=new_module), config=initial)
    with pytest.raises(InputError, match="File module override"):
        AnalysisSession.from_snapshot(changed, snapshot)


def test_frozen_rerun_preserves_unchanged_explicit_file_module(tmp_path: Path) -> None:
    path = tmp_path / "source.py"
    path.write_text("x=4")
    target = File(path, module="renamed_subject")
    initial = resolve_config(target, {"engine": "ai_primary"})
    snapshot = snapshot_target(target, initial)
    request = resolve_request(target, config=initial, queries=["values"])
    with AnalysisSession.from_snapshot(request, snapshot) as session:
        result = session.execute()
    assert require_object(result.query(ValueQuery("renamed_subject", "x")).value)["constants"] == [
        4
    ]


def test_frozen_project_rerun_preserves_all_default_initialization_entries(tmp_path: Path) -> None:
    (tmp_path / "one.py").write_text("x=1")
    (tmp_path / "two.py").write_text("x=2")
    target = Project(tmp_path)
    initial = resolve_config(target, {"engine": "ai_primary"})
    snapshot = snapshot_target(target, initial)
    request = resolve_request(target, config=initial, queries=["values"])
    with AnalysisSession.from_snapshot(request, snapshot) as session:
        result = session.execute()
    assert [(scenario.root_module, scenario.entry_qualname) for scenario in snapshot.scenarios] == [
        ("one", None),
        ("two", None),
    ]
    assert require_object(result.query(ValueQuery("one", "x")).value)["constants"] == [1]
    assert require_object(result.query(ValueQuery("two", "x")).value)["constants"] == [2]


def test_phase_durations_exclude_explicit_idle_but_deadline_elapsed_retains_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock: list[float] = [100.0]

    def tick() -> float:
        value = clock[0]
        clock[0] += 1.0
        return value

    monkeypatch.setattr(time, "monotonic", tick)
    request = resolve_request(
        Source("x=1"), config={"engine": "ai_primary", "resources": {"max_seconds": 0}}
    )
    with AnalysisSession(request) as session:
        prepared = session.prepare()
        measured_preparation = prepared.preparation_seconds
        clock[0] += 1000.0
        result = session.execute()
    stats = require_object(result.raw_snapshot()["stats"])
    assert request.resolution_seconds == 1.0
    assert stats["configuration_resolution_seconds"] == request.resolution_seconds
    assert stats["source_preparation_seconds"] == measured_preparation == 1.0
    assert isinstance(stats["engine_execution_seconds"], (int, float))
    assert stats["engine_execution_seconds"] > 0
    assert isinstance(stats["total_wall_seconds"], (int, float))
    assert stats["total_wall_seconds"] >= 1000.0
