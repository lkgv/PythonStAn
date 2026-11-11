#!/usr/bin/env python3
"""Recompute Round11 engineering results without promoting research obligations."""
import hashlib
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from benchmark.round11_protocol.metrics import validate_directory, compare_phases, compare_reused_fair
from benchmark.round11_old_hardcases.run import validate as validate_fair_hardcases
from pythonstan.analysis.project.analysis import _implementation_hash

EVIDENCE = ROOT / "docs/certified_analysis/evidence"


def read(name):
    return json.loads((EVIDENCE / name).read_text())


def digest(path):
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(65536), b""):
            value.update(chunk)
    return value.hexdigest()


def main():
    names = ("baseline_broad", "candidate_broad", "baseline_timing", "candidate_timing", "candidate_legacy_broad")
    phases = {name: validate_directory(EVIDENCE / ("round11_" + name)) for name in names}
    core = _implementation_hash()
    assert all(phases[name]["engine_core_sha256"] == core for name in names if name.startswith("candidate"))
    cycle = read("round11_cycle/summary.json")
    assert cycle["gates_passed"] and cycle["implementation_stable"]
    assert cycle["implementation_sha256_before"] == cycle["implementation_sha256_after"] == core
    for row in cycle["phases"]:
        assert row["exit_code"] == 0
        assert digest(EVIDENCE / "round11_cycle" / row["log"]) == row["log_sha256"]
    log = (EVIDENCE / "round11_final_full_suite.log").read_text()
    match = re.search(r"^(\d+) passed, (\d+) warning in ([\d.]+)s", log, re.M)
    assert match and not re.search(r"\b\d+ (?:failed|errors?|skipped)\b", log)
    legacy = read("round11_hardcases.json")
    assert legacy["implementation_stable"] and legacy["implementation_sha256_after"] == core
    fair = validate_fair_hardcases(read("round11_fair_old_hardcases.json"))
    c3 = read("round11_super_configuration_matrix.json")
    assert all(r["measured_configurations"] == 12 and r["required_edges_recovered"] == 12
               and r["negative_violations"] == 0 for r in c3["summary"].values())
    frontier = read("round11_applications/frontier_summary.json")
    assert frontier["implementation_sha256"] == core
    assert frontier["raw_sha256"] == digest(EVIDENCE / frontier["raw_path"])
    entries = read("round11_applications/candidate_entry_summary.json")
    assert entries["ledger_sha256"] == digest(EVIDENCE / "round11_applications/candidate_entry_audit.json")
    broad = {}
    for name in ("baseline_broad", "candidate_broad", "candidate_legacy_broad"):
        phase = phases[name]
        rows = phase["rows"]
        broad[name] = {"trials": phase["validated_trials"], "source_files": sum(r["source_files"] for r in rows),
            "statuses": phase["statuses"], "strict_module_solver_report_completed": phase["stage_completed_trials"],
            "observed_prefix": {k: sum(r["coverage_observed"].get(k, 0) for r in rows)
                                for k in ("parsed_modules", "lowered_modules", "translated_modules")},
            "bound_memory_error_projects": [r["project_id"] for r in rows if r["memory_error_observed"]]}
    files = ["round11_cycle/summary.json", "round11_final_full_suite.log", "round11_comparison.json",
        "round11_empirical/final_audit.json", "round11_hardcases.json", "round11_fair_old_hardcases.json",
        "round11_fair_old_hardcases_validated_summary.json", "round11_super_configuration_matrix.json",
        "round11_applications/frontier_results.json", "round11_applications/frontier_summary.json",
        "round11_applications/candidate_entry_audit.json", "round11_applications/candidate_entry_summary.json",
        "round11_applications/composite_key_gap.json", "round11_descriptor_native_callback_gap.json",
        "round11_solver/fair_source_equivalence_final.json", "round11_solver/starvation_control_final.json",
        "round11_liveness/runner_all_facts.json", "round11_liveness/original_migration_control.json",
        "round11_liveness/original_migration_allocation_profile.json", "round11_native/scalar_key_control.json",
        "round11_native/native_mapping_callback_repaired.json", "round11_process_runtime/timeout_output_control.json"]
    report = {"schema_version": 1, "baseline_revision": "7d1a7b60b647e8ef4d27ec28f34c94d29fecf77d",
        "implementation_sha256": core, "engineering_gates_passed": True,
        "full_repository": {"passed": int(match[1]), "warnings": int(match[2]), "failed": 0,
                            "errors": 0, "skipped": 0, "wall_seconds": float(match[3])},
        "unique_project_study_trials": sum(p["validated_trials"] for p in phases.values()),
        "primary_study_trials": sum(phases[n]["validated_trials"] for n in names if n != "candidate_legacy_broad"),
        "broad": broad,
        "timing": compare_phases(phases["baseline_timing"], phases["candidate_timing"]),
        "same_candidate_scheduling": compare_reused_fair(phases["candidate_legacy_broad"], phases["candidate_broad"]),
        "old_hardcases_legacy_with_synthetic_roots": legacy["totals"],
        "old_hardcases_explicit_fair_by_regime": fair,
        "C3_configuration_regression": c3["summary"], "new_frontiers": frontier["counts"],
        "new_frontier_research_gate_exit_code": frontier["research_gate_exit_code"],
        "entry_observations": entries["counts"],
        "advanced_end_to_end_challenges": {"required": 14, "proved": 0, "required_properties": 60},
        "full_python_soundness_proved": False, "global_acceptance_achieved": False,
        "online_llm_calls": 0, "upstream_package_source_executed_by_project_study": False,
        "interpretation": "Engineering, module/report completion, justified entries, native/effect/continuation relations and global soundness are independent gates. Diagnostic and configuration trials are not additional applications.",
        "evidence": [{"path": name, "sha256": digest(EVIDENCE / name)} for name in files]}
    target = EVIDENCE / "round11_release_gate.json"
    target.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"output": str(target.relative_to(ROOT)), "tests_passed": int(match[1]),
        "unique_project_study_trials": report["unique_project_study_trials"],
        "explicit_fair_old_hardcase_trials": sum(r["cases"] for r in fair.values()),
        "engineering_gates_passed": True, "global_acceptance_achieved": False}))


if __name__ == "__main__":
    main()
