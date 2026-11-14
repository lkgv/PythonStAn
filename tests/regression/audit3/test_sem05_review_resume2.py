"""Independent Python 3.12 ClassInfo and capability witnesses."""

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

from pythonstan import Source, ValueQuery, analyze
from pythonstan.analysis.contracts.json_data import JsonObject, require_object

from .domain_oracles import wire_contains


@dataclass(frozen=True)
class Case:
    identity: str
    source: str
    expected: bool | str
    mode: str = "exact"
    log: str | None = None
    context_zero: bool = False


CASES = (
    Case("nested_empty_invalid_subclass_subject", "result=issubclass(42,((),(() ,)))\n", False),
    Case("empty_classinfo_tuple_instance", "result=isinstance(1,((),()))\n", False),
    Case(
        "nested_match_skips_invalid_tail", "result=isinstance(1,(((),(str,(int,42))),None))\n", True
    ),
    Case(
        "invalid_head_preempts_later_match",
        "try:\n result=isinstance(1,((42,),int))\nexcept TypeError:\n result='caught'\n",
        "caught",
    ),
    Case(
        "subclass_invalid_subject_after_empty",
        "try:\n result=issubclass(42,((),int))\nexcept TypeError:\n result='caught'\n",
        "caught",
    ),
    Case(
        "subclass_nested_match_skips_invalid", "result=issubclass(bool,((str,()),(int,42)))\n", True
    ),
    Case(
        "nonclass_list_arg_raises",
        "try:\n result=isinstance(1,((),[int]))\nexcept TypeError:\n result='caught'\n",
        "caught",
    ),
    Case(
        "argument_evaluation_before_lazy_match",
        "log=''\ndef tail():\n global log\n log+='t'\n return 42\nmatched=isinstance(1,(int,tail()))\nresult=log+('T' if matched else 'F')\n",
        "tT",
        log="t",
    ),
    Case(
        "nominal_diamond_source_mro",
        "class A: pass\nclass B(A): pass\nclass C(A): pass\nclass D(B,C): pass\nresult=isinstance(D(),C) and issubclass(D,A) and not issubclass(A,D)\n",
        True,
    ),
    Case(
        "bool_int_nominal_direction",
        "result=issubclass(bool,int) and not issubclass(int,bool) and isinstance(False,int)\n",
        True,
    ),
    Case(
        "exception_class_nested_match",
        "result=isinstance(ValueError(),((),(KeyError,Exception),42)) and issubclass(ValueError,BaseException)\n",
        True,
    ),
    Case(
        "source_exception_subclass",
        "class E(ValueError): pass\nresult=isinstance(E(),ValueError) and issubclass(E,Exception)\n",
        True,
    ),
    Case(
        "object_fastpath_skips_class_property",
        "log=''\nclass C:\n @property\n def __class__(self):\n  global log\n  log+='c'\n  raise ValueError\nresult=isinstance(C(),object)\n",
        True,
        log="",
    ),
    Case(
        "class_property_capability",
        "log=''\nclass C:\n @property\n def __class__(self):\n  global log\n  log+='c'\n  return int\nresult=isinstance(C(),int)\n",
        True,
        mode="open",
        log="c",
    ),
    Case(
        "class_getattribute_capability",
        "log=''\nclass C:\n def __getattribute__(self,name):\n  global log\n  if name=='__class__':\n   log+='g'\n   return int\n  return object.__getattribute__(self,name)\nresult=isinstance(C(),int)\n",
        True,
        mode="open",
        log="g",
    ),
    Case(
        "metaclass_can_reject_nominal_subclass",
        "log=''\nclass M(type):\n def __instancecheck__(cls,obj):\n  global log\n  log+='m'\n  return False\nclass A(metaclass=M): pass\nclass B(A): pass\nresult=isinstance(B(),A)\n",
        False,
        mode="open",
        log="m",
    ),
    Case(
        "duck_classinfo_instancecheck",
        "log=''\nclass Check:\n def __instancecheck__(self,obj):\n  global log\n  log+='d'\n  return True\nresult=isinstance(1,Check())\n",
        True,
        mode="open",
        log="d",
    ),
    Case(
        "duck_classinfo_subclasscheck",
        "log=''\nclass Check:\n def __subclasscheck__(self,obj):\n  global log\n  log+='s'\n  return False\nresult=issubclass(int,Check())\n",
        False,
        mode="open",
        log="s",
    ),
    Case(
        "lazy_match_skips_unsupported_hook_tail",
        "log=''\nclass Check:\n def __instancecheck__(self,obj):\n  global log\n  log+='d'\n  return False\nmatched=isinstance(1,(int,Check()))\nresult='yes:'+log if matched else 'no'\n",
        "yes:",
        log="",
    ),
    Case(
        "class_factory_summary_instance",
        "def make():\n class C: pass\n return C\nA=make()\nB=make()\nresult=isinstance(A(),B)\n",
        False,
        mode="cover",
        context_zero=True,
    ),
    Case(
        "class_factory_summary_subclass",
        "def make():\n class C: pass\n return C\nA=make()\nB=make()\nresult=issubclass(A,B)\n",
        False,
        mode="cover",
        context_zero=True,
    ),
    Case(
        "class_factory_varying_bases",
        "class A: pass\nclass B: pass\ndef make(base):\n class C(base): pass\n return C\nD=make(A)\nE=make(B)\nresult=issubclass(E,A)\n",
        False,
        mode="cover",
        context_zero=True,
    ),
    Case("unsupported_union_not_false_closed", "result=isinstance(1,int|str)\n", True, mode="open"),
)


