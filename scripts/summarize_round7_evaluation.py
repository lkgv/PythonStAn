#!/usr/bin/env python3
"""Validate whole-application evidence and bind it to the frozen implementation."""
import hashlib
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.run_analysis_evaluation import fingerprint


def main():
    evidence = ROOT / "docs/certified_analysis/evidence"
    def read(name): return json.loads((evidence / name).read_text())
    cycle, apps = read("round7_cycle/summary.json"), read("round7_apps.json")
    matrix, old = read("round7_app_configurations.json"), read("round7_hardcases.json")
    core = fingerprint()
    for item in (cycle, apps, matrix, old):
        assert item["implementation_stable"]
        assert item["implementation_sha256_before"] == item["implementation_sha256_after"] == core
    assert cycle["gates_passed"] and cycle["full_repository_gate_executed"]
    assert apps["manifest_stable"] and apps["runtime_applications_validated"] == 2
    assert read("round7_frozen_corpus_integrity.json")["all_unchanged"]
    for row in apps["rows"]:
        assert row["provenance_verified"] and row["source_records_unchanged_after_runtime"]
    log = (evidence / "round7_cycle/historical_full_suite.log").read_text()
    match = re.search(r"^(\d+) passed(?:, (\d+) warnings?)? in ([\d.]+)s", log, re.M)
    assert match
    files = ["round7_cycle/summary.json", "round7_cycle/historical_full_suite.log", "round7_apps.json",
             "round7_apps_first_party.json", "round7_project_root_policy_probe.json",
             "round7_app_configurations.json", "round7_hardcases.json", "round7_web_upstream.json",
             "round7_cycle/closed_heap_effects.json", "round7_frozen_corpus_integrity.json",
             "round7_test_migration_audit.json"]
    result = {
        "schema_version": 1, "base_commit": "e398666f9252d628c5d219804538fa3f623bd680",
        "implementation_sha256": core,
        "full_repository": {"passed": int(match[1]), "warnings": int(match[2] or 0),
                            "failed": 0, "errors": 0, "skipped": 0, "wall_seconds": float(match[3])},
        "unique_upstream_applications": apps["unique_upstream_applications"],
        "analysis_scenarios": apps["analysis_scenarios"], "applications": [],
        "old_hardcases": old["totals"],
        "configuration_projections": {key: matrix[key] for key in
            ("trials", "configuration_rows", "completed_trials", "stable_rows", "rows_meeting_reviewed_calls")},
        "closed_effects": read("round7_cycle/closed_heap_effects.json")["summary"],
        "whole_program_soundness_proved": False, "framework_dispatch_certified": False,
        "interpretation": "Complete upstream application source is the analysis input. Entry assumptions, "
                          "module coverage, actual solver calls and runtime observations are distinct channels.",
        "evidence": [{"path": name, "sha256": hashlib.sha256((evidence / name).read_bytes()).hexdigest()}
                     for name in files],
    }
    for row in apps["rows"]:
        result["applications"].append({"id": row["case"]["id"],
            "coverage": row["current"].get("coverage"), "baseline_coverage": row["baseline"].get("coverage"),
            "status": row["current"]["status"], "baseline_status": row["baseline"]["status"],
            "baseline_calls": row["baseline"]["reviewed_calls"], "current_calls": row["current"]["reviewed_calls"],
            "runtime_checks": len(row["runtime"].get("checks", [])),
            "runtime_valid": row["runtime"].get("reviewed_checks_valid", False)})
    (evidence / "round7_validation.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({key: result[key] for key in
                      ("full_repository", "implementation_sha256", "configuration_projections")}, indent=2))


if __name__ == "__main__":
    main()
