"""A sealed native callback cannot complete a failed OBS process budget."""
import copy

import pytest

from benchmark.round12_projects.run import summarize_trial


def _observed(status):
    return {"project_id": "example", "arm": "observed", "declared_python_files": 1,
        "implementation_stable": True, "source_after_verified": True,
        "result": {"status": status, "capture_state": {"status": "sealed"},
            "bundle": "bounded-snapshot", "native_report_origin": "sealed_callback_before_worker_error",
            "native_report": {"status": "completed_with_residuals", "solver_completed": True,
                "semantic_complete": False, "first_party_translation_complete": True,
                "frontend_errors": [], "unresolved_entries": [], "implementation_stable": True,
                "source_snapshot_stable": True,
                "coverage": {"whole_declared_first_party_source_lowered": True}}}}


@pytest.mark.parametrize("process_status", ["timeout", "worker_error"])
def test_recovered_native_report_does_not_promote_failed_observed_process(process_status):
    row = _observed(process_status)
    before = copy.deepcopy(row)
    outcome = summarize_trial(row)
    assert outcome["status"] == process_status
    assert outcome["strict_module_solver_report_completion"] is False
    assert outcome["solver_completed"] is False
    assert outcome["semantic_complete"] is False
    assert outcome["native_report_status"] == "completed_with_residuals"
    assert outcome["capture_state"]["status"] == "sealed"
    assert outcome["bundle"] == "bounded-snapshot"
    assert row == before  # Preserve the valid local native facts as evidence.


def test_successful_observed_process_can_satisfy_only_the_narrow_native_gate():
    row = _observed("worker_finished")
    outcome = summarize_trial(row)
    assert outcome["status"] == "completed_with_residuals"
    assert outcome["solver_completed"] is True
    assert outcome["strict_module_solver_report_completion"] is True
    assert outcome["semantic_complete"] is False
