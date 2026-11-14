"""Finite real input worlds for uncertain duck bases and special lookup."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pythonstan import Source, ValueQuery, analyze
from pythonstan.analysis.contracts.json_data import JsonObject, require_object

from .test_sem05_review3 import _member

_GOLD = Path(__file__).with_name("data") / "sem05_review3_uncertain_cases.json"
CASES: tuple[dict[str, object], ...] = tuple(json.loads(_GOLD.read_text())["cases"])


@pytest.mark.parametrize("case", CASES, ids=lambda case: str(case["id"]))
def test_uncertain_duck_worlds(case: dict[str, object]) -> None:
    options: JsonObject = {
        "engine": "ai_primary",
        "resources": {"max_work": 10000, "max_seconds": 2, "max_memory_mb": 512},
        "inputs": {"bindings": {"subject.flag": {"type": "bool", "top": True}}},
    }
    query = ValueQuery("subject", "result")
    result = analyze(Source(str(case["source"]), module="subject"), config=options, queries=[query])
    answer = result.query(query)
    row = {
        "id": case["id"],
        "expected_worlds": case["worlds"],
        "status": answer.status,
        "value": answer.value,
        "coverage": result.coverage_status,
        "solver": result.solver_status,
    }
    output = Path("artifacts/next/evidence/SEM05_REVIEW3/uncertain")
    output.mkdir(parents=True, exist_ok=True)
    (output / (str(case["id"]) + ".json")).write_text(json.dumps(row, indent=2) + "\n")
    assert answer.status == "answered", row
    value = require_object(answer.value)
    assert isinstance(case["worlds"], list)
    for expected in case["worlds"]:
        assert _member(value, expected), row
    if case["exact"]:
        assert value.get("constants") == [True], row
        assert result.coverage_status == "closed_under_profile", row
