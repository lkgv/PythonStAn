"""Independently preregistered Python 3.12 duck-class protocol checks."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from pythonstan import Source, ValueQuery, analyze
from pythonstan.analysis.contracts.json_data import JsonObject, require_object

from .domain_oracles import wire_contains

_GOLD = Path(__file__).with_name("data") / "sem05_review3_cases.json"
CASES: tuple[dict[str, object], ...] = tuple(json.loads(_GOLD.read_text())["cases"])
_EVIDENCE = Path("artifacts/next/evidence/SEM05_REVIEW3")


def _member(value: JsonObject, expected: object) -> bool:
    kind = "bool" if type(expected) is bool else "str"
    assert isinstance(expected, (bool, str))
    return (
        bool(value.get("top"))
        or kind in value.get("top_sorts", [])
        or wire_contains(value, expected)
    )


@pytest.mark.parametrize("case", CASES, ids=lambda case: str(case["id"]))
def test_preregistered_cpython_gold(case: dict[str, object]) -> None:
    program = (
        "import json,sys;scope={};"
        "exec(compile(sys.stdin.read(),'<review3-gold>','exec'),scope);"
        "print(json.dumps([scope['result'],scope.get('log')]))"
    )
    completed = subprocess.run(
        [sys.executable, "-I", "-c", program],
        input=str(case["source"]),
        text=True,
        capture_output=True,
        timeout=2,
        check=True,
    )
    assert json.loads(completed.stdout) == [case["expected"], case["expected_log"]]


@pytest.mark.parametrize("case", CASES, ids=lambda case: str(case["id"]))
def test_preregistered_duck_protocol(case: dict[str, object]) -> None:
    options: JsonObject = {
        "engine": "ai_primary",
        "resources": {"max_work": 10000, "max_seconds": 2, "max_memory_mb": 512},
    }
    if case["context_zero"]:
        options["abstract"] = {"context_depth": 0}
    query = ValueQuery("subject", "result")
    log_query = ValueQuery("subject", "log")
    queried = [query, log_query] if case["expected_log"] is not None else [query]
    result = analyze(Source(str(case["source"]), module="subject"), config=options, queries=queried)
    answer = result.query(query)
    row: JsonObject = {
        "id": str(case["id"]),
        "answer_status": answer.status,
        "value": answer.value,
        "solver_status": result.solver_status,
        "coverage_status": result.coverage_status,
    }
    if case["expected_log"] is not None:
        log_answer = result.query(log_query)
        row["log_status"] = log_answer.status
        row["log_value"] = log_answer.value
    if case["mode"] != "exact" or answer.status != "answered":
        row["obligations"] = result.raw_snapshot().get("obligations", [])
    _EVIDENCE.mkdir(parents=True, exist_ok=True)
    (_EVIDENCE / (str(case["id"]) + ".json")).write_text(json.dumps(row, indent=2) + "\n")
    assert answer.status == "answered", row
    value = require_object(answer.value)
    assert _member(value, case["expected"]), row
    if case["mode"] == "exact":
        assert value.get("constants") == [case["expected"]], row
        assert not value.get("top") and not value.get("top_sorts"), row
        assert result.coverage_status == "closed_under_profile", row
    if case["mode"] == "open":
        assert result.coverage_status == "open" and row["obligations"], row
    if case["expected_log"] is not None:
        assert row["log_status"] == "answered", row
        assert _member(require_object(row["log_value"]), case["expected_log"]), row
