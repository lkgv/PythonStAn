"""Offline postprocess requests and honest aggregate status distinctions."""
import importlib.util
import json
from pathlib import Path

from benchmark.round12_projects import inspect_bundles as inspector
from benchmark.round8_corpus.registry import projects
from pythonstan_inspector.contract import QueryLimits
from pythonstan_inspector.storage import load_bundle


_spec = importlib.util.spec_from_file_location("round12_fixed_inspector_fixtures",
    Path(__file__).resolve().parents[1] / "inspector/fixtures.py")
_fixtures = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_fixtures)


def test_selected_point_builds_a_supported_explain_request():
    outcome = inspector.query_bundle(_fixtures.bundle())
    result = outcome["queries"]["explain"]
    assert outcome["selected_point"] is not None
    assert result["status"] == "returned"
    assert result["result"]["status"] == "ok"
    assert result["result"]["answer"] == "reconstructed_support"
    assert "view" not in result["result"]["parameters"]


def test_missing_point_is_not_reported_as_an_explanation():
    data = _fixtures.artifact()
    data["snapshot"]["views"]["node_points"]["rows"] = []
    outcome = inspector.query_bundle(_fixtures.bundle(data))
    assert outcome["selected_point"] is None
    assert outcome["queries"]["explain"] == {
        "status": "not_run", "reason": "no_point_selected_within_query_budget"}
    assert outcome["point_selector"]["status"] == "returned"


def test_point_selection_refusal_is_retained(monkeypatch):
    monkeypatch.setattr(inspector, "LIMITS", QueryLimits(max_visited=1))
    data = _fixtures.artifact()
    data["run"]["padding"] = "x" * 8192  # More than one admission block.
    outcome = inspector.query_bundle(_fixtures.bundle(data))
    assert outcome["point_selector"]["status"] == "refused"
    assert outcome["queries"]["explain"]["status"] == "not_run"
    assert outcome["queries"]["summary"]["status"] == "refused"


def test_transport_returns_do_not_hide_unsupported_responses(tmp_path, monkeypatch):
    records = projects()
    plan = {"plan_id": "b" * 64, "identity": {"arm": "observed", "engine_sha256": "c" * 64,
        "source_identity_sha256": {record["id"]: "a" * 64 for record in records}},
        "declared_projects": 14, "declared_runtime_python_files": 8482,
        "project_order": [record["id"] for record in records]}
    (tmp_path / "plan.json").write_text(json.dumps(plan))
    def row(directory, record, actual_plan):
        assert actual_plan == plan
        queries = {name: {"status": "returned", "result": {
            "status": "unsupported" if name in ("show", "explain") else "ok",
            "answer": "unknown" if name in ("show", "explain") else "bounded_observation"}}
            for name in inspector.OPERATIONS}
        queries["check"]["result"]["check_status"] = "indeterminate"
        return {"project_id": record["id"], "status": "queried", "queries": queries}
    monkeypatch.setattr(inspector, "_trial", row)
    result = inspector.inspect_cohort(tmp_path)
    assert result["declared_projects"] == 14 and result["declared_runtime_python_files"] == 8482
    assert result["query_outcomes"] == {"returned": 98}
    assert result["query_result_statuses"] == {"unsupported": 28, "ok": 70}
    assert result["query_answers"]["unknown"] == 28
    assert result["operation_outcomes"]["explain"]["response_status"] == {"unsupported": 14}
    assert result["named_check_outcomes"] == {"indeterminate": 14}


def test_actual_sealed_django_bundle_explain_path():
    root = Path(__file__).resolve().parents[2]
    bundle = load_bundle(root / "docs/inspector/examples/django")
    outcome = inspector.query_bundle(bundle)
    assert outcome["selected_point"] is not None
    assert outcome["queries"]["explain"]["status"] == "returned"
    assert outcome["queries"]["explain"]["result"]["answer"] == "reconstructed_support"
    assert outcome["queries"]["check"]["result"]["check_status"] == "passed"
