import copy
import json

import pytest

from benchmark.round11_protocol import metrics, run_pair
from tests.round11_empirical.test_round11_bindings_and_censoring import (
    candidate, mutate_raw, study, validated,
)


def write_execution(directory, mutate):
    path = directory / "execution.json"
    header = json.loads(path.read_text())
    mutate(header)
    path.write_text(json.dumps(header))
    path = directory / "summary.json"
    summary = json.loads(path.read_text())
    summary.update(header)
    summary["execution_sha256"] = metrics.file_hash(directory / "execution.json")
    path.write_text(json.dumps(summary))


def policy_study(directory, policy):
    directory.mkdir()
    record, protocol = candidate(directory)
    write_execution(directory, lambda header: header.update(scheduling_policy_override=policy))
    def set_arm(raw):
        raw["scheduling_policy_override"] = policy
        raw["configuration"]["work_scheduling_policy"] = policy
        raw["input_configuration_sha256"] = metrics.canonical_hash(raw["configuration"])
        raw["report"]["effective_config"]["work_scheduling_policy"] = policy
        raw["report"]["configuration_sha256"] = metrics.canonical_hash(raw["report"]["effective_config"])
    mutate_raw(directory, set_arm)
    return record, protocol


@pytest.mark.parametrize("policy", ["legacy", "fair"])
def test_policy_arm_has_declared_separate_input_and_default_binding(tmp_path, policy):
    directory = tmp_path / policy
    record, protocol = policy_study(directory, policy)
    result = validated(directory, record, protocol)
    assert result["scheduling_policy_override"] == policy
    assert result["rows"][0]["configuration"]["work_scheduling_policy"] == policy
    assert result["rows"][0]["configured_scheduling_policy"] == policy
    assert result["stage_completed_trials"] == 1


def test_input_policy_cannot_silently_change_without_declared_arm(tmp_path):
    record, protocol = candidate(tmp_path)
    def unannounced(raw):
        raw["configuration"]["work_scheduling_policy"] = "legacy"
        raw["input_configuration_sha256"] = metrics.canonical_hash(raw["configuration"])
    mutate_raw(tmp_path, unannounced)
    with pytest.raises(ValueError, match="input configuration"):
        validated(tmp_path, record, protocol)


def test_declared_arm_cannot_be_ignored_by_worker(tmp_path):
    directory = tmp_path / "legacy"
    record, protocol = policy_study(directory, "legacy")
    def ignored(raw):
        raw["report"]["effective_config"]["work_scheduling_policy"] = "fair"
        raw["report"]["configuration_sha256"] = metrics.canonical_hash(raw["report"]["effective_config"])
    mutate_raw(directory, ignored)
    with pytest.raises(ValueError, match="effective configuration"):
        validated(directory, record, protocol)


def test_effective_fair_project_cannot_hide_legacy_solver_execution(tmp_path):
    record, protocol = candidate(tmp_path)
    mutate_raw(tmp_path, lambda raw: raw["report"]["statistics"].update(work_scheduling_policy="legacy"))
    with pytest.raises(ValueError, match="Solver scheduling policy"):
        validated(tmp_path, record, protocol)


def test_baseline_cannot_acquire_candidate_policy_arm(tmp_path):
    record, protocol = study(tmp_path)
    write_execution(tmp_path, lambda header: header.update(scheduling_policy_override="legacy"))
    with pytest.raises(ValueError, match="policy arm"):
        validated(tmp_path, record, protocol)


def test_raw_arm_must_match_execution_header(tmp_path):
    record, protocol = candidate(tmp_path)
    mutate_raw(tmp_path, lambda raw: raw.update(scheduling_policy_override="fair"))
    with pytest.raises(ValueError, match="identity"):
        validated(tmp_path, record, protocol)


def test_same_candidate_policy_comparison_never_mixes_primary_or_speed_claims(tmp_path):
    results = []
    for policy in ("legacy", "fair"):
        directory = tmp_path / policy
        record, protocol = policy_study(directory, policy)
        results.append(validated(directory, record, protocol))
    result = metrics.compare_policies(*results)
    assert result["declared_pairs"] == 1
    assert result["pairs"][0]["causal_runtime_ratio"] is None
    assert result["semantic_equivalence_proved"] is False
    with pytest.raises(ValueError, match="ablations"):
        before = copy.deepcopy(results[0]); before["phase"] = "baseline"
        metrics.compare_phases(before, results[1])


