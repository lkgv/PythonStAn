#!/usr/bin/env python3
"""Recompute the Round10 engineering/research gate distinction from evidence."""
import hashlib
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from benchmark.round10_protocol.metrics import validate_directory, compare_phases
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
    phases = {name: validate_directory(EVIDENCE / ("round10_" + name))
              for name in ("baseline_broad", "candidate_broad", "baseline_timing", "candidate_timing")}
    core = _implementation_hash()
    assert phases["candidate_broad"]["engine_core_sha256"] == core
    assert phases["candidate_timing"]["engine_core_sha256"] == core
    cycle = read("round10_cycle/summary.json")
    assert cycle["gates_passed"] and cycle["implementation_stable"]
    assert cycle["implementation_sha256_before"] == cycle["implementation_sha256_after"] == core
    for row in cycle["phases"]:
        assert row["exit_code"] == 0
        assert digest(EVIDENCE / "round10_cycle" / row["log"]) == row["log_sha256"]
    log = (EVIDENCE / "round10_final_full_suite.log").read_text()
    match = re.search(r"^(\d+) passed, (\d+) warning in ([\d.]+)s", log, re.M)
    assert match and not re.search(r"\b\d+ (?:failed|errors?|skipped)\b", log)
    hard = read("round10_hardcases.json")
    assert hard["implementation_stable"] and not hard["reviewed_call_gate_passed"]
    super_matrix = read("round10_super_configuration_matrix.json")
    assert all(row["required_edges_recovered"] == row["required_edge_obligations"]
               and row["measured_configurations"] == 12 and row["negative_violations"] == 0
               for row in super_matrix["summary"].values())
    entries = read("round10_applications/candidate_entry_audit.json")
    assert entries["whole_program_soundness_proved"] is False
    summaries = {}
    for name in ("baseline_broad", "candidate_broad"):
        phase = phases[name]
        rows = phase["rows"]
        summaries[name] = {
            "projects": phase["validated_trials"], "python_files": sum(r["source_files"] for r in rows),
            "statuses": phase["statuses"], "strict_module_solver_report_completed": phase["stage_completed_trials"],
            "observed_prefix": {key: sum(r["coverage_observed"].get(key, 0) for r in rows)
                                for key in ("parsed_modules", "lowered_modules", "translated_modules")},
            "memory_error_observations": [r["project_id"] for r in rows if r["memory_error_observed"]],
        }
    files = ["round10_cycle/summary.json", "round10_final_full_suite.log", "round10_comparison.json",
             "round10_hardcases.json", "round10_super_configuration_matrix.json",
             "round10_applications/candidate_entry_audit.json", "round10_native_mapping_callback_gap.json",
             "round10_empirical/final_failure_cause_and_trajectory.json",
             "round10_frontend/frontend_failure_audit.json",
             "round10_frontend/module_closure_ansible_runner_final.json",
             "round10_solver/enqueue_equivalence.json", "round10_solver/duplicate_work_control.json"]
    report = {
        "schema_version": 1, "baseline_revision": "34c13c4fce258e2a99df47ab3f4ad5637a790c63",
        "implementation_sha256": core, "engineering_gates_passed": True,
        "full_repository": {"passed": int(match[1]), "warnings": int(match[2]),
                            "failed": 0, "errors": 0, "skipped": 0, "wall_seconds": float(match[3])},
        "study_trials_validated": sum(p["validated_trials"] for p in phases.values()),
        "broad": summaries,
        "timing": compare_phases(phases["baseline_timing"], phases["candidate_timing"]),
        "old_hardcases": hard["totals"], "research_call_gate_passed": False,
        "C3_configuration_regression": super_matrix["summary"],
        "advanced_end_to_end_challenges": {"required": 14, "proved": 0, "required_properties": 60},
        "full_python_soundness_proved": False, "global_acceptance_achieved": False,
        "online_llm_calls": 0, "upstream_package_source_executed": False,
        "interpretation": "Passing engineering gates does not discharge precision, effects, entry/continuation, native, or global semantic obligations.",
        "evidence": [{"path": name, "sha256": digest(EVIDENCE / name)} for name in files],
    }
    target = EVIDENCE / "round10_release_gate.json"
    target.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"output": str(target.relative_to(ROOT)), "tests_passed": int(match[1]),
                      "study_trials_validated": report["study_trials_validated"],
                      "engineering_gates_passed": True, "global_acceptance_achieved": False}))


if __name__ == "__main__":
    main()
