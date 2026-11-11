#!/usr/bin/env python3
"""Retest the unchanged Round8 missed edge across production configurations."""
import argparse
import hashlib
import itertools
import json
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def run(baseline, output):
    old = json.loads((ROOT / "docs/certified_analysis/evidence/round8_failure_diagnostics/super_mro_sibling.json").read_text())
    source = ROOT / old["source_path"]
    assert hashlib.sha256(source.read_bytes()).hexdigest() == old["source_sha256"]
    required = old["test_only_runtime_witness"]["expected_required_edge"]
    # These source bodies only return constants, and contain no calls at all.
    negatives = [["super_mro_sibling.C.value", "super_mro_sibling.B.value"],
                 ["super_mro_sibling.A.value", "super_mro_sibling.C.value"]]
    rows = []
    for k, index, synthetic in itertools.product(range(3), (False, True), (False, True)):
        config = {"project_root": str(source.parent), "entry_file": source.name, "source_roots": [source.name],
            "import_roots": ["."], "entry_points": [], "auto_framework_entries": False,
            "context_policy": str(k) + "-cfa", "index_sensitive": index,
            "synthetic_method_contexts": synthetic, "enable_ai_intrinsics": False,
            "max_iterations": 20000}
        row = {"configuration": config, "required_edge": required, "reviewed_negative_edges": negatives}
        for mode, repository in (("baseline", Path(baseline).resolve()), ("candidate", ROOT)):
            with tempfile.TemporaryDirectory(prefix="r9-super-matrix-") as temp:
                settings = Path(temp) / "config.json"
                report_path = Path(temp) / "report.json"
                settings.write_text(json.dumps(config))
                completed = subprocess.run([sys.executable, str(repository / "scripts/analyze_project.py"),
                    "--config", str(settings), "--output", str(report_path), "--timeout", "20", "--memory-mb", "1536"],
                    cwd=repository, text=True, capture_output=True, timeout=40)
                report = json.loads(report_path.read_text())
            measured = (report.get("solver_completed") is True and report.get("first_party_translation_complete") is True
                        and report.get("implementation_stable") is True and report.get("source_snapshot_stable") is True)
            edges = report.get("call_edges", [])
            row[mode] = {"status": report["status"], "measured": measured, "exit_code": completed.returncode,
                "required_edge_present": required in edges if measured else None,
                "violated_negatives": [edge for edge in negatives if edge in edges] if measured else None,
                "report": report}
        rows.append(row)
    summary = {}
    for mode in ("baseline", "candidate"):
        measured = [r[mode] for r in rows if r[mode]["measured"]]
        summary[mode] = {"configurations": len(rows), "measured_configurations": len(measured),
            "unmeasured_configurations": len(rows) - len(measured),
            "required_edges_recovered": sum(r["required_edge_present"] for r in measured),
            "required_edge_obligations": len(rows), "negative_edge_obligations": len(rows) * len(negatives),
            "negative_violations": sum(len(r["violated_negatives"]) for r in measured)}
    result = {"schema_version": 1, "source_path": old["source_path"], "source_sha256": old["source_sha256"],
        "gold_origin": "Unchanged Round8 independent concrete witness and two source bodies with no calls",
        "source_executed_by_this_check": False, "rows": rows, "summary": summary,
        "whole_program_soundness_proved": False,
        "interpretation": "One exact-source necessary edge under twelve options, not twelve independent programs or a global precision/soundness estimate."}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2) + "\n")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-repository", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = run(args.baseline_repository, args.output)
    print(json.dumps(result["summary"]))
    current = result["summary"]["candidate"]
    raise SystemExit(0 if current["required_edges_recovered"] == current["required_edge_obligations"] and not current["negative_violations"] else 2)
