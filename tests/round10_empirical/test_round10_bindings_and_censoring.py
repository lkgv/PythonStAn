import copy
import json
from pathlib import Path

import pytest

from benchmark.round8_corpus.registry import project_configuration
from benchmark.round10_protocol import metrics


CORE = "a" * 64
SCRIPT = "b" * 64
LIMITS = {"timeout_seconds": 60, "memory_mb": 1536, "max_iterations": 50000,
          "max_modules": 10000, "max_source_bytes": 209715200}


def study(tmp_path, *, stage="solver", status="completed_with_residuals", repeat=1):
    record = {"id": "demo", "package": "demo", "version": "1", "archive_sha256": "d" * 64,
        "python_files": 1, "source_files": [{"path": "demo.py", "sha256": "e" * 64}],
        "project_root": "demo", "entry_file": "demo.py", "source_roots": ["demo.py"],
        "import_roots": ["."], "entry_points": ["demo.entry"]}
    source = {"package": "demo", "version": "1", "archive_sha256": "d" * 64,
        "python_files": 1, "source_files_sha256": metrics.canonical_hash(record["source_files"])}
    kind = "broad" if stage == "solver" and repeat == 1 else "timing"
    protocol = {"study_id": "round10-two-track-v1", "baseline": {"git_commit": "baseline", "core_sha256": CORE},
        "projects": [{"project_id": "demo", **source}], "limits": LIMITS,
        "broad": {"parallel_workers": 2},
        "timing": {"controls": [{"project_id": "demo", "analysis_stage": stage}],
                   "repeat_count": repeat, "parallel_workers": 1}}
    config = project_configuration(record, "/cache", ai=True)
    config["analysis_stage"] = stage
    effective = {**metrics.DEFAULTS, **config}
    report = {"status": status, "solver_completed": stage == "solver",
        "first_party_translation_complete": stage == "solver", "source_snapshot_stable": True,
        "implementation_stable": True, "implementation_sha256_before": CORE, "implementation_sha256_after": CORE,
        "effective_config": effective, "configuration_sha256": metrics.canonical_hash(effective),
        "frontend_errors": [], "unresolved_entries": [], "entry_points": ["demo.entry"],
        "statistics": {"converged": 1, "pending_work": 0, "iteration_limit_reached": 0},
        "coverage": {"discovered_python_files": 1, "parsed_modules": 1, "lowered_modules": 1,
            "translated_modules": 1 if stage == "solver" else 0,
            "unprocessed_files": 0 if stage == "solver" else 1,
            "whole_declared_first_party_source_lowered": True,
            "whole_declared_first_party_module_constraints_translated": stage == "solver"},
        "inventory": [{"path": "demo.py", "sha256": "e" * 64, "ownership": "first_party"}],
        "call_edges": [{"caller": "demo.entry", "callee": "demo.target"}],
        "supervisor_wall_seconds": 2.0, "peak_rss_bytes": 10000,
        "peak_rss_method": "linux_proc_self_status_VmHWM", "semantic_complete": False}
    verified = {"source_integrity_verified": True, "python_files": 1, "project_root": config["project_root"]}
    raw = {"schema_version": 1, "study_id": protocol["study_id"], "project_id": "demo", "stage": stage,
        "repeat": 1, "phase": "baseline", "kind": kind, "mode": "current",
        "configuration": config, "input_configuration_sha256": metrics.canonical_hash(config),
        "source_identity": source, "source_before": verified, "source_after": verified,
        "engine_core_matches_phase": True, "engine_core_sha256_before": CORE, "engine_core_sha256_after": CORE,
        "engine_script_stable": True, "engine_script_sha256_before": SCRIPT, "engine_script_sha256_after": SCRIPT,
        "limits": LIMITS, "target_source_executed": False, "report": report, "driver_wall_seconds": 2.1}
    execution = {"study_id": protocol["study_id"], "phase": "baseline", "kind": kind, "cache": "/cache",
        "git_commit": "baseline", "tracked_worktree_dirty": False, "engine_core_sha256": CORE,
        "engine_script_sha256": SCRIPT, "limits": LIMITS, "parallel_workers": protocol[kind]["parallel_workers"],
        "expected_trials": [{"project_id": "demo", "stage": stage, "repeat": r} for r in range(1, repeat + 1)]}
    summary = {**execution, "implementation_stable": True, "engine_core_sha256_after": CORE,
        "runner_stable": True, "engine_script_stable": True, "rows": []}
    for r in range(1, repeat + 1):
        row = copy.deepcopy(raw)
        row["repeat"] = r
        name = f"demo_{stage}_r{r}.json"
        (tmp_path / name).write_text(json.dumps(row))
        summary["rows"].append({"project_id": "demo", "stage": stage, "repeat": r,
            "path": name, "sha256": metrics.file_hash(tmp_path / name), "status": status})
    (tmp_path / "execution.json").write_text(json.dumps(execution))
    summary["execution_sha256"] = metrics.file_hash(tmp_path / "execution.json")
    (tmp_path / "summary.json").write_text(json.dumps(summary))
    return record, protocol


