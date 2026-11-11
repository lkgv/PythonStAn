#!/usr/bin/env python3
"""Validate final round-6 evidence identities and write a machine-readable ledger."""
from pathlib import Path
import hashlib
import json
import platform
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.run_analysis_evaluation import fingerprint
from pythonstan.analysis.pointer.ai.certified.finite_entry import _implementation_digest

EVIDENCE = ROOT / "docs/certified_analysis/evidence"


def read(name):
    return json.loads((EVIDENCE / name).read_text())


def main():
    cycle = read("round6_cycle/summary.json")
    hard = read("round6_hardcases.json")
    difference = read("round6_hardcases_differential.json")
    matrix = read("round6_config_matrix.json")
    copies = read("round6_copy_config_matrix.json")
    composition = read("round6_compositions.json")
    closed = read("round6_cycle/closed_heap_effects.json")
    finite = read("round6_finite_entry.json")
    projection = read("round6_config_audit.json")
    copy_projection = read("round6_copy_config_audit.json")
    integrity = read("round6_frozen_corpus_integrity.json")
    migration = read("round6_test_migration_audit.json")
    digest = fingerprint()
    for report in (cycle, hard, matrix, copies, composition):
        assert report["implementation_stable"]
        assert report["implementation_sha256_before"] == report["implementation_sha256_after"] == digest
    assert cycle["gates_passed"] and cycle["full_repository_gate_executed"]
    assert difference["no_reviewed_regressions"]
    assert integrity["all_unchanged"] and migration["phase"] == "final"
    assert composition["corpus_stable"] and not composition["runtime_failed"] and not composition["source_integrity_failed"]
    for report in (projection, copy_projection):
        assert not report["failed_checks"] and not report["unmeasured_checks"]
    assert finite["implementation_stable"] and finite["implementation_sha256"] == _implementation_digest()
    assert finite["status"] == "conditional_family_verified"
    for name, expected in closed["checker_files_sha256"].items():
        assert hashlib.sha256((ROOT / name).read_bytes()).hexdigest() == expected
    log = (EVIDENCE / "round6_cycle/historical_full_suite.log").read_text()
    match = re.search(r"^(\d+) passed(?:, \d+ warnings?)? in ([\d.]+)s$", log, re.M)
    assert match, "Full suite must contain only passes (optional warnings), no skipped/failing tests"
    full = {"passed": int(match[1]), "failed": 0, "errors": 0, "skipped": 0,
            "warnings": int(re.search(r"(\d+) warnings?", match[0])[1]) if "warning" in match[0] else 0,
            "wall_seconds": float(match[2])}
    filenames = [
        "round6_cycle/summary.json", "round6_cycle/historical_full_suite.log",
        "round6_hardcases.json", "round6_hardcases_differential.json",
        "round6_config_matrix.json", "round6_copy_config_matrix.json",
        "round6_config_audit.json", "round6_copy_config_audit.json",
        "round6_cycle/closed_heap_effects.json", "round6_cycle/exception_oracle.json",
        "round6_compositions.json", "round6_finite_entry.json",
        "round6_frozen_corpus_integrity.json", "round6_test_migration_audit.json",
    ]
    summary = {
        "schema_version": 1,
        "base_commit": "52668d52ab22821c907496ccfb8086b0d76691bb",
        "implementation_sha256": digest, "python": platform.python_version(),
        "full_repository": full, "hardcases": hard["totals"],
        "reviewed_regressions": difference["totals"],
        "six_project_configurations": matrix["totals"],
        "dict_copy_configurations": copies["totals"],
        "projected_inclusion_checks": projection["total_checks"] + copy_projection["total_checks"],
        "conditional_effects": closed["summary"],
        "new_compositions": {"calls": composition["totals"], "effects": composition["conditional"],
                             "runtime_validated": composition["runtime_validated"]},
        "finite_entry": {key: finite[key] for key in
                         ("status", "declared_variants", "covered_variants", "verified_variants")},
        "whole_applications_analyzed": 0, "whole_packages_statically_analyzed": 0,
        "full_python_soundness_proved": False, "global_acceptance_achieved": False,
        "guard_pruning_shipped": False, "all_frozen_gold_unchanged": True,
        "methodology": "Conditional entry/finite-family results are separate from KCFA or unrestricted proofs. "
                       "New integration slices are development evidence. No global theorem follows from these finite tests.",
        "evidence": [{"path": name, "sha256": hashlib.sha256((EVIDENCE / name).read_bytes()).hexdigest()}
                     for name in filenames],
    }
    (EVIDENCE / "round6_validation.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps({"full_repository": full, "fingerprint": digest,
                      "conditional_effects": closed["summary"], "finite_entry": summary["finite_entry"]}, indent=2))


if __name__ == "__main__":
    main()
