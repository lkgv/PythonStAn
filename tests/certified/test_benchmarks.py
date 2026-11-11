"""Independent frozen expectations and explicit coverage accounting."""

import copy
import hashlib
import json

import pytest

from benchmark.certified.acquire_web_sources import verify_sources
from benchmark.certified.runner import ROOT, run_suite
from pythonstan.analysis.pointer.ai.certified import check_source, parse_candidate


def manifest():
    return json.loads((ROOT / "manifest.json").read_text())


def test_frozen_inputs_and_required_feature_families():
    cases = manifest()["cases"]
    assert len({case["id"] for case in cases}) == len(cases)
    required = {
        "prefix_divergence", "prefix_recursive_divergence", "mutual_recursive_divergence",
        "exception_finally", "recursive_closure", "alias_mutation", "descriptor_hook",
        "getattr_hook", "metaclass_hook", "eval_exec", "import_hook", "callback_mutation",
        "list_read", "iterator_identity", "await_suspend", "yield_cleanup", "truthiness_hook",
    }
    assert required <= {case["family"] for case in cases}
    for case in cases:
        assert hashlib.sha256((ROOT / case["source"]).read_bytes()).hexdigest() == case["sha256"]
        assert case["reviewed_obligations"]
        if case.get("candidate"):
            assert hashlib.sha256((ROOT / case["candidate"]).read_bytes()).hexdigest() == case["candidate_sha256"]


@pytest.mark.parametrize("mode", ["baseline", "candidate"])
def test_all_manifest_rows_accounted_and_source_obligations_hold(mode):
    report = run_suite(mode, include_web=True)
    assert report["totals"]["manifest_rows"] == len(manifest()["cases"]) + 8
    failures = [row for row in report["rows"] if not row["expectation_met"]]
    assert not failures, failures
    assert report["totals"]["supported_cases"] == 15
    assert report["totals"]["supported_obligations_discharged"] == 15
    assert report["totals"]["required_event_obligations"] == 37
    assert report["totals"]["undischarged_event_obligations"] == 0
    assert report["totals"]["missing_required_events"] == 0
    assert report["totals"]["unsupported_cases"] == 21
    assert report["totals"]["unsupported_obligations_discharged"] == 0
    assert report["totals"]["whole_app_cases_analyzed"] == 0
    assert report["totals"]["guard_cases_exercised"] == 0
    assert all(row["diagnostics"] for row in report["rows"] if row["status"] == "unsupported")
    if mode == "candidate":
        assert all(row["candidate_attempted"] for row in report["rows"])


def test_real_framework_extractions_are_pinned_to_original_source():
    records = verify_sources()
    assert len(records) == 8
    assert {record["project"] for record in records} == {"django", "fastapi", "starlette", "flask"}
    assert all(len(record["revision"]) == 40 for record in records)
    assert all(record["end_line"] > record["start_line"] for record in records)


def test_omitting_any_reviewed_event_from_frozen_candidates_is_rejected():
    checked = 0
    for case in manifest()["cases"]:
        if not case.get("candidate"):
            continue
        source = (ROOT / case["source"]).read_text()
        candidate = json.loads((ROOT / case["candidate"]).read_text())
        for function, summary in candidate["summaries"].items():
            for event in summary["events"]:
                changed = copy.deepcopy(candidate)
                changed["summaries"][function]["events"].remove(event)
                result = check_source(source, parse_candidate(changed))
                assert not result.certified, (case["id"], function, event)
                assert result.status == "rejected"
                checked += 1
    assert checked >= 15


def test_replay_certificate_is_bound_to_exact_source():
    case = next(case for case in manifest()["cases"] if case["id"] == "prefix_loop")
    source = (ROOT / case["source"]).read_text()
    candidate = parse_candidate(json.loads((ROOT / case["candidate"]).read_text()))
    result = check_source(source + "\n# source changed\n", candidate)
    assert result.status == "rejected"
    assert not result.certified


def test_resource_exhaustion_is_not_counted_as_success():
    report = run_suite("candidate", include_web=False, max_iterations=0)
    assert report["totals"]["expectation_failures"] > 0
    assert all(row["status"] != "accepted" for row in report["rows"])
    assert report["totals"]["supported_obligations_discharged"] == 0