def mutate_raw(directory, change, *, rebind=True):
    summary = json.loads((directory / "summary.json").read_text())
    wrapper = summary["rows"][0]
    path = directory / wrapper["path"]
    raw = json.loads(path.read_text())
    change(raw)
    path.write_text(json.dumps(raw))
    if rebind:
        wrapper["sha256"] = metrics.file_hash(path)
        (directory / "summary.json").write_text(json.dumps(summary))


def validated(tmp_path, record, protocol):
    return metrics.validate_directory(tmp_path, records={record["id"]: record}, protocol=protocol)


def test_completion_keeps_semantic_unknowns_separate(tmp_path):
    record, protocol = study(tmp_path)
    value = validated(tmp_path, record, protocol)
    assert value["stage_completed_trials"] == 1
    assert value["rows"][0]["semantic_complete"] is False
    assert value["whole_program_soundness_proved"] is False


@pytest.mark.parametrize("change", [
    lambda r: r.update(project_id="another"),
    lambda r: r["source_identity"].update(version="2"),
    lambda r: r["report"]["inventory"][0].update(sha256="f" * 64),
    lambda r: r["report"].update(implementation_sha256_before="f" * 64),
    lambda r: r["report"].update(configuration_sha256="f" * 64),
    lambda r: r["configuration"].update(synthetic_method_contexts=True),
    lambda r: r.update(limits={**LIMITS, "max_iterations": 100000}),
    lambda r: r["source_after"].update(source_integrity_verified=False),
])
def test_rebound_raw_still_rejects_wrong_identity_source_config_or_engine(tmp_path, change):
    record, protocol = study(tmp_path)
    mutate_raw(tmp_path, change)
    with pytest.raises(ValueError):
        validated(tmp_path, record, protocol)


def test_raw_changes_without_rebinding_are_rejected(tmp_path):
    record, protocol = study(tmp_path)
    mutate_raw(tmp_path, lambda r: r["report"].update(call_edges=[]), rebind=False)
    with pytest.raises(ValueError, match="hash"):
        validated(tmp_path, record, protocol)


@pytest.mark.parametrize("missing", [True, False])
def test_empty_and_duplicate_trial_matrices_cannot_change_denominator(tmp_path, missing):
    record, protocol = study(tmp_path)
    path = tmp_path / "summary.json"
    value = json.loads(path.read_text())
    value["rows"] = [] if missing else value["rows"] * 2
    path.write_text(json.dumps(value))
    with pytest.raises(ValueError):
        validated(tmp_path, record, protocol)


def test_solver_checkpoint_is_not_completion_or_timing(tmp_path):
    record, protocol = study(tmp_path)
    def timeout(raw):
        old = raw["report"]
        raw["report"] = {"status": "timeout", "solver_completed": False,
            "partial_progress": {"last_phase": "solver_completed", "coverage_observed": old["coverage"],
                "inventory": old["inventory"], "implementation_sha256_before": CORE,
                "configuration_sha256": old["configuration_sha256"]}, "supervisor_wall_seconds": 60.1}
    mutate_raw(tmp_path, timeout)
    path = tmp_path / "summary.json"
    value = json.loads(path.read_text()); value["rows"][0]["status"] = "timeout"; path.write_text(json.dumps(value))
    result = validated(tmp_path, record, protocol)
    assert result["declared_trials"] == 1 and result["stage_completed_trials"] == 0
    assert result["rows"][0]["last_phase"] == "solver_completed"
    assert result["rows"][0]["observable_signature"] is None


