"""Class-query capability boundaries retain possible values and effects."""

from __future__ import annotations

import pytest

from pythonstan import Source, ValueQuery, analyze

from .domain_oracles import require_members
from .test_sem05_resume2 import concrete

BASES = (
    "class C(int): pass\nresult=isinstance(C(),int)\n",
    "class C(list): pass\nresult=isinstance(C(),list)\n",
)


@pytest.mark.parametrize("source", BASES)
def test_cpython_valid_builtin_bases(source: str) -> None:
    assert concrete(source) == [True, None]


@pytest.mark.parametrize("source", BASES)
def test_unmodeled_valid_builtin_base_is_open_not_false_typeerror(source: str) -> None:
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
    assert answer.status == "answered" and answer.value is not None
    require_members(answer.value, [True])
    assert result.coverage_status == "open"


def test_typed_object_subject_may_be_an_integer() -> None:
    assert concrete("thing=1\nresult=isinstance(thing,int)\n") == [True, None]
    query = ValueQuery("subject", "result")
    result = analyze(
        Source("result=isinstance(thing,int)\n", module="subject"),
        config={
            "engine": "ai_primary",
            "inputs": {"bindings": {"subject.thing": {"type": "object", "top": True}}},
            "resources": {"max_work": 10000, "max_seconds": 2, "max_memory_mb": 512},
        },
        queries=[query],
    )
    answer = result.query(query)
    assert answer.status == "answered" and answer.value is not None
    require_members(answer.value, [True])
    assert result.coverage_status == "open"


def test_typed_object_classinfo_may_be_a_valid_class() -> None:
    assert concrete("klass=int\nresult=isinstance(1,klass)\n") == [True, None]
    query = ValueQuery("subject", "result")
    result = analyze(
        Source(
            "try:\n result=isinstance(1,klass)\nexcept TypeError:\n result='invalid'\n",
            module="subject",
        ),
        config={
            "engine": "ai_primary",
            "inputs": {"bindings": {"subject.klass": {"type": "object", "top": True}}},
            "resources": {"max_work": 10000, "max_seconds": 2, "max_memory_mb": 512},
        },
        queries=[query],
    )
    answer = result.query(query)
    assert answer.status == "answered" and answer.value is not None
    require_members(answer.value, [True, "invalid"])
    assert result.coverage_status == "open"
