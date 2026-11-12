"""Mandatory R3-DOM regressions; known defects remain real failing tests."""

from __future__ import annotations

import ast
import itertools

import pytest

from pythonstan.analysis.domains.ints import IntLattice
from pythonstan.analysis.domains.strings import StrLattice
from pythonstan.analysis.domains.values import Value, ValueOperations, ValuePolicy
from pythonstan.analysis.engines.abstract.state import HeapObject, Store
from pythonstan.analysis.engines.abstract.state_transform import remap_store

from .domain_oracles import concrete_result, public_answer, require_members


@pytest.mark.parametrize("integer,left,right", [(1, "a", "b"), (4, "x", "y")])
def test_scalar_sort_fallback_preserves_normal_and_raise(
    integer: int, left: str, right: str
) -> None:
    """R3-DOM-01: every sort pair survives the enumeration cap."""
    ops = ValueOperations(ValuePolicy(max_scalar_pairs=1))
    first = ops.join(Value.constant(integer), Value.constant(left))
    second = ops.join(Value.constant(2), Value.constant(right))
    value, errors = ops.scalar_binary(ast.Add(), first, second)
    assert value.ints.may_contain(integer + 2)
    assert not value.strings.is_bottom()
    assert "TypeError" in errors
    assert concrete_result("result = x + y", {"x": integer, "y": 2}) == integer + 2
    assert concrete_result("result = x + y", {"x": left, "y": right}) == left + right


@pytest.mark.parametrize("cap", [1, 8])
def test_public_binary_cap_keeps_both_normal_sorts(cap: int) -> None:
    value = public_answer(
        "result=x+y\n",
        bindings={"x": {"constants": [1, "a"]}, "y": {"constants": [2, "b"]}},
        abstract={"max_scalar_pairs": cap},
    )
    require_members(value, [3, "ab"])


@pytest.mark.parametrize("truthy", ["a", "other"])
def test_float_zero_truth_survives_nonfinite_other_sort(truthy: str) -> None:
    """R3-DOM-02 held-out variant also uses an unbounded integer facet."""
    ops = ValueOperations(ValuePolicy(max_scalar_pairs=1))
    mixed = ops.join(Value.constant(truthy), Value.constant(0.0))
    assert ops.truth(mixed) == frozenset({False, True})
    interval = Value(ints=IntLattice.from_interval(1, None), floats=frozenset({0.0}))
    assert ops.truth(interval) == frozenset({False, True})


@pytest.mark.parametrize("cap", [1, 8])
def test_public_float_truth_branch_coverage(cap: int) -> None:
    source = "if x:\n    result='truthy'\nelse:\n    result='falsey'\n"
    expected = [concrete_result(source, {"x": member}) for member in ("a", 0.0)]
    answer = public_answer(
        source,
        bindings={"x": {"constants": ["a", 0.0]}},
        abstract={"max_scalar_pairs": cap},
    )
    require_members(answer, expected)


@pytest.mark.parametrize("prefix,residual", [("a", "z"), ("prefix", "outside")])
def test_wire_preserves_pattern_and_residual_constant(prefix: str, residual: str) -> None:
    """R3-DOM-03: independent wire membership, not Value.exact_constants."""
    raw = Value(strings=StrLattice.from_consts(frozenset({prefix + "a", prefix + "b", residual})))
    widened = ValueOperations(ValuePolicy(max_const_strings=1)).widen(raw, raw)
    assert residual in widened.strings.consts, "The internal widening control retains the residual"
    require_members(widened.export(), [prefix + "a", prefix + "b", residual])


@pytest.mark.parametrize("yielded,cap", itertools.product((1, 7), (1, 2)))
def test_generator_resume_reresolves_epoch_handle(yielded: int, cap: int) -> None:
    """R3-DOM-04: public source witness and distinct held-out return value."""
    source = (
        f"def gen():\n    yield {yielded}\n"
        "a=gen()\nb=gen()\nif flag:\n    g=a\nelse:\n    g=b\nresult=next(g)\n"
    )
    assert all(concrete_result(source, {"flag": branch}) == yielded for branch in (False, True))
    value = public_answer(
        source,
        bindings={"flag": {"type": "bool", "top": True}},
        abstract={"max_addr_set_size": cap, "solver_options": {"max_traces_per_block": 1}},
    )
    require_members(value, [yielded])


@pytest.mark.parametrize("collision", [False, True])
def test_cyclic_graph_quotient_preserves_root_alias(collision: bool) -> None:
    """R3-DOM-05 component evidence; no claim this graph is source-generated."""
    root = Store(cells={"x": Value.constant("before")})
    root.heap = {name: HeapObject("generator", metadata={"entry": root}) for name in ("A", "B")}
    mapping = {"A": "S", "B": "S"} if collision else {"A": "S"}
    merged = remap_store(root, mapping, ValueOperations())
    assert merged.heap["S"].metadata["entry"] is merged
    merged.write_cell("x", Value.constant("after"))
    assert merged.heap["S"].metadata["entry"].cells["x"].exact_constants() == ["after"]
    if collision:
        assert not merged.heap["S"].singleton
    assert root.cells["x"].exact_constants() == ["before"]


def test_cyclic_join_preserves_embedded_root_alias() -> None:
    root = Store(cells={"x": Value.constant(3)})
    root.heap = {"G": HeapObject("generator", metadata={"entry": root})}
    merged = root.join(root, ValueOperations())
    assert merged.heap["G"].metadata["entry"] is merged
    merged.write_cell("x", Value.constant(9))
    assert merged.heap["G"].metadata["entry"].cells["x"].exact_constants() == [9]


def test_collision_without_cycle_positive_control() -> None:
    root = Store(
        heap={name: HeapObject("instance", {"value": Value.constant(name)}) for name in ("A", "B")}
    )
    merged = remap_store(root, {"A": "S", "B": "S"}, ValueOperations())
    assert set(merged.heap["S"].fields["value"].exact_constants()) == {"A", "B"}
    assert not merged.heap["S"].singleton


def test_finite_string_join_widen_membership_positive_control() -> None:
    """Independent held-out gamma checks for a finite string universe."""
    from pythonstan.analysis.domains.strings import StringDomain

    universe = ("", "a", "ax", "ay", "z", "zx", "middle")
    elements = (
        StrLattice.bottom(),
        StrLattice.const("z"),
        StrLattice.from_prefix("a"),
        StrLattice.from_suffix("x"),
        StrLattice.from_consts(frozenset({"ax", "ay", "z"})),
    )
    domain = StringDomain(max_consts=1)

    def gamma(value: StrLattice) -> set[str]:
        return {
            member
            for member in universe
            if value.has_unknown
            or member in value.consts
            or any(member.startswith(prefix) for prefix in value.prefixes)
            or any(member.endswith(suffix) for suffix in value.suffixes)
            or any(
                member.startswith(prefix) and member.endswith(suffix)
                for prefix, suffix in value.conjunctions
            )
        }

    for left, right in itertools.product(elements, repeat=2):
        expected = gamma(left) | gamma(right)
        assert expected <= gamma(domain.join(left, right))
        assert expected <= gamma(domain.widen(left, right))
