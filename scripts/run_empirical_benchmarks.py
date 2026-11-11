#!/usr/bin/env python3
"""Run actual KCFA/AI configurations on source-reviewed web callback scenarios."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from benchmark.empirical.runner import ROOT, run_suite


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--timeout-seconds",type=float,default=30)
    parser.add_argument("--max-iterations",type=int,default=50000)
    parser.add_argument("--context-policy",default="2-cfa")
    parser.add_argument("--no-index-sensitive",action="store_true")
    parser.add_argument("--no-runtime",action="store_true")
    parser.add_argument("--holdouts",action="store_true",help="Run the separate frozen holdout manifest")
    parser.add_argument("--case",action="append",dest="case_ids")
    parser.add_argument("--output",type=Path)
    args = parser.parse_args()
    report = run_suite(timeout_seconds=args.timeout_seconds,max_iterations=args.max_iterations,case_ids=args.case_ids,context_policy=args.context_policy,index_sensitive=not args.no_index_sensitive,include_runtime=not args.no_runtime,manifest_path=ROOT/"holdouts/manifest.json" if args.holdouts else None)
    if args.output:
        args.output.parent.mkdir(parents=True,exist_ok=True)
        args.output.write_text(json.dumps(report,indent=2)+"\n")
        print(json.dumps({"output":str(args.output),"totals":report["totals"]},indent=2))
    else:
        print(json.dumps(report,indent=2))
    # Known semantic misses are measured results. Infrastructure failures fail CI.
    return int(not report["harness_validation"]["passed"] or any("integrity_error" in row or any(row.get(mode,{}).get("status") in {"error","worker_error","timeout"} for mode in ("baseline","current","certified")) for row in report["rows"]))


if __name__ == "__main__":
    raise SystemExit(main())
