import copy
import json

import pytest

from benchmark.round11_protocol.metrics import phase_timing_observation
from scripts.analyze_project import _read_progress


def event(sequence, phase, **changes):
    return {"run_id": "run", "request_sha256": "request", "sequence": sequence,
            "implementation_sha256_before": "a" * 64, "configuration_sha256": "b" * 64,
            "phase": phase, **changes}


def frames():
    inventory = [{"path": "app.py", "module": "app", "ownership": "first_party", "sha256": "c" * 64}]
    return [event(1, "inventory_started", worker_elapsed_seconds=0.1),
            event(2, "inventory_completed", inventory=inventory, worker_elapsed_seconds=0.2),
            event(3, "worker_serialization_started", worker_elapsed_seconds=1.0),
            event(4, "worker_json_ready", worker_elapsed_seconds=1.5, worker_json_bytes=200),
            event(5, "worker_output_completed", worker_elapsed_seconds=2.0)]


def observe(tmp_path, values):
    path = tmp_path / "events.jsonl"
    path.write_text("".join(json.dumps(frame) + "\n" for frame in values))
    return _read_progress(path, "run", "request")


def test_serialization_and_output_timings_are_bound_observations_only(tmp_path):
    result = observe(tmp_path, frames())
    metric = phase_timing_observation(result, engine_bound=True)
    assert metric["serialization_wall_seconds_observed"] == 0.5
    assert metric["output_wall_seconds_observed"] == 0.5
    assert metric["serialized_json_bytes"] == 200
    assert result["call_projection_completed"] is False and result["semantic_complete"] is False
    assert "solver_completed" not in metric


@pytest.mark.parametrize("bad", [-1.0, float("nan"), float("inf"), True, "1.5"])
def test_bad_timestamp_does_not_erase_other_phase_or_drop_bound_frame(tmp_path, bad):
    values = frames(); values[3]["worker_elapsed_seconds"] = bad
    result = observe(tmp_path, values)
    assert result["events"] == 5
    assert result["timing_observations_rejected"] == 1
    assert "worker_json_ready" not in result["phase_timing_observations"]
    assert result["phase_timing_observations"]["worker_serialization_started"]["worker_elapsed_seconds"] == 1.0
    assert phase_timing_observation(result, engine_bound=True)["serialization_wall_seconds_observed"] is None


def test_decreasing_time_does_not_replace_first_valid_phase(tmp_path):
    values = frames()
    values.insert(4, event(5, "worker_serialization_started", worker_elapsed_seconds=0.8))
    values[-1]["sequence"] = 6
    result = observe(tmp_path, values)
    assert result["timing_observations_rejected"] == 1
    assert result["phase_timing_observations"]["worker_serialization_started"]["sequence"] == 3


@pytest.mark.parametrize("change", [
    {"run_id": "foreign"}, {"request_sha256": "foreign"},
    {"implementation_sha256_before": "d" * 64}, {"configuration_sha256": "d" * 64},
    {"module_record": {"path": "app.py", "sha256": "d" * 64}},
])
def test_foreign_or_changed_source_frame_cannot_supply_elapsed_or_size(tmp_path, change):
    values = frames()[:3]
    foreign = event(4, "worker_json_ready", worker_elapsed_seconds=2.0, worker_json_bytes=999)
    foreign.update(change)
    values += [foreign, event(4, "worker_output_completed", worker_elapsed_seconds=1.5)]
    result = observe(tmp_path, values)
    assert result["events"] == 4 and result["rejected_or_torn_frames"] == 1
    assert result["serialized_json_bytes"] is None
    assert "worker_json_ready" not in result["phase_timing_observations"]
    assert result["phase_timing_observations"]["worker_output_completed"]["worker_elapsed_seconds"] == 1.5


@pytest.mark.parametrize("bad", [-1, True, "200", 1.5])
def test_malformed_output_byte_count_is_not_a_measurement(tmp_path, bad):
    values = frames(); values[3]["worker_json_bytes"] = bad
    result = observe(tmp_path, values)
    assert result["serialized_json_bytes"] is None
    assert phase_timing_observation(result, engine_bound=True)["serialization_wall_seconds_observed"] == 0.5


@pytest.mark.parametrize("mutation", [
    lambda value: value.update(serialized_json_bytes=True),
    lambda value: value.update(timing_observations_rejected=-1),
    lambda value: value["phase_timing_observations"].update(bad={"sequence": True, "worker_elapsed_seconds": 1.0}),
    lambda value: value["phase_timing_observations"].update(bad={"sequence": 99, "worker_elapsed_seconds": float("nan")}),
    lambda value: value["phase_timing_observations"].update(bad={"sequence": 99, "worker_elapsed_seconds": 0.0}),
    lambda value: value["phase_timing_observations"].update(bad={"sequence": 1, "worker_elapsed_seconds": 0.1}),
])
def test_metrics_reject_rebound_malformed_timing_observations(tmp_path, mutation):
    result = observe(tmp_path, frames())
    changed = copy.deepcopy(result); mutation(changed)
    with pytest.raises(ValueError):
        phase_timing_observation(changed, engine_bound=True)


def test_unbound_phase_timings_remain_unbound_and_legacy_absence_is_unmeasured(tmp_path):
    result = observe(tmp_path, frames())
    assert phase_timing_observation(result, engine_bound=False)["bound"] is False
    assert phase_timing_observation({}, engine_bound=True) is None
