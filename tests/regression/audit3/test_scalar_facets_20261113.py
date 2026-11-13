"""Finite scalar witnesses independent of the transfer's approximation choices."""

from __future__ import annotations

import ast
import itertools
import math
import operator
from collections.abc import Callable

import pytest

from pythonstan.analysis.domains.ints import IntLattice
from pythonstan.analysis.domains.strings import StrLattice
from pythonstan.analysis.domains.values import Value, ValueOperations, ValuePolicy


def contains(value: Value, member: object) -> bool:
    """Finite interpretation of Value fields; do not call transfer or leq."""
    if value.top or type(member).__name__ in value.top_sorts:
        return True
    if member is None:
        return value.none or "none" in value.top_sorts
    if type(member) is bool:
        return member in value.bools
    if type(member) is int:
        part = value.ints
        return not part.is_bottom and (
            member in part.consts
            if part.consts
            else (part.lo is None or part.lo <= member)
            and (part.hi is None or part.hi >= member)
            and (part.parity.value == "top" or member % 2 == (part.parity.value == "odd"))
        )
    if type(member) is float:
        return member in value.floats
    if type(member) is str:
        text = value.strings
        return (
            text.has_unknown
            or member in text.consts
            or any(member.startswith(prefix) for prefix in text.prefixes)
            or any(member.endswith(suffix) for suffix in text.suffixes)
            or any(
                member.startswith(prefix) and member.endswith(suffix)
                for prefix, suffix in text.conjunctions
            )
        )
    return False


def scalar_operation(op: ast.operator | ast.cmpop, left: object, right: object) -> object:
    """Use CPython's operators to establish concrete normal/exception witnesses."""
    operations: dict[type[ast.AST], Callable[[object, object], object]] = {
        ast.Add: operator.add,
        ast.Sub: operator.sub,
        ast.Mult: operator.mul,
        ast.Div: operator.truediv,
        ast.FloorDiv: operator.floordiv,
        ast.Mod: operator.mod,
        ast.Pow: operator.pow,
        ast.LShift: operator.lshift,
        ast.RShift: operator.rshift,
        ast.BitAnd: operator.and_,
        ast.BitOr: operator.or_,
        ast.BitXor: operator.xor,
        ast.Eq: operator.eq,
        ast.NotEq: operator.ne,
        ast.Lt: operator.lt,
        ast.LtE: operator.le,
        ast.Gt: operator.gt,
        ast.GtE: operator.ge,
    }
    return operations[type(op)](left, right)


OPERATORS = (
    ast.Add(),
    ast.Sub(),
    ast.Mult(),
    ast.Div(),
    ast.FloorDiv(),
    ast.Mod(),
    ast.Pow(),
    ast.LShift(),
    ast.RShift(),
    ast.BitAnd(),
    ast.BitOr(),
    ast.BitXor(),
    ast.Eq(),
    ast.NotEq(),
    ast.Lt(),
    ast.LtE(),
    ast.Gt(),
    ast.GtE(),
)
MEMBERS = (-2, -1, 0, 1, 2, False, True, 0.0, 0.5, "", "a", "%s", None)


@pytest.mark.parametrize("op", OPERATORS, ids=lambda op: type(op).__name__)
@pytest.mark.parametrize("cap", (1, 8))
def test_finite_mixed_scalar_cover(op: ast.operator | ast.cmpop, cap: int) -> None:
    ops = ValueOperations(ValuePolicy(max_scalar_pairs=cap))
    value = ops.join_all(Value.constant(item) for item in MEMBERS)
    answer, errors = ops.scalar_binary(op, value, value)
    assert not answer.top, "Known scalar facets must retain sort-specific covers"
    for left, right in itertools.product(MEMBERS, repeat=2):
        try:
            concrete = scalar_operation(op, left, right)
        except (TypeError, ArithmeticError, ValueError) as exc:
            assert type(exc).__name__ in errors, (left, right, type(op), errors)
        else:
            assert contains(answer, concrete), (left, right, type(op), concrete, answer)


@pytest.mark.parametrize("op", (ast.UAdd(), ast.USub(), ast.Invert()))
@pytest.mark.parametrize("cap", (1, 8))
def test_unary_union_preserves_normal_and_type_error(op: ast.unaryop, cap: int) -> None:
    ops = ValueOperations(ValuePolicy(max_scalar_pairs=cap))
    value = ops.join_all(Value.constant(item) for item in MEMBERS)
    answer, errors = ops.scalar_unary(op, value)
    assert "TypeError" in errors
    assert not answer.top
    for item in MEMBERS:
        try:
            if isinstance(op, ast.UAdd):
                concrete = operator.pos(item)
            elif isinstance(op, ast.USub):
                concrete = operator.neg(item)
            else:
                concrete = operator.invert(item)
        except TypeError:
            continue
        assert contains(answer, concrete), (type(op), item, concrete, answer)