def test_all_files_lowered_does_not_close_pending_solver(tmp_path):
    record, protocol = study(tmp_path)
    mutate_raw(tmp_path, lambda r: r["report"]["statistics"].update(converged=0, pending_work=5, iteration_limit_reached=1))
    assert validated(tmp_path, record, protocol)["stage_completed_trials"] == 0


def test_caught_memory_error_retains_bound_execution_checkpoints_without_success(tmp_path):
    record, protocol = study(tmp_path)
    def oom(raw):
        old = raw["report"]
        raw["report"] = {"status": "analysis_error", "diagnostic": "MemoryError: ",
            "solver_completed": False, "execution_progress": {
                "coverage_observed": old["coverage"], "last_phase": "final_source_verification_started",
                "implementation_sha256_before": CORE, "configuration_sha256": old["configuration_sha256"],
                "last_observed_statistics": {"converged": 1, "pending_work": 0},
                "last_observed_statistics_phase": "solver_completed"}}
    mutate_raw(tmp_path, oom)
    path = tmp_path / "summary.json"
    value = json.loads(path.read_text()); value["rows"][0]["status"] = "analysis_error"; path.write_text(json.dumps(value))
    result = validated(tmp_path, record, protocol)
    row = result["rows"][0]
    assert result["stage_completed_trials"] == 0
    assert row["coverage_observed"]["lowered_modules"] == 1
    assert row["coverage_observed_origin"] == "execution_progress"
    assert row["last_observed_statistics"]["converged"] == 1
    assert row["statistics"] == {} and row["observable_signature"] is None


def test_execution_checkpoint_configuration_cannot_be_rebound_to_wrong_worker(tmp_path):
    record, protocol = study(tmp_path)
    mutate_raw(tmp_path, lambda r: r["report"].update(execution_progress={
        "implementation_sha256_before": "f" * 64,
        "configuration_sha256": r["report"]["configuration_sha256"]}))
    with pytest.raises(ValueError, match="Progress core"):
        validated(tmp_path, record, protocol)


def test_frontend_control_can_complete_without_solver_claim(tmp_path):
    record, protocol = study(tmp_path, stage="frontend", status="frontend_completed", repeat=3)
    result = validated(tmp_path, record, protocol)
    assert result["declared_trials"] == result["stage_completed_trials"] == 3


def test_legacy_inherited_memory_is_not_an_isolated_peak(tmp_path):
    record, protocol = study(tmp_path)
    mutate_raw(tmp_path, lambda r: r["report"].pop("peak_rss_method"))
    assert validated(tmp_path, record, protocol)["rows"][0]["isolated_peak_rss_bytes"] is None


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -1.0])
def test_nonfinite_or_negative_wall_time_is_not_a_measurement(tmp_path, value):
    record, protocol = study(tmp_path)
    mutate_raw(tmp_path, lambda r: r["report"].update(supervisor_wall_seconds=value))
    with pytest.raises(ValueError, match="wall"):
        validated(tmp_path, record, protocol)


@pytest.mark.parametrize("value", [-1, True])
def test_invalid_memory_peak_is_rejected(tmp_path, value):
    record, protocol = study(tmp_path)
    mutate_raw(tmp_path, lambda r: r["report"].update(peak_rss_bytes=value))
    with pytest.raises(ValueError, match="memory"):
        validated(tmp_path, record, protocol)


def test_boolean_repeat_cannot_impersonate_repeat_one(tmp_path):
    record, protocol = study(tmp_path)
    mutate_raw(tmp_path, lambda r: r.update(repeat=True))
    with pytest.raises(ValueError, match="identity"):
        validated(tmp_path, record, protocol)


def pair_rows(*, kind="timing", complete=True, changed=False):
    base = {"phase": "baseline", "kind": kind, "parallel_workers": 1 if kind == "timing" else 2,
        "rows": [{"project_id": "demo", "stage": "solver", "repeat": i,
            "configuration_sha256": "c", "source_files_sha256": "s", "status": "completed_with_residuals",
            "stage_completed": True, "observable_signature": "projection", "wall_seconds": 4.0} for i in range(1, 4)]}
    after = copy.deepcopy(base); after["phase"] = "candidate"
    for r in after["rows"]:
        r.update(wall_seconds=2.0, stage_completed=complete,
                 observable_signature="changed" if changed else "projection")
    return base, after