def member(value: JsonObject, expected: bool | str) -> bool:
    kind = "bool" if isinstance(expected, bool) else "str"
    return (
        bool(value.get("top"))
        or kind in value.get("top_sorts", [])
        or wire_contains(value, expected)
    )


def concrete(case: Case) -> list[object]:
    program = "import json,sys;scope={};exec(compile(sys.stdin.read(),'<gold>','exec'),scope);print(json.dumps([scope['result'],scope.get('log')]))"
    process = subprocess.run(
        [sys.executable, "-I", "-c", program],
        input=case.source,
        text=True,
        capture_output=True,
        timeout=2,
        check=True,
    )
    return json.loads(process.stdout)


def run_case(case: Case) -> JsonObject:
    options: JsonObject = {
        "engine": "ai_primary",
        "resources": {"max_work": 10000, "max_seconds": 2, "max_memory_mb": 512},
    }
    if case.context_zero:
        options["abstract"] = {"context_depth": 0}
    query = ValueQuery("subject", "result")
    log_query = ValueQuery("subject", "log")
    result = analyze(
        Source(case.source, module="subject"),
        config=options,
        queries=[query, log_query] if case.log is not None else [query],
    )
    answer = result.query(query)
    row: JsonObject = {
        "id": case.identity,
        "expected": case.expected,
        "mode": case.mode,
        "status": answer.status,
        "value": answer.value,
        "coverage": result.coverage_status,
        "solver": result.solver_status,
    }
    if case.log is not None:
        log = result.query(log_query)
        row["log_status"] = log.status
        row["log_value"] = log.value
    if case.mode != "exact" or answer.status != "answered":
        raw = result.raw_snapshot()
        row["obligations"] = raw.get("obligations", [])
    target = Path("artifacts/next/evidence/SEM05_REVIEW_RESUME2")
    target.mkdir(parents=True, exist_ok=True)
    (target / (case.identity + ".json")).write_text(json.dumps(row, indent=2) + "\n")
    return row


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.identity)
def test_independent_cpython_gold(case: Case) -> None:
    assert concrete(case) == [case.expected, case.log]


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.identity)
def test_source_classinfo_and_capabilities(case: Case) -> None:
    row = run_case(case)
    assert row["status"] == "answered", row
    value = require_object(row["value"])
    assert member(value, case.expected), row
    if case.mode == "exact":
        assert value["tagged_constants"] == [
            {"type": "bool" if isinstance(case.expected, bool) else "str", "value": case.expected}
        ], row
        assert not value["top"] and not value["top_sorts"], row
        assert row["coverage"] == "closed_under_profile", row
    elif case.mode == "open":
        assert row["coverage"] == "open", row
    if case.log is not None:
        assert row["log_status"] == "answered", row
        assert member(require_object(row["log_value"]), case.log), row
        if case.mode == "exact":
            assert require_object(row["log_value"])["constants"] == [case.log], row
