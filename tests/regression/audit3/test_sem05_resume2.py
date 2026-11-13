"""ClassInfo ordered matching with independently frozen Python 3.12 gold."""

from __future__ import annotations

import json
import subprocess
import sys

import pytest

from pythonstan import Source, ValueQuery, analyze

from .domain_oracles import require_members

CASES: tuple[tuple[str, str, bool | str], ...] = (
    ("builtin_list", "result=isinstance([],list)\n", True),
    ("bool_int", "result=isinstance(True,int) and not isinstance(1,bool)\n", True),
    ("none_object", "result=isinstance(None,object)\n", True),
    ("class_object_type", "result=isinstance(int,type)\n", True),
    ("source_inheritance", "class A: pass\nclass B(A): pass\nresult=isinstance(B(),A)\n", True),
    ("source_subclass", "class A: pass\nclass B(A): pass\nresult=issubclass(B,A)\n", True),
    ("nested_early_match", "result=isinstance(1,(str,((int,42),)))\n", True),
    ("early_match", "result=isinstance(1,(int,42))\n", True),
    (
        "invalid_tail",
        "try:\n isinstance('x',(int,42))\n result='wrong'\nexcept TypeError:\n result='caught'\n",
        "caught",
    ),
    (
        "invalid_prefix",
        "try:\n isinstance(1,(42,int))\n result='wrong'\nexcept TypeError:\n result='caught'\n",
        "caught",
    ),
    ("empty_tuple", "result=isinstance(1,())\n", False),
    ("nested_empty", "result=isinstance(1,((),int))\n", True),
    ("invalid_subclass_empty_tuple", "result=issubclass(1,())\n", False),
    (
        "invalid_subclass",
        "try:\n issubclass(1,int)\n result='wrong'\nexcept TypeError:\n result='caught'\n",
        "caught",
    ),
    (
        "list_not_classinfo",
        "try:\n isinstance(1,[int])\n result='wrong'\nexcept TypeError:\n result='caught'\n",
        "caught",
    ),
    (
        "function_not_classinfo",
        "try:\n isinstance(1,len)\n result='wrong'\nexcept TypeError:\n result='caught'\n",
        "caught",
    ),
    ("exception_hierarchy", "result=isinstance(ValueError(),Exception)\n", True),
    ("builtin_subclass_chain", "result=issubclass(bool,object)\n", True),
    ("source_tuple", "class A: pass\nclass B(A): pass\nresult=isinstance(B(),(A,42))\n", True),
    ("function_is_object", "result=isinstance(lambda:None,object)\n", True),
    (
        "exact_class_skips_property",
        "class C:\n @property\n def __class__(self):\n  raise ValueError()\nresult=isinstance(C(),C)\n",
        True,
    ),
)

PROTOCOLS: tuple[tuple[str, str], ...] = (
    (
        "metaclass",
        "log=''\nclass M(type):\n def __instancecheck__(cls,obj):\n  global log\n  log=log+'m'\n  return True\nclass C(metaclass=M): pass\nresult=isinstance(1,C)\n",
    ),
    (
        "class_property",
        "log=''\nclass C:\n @property\n def __class__(self):\n  global log\n  log=log+'c'\n  return int\nresult=isinstance(C(),int)\n",
    ),
)


def concrete(source: str) -> object:
    worker = "import json,sys; scope={}; exec(compile(sys.stdin.read(),'<gold>','exec'),scope); print(json.dumps([scope['result'],scope.get('log')]))"
    process = subprocess.run(
        [sys.executable, "-c", worker], input=source, text=True, capture_output=True, timeout=3
    )
    assert process.returncode == 0, process.stderr
    return json.loads(process.stdout)


@pytest.mark.parametrize("case,source,expected", CASES, ids=[item[0] for item in CASES])
def test_cpython_gold(case: str, source: str, expected: bool | str) -> None:
    assert concrete(source) == [expected, None], case


@pytest.mark.parametrize("case,source,expected", CASES, ids=[item[0] for item in CASES])
def test_classinfo_analysis(case: str, source: str, expected: bool | str) -> None:
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
    assert not answer.value["top"]
    assert not answer.value["top_sorts"]
    assert answer.value["tagged_constants"] == [
        {"type": "bool" if isinstance(expected, bool) else "str", "value": expected}
    ], (case, answer.to_dict())
    assert result.coverage_status == "closed_under_profile", (case, result.raw_snapshot())


@pytest.mark.parametrize("case,source", PROTOCOLS, ids=[item[0] for item in PROTOCOLS])
def test_protocol_cpython_gold(case: str, source: str) -> None:
    assert concrete(source) == [True, "m" if case == "metaclass" else "c"]


@pytest.mark.parametrize("case,source", PROTOCOLS, ids=[item[0] for item in PROTOCOLS])
def test_unsupported_protocol_retains_effects(case: str, source: str) -> None:
    value_query = ValueQuery("subject", "result")
    log_query = ValueQuery("subject", "log")
    result = analyze(
        Source(source, module="subject"),
        config={
            "engine": "ai_primary",
            "resources": {"max_work": 10000, "max_seconds": 2, "max_memory_mb": 512},
        },
        queries=[value_query, log_query, "obligations"],
    )
    answer, log = result.query(value_query), result.query(log_query)
    assert answer.status == "answered" and answer.value is not None
    require_members(answer.value, [True])
    assert log.status == "answered" and log.value is not None
    require_members(log.value, ["m" if case == "metaclass" else "c"])
    assert result.coverage_status == "open"
