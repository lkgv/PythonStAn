"""Controlled SEM04 timing samples; results do not qualify acceptance gates."""

from __future__ import annotations

import ast
import cProfile
import hashlib
import io
import json
import pstats
import sys
import time
from pathlib import Path

from pythonstan import Source, ValueQuery, analyze
from pythonstan.analysis.contracts.json_data import JsonObject, require_object

ROOT: Path = Path(__file__).resolve().parents[2]
FIXTURE: Path = ROOT / "tests/regression/audit3/test_sem04_review_resume2.py"
CASE_ID: str = "callback_over_thirty_two_value_coverage"
SOURCE: str = (
    "state=[0]\n"
    "def remember(i):\n"
    "    state[0]=i\n"
    "    return i\n"
    "items=[remember(i) for i in range(35)]\n"
    "result=len(items)*100+state[0]\n"
)


def string_literal(node: ast.AST) -> str | None:
    if not isinstance(node, ast.Constant):
        return None
    value: object = node.value
    return value if isinstance(value, str) else None


def validate_source() -> None:
    tree = ast.parse(FIXTURE.read_text(encoding="utf-8"), filename=str(FIXTURE))
    found: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not isinstance(node.func, ast.Name) or node.func.id != "Case":
            continue
        if len(node.args) < 2 or string_literal(node.args[0]) != CASE_ID:
            continue
        source = string_literal(node.args[1])
        if source is None:
            raise RuntimeError("The selected Case source is not a string literal")
        found.append(source)
    if found != [SOURCE]:
        raise RuntimeError("Utility source differs from the selected original Case")


def run_sample(role: str, profiler: cProfile.Profile | None = None) -> JsonObject:
    config: JsonObject = {
        "engine": "ai_primary",
        "resources": {
            "max_work": 10000,
            "max_seconds": 2,
            "max_memory_mb": 512,
        },
    }
    query = ValueQuery("subject", "result")
    started = time.perf_counter()
    if profiler is not None:
        profiler.enable()
    try:
        result = analyze(
            Source(SOURCE, module="subject"),
            config=config,
            queries=(query,),
        )
        answer = result.query(query)
        row: JsonObject = {
            "role": role,
            "qualifier": False,
            "answer_status": answer.status,
            "value": answer.value,
            "solver_status": result.solver_status,
            "coverage_status": result.coverage_status,
            "ledger_stop_reason": None,
            "ledger_projection": "not_read_for_answered_result",
        }
        # Match the original test's snapshot-read condition.
        if answer.status != "answered":
            partial = result.raw_snapshot()
            ledger = require_object(partial.get("resource_ledger", {}))
            row["ledger_stop_reason"] = ledger.get("stop_reason")
            row["ledger_projection"] = "unanswered_snapshot"
            row["resource_ledger"] = ledger
            row["obligations"] = partial.get("obligations")
            row["phase_trace"] = partial.get("phase_trace")
    finally:
        if profiler is not None:
            profiler.disable()
    row["sample_wall_seconds"] = time.perf_counter() - started
    return row


def print_profile(profiler: cProfile.Profile, key: pstats.SortKey) -> None:
    output = io.StringIO()
    stats = pstats.Stats(profiler, stream=output).strip_dirs()
    stats.sort_stats(key).print_stats(25)
    print(f"\ncontrolled_instrumentation: top 25 by {key.value}")
    print(output.getvalue(), end="")


def main() -> None:
    validate_source()
    print(
        json.dumps(
            {
                "case": CASE_ID,
                "source_sha256": hashlib.sha256(SOURCE.encode("utf-8")).hexdigest(),
                "fixture": str(FIXTURE.relative_to(ROOT)),
                "profile_role": "controlled_instrumentation",
                "sampling_order": "baseline_then_profile_same_process",
                "interpretation": (
                    "Warm caches and profiler overhead affect timing; "
                    "a stopped sample profiles only its capped prefix."
                ),
                "python": sys.version,
                "separate_requests": True,
                "qualifier": False,
            },
            sort_keys=True,
        )
    )
    print(json.dumps(run_sample("uninstrumented_baseline"), sort_keys=True))

    profiler = cProfile.Profile()
    try:
        row = run_sample("controlled_instrumentation", profiler)
        print(json.dumps(row, sort_keys=True))
    finally:
        print_profile(profiler, pstats.SortKey.CUMULATIVE)
        print_profile(profiler, pstats.SortKey.TIME)


if __name__ == "__main__":
    main()
