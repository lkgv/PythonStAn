"""Reconstructed sequential-comprehension witnesses, with independent Python gold."""

from __future__ import annotations

import json
import subprocess
import sys

import pytest

from pythonstan import Source, ValueQuery, analyze

from .domain_oracles import require_members

CASES: tuple[tuple[str, str, int | str], ...] = (
    (
        "ordered",
        "log=''\ndef stamp(x):\n global log\n log=log+x\n return x\nitems=[stamp(x) for x in ['a','b']]\nresult=log\n",
        "ab",
    ),
    (
        "nested",
        "items=[(i,j) for i in [1] for j in [i]]\nresult=items[0][0]+items[0][1]+len(items)\n",
        3,
    ),
    (
        "filter",
        "log=''\ndef stamp(x):\n global log\n log=log+x\n return x!='b'\nitems=[x for x in ['a','b','c'] if stamp(x)]\nresult=log+':'+str(len(items))\n",
        "abc:2",
    ),
    ("empty", "marker=7\nitems=[x for x in []]\nresult=marker+len(items)\n", 7),
    (
        "dict_order",
        "log=''\ndef key(x):\n global log\n log=log+'k'+str(x)\n return 0\ndef val(x):\n global log\n log=log+'v'+str(x)\n return x\nitems={key(x):val(x) for x in [1,2]}\nresult=log+':'+str(items[0])\n",
        "k1v1k2v2:2",
    ),
    (
        "prefix",
        "log=''\nitems=[9]\ndef step(x):\n global log\n log=log+str(x)\n if x==2: raise ValueError()\n return x\ntry:\n items=[step(x) for x in [1,2,3]]\nexcept ValueError:\n result=log+':'+str(items[0])\n",
        "12:9",
    ),
    (
        "class_scope",
        "seed=20\nclass C:\n seed=1\n items=[seed+x for x in [seed]]\nresult=C.items[0]\n",
        21,
    ),
    (
        "closure_shadow",
        "def outer(seed):\n def inner():\n  return [seed for seed in [seed]][0]\n return inner()\nresult=outer(13)\n",
        13,
    ),
    (
        "attribute_target",
        "class Holder:\n pass\nholder=Holder()\nholder.x=0\nitems=[holder.x for holder.x in [1,2]]\nresult=holder.x*100+items[0]*10+items[1]\n",
        212,
    ),
    (
        "subscript_target",
        "state=[0]\nlog=''\ndef index():\n global log\n log=log+'i'\n return 0\nitems=[state[0] for state[index()] in [1,2]]\nresult=log+':'+str(state[0])+str(items[0])+str(items[1])\n",
        "ii:212",
    ),
    ("set_cardinality", "items={x for x in [1,1,2]}\nresult=len(items)\n", 2),
    ("walrus", "def f():\n items=[(answer:=x) for x in [4,5]]\n return answer\nresult=f()\n", 5),
    (
        "nonlocal_walrus",
        "def f():\n answer=1\n def g():\n  nonlocal answer\n  items=[(answer:=x) for x in [3,4]]\n g()\n return answer\nresult=f()\n",
        4,
    ),
)


@pytest.mark.parametrize("case,source,expected", CASES, ids=[case[0] for case in CASES])
def test_cpython_gold(case: str, source: str, expected: int | str) -> None:
    worker = "import json,sys; scope={}; exec(compile(sys.stdin.read(),'<gold>','exec'),scope); print(json.dumps(scope['result']))"
    process = subprocess.run(
        [sys.executable, "-c", worker], input=source, text=True, capture_output=True, timeout=3
    )
    assert process.returncode == 0, (case, process.stderr)
    assert json.loads(process.stdout) == expected


@pytest.mark.parametrize("case,source,expected", CASES, ids=[case[0] for case in CASES])
def test_sequential_analysis(case: str, source: str, expected: int | str) -> None:
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
    assert answer.status == "answered", (case, answer.to_dict())
    assert answer.value is not None
    require_members(answer.value, [expected])
    assert result.coverage_status == "closed_under_profile", (case, result.raw_snapshot())
