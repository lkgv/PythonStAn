"""Independent CPython witnesses for finite trusted scalar operations."""

from __future__ import annotations

import pytest

from pythonstan.analysis.contracts.json_data import JsonScalar
from pythonstan.analysis.semantics.scalars import (
    scalar_aggregate,
    scalar_conversion,
    scalar_range,
    scalar_sort,
)


@pytest.mark.parametrize("value", [None, False, True, 0, 1.5])
@pytest.mark.parametrize("base", [-1, 1, 37, True, 10**100])
def test_invalid_base_is_validated_before_nontext(value: JsonScalar, base: int) -> None:
    with pytest.raises(ValueError):
        scalar_conversion("int", (value, base), {})


def test_empty_sum_returns_none_start() -> None:
    assert scalar_aggregate("sum", (), None) is None


def test_sum_preserves_prefix_overflow_before_later_invalid_element() -> None:
    with pytest.raises(OverflowError):
        scalar_aggregate("sum", (10**1000, "bad"), 0.0)


def test_sum_keeps_python_312_compensation_and_bool_initial_type() -> None:
    numbers = (1e16, 1.0, -1e16)
    assert scalar_aggregate("sum", numbers) == sum(numbers) == 1.0
    assert scalar_aggregate("sum", (), False) is False


def test_range_bool_zero_step_and_nonindex_controls() -> None:
    assert tuple(scalar_range((False, True))) == tuple(range(False, True))
    with pytest.raises(ValueError):
        scalar_range((0, 3, False))
    with pytest.raises(TypeError):
        scalar_range((1.5,))


def test_string_noobject_decode_options_and_heterogeneous_sort() -> None:
    assert scalar_conversion("str", (), {"encoding": "not-a-codec"}) == ""
    with pytest.raises(TypeError):
        scalar_sort((None, 1))
    assert scalar_aggregate("min", (None,)) is None


@pytest.mark.parametrize(
    ("source", "expected"),
    (
        ("result=int('12',base=8)\n", 10),
        ("try:\n    result=int(None,1)\nexcept ValueError:\n    result='base'\n", "base"),
        ("result=sum([],None)\n", None),
        ("result=sum([1e16,1.0,-1e16])\n", 1.0),
        ("result=sorted([None])[0]\n", None),
        ("try:\n    result=sorted([None,1])\nexcept TypeError:\n    result='order'\n", "order"),
        ("result=range(False,True)[0]\n", 0),
        ("try:\n    result=range(0,3,False)\nexcept ValueError:\n    result='step'\n", "step"),
    ),
)
def test_actual_source_scalar_service_consumers(source: str, expected: JsonScalar) -> None:
    from pythonstan import Source, ValueQuery, analyze
    from pythonstan.analysis.contracts.json_data import require_object

    query = ValueQuery("subject", "result")
    result = analyze(
        Source(source, module="subject"), config={"engine": "ai_primary"}, queries=[query]
    )
    answer = result.query(query)
    assert answer.status == "answered" and result.coverage_status == "closed_under_profile"
    value = require_object(answer.value)
    assert value["constants"] == [expected]
    assert not value["top"] and not value["top_sorts"] and not value["may_unbound"]
