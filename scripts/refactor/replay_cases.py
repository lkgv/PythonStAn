"""Replay frozen semantic gold through the public API and separate oracle.

Run from an installed checkout, for example:
    python scripts/refactor/replay_cases.py --output evidence/run.json
The JSON retains failed cases and raw oracle outputs. Exit zero means every
selected fixture passed; it does not imply arbitrary Python soundness.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
import time
import traceback

if __package__:
    from .semantic_oracle import (
        GOLD_PATH, HELDOUT_PATH, REPO, analyze_fixture, assert_analysis_gold, assert_concrete_gold,
        concrete_witness, source_fixtures, load_gold,
    )
else:
    from semantic_oracle import (
        GOLD_PATH, HELDOUT_PATH, REPO, analyze_fixture, assert_analysis_gold, assert_concrete_gold,
        concrete_witness, source_fixtures, load_gold,
    )


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine", choices=("ai_primary", "hybrid"), default="ai_primary")
    parser.add_argument("--case", action="append", dest="cases")
    parser.add_argument("--no-heldout", action="store_true")
    parser.add_argument("--supported-only", action="store_true", help="Omit required-open source boundaries for a focused diagnostic run")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    fixtures = source_fixtures(heldout=not args.no_heldout)
    if not args.supported_only:
        fixtures.extend(case for case in load_gold()["required_open_behavior_fixtures"] if case["kind"] == "source")
    if args.cases:
        known = {case["id"] for case in fixtures}
        unknown = set(args.cases) - known
        if unknown:
            parser.error("unknown fixture IDs: " + ", ".join(sorted(unknown)))
        fixtures = [case for case in fixtures if case["id"] in args.cases]
    rows = []
    for case in fixtures:
        start = time.perf_counter()
        row = {"id": case["id"], "engine": args.engine,
               "source_sha256": hashlib.sha256(case["source"].encode()).hexdigest()}
        try:
            if case.get("input_contract"):
                # These finite controlled samples demonstrate actual boundary
                # behavior; they never replace the analyzer's all-string bound.
                row["concrete_witnesses"] = [concrete_witness(case, bindings={
                    "unknown_source": text}) for text in ("None", "setattr(box, 'value', 'after')", "1 / 0")]
                row["oracle_limit"] = "Finite samples do not establish coverage of the all-string input bound"
            else:
                witness = concrete_witness(case)
                row["concrete_witness"] = witness
                assert_concrete_gold(case, witness)
            result, answer = analyze_fixture(case, engine=args.engine, validate=False)
            row.update(result=result.raw_snapshot(), answer=answer.to_dict())
            assert_analysis_gold(case, result, answer)
            row["status"] = "passed"
        except Exception as error:
            row.update(status="failed", exception=type(error).__name__, message=str(error),
                       traceback=traceback.format_exc())
        row["elapsed_seconds"] = time.perf_counter() - start
        rows.append(row)
        print(json.dumps({"id": case["id"], "status": row["status"]}), file=sys.stderr, flush=True)
    report = {
        "schema_version": 1, "python": sys.version,
        "gold_sha256": hashlib.sha256(GOLD_PATH.read_bytes()).hexdigest(),
        "heldout_sha256": hashlib.sha256(HELDOUT_PATH.read_bytes()).hexdigest(),
        "analysis_import": "installed pythonstan public API; no private-state query fallback",
        "source_root": str(REPO), "engine": args.engine,
        "scope": "Selected finite fixtures; no universal soundness claim and no project execution",
        "selected": len(rows), "passed": sum(row["status"] == "passed" for row in rows),
        "failed": sum(row["status"] != "passed" for row in rows), "cases": rows,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps({k: report[k] for k in ("selected", "passed", "failed")}))
    return 0 if report["failed"] == 0 and rows else 1


if __name__ == "__main__":
    raise SystemExit(main())
