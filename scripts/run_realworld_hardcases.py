#!/usr/bin/env python3
"""Evaluate frozen real-world hard cases; report misses rather than hiding them."""
import argparse
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from benchmark.realworld_hardcases.runner import COHORTS, ROOT, run_suite


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cohort', choices=COHORTS, action='append')
    parser.add_argument('--case', action='append')
    parser.add_argument('--timeout', type=float, default=30)
    parser.add_argument('--max-iterations', type=int, default=50000)
    parser.add_argument('--no-runtime', action='store_true')
    parser.add_argument('--allow-analysis-gaps', action='store_true', help='return zero for a valid corpus despite retained analyzer misses')
    parser.add_argument('--output', type=Path, default=ROOT/'results/latest.json')
    args=parser.parse_args()
    try:
        report=run_suite(args.cohort or COHORTS, args.case, args.timeout, args.max_iterations, not args.no_runtime)
    except (ValueError,OSError) as exc:
        parser.error(str(exc))
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(dict(output=str(args.output), totals=report['totals'],
                         runtime_validated=report['runtime_validated'], runtime_failed=report['runtime_failed'],
                         reviewed_call_gate_passed=report['reviewed_call_gate_passed'],
                         implementation_stable=report['implementation_stable']),indent=2))
    # Corpus infrastructure/semantic expectations are separate. Discovered
    # analyzer misses, errors and timeouts remain research results, not success.
    bad_harness = report['runtime_failed'] or report['source_integrity_failed'] or not report['implementation_stable']
    return 2 if bad_harness or (not args.allow_analysis_gaps and not report['reviewed_call_gate_passed']) else 0


if __name__=='__main__':
    raise SystemExit(main())
