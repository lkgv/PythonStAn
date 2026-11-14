"""ClassInfo distinguishes mapping items, own attributes and delegated ones."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pythonstan import Source, ValueQuery, analyze
from pythonstan.analysis.contracts.json_data import require_object

from .test_sem05_review3 import _member

_GOLD = Path(__file__).with_name("data") / "sem05_review3_namespace_cases.json"
CASES: tuple[dict[str, object], ...] = tuple(json.loads(_GOLD.read_text())["cases"])


@pytest.mark.parametrize("case", CASES, ids=lambda case: str(case["id"]))
def test_classinfo_attribute_namespace(case: dict[str, object]) -> None:
    query = ValueQuery("subject", "result")
    result = analyze(
        Source(str(case["source"]), module="subject"),
        config={
            "engine": "ai_primary",
            "resources": {"max_work": 10000, "max_seconds": 2, "max_memory_mb": 512},
        },
        queries=[query],
    )
    answer = result.query(query)
    row = {
        "id": case["id"],
        "expected": case["expected"],
        "status": answer.status,
        "value": answer.value,
        "solver": result.solver_status,
        "coverage": result.coverage_status,
        "obligations": result.raw_snapshot().get("obligations", []),
    }
    output = Path("artifacts/next/evidence/SEM05_REVIEW3/namespace-before")
    output.mkdir(parents=True, exist_ok=True)
    (output / (str(case["id"]) + ".json")).write_text(json.dumps(row, indent=2) + "\n")
    assert answer.status == "answered", row
    value = require_object(answer.value)
    assert _member(value, case["expected"]), row
    if case["mode"] == "exact":
        assert value.get("constants") == [case["expected"]], row
        assert result.coverage_status == "closed_under_profile", row
