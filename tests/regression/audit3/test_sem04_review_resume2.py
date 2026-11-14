"""Independent CPython and public-query comprehension witnesses after recovery."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pytest

from pythonstan import Source, ValueQuery, analyze
from pythonstan.analysis.contracts.json_data import JsonObject, require_object

from .domain_oracles import concrete_result, wire_contains


@dataclass(frozen=True)
class Case:
    identity: str
    source: str
    expected: int | str
    local_limit: int | None = None
    exact: bool = True


CASES = (
    Case(
        "class_outer_iterable_global_filter",
        "x=1\nclass C:\n    x=[2]\n    items=[i for i in x if x==1]\nresult=C.items[0]*10+len(C.items)\n",
        21,
    ),
    Case(
        "closure_target_shadow",
        "def outer(seed):\n    def inner():\n        items=[seed for seed in [seed]]\n        return items[0]\n    return inner()\nresult=outer(13)\n",
        13,
    ),
    Case(
        "attribute_target_binding",
        "class Holder:\n    pass\nholder=Holder()\nholder.x=0\nitems=[holder.x for holder.x in [1,2]]\nresult=holder.x*100+items[0]*10+len(items)\n",
        212,
    ),
    Case(
        "subscript_target_index_effects",
        "log=''\nstate=[0]\ndef position():\n    global log\n    log+='i'\n    return 0\nitems=[state[0] for state[position()] in [2,7]]\nresult=log+':'+str(len(items))+str(items[1])+str(state[0])\n",
        "ii:277",
    ),
    Case(
        "second_iteration_exception_prefix",
        "log=''\nprevious='old'\ndef tick(n):\n    global log\n    log+=str(n)\n    if n==2:\n        raise ValueError\n    return n\ntry:\n    previous=[tick(i) for i in [1,2,3]]\nexcept ValueError:\n    pass\nresult=log+':'+previous\n",
        "12:old",
    ),
    Case(
        "false_filter_preserves_latest_state",
        "state=[0]\ndef gate(i):\n    state[0]+=i\n    return False\nitems=[i for i in [1,2,3] if gate(i)]\nresult=state[0]*10+len(items)\n",
        60,
    ),
    Case(
        "dictionary_key_value_order_and_overwrite",
        "log=''\ndef key(i):\n    global log\n    log+='k'+str(i)\n    return 0\ndef val(i):\n    global log\n    log+='v'+str(i)\n    return i\nitems={key(i):val(i) for i in [1,2]}\nresult=log+':'+str(items[0])\n",
        "k1v1k2v2:2",
    ),
    Case(
        "class_later_iterable_uses_global_scope",
        "data=[6]\nclass C:\n    data=[2]\n    items=[10*i+j for i in data for j in data]\nresult=C.items[0]\n",
        26,
    ),
    Case(
        "callback_residual_value_coverage",
        "state=[0]\ndef add(i):\n    state[0]+=i\n    return i\nitems=[add(i) for i in [1,2,3]]\nresult=state[0]\n",
        6,
        local_limit=1,
        exact=False,
    ),
    Case(
        "callback_over_thirty_two_value_coverage",
        "state=[0]\ndef remember(i):\n    state[0]=i\n    return i\nitems=[remember(i) for i in range(35)]\nresult=len(items)*100+state[0]\n",
        3534,
        exact=False,
    ),
    Case(
        "direct_target_residual_effect_coverage",
        "state=[0]\nitems=[state[0] for state[0] in [1,2,3]]\nresult=state[0]\n",
        3,
        local_limit=1,
        exact=False,
    ),
    Case(
        "direct_target_over_thirty_two",
        "state=[0]\nitems=[state[0] for state[0] in range(35)]\nresult=state[0]*100+items[34]\n",
        3434,
        exact=False,
    ),
)


def run_case(case: Case) -> JsonObject:
    config: JsonObject = {
        "engine": "ai_primary",
        "resources": {"max_work": 10000, "max_seconds": 2, "max_memory_mb": 512},
    }
    if case.local_limit is not None:
        config["abstract"] = {"max_stmt_visits": case.local_limit}
    query = ValueQuery("subject", "result")
    result = analyze(Source(case.source, module="subject"), config=config, queries=(query,))
    answer = result.query(query)
    row: JsonObject = {
        "id": case.identity,
        "expected": case.expected,
        "answer_status": answer.status,
        "value": answer.value,
        "solver_status": result.solver_status,
        "coverage_status": result.coverage_status,
    }
    if answer.status != "answered":
        partial = result.raw_snapshot()
        row["obligations"] = partial.get("obligations", [])
        row["phase_trace"] = partial.get("phase_trace", [])
        row["resource_ledger"] = partial.get("resource_ledger")
        stats = require_object(partial.get("stats", {}))
        row["work_statistics"] = {
            name: stats.get(name)
            for name in (
                "work_by_kind",
                "unit_evaluations",
                "ast_work",
                "entry_growth",
                "trace_merges",
                "total_work",
                "elapsed_seconds",
                "pending_units",
                "work_scope",
            )
        }
    root = Path("artifacts/next/evidence/SEM_PTA_REVIEW_RESUME2")
    root.mkdir(parents=True, exist_ok=True)
    (root / ("sem-" + case.identity + ".json")).write_text(json.dumps(row, indent=2) + "\n")
    return row


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.identity)
def test_independent_cpython_gold(case: Case) -> None:
    assert concrete_result(case.source) == case.expected


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.identity)
def test_public_comprehension_value(case: Case) -> None:
    row = run_case(case)
    assert row["answer_status"] == "answered", json.dumps(row, sort_keys=True)
    value = require_object(row["value"])
    assert wire_contains(value, case.expected), json.dumps(row, sort_keys=True)
    if case.exact:
        assert not value.get("top") and not value.get("top_sorts"), json.dumps(row, sort_keys=True)
        assert value["constants"] == [case.expected], json.dumps(row, sort_keys=True)
        assert row["coverage_status"] == "closed_under_profile", json.dumps(row, sort_keys=True)
