#!/usr/bin/env python3
"""Repeated project call projections across 0/1/2-CFA and AI settings."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from benchmark.round7_apps.runner import run
from scripts.run_analysis_evaluation import fingerprint


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    before = fingerprint()
    # Repeat complete first-party application scopes. The dependency-inclusive
    # stress scenario has its own bounded AI-off/on run in round7_apps.json;
    # do not spend repeated timeout budgets on that distinct scalability gate.
    selected = ["flask_official_tutorial_3_1_2", "eggsample_complete_application"]
    runs = [run(context_policy=policy, case_ids=selected) for policy in ("0-cfa", "1-cfa", "2-cfa") for _ in range(2)]
    comparisons = []
    for index, policy in enumerate(("0-cfa", "1-cfa", "2-cfa")):
        first, second = runs[index * 2:index * 2 + 2]
        for a, b in zip(first["rows"], second["rows"]):
            assert a["case"]["id"] == b["case"]["id"]
            for mode in ("baseline", "current"):
                completed = all(r[mode].get("reviewed_calls", {}).get("completed_call_projection") for r in (a, b))
                stable = completed and a[mode]["call_edges"] == b[mode]["call_edges"]
                comparisons.append({"case": a["case"]["id"], "context_policy": policy, "mode": mode,
                                    "both_completed": completed, "stable": stable,
                                    "reviewed_calls_met": all(r[mode].get("reviewed_calls", {}).get("all_reviewed_calls_met") for r in (a, b))})
    after = fingerprint()
    report = {"schema_version": 1, "implementation_sha256_before": before,
              "implementation_sha256_after": after,
              "implementation_stable": before == after and all(r["implementation_stable"] for r in runs),
              "unique_applications": 2, "scope_variants_per_configuration": 2,
              "dependency_source_stress_evidence": "round7_apps.json; separate bounded AI-off/on scope",
              "trials": len(comparisons) * 2, "configuration_rows": len(comparisons),
              "completed_trials": sum(bool(row[mode].get("reviewed_calls", {}).get("completed_call_projection"))
                                      for run_report in runs for row in run_report["rows"] for mode in ("baseline", "current")),
              "stable_rows": sum(x["stable"] for x in comparisons),
              "rows_meeting_reviewed_calls": sum(x["reviewed_calls_met"] for x in comparisons),
              "comparisons": comparisons, "runs": runs, "full_application_soundness_proved": False,
              "interpretation": "Paired source-scope/configuration projections, not independent applications or universal configuration monotonicity."}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({k: v for k, v in report.items() if k not in {"comparisons", "runs"}}, indent=2))
    return 0 if report["implementation_stable"] and all(x["stable"] and x["reviewed_calls_met"] for x in comparisons) else 2


if __name__ == "__main__":
    raise SystemExit(main())