def test_broad_parallel_runs_never_get_speed_ratios():
    result = metrics.compare_phases(*pair_rows(kind="broad"))
    assert all(r["baseline_over_candidate_ratio"] is None for r in result["pairs"])
    assert result["causal_speedup_claim"] is False


@pytest.mark.parametrize("which", ["censored", "changed"])
def test_one_missing_or_changed_repeat_prevents_selected_success_median(which):
    before, after = pair_rows()
    after["rows"][0]["stage_completed"] = which != "censored"
    after["rows"][0]["observable_signature"] = "changed" if which == "changed" else "projection"
    result = metrics.compare_phases(before, after)
    assert result["timing_controls"][0]["median_paired_ratio"] is None
    assert result["timing_controls"][0]["censored_or_changed_pairs"] == 1


def test_three_same_projection_pairs_are_descriptive_not_semantic_proof():
    result = metrics.compare_phases(*pair_rows())
    assert result["timing_controls"][0]["median_paired_ratio"] == 2.0
    assert result["semantic_equivalence_proved"] is False
    assert result["timing_controls"][0]["statistical_significance_established"] is False


def test_comparison_rejects_config_drift_and_missing_rows():
    before, after = pair_rows()
    after["rows"].pop()
    with pytest.raises(ValueError, match="matrices"):
        metrics.compare_phases(before, after)
    before, after = pair_rows()
    after["rows"][0]["configuration_sha256"] = "easier-context"
    with pytest.raises(ValueError, match="inputs"):
        metrics.compare_phases(before, after)


def test_convergence_flags_do_not_override_a_failure_status(tmp_path):
    record, protocol = study(tmp_path)
    mutate_raw(tmp_path, lambda r: r["report"].update(status="analysis_error"))
    path = tmp_path / "summary.json"
    value = json.loads(path.read_text()); value["rows"][0]["status"] = "analysis_error"; path.write_text(json.dumps(value))
    result = validated(tmp_path, record, protocol)
    assert result["stage_completed_trials"] == 0
    assert result["rows"][0]["observable_signature"] is None


def test_timeout_keeps_observed_allocation_failure_as_a_distinct_cause(tmp_path):
    record, protocol = study(tmp_path)
    def timeout(raw):
        old = raw["report"]
        raw["report"] = {"status": "timeout", "solver_completed": False,
            "partial_progress": {"coverage_observed": old["coverage"], "inventory": old["inventory"],
                "implementation_sha256_before": CORE, "configuration_sha256": old["configuration_sha256"],
                "last_phase": "analysis_resource_exhausted", "last_observed_analysis_error": {
                    "diagnostic": "MemoryError in frontend", "phase": "module_parse_started"}}}
    mutate_raw(tmp_path, timeout)
    path = tmp_path / "summary.json"
    value = json.loads(path.read_text()); value["rows"][0]["status"] = "timeout"; path.write_text(json.dumps(value))
    result = validated(tmp_path, record, protocol)
    row = result["rows"][0]
    assert row["status"] == "timeout" and row["memory_error_observed"] is True
    assert row["analysis_failure_phase"] == "module_parse_started"
    assert result["stage_completed_trials"] == 0


def test_memory_error_word_inside_other_exception_is_not_a_memory_event(tmp_path):
    record, protocol = study(tmp_path)
    mutate_raw(tmp_path, lambda r: r["report"].update(diagnostic="ValueError: text mentions MemoryError"))
    assert validated(tmp_path, record, protocol)["rows"][0]["memory_error_observed"] is False


def test_malformed_error_checkpoint_is_rejected(tmp_path):
    record, protocol = study(tmp_path)
    mutate_raw(tmp_path, lambda r: r["report"].update(execution_progress={"last_observed_analysis_error": ["MemoryError"]}))
    with pytest.raises(ValueError, match="error checkpoint"):
        validated(tmp_path, record, protocol)


