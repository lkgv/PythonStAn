"""Real duck-class __bases__ witnesses, CPython gold frozen before analysis."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pythonstan import Source, ValueQuery, analyze
from pythonstan.analysis.contracts.json_data import require_object

from .domain_oracles import wire_contains

CASES: tuple[tuple[str, str, bool], ...] = (
    (
        "instance_owned_bases_subject",
        "class Proxy: pass\np=Proxy()\np.__bases__=(int,)\nresult=issubclass(p,int)\n",
        True,
    ),
    (
        "function_owned_bases_subject",
        "def proxy(): pass\nproxy.__bases__=(int,)\nresult=issubclass(proxy,int)\n",
        True,
    ),
    (
        "instance_owned_bases_classinfo",
        "class Proxy: pass\np=Proxy()\np.__bases__=(int,)\nresult=issubclass(bool,p)\n",
        False,
    ),
    (
        "function_owned_bases_classinfo",
        "def proxy(): pass\nproxy.__bases__=(int,)\nresult=issubclass(bool,proxy)\n",
        False,
    ),
    (
        "instance_empty_bases_subject",
        "class Proxy: pass\np=Proxy()\np.__bases__=()\nresult=issubclass(p,int)\n",
        False,
    ),
    (
        "function_same_duck_identity",
        "def proxy(): pass\nproxy.__bases__=(int,)\nresult=issubclass(proxy,proxy)\n",
        True,
    ),
)


@pytest.mark.parametrize("identity,source,expected", CASES, ids=[case[0] for case in CASES])
def test_assigned_duck_bases_cannot_be_closed_typeerror(
    identity: str, source: str, expected: bool
) -> None:
    query = ValueQuery("subject", "result")
    result = analyze(
        Source(source, module="subject"),
        config={
            "engine": "ai_primary",
            "resources": {"max_work": 10000, "max_seconds": 2, "max_memory_mb": 512},
        },
        queries=[query],
    )
    answer = result.query(query)
    raw = result.raw_snapshot()
    row = {
        "id": identity,
        "expected": expected,
        "status": answer.status,
        "value": answer.value,
        "solver": result.solver_status,
        "coverage": result.coverage_status,
        "obligations": raw.get("obligations", []),
    }
    evidence = Path("artifacts/next/evidence/SEM05_REVIEW_RESUME2")
    evidence.mkdir(parents=True, exist_ok=True)
    (evidence / (identity + ".json")).write_text(json.dumps(row, indent=2) + "\n")
    assert answer.status == "answered", row
    value = require_object(answer.value)
    assert (
        value.get("top") or "bool" in value.get("top_sorts", []) or wire_contains(value, expected)
    ), row
