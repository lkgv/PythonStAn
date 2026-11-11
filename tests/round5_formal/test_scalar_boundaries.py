"""Bounded scalar enumeration must cover inputs or decline the specialization."""
import ast

import pytest

from pythonstan.analysis.pointer.ai import AbsVal
from pythonstan.analysis.pointer.ai.domains.ints import IntLattice
from pythonstan.analysis.pointer.ai.scalar_transfer import finite_integer_transfer


@pytest.mark.parametrize("other", [AbsVal.from_str("s"), AbsVal.none_val(), AbsVal.bool_false(), AbsVal.top()])
@pytest.mark.parametrize("expression", ["x + 1", "x == 1"])
def test_mixed_scalar_joins_do_not_specialize_as_exhaustive_integers(other, expression):
    mixed = AbsVal.from_int(1).join(other)
    result = finite_integer_transfer(ast.parse(expression, mode="eval").body,
                                     lambda _: mixed, None, None)
    assert result is None


def test_finite_interval_enumeration_covers_every_represented_integer():
    interval = AbsVal(components={"int": IntLattice.from_interval(-2, 2)})
    result = finite_integer_transfer(ast.parse("x + 1", mode="eval").body,
                                     lambda _: interval, None, None)
    assert result.get_int_lattice().get_consts() == frozenset({-1, 0, 1, 2, 3})


@pytest.mark.parametrize("expression,value,options", [
    ("x * x", AbsVal.from_ints(frozenset(range(20))), {"max_pairs": 256}),
    ("x * x", AbsVal.from_int(16), {"max_bits": 5}),
    ("x + 1", AbsVal(components={"int": IntLattice.from_interval(0, None)}), {}),
    ("x / 1", AbsVal.from_int(1), {}),
])
def test_unbounded_or_unsupported_operations_decline_whole_specialization(expression, value, options):
    assert finite_integer_transfer(ast.parse(expression, mode="eval").body,
                                   lambda _: value, None, None, **options) is None