def candidate(tmp_path, *, policy="auto"):
    record, protocol = study(tmp_path)
    execution = json.loads((tmp_path / "execution.json").read_text()); execution["phase"] = "candidate"
    (tmp_path / "execution.json").write_text(json.dumps(execution))
    summary = json.loads((tmp_path / "summary.json").read_text())
    summary["phase"] = "candidate"; summary["execution_sha256"] = metrics.file_hash(tmp_path / "execution.json")
    (tmp_path / "summary.json").write_text(json.dumps(summary))
    def change(raw):
        raw["phase"] = "candidate"
        raw["report"]["effective_config"]["frontend_lifetime_policy"] = policy
        raw["report"]["configuration_sha256"] = metrics.canonical_hash(raw["report"]["effective_config"])
    mutate_raw(tmp_path, change)
    return record, protocol


def test_candidate_declared_default_extension_does_not_change_input_config(tmp_path):
    record, protocol = candidate(tmp_path)
    result = validated(tmp_path, record, protocol)
    assert result["stage_completed_trials"] == 1
    assert result["rows"][0]["configuration_schema_extension"] == {"frontend_lifetime_policy": "auto"}
    raw = json.loads((tmp_path / "demo_solver_r1.json").read_text())
    assert "frontend_lifetime_policy" not in raw["configuration"]


def test_candidate_cannot_silently_switch_fixed_default_to_retain(tmp_path):
    record, protocol = candidate(tmp_path, policy="retain")
    with pytest.raises(ValueError, match="configuration"):
        validated(tmp_path, record, protocol)


def test_baseline_schema_does_not_accept_the_candidate_default(tmp_path):
    record, protocol = study(tmp_path)
    def change(raw):
        raw["report"]["effective_config"]["frontend_lifetime_policy"] = "auto"
        raw["report"]["configuration_sha256"] = metrics.canonical_hash(raw["report"]["effective_config"])
    mutate_raw(tmp_path, change)
    with pytest.raises(ValueError, match="configuration"):
        validated(tmp_path, record, protocol)


def test_real_protocol_route_checks_execution_protocol_digest(tmp_path, monkeypatch):
    record, protocol = study(tmp_path)
    frozen = tmp_path / "protocol"
    frozen.mkdir()
    (frozen / "protocol.json").write_text(json.dumps(protocol))
    (frozen / "protocol_sha256.json").write_text(json.dumps({"sha256": metrics.file_hash(frozen / "protocol.json")}))
    monkeypatch.setattr(metrics, "HERE", frozen)
    execution = json.loads((tmp_path / "execution.json").read_text()); execution["protocol_sha256"] = "f" * 64
    (tmp_path / "execution.json").write_text(json.dumps(execution))
    summary = json.loads((tmp_path / "summary.json").read_text()); summary.update(execution)
    summary["execution_sha256"] = metrics.file_hash(tmp_path / "execution.json")
    (tmp_path / "summary.json").write_text(json.dumps(summary))
    with pytest.raises(ValueError, match="Execution protocol digest"):
        metrics.validate_directory(tmp_path, records={record["id"]: record})


def test_rebound_header_cannot_substitute_a_different_driver(tmp_path, monkeypatch):
    record, protocol = study(tmp_path)
    frozen = tmp_path / "protocol"
    frozen.mkdir()
    (frozen / "protocol.json").write_text(json.dumps(protocol))
    digest = metrics.file_hash(frozen / "protocol.json")
    (frozen / "protocol_sha256.json").write_text(json.dumps({"sha256": digest}))
    (frozen / "run_pair.py").write_text("# frozen measurement driver\n")
    monkeypatch.setattr(metrics, "HERE", frozen)
    execution = json.loads((tmp_path / "execution.json").read_text())
    execution.update(protocol_sha256=digest, runner_sha256="f" * 64)
    (tmp_path / "execution.json").write_text(json.dumps(execution))
    summary = json.loads((tmp_path / "summary.json").read_text()); summary.update(execution)
    summary["execution_sha256"] = metrics.file_hash(tmp_path / "execution.json")
    (tmp_path / "summary.json").write_text(json.dumps(summary))
    with pytest.raises(ValueError, match="Execution driver digest"):
        metrics.validate_directory(tmp_path, records={record["id"]: record})
