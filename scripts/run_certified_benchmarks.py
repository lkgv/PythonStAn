#!/usr/bin/env python3
"""Run frozen certified-summary benchmarks without executing target programs."""

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from benchmark.certified.runner import run_suite


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("baseline", "candidate", "both"), default="both")
    parser.add_argument("--no-web", action="store_true", help="Exclude the eight pinned framework method extractions")
    parser.add_argument("--max-iterations", type=int, default=1000)
    parser.add_argument("--output", type=Path, help="Write the complete JSON report to this path")
    args = parser.parse_args()
    modes = ("baseline", "candidate") if args.mode == "both" else (args.mode,)
    reports = [run_suite(mode, not args.no_web, args.max_iterations) for mode in modes]
    document = {"schema_version": 1, "reports": reports}
    output = json.dumps(document, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(output)
        print(json.dumps({"output": str(args.output), "totals": [r["totals"] for r in reports]}, indent=2))
    else:
        print(output, end="")
    return 1 if any(report["totals"]["expectation_failures"] for report in reports) else 0


if __name__ == "__main__":
    raise SystemExit(main())
