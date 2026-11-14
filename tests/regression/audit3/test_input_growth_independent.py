"""Concrete growth witnesses for cyclic and acyclic summary feedback."""

from __future__ import annotations

import json
from dataclasses import dataclass

import pytest

from pythonstan import Source, ValueQuery, analyze
from pythonstan.analysis.contracts.json_data import (
    require_array,
    require_int,
    require_object,
    require_string,
)


@dataclass(frozen=True, slots=True)
class GrowthCase:
    identity: str
    source: str
    expected: int


CASES: tuple[GrowthCase, ...] = (
    GrowthCase(
        "recursive_growing_input",
        "def advance(value):\n"
        "    if value >= 20:\n"
        "        return value\n"
        "    return advance(value + 1)\n"
        "result = advance(0)\n",
        20,
    ),
    GrowthCase(
        "acyclic_global_feedback",
        "g = 0\n"
        "def inc():\n"
        "    global g\n"
        "    g = g + 1\n"
        "    return g\n"
        "a = inc()\n"
        "result = inc()\n",
        2,
    ),
)


def case_id(case: GrowthCase) -> str:
    return case.identity


@pytest.mark.parametrize("case", CASES, ids=case_id)
def test_independent_concrete_growth_gold(case: GrowthCase) -> None:
    namespace: dict[str, object] = {}
    exec(compile(case.source, "<independent-input-growth>", "exec"), namespace)
    assert type(namespace["result"]) is int
    assert namespace["result"] == case.expected


@pytest.mark.parametrize("case", CASES, ids=case_id)
def test_summary_growth_converges_and_covers_concrete_value(case: GrowthCase) -> None:
    query = ValueQuery("subject", "result", "module_exit")
    result = analyze(
        Source(case.source, module="subject"),
        config={
            "engine": "ai_primary",
            "abstract": {"context_depth": 0},
            "resources": {"max_work": 10000, "max_seconds": 2, "max_memory_mb": 512},
        },
        queries=[query],
    )
    answer = result.query(query)
    public = answer.to_dict()
    detail = json.dumps(public, sort_keys=True)
    assert result.solver_status == "converged", detail
    assert answer.status == "answered", detail
    assert answer.coverage_status == "closed_under_profile", detail
    value = require_object(public["value"])
    assert not value["top"] and not value["top_sorts"], detail
    assert not value["bottom"] and not value["may_unbound"], detail
    constants = [require_object(row) for row in require_array(value["tagged_constants"])]
    if any(row.get("type") == "int" and row.get("value") == case.expected for row in constants):
        return
    interval = require_object(value["integer_interval"])
    lower, upper = interval["lower"], interval["upper"]
    assert lower is None or require_int(lower) <= case.expected, detail
    assert upper is None or require_int(upper) >= case.expected, detail
    parity = require_string(interval["parity"])
    assert parity in {"top", "odd", "even"}, detail
    assert parity == "top" or (case.expected % 2 == 1) == (parity == "odd"), detail
