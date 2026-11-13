"""Separate public protocol probes; fixed existing 10000-work/eight-second profile."""

from __future__ import annotations

import pytest

from .domain_oracles import concrete_result, public_answer, require_members


def _source(operation: str) -> str:
    prefix = (
        "def leaf(value):\n"
        "    try:\n"
        "        yield value\n"
        "    except ValueError:\n"
        "        yield value+10\n"
        "def outer(value):\n"
        "    yield from leaf(value)\n"
        "a=outer(3)\nb=outer(7)\n"
        "if flag:\n    g=a\nelse:\n    g=b\n"
    )
    return prefix + (
        "result=next(g)\n"
        if operation == "next"
        else "first=next(g)\nresult=g.throw(ValueError())\n"
    )


@pytest.mark.parametrize("operation", ["next", "throw"])
@pytest.mark.parametrize("cap", [1, 2])
def test_public_yield_from_correlated_instances_cover_both_concrete_paths(
    operation: str, cap: int
) -> None:
    source = _source(operation)
    expected = {3, 7} if operation == "next" else {13, 17}
    concrete = {concrete_result(source, {"flag": flag}) for flag in (False, True)}
    assert concrete == expected
    actual = public_answer(
        source,
        bindings={"flag": {"type": "bool", "top": True}},
        abstract={"max_addr_set_size": cap, "solver_options": {"max_traces_per_block": 1}},
    )
    require_members(actual, expected)
    if cap == 2:
        assert not actual.get("top") and not actual.get("top_sorts")
        assert set(actual.get("constants", [])) == expected