@pytest.mark.parametrize("mutation", [
    lambda value: value.update(engine_core_sha256="other core"),
    lambda value: value.update(engine_script_sha256="other CLI"),
    lambda value: value.update(parallel_workers=1),
    lambda value: value["rows"][0]["configuration"].update(index_sensitive=False),
    lambda value: value["rows"][0].update(source_files_sha256="other source"),
    lambda value: value["rows"].clear(),
])
def test_policy_comparison_rejects_other_drift(tmp_path, mutation):
    results = []
    for policy in ("legacy", "fair"):
        directory = tmp_path / policy
        record, protocol = policy_study(directory, policy)
        results.append(validated(directory, record, protocol))
    mutation(results[1])
    with pytest.raises(ValueError):
        metrics.compare_policies(*results)


def static_only():
    return {"iterations": 50000, "work_items_static": 50000,
        "work_items_constraint_replay": 0, "work_items_builtin_replay": 0, "work_items_points_to": 0,
        "initial_pending_work_by_kind": {"static": 40816, "constraint_replay": 0, "builtin_replay": 0, "points_to": 59994},
        "pending_work_by_kind": {"static": 4888, "constraint_replay": 0, "builtin_replay": 0, "points_to": 125036},
        "converged": 0, "iteration_limit_reached": 1}


def test_static_only_checkpoint_is_bound_finite_prefix_not_complete():
    partial = {"last_observed_statistics": static_only(), "last_observed_statistics_phase": "report_ready"}
    result = metrics.queue_observation({"status": "timeout"}, partial, engine_bound=True)
    assert result["point_queue_initially_pending_but_unserved"] is True
    assert result["origin"] == "checkpoint" and result["origin_phase"] == "report_ready"
    assert result["work_kind_first_service_iteration"] is None
    assert "converged" not in result


def test_unbound_checkpoint_cannot_establish_starvation_observation():
    result = metrics.queue_observation({}, {"last_observed_statistics": static_only()}, engine_bound=False)
    assert result["bound"] is False
    assert result["point_queue_initially_pending_but_unserved"] is False


@pytest.mark.parametrize("mutation", [
    lambda stats: stats.update(iterations=True),
    lambda stats: stats.update(work_items_points_to=-1),
    lambda stats: stats.update(work_items_static=49999),
    lambda stats: stats.update(work_kind_first_service_iteration={"points_to": 1}),
    lambda stats: stats.update(work_kind_first_service_iteration={"static": 50001}),
    lambda stats: stats.update(work_kind_first_service_iteration={"static": True}),
    lambda stats: stats.update(pending_work_by_kind={"points_to": -1}),
    lambda stats: stats.update(initial_pending_work_by_kind={"invented queue": 1}),
    lambda stats: stats.update(work_scheduling_policy="adaptive"),
    lambda stats: stats.update(work_scheduler_cursor=4),
    lambda stats: stats.update(work_scheduler_cursor=True),
    lambda stats: stats.update(work_scheduler_kind_order=["points_to", "static", "constraint_replay", "builtin_replay"]),
])
def test_forged_or_malformed_work_observations_rejected(mutation):
    stats = static_only(); mutation(stats)
    with pytest.raises(ValueError):
        metrics.queue_observation({"statistics": stats}, {}, engine_bound=True)


def test_final_statistics_and_checkpoint_origin_are_not_combined():
    final = {"iterations": 4, "work_items_static": 1, "work_items_constraint_replay": 1,
        "work_items_builtin_replay": 1, "work_items_points_to": 1,
        "work_scheduling_policy": "fair", "work_scheduler_cursor": 0,
        "work_kind_first_service_iteration": {"static": 1, "constraint_replay": 2, "builtin_replay": 3, "points_to": 4}}
    result = metrics.queue_observation({"statistics": final}, {"last_observed_statistics": static_only()}, engine_bound=True)
    assert result["origin"] == "final_report"
    assert result["selected_work_by_kind"]["points_to"] == 1
    assert result["initial_pending_work_by_kind"] is None
    assert result["point_queue_initially_pending_but_unserved"] is False


@pytest.mark.parametrize("phase,kind,policy", [
    ("baseline", "broad", "fair"), ("candidate", "timing", "legacy"), ("candidate", "broad", "adaptive"),
])
def test_driver_rejects_unregistered_policy_regimes_before_running(tmp_path, phase, kind, policy):
    with pytest.raises(ValueError, match="scheduling diagnostics"):
        run_pair.run(tmp_path, tmp_path / "python", tmp_path, phase, kind, tmp_path / "output", policy)
