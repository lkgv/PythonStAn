#!/usr/bin/env python3
"""Reuse already measured, same-core application scenarios without rerunning timeouts."""
from collections import Counter
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.run_analysis_evaluation import fingerprint
from benchmark.round7_apps.runner import score

evidence = ROOT / "docs/certified_analysis/evidence"
sources = ["round7_apps_first_party.json", "round7_project_root_policy_probe.json"]
reports = [json.loads((evidence / name).read_text()) for name in sources]
for report in reports:
    assert report["implementation_stable"] and report["manifest_stable"]
    assert report["implementation_sha256_before"] == report["implementation_sha256_after"] == fingerprint()
assert reports[0]["manifests"] == reports[1]["manifests"]
result = reports[0]
result["rows"] = [row for report in reports for row in report["rows"]]
assert len(result["rows"]) == len({row["case"]["id"] for row in result["rows"]}) == 3
result["analysis_scenarios"] = 3
result["evidence_reused_without_new_execution"] = sources
result["reuse_reason"] = "The dependency stress case already has bounded same-core results; its incomplete/timeout outcomes are retained verbatim."
for mode in ("baseline", "current"):
    calls = [score(row["case"], row[mode]) for row in result["rows"]]
    result["totals"][mode] = {
        "analysis_scenarios": len(calls), "statuses": dict(Counter(row[mode]["status"] for row in result["rows"])),
        "required_pairs": sum(x["required_pairs"] for x in calls), "recovered_pairs": sum(x["recovered_pairs"] for x in calls),
        "negative_pairs": sum(x["negative_pairs"] for x in calls), "violated_negatives": sum(len(x["violated_negatives"]) for x in calls),
        "unmeasured_required_pairs": sum(x["unmeasured_required_pairs"] for x in calls),
        "unmeasured_negative_pairs": sum(x["unmeasured_negative_pairs"] for x in calls),
        "scenarios_meeting_all_reviewed_calls": sum(x["all_reviewed_calls_met"] for x in calls),
    }
(evidence / "round7_apps.json").write_text(json.dumps(result, indent=2) + "\n")
print(json.dumps(result["totals"], indent=2))
