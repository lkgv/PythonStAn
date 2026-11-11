from benchmark.round12_projects.run import summarize_trial, timing_fields


def test_observed_supervisor_is_parent_clock_not_recovered_native_analysis():
    row = {"project_id": "example", "arm": "observed", "declared_python_files": 1,
        "orchestration_wall_seconds": 61.5,
        "result": {"status": "timeout", "supervisor_wall_seconds": 60.7,
            "native_report": {"status": "incomplete", "wall_seconds": 49.7}}}
    clocks = timing_fields(row)
    assert clocks == {"wall_seconds_native_analysis": 49.7,
        "wall_seconds_project_supervisor": 60.7, "wall_seconds_outer_orchestration": 61.5}
    assert summarize_trial(row)["wall_seconds_native_supervisor"] == 60.7
    assert summarize_trial(row)["status"] == "timeout"


def test_missing_observed_supervisor_never_falls_back_to_native_analysis():
    row = {"project_id": "example", "arm": "observed", "declared_python_files": 1,
        "result": {"status": "worker_error", "native_report": {"wall_seconds": 12.0}}}
    assert timing_fields(row)["wall_seconds_project_supervisor"] is None
    assert timing_fields(row)["wall_seconds_native_analysis"] == 12.0


def test_off_timeout_wall_is_supervisor_only_not_analysis():
    row = {"arm": "candidate_off", "result": {"status": "timeout", "wall_seconds": 60.1}}
    assert timing_fields(row)["wall_seconds_project_supervisor"] == 60.1
    assert timing_fields(row)["wall_seconds_native_analysis"] is None