def test_truth_is_independent_of_scalar_enumeration_budget() -> None:
    ops = ValueOperations(ValuePolicy(max_scalar_pairs=1))
    for left, right in itertools.product(MEMBERS, repeat=2):
        value = ops.join(Value.constant(left), Value.constant(right))
        assert {bool(left), bool(right)} <= ops.truth(value)
    assert ops.truth(Value(strings=StrLattice.const(""))) == {False}
    assert ops.truth(Value(strings=StrLattice.from_prefix("prefix"))) == {True}
    assert ops.truth(Value(ints=IntLattice.from_interval(1, None), floats=frozenset((0.0,)))) == {
        False,
        True,
    }
    for nonfinite in (math.inf, -math.inf, math.nan):
        assert True in ops.truth(Value.constant(nonfinite))


def test_bottom_unbound_unknown_and_reference_facets_are_distinct() -> None:
    ops = ValueOperations()
    assert ops.facets(Value.bottom()) == ()
    marker = ops.join_all((Value.unknown(), Value(unbound=True), Value.ref("R"), Value.constant(1)))
    assert {part.kind for part in ops.facets(marker)} == {"int", "unknown", "unbound", "reference"}
    assert ops.truth(Value(unbound=True)) == frozenset()
    assert ops.scalar_binary(ast.Add(), Value.bottom(), marker) == (Value.bottom(), set())


def test_integer_resource_fallback_keeps_other_results_and_errors() -> None:
    ops = ValueOperations(ValuePolicy(max_int_bits=8, max_scalar_pairs=1))
    left = ops.join(Value.constant(2), Value.constant("text"))
    right = ops.join(Value.constant(200), Value.constant("other"))
    answer, errors = ops.scalar_binary(ast.Pow(), left, right)
    assert contains(answer, 2**200)
    assert "TypeError" in errors
    assert not answer.top


def test_join_and_widen_cover_finite_members_across_sorts() -> None:
    ops = ValueOperations(ValuePolicy(max_const_ints=1, max_const_strings=1))
    elements = [Value.constant(member) for member in MEMBERS]
    for first, second in itertools.product(elements, repeat=2):
        for merged in (ops.join(first, second), ops.widen(first, second)):
            for source in (first, second):
                concrete = source.exact_constants()
                assert concrete is not None
                assert all(contains(merged, member) for member in concrete)


def test_default_budget_exhaustion_preserves_unrelated_facets() -> None:
    ops = ValueOperations()
    integer_members = frozenset(range(17))
    first = Value(ints=IntLattice.from_consts(integer_members), strings=StrLattice.const("a"))
    second = Value(ints=IntLattice.from_consts(integer_members), strings=StrLattice.const("b"))
    answer, errors = ops.scalar_binary(ast.Add(), first, second)
    assert all(
        contains(answer, left + right)
        for left, right in itertools.product(integer_members, repeat=2)
    )
    assert contains(answer, "ab")
    assert errors == {"TypeError"}
    mixed = Value(ints=IntLattice.from_consts(frozenset(range(1, 300))), floats=frozenset((0.0,)))
    assert ops.truth(mixed) == {False, True}


@pytest.mark.parametrize("op", (ast.UAdd(), ast.USub(), ast.Invert()))
def test_string_top_unary_has_only_type_error(op: ast.unaryop) -> None:
    value, errors = ValueOperations().scalar_unary(op, Value(strings=StrLattice.top()))
    assert value.is_bottom()
    assert errors == {"TypeError"}


def test_opaque_named_sort_preserves_protocol_obligation() -> None:
    ops = ValueOperations()
    value, errors = ops.scalar_binary(ast.Lt(), Value.constant(b"a"), Value.constant(b"b"))
    assert value.top
    assert errors == {"unknown_operator"}
    negated, errors = ops.scalar_unary(ast.USub(), Value.constant(1 + 2j))
    assert negated.top_sorts == {"complex"}
    assert not errors
    truth, errors = ops.scalar_unary(ast.Not(), Value.ref("R"))
    assert truth.bools == {False, True}
    assert errors == {"unknown_truth_protocol"}


@pytest.mark.parametrize("text", ("", "x"))
@pytest.mark.parametrize("sign", (-1, 1))
def test_repetition_ssize_overflow_keeps_other_normal_pairs(text: str, sign: int) -> None:
    import sys

    count = sign * (sys.maxsize + 2)
    with pytest.raises(OverflowError):
        operator.mul(text, count)
    ops = ValueOperations()
    choices = ops.join(Value.constant(count), Value.constant(0))
    answer, errors = ops.scalar_binary(ast.Mult(), Value.constant(text), choices)
    assert contains(answer, "")
    assert "OverflowError" in errors
