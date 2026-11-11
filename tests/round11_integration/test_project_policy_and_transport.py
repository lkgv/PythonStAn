"""Project policy plumbing, preserved evidence and worker transport boundaries."""
from dataclasses import asdict
import json

import pytest

from benchmark.round8_corpus import collect_results
from pythonstan.analysis.project import ProjectConfig
from scripts.analyze_project import run_isolated
from tests.round10_empirical.test_round10_historical_schema_and_progress import (
    historical_langserve, mutate_historical,
)


@pytest.mark.parametrize("value", [None, True, 1, "", "fifo", [], {}])
def test_project_rejects_unknown_scheduling_policy(value):
    with pytest.raises(ValueError, match="work_scheduling_policy"):
        ProjectConfig("/unused", "app.py", work_scheduling_policy=value)


@pytest.mark.parametrize("policy", ["legacy", "fair"])
def test_explicit_scheduling_policy_cannot_be_erased_from_historical_digest(historical_langserve, policy):
    directory, records = historical_langserve
    mutate_historical(directory, lambda raw: raw["configuration"].update(work_scheduling_policy=policy))
    with pytest.raises(ValueError, match="configuration binding"):
        collect_results.static_results(directory, records)


def test_original_round8_effective_schema_remains_verifiable(historical_langserve):
    directory, records = historical_langserve
    assert collect_results.static_results(directory, records)["trials"] == 2


def test_real_worker_records_bound_terminal_timings_and_exact_ascii_payload_size(tmp_path):
    (tmp_path / "应用.py").write_text("raise RuntimeError('source must not execute')\n")
    config = asdict(ProjectConfig(str(tmp_path), "应用.py", analysis_stage="inventory"))
    report = run_isolated(config, timeout=20)
    assert report["status"] == "inventory_completed"
    assert report["effective_config"]["work_scheduling_policy"] == "fair"
    progress = report["execution_progress"]
    timings = progress["phase_timing_observations"]
    phases = ("worker_serialization_started", "worker_json_ready", "worker_output_completed")
    assert all(name in timings for name in phases)
    assert [timings[name]["worker_elapsed_seconds"] for name in phases] == sorted(
        timings[name]["worker_elapsed_seconds"] for name in phases)
    assert progress["timing_observations_rejected"] == 0
    assert progress["last_phase"] == "worker_output_completed"
    assert progress["semantic_complete"] is False
    assert progress["call_projection_completed"] is False
    original = {key: value for key, value in report.items()
                if key not in {"resource_limits", "execution_progress", "supervisor_wall_seconds"}}
    original["_memory_limit_enforced"] = report["resource_limits"]["address_space_limit_enforced"]
    assert len(json.dumps(original).encode("ascii")) == progress["serialized_json_bytes"]
