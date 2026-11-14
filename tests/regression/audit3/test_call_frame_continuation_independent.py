"""Independent call-frame witnesses and shared-state controls."""

from __future__ import annotations

import json
from dataclasses import dataclass

import pytest

from pythonstan import Source, ValueQuery, analyze
from pythonstan.analysis.contracts.json_data import require_array, require_object


@dataclass(frozen=True, slots=True)
class Case:
    identity: str
    source: str
    expected: int


CASES: tuple[Case, ...] = (
    Case(
        "recursive_private_after_call",
        "def f(flag):\n"
        "    local = 17\n"
        "    if flag:\n"
        "        f(False)\n"
        "        return local\n"
        "    else:\n"
        "        local = 19\n"
        "        return 0\n"
        "result = f(True)\n",
        17,
    ),
    Case(
        "recursive_private_and_shared_global",
        "g = 0\n"
        "def f(flag):\n"
        "    global g\n"
        "    local = 17\n"
        "    if flag:\n"
        "        f(False)\n"
        "        return local + g\n"
        "    else:\n"
        "        local = 19\n"
        "        g = 23\n"
        "        return 0\n"
        "result = f(True)\n",
        40,
    ),
    Case(
        "nonrecursive_private_control",
        "def leaf():\n"
        "    local = 19\n"
        "    return 0\n"
        "def outer():\n"
        "    local = 17\n"
        "    leaf()\n"
        "    return local\n"
        "result = outer()\n",
        17,
    ),
    Case(
        "global_writeback_control",
        "g = 0\n"
        "def mutate():\n"
        "    global g\n"
        "    g = 23\n"
        "mutate()\n"
        "result = g\n",
        23,
    ),
    Case(
        "escaping_parameter_cell_control",
        "def make(value):\n"
        "    def read():\n"
        "        return value\n"
        "    return read\n"
        "saved = make(17)\n"
        "result = saved()\n",
        17,
    ),
    Case(
        "recursive_escaping_cell_control",
        "saved = None\n"
        "def f(flag):\n"
        "    global saved\n"
        "    x = 17\n"
        "    if flag:\n"
        "        f(False)\n"
        "        return x + saved()\n"
        "    x = 19\n"
        "    def read():\n"
        "        return x\n"
        "    saved = read\n"
        "    return 0\n"
        "result = f(True)\n",
        36,
    ),
    Case(
        "nonlocal_writeback_control",
        "def outer():\n"
        "    x = 17\n"
        "    def setx():\n"
        "        nonlocal x\n"
        "        x = 19\n"
        "    setx()\n"
        "    return x\n"
        "result = outer()\n",
        19,
    ),
    Case(
        "recursive_reference_alias_control",
        "left = [17]\n"
        "right = [19]\n"
        "def f(flag):\n"
        "    local = left\n"
        "    if flag:\n"
        "        f(False)\n"
        "        return local[0]\n"
        "    local = right\n"
        "    return 0\n"
        "result = f(True)\n",
        17,
    ),
    Case(
        "recursive_reference_heap_write_control",
        "left = [17]\n"
        "right = [19]\n"
        "def f(flag):\n"
        "    local = left\n"
        "    if flag:\n"
        "        f(False)\n"
        "        return local[0] + left[0]\n"
        "    local = right\n"
        "    left[0] = 23\n"
        "    return 0\n"
        "result = f(True)\n",
        46,
    ),
    Case(
        "repeated_parameter_closure_control",
        "def make(value):\n"
        "    def read():\n"
        "        return value\n"
        "    return read\n"
        "a = make(17)\n"
        "b = make(19)\n"
        "result = b()\n",
        19,
    ),
    Case(
        "wrapped_parameter_closure_control",
        "def make(value):\n"
        "    def read():\n"
        "        return value\n"
        "    return read\n"
        "def wrap(value):\n"
        "    return make(value)\n"
        "a = wrap(17)\n"
        "b = wrap(19)\n"
        "result = b()\n",
        19,
    ),
    Case(
        "finite_two_wrappers_late19",
        "def leaf(value):\n"
        "    return value\n"
        "def inner(value):\n"
        "    return leaf(value)\n"
        "def outer(value):\n"
        "    return inner(value)\n"
        "a = outer(17)\n"
        "result = outer(19)\n",
        19,
    ),
    Case(
        "shared_callee_two_callers",
        "def shared(value):\n"
        "    return value\n"
        "def left():\n"
        "    return shared(17)\n"
        "def right():\n"
        "    return shared(19)\n"
        "a = left()\n"
        "result = right()\n",
        19,
    ),
)


def case_id(case: Case) -> str:
    return case.identity


@pytest.mark.parametrize("case", CASES, ids=case_id)
def test_independent_concrete_call_frame_gold(case: Case) -> None:
    namespace: dict[str, object] = {}
    exec(compile(case.source, "<independent-call-frame>", "exec"), namespace)
    assert type(namespace["result"]) is int
    assert namespace["result"] == case.expected


@pytest.mark.parametrize("case", CASES, ids=case_id)
def test_public_continuation_keeps_finite_concrete_return(case: Case) -> None:
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
    assert result.solver_status == "converged", public
    assert answer.status == "answered", public
    assert answer.coverage_status == "closed_under_profile", public
    value = require_object(public["value"])
    assert not value["top"] and not value["top_sorts"], public
    assert not value["bottom"] and not value["may_unbound"], public
    constants = [require_object(row) for row in require_array(value["tagged_constants"])]
    assert any(
        row.get("type") == "int" and row.get("value") == case.expected for row in constants
    ), json.dumps(public, sort_keys=True)
