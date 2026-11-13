"""Fresh post-interruption iterator and constructor obligation witnesses."""

from __future__ import annotations

import pytest

from pythonstan import Source, ValueQuery, analyze
from pythonstan.analysis.contracts.json_data import JsonScalar, require_object


def _exact(source: str, expected: JsonScalar) -> None:
    query = ValueQuery("subject", "result")
    result = analyze(Source(source, module="subject"), queries=[query])
    answer = require_object(result.query(query).value)
    assert answer["constants"] == [expected]
    assert answer["top"] is False and answer["top_sorts"] == []
    assert result.coverage_status == "closed_under_profile"


@pytest.mark.parametrize(
    "expression",
    ["list(42)", "dict(42)", "next(42)", "next(42, 9)", "next('x', 9)", "(lambda *a: a)(*42)"],
)
def test_noniterable_scalars_preserve_caught_typeerror(expression: str) -> None:
    source = (
        f"result='missing'\ntry:\n    value={expression}\nexcept TypeError:\n    result='caught'\n"
    )
    _exact(source, "caught")


@pytest.mark.parametrize("returned", ["42", "None", "'text'", "[]"])
def test_iter_result_must_have_a_next_slot(returned: str) -> None:
    source = f"class Bad:\n    def __iter__(self):\n        return {returned}\nresult='missing'\ntry:\n    iter(Bad())\nexcept TypeError:\n    result='caught'\n"
    _exact(source, "caught")


def test_next_default_does_not_hide_non_stopiteration() -> None:
    source = "class Cursor:\n    def __next__(self):\n        raise ValueError('bad')\nresult='missing'\ntry:\n    next(Cursor(),9)\nexcept ValueError:\n    result='caught'\n"
    _exact(source, "caught")


def test_dict_pair_iteration_runs_real_callbacks() -> None:
    source = "log=''\ndef pairs():\n    global log\n    log+='a'\n    yield ('x',1)\n    log+='b'\n    yield ('y',2)\nvalue=dict(pairs())\nresult=log+str(value['x']+value['y'])\n"
    _exact(source, "ab3")


def test_dict_mapping_exhausts_keys_before_fetching_values() -> None:
    source = "log=''\nclass Mapping:\n    def keys(self):\n        global log\n        log+='k'\n        yield 'a'\n        log+='e'\n    def __getitem__(self,key):\n        global log\n        log+='v'\n        return 4\nvalue=dict(Mapping())\nresult=log+str(value['a'])\n"
    _exact(source, "kev4")


def test_constructor_list_invokes_original_iterable_length_hint() -> None:
    source = "log=''\nclass Cursor:\n    def __iter__(self):\n        global log\n        log+='i'\n        return iter([1])\n    def __len__(self):\n        global log\n        log+='l'\n        return 1\nvalue=list(Cursor())\nresult=log+str(value[0])\n"
    _exact(source, "il1")


def test_bool_operands_keep_their_distinct_callback_contexts() -> None:
    source = "log=''\nclass Flag:\n    def __init__(self,name,truth):\n        self.name=name\n        self.truth=truth\n    def __bool__(self):\n        global log\n        log+=self.name\n        return self.truth\na=Flag('a',True)\nb=Flag('b',False)\nc=a and b and 7\nresult=log\n"
    _exact(source, "ab")


def test_typeerror_subclass_from_length_falls_back_to_length_hint() -> None:
    source = "class MyTypeError(TypeError):\n    pass\nclass Cursor:\n    def __iter__(self):\n        return iter([7])\n    def __len__(self):\n        raise MyTypeError\n    def __length_hint__(self):\n        return 1\nresult=list(Cursor())[0]\n"
    _exact(source, 7)


@pytest.mark.parametrize("items,kind,value", [("[True,1]", "bool", True), ("[1,True]", "int", 1)])
def test_set_constructor_preserves_first_equal_representative(
    items: str, kind: str, value: JsonScalar
) -> None:
    query = ValueQuery("subject", "result")
    size = ValueQuery("subject", "size")
    result = analyze(
        Source(f"data=set({items})\nresult=next(iter(data))\nsize=len(data)\n", module="subject"),
        queries=[query, size],
    )
    answer = require_object(result.query(query).value)
    assert answer["tagged_constants"] == [{"type": kind, "value": value}]
    assert answer["top"] is False
    assert require_object(result.query(size).value)["constants"] == [1]
