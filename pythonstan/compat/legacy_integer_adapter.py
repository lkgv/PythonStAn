"""Native representation projection onto the one canonical scalar implementation."""

from __future__ import annotations

import ast
from collections.abc import Callable, Collection
from typing import TYPE_CHECKING

from pythonstan.analysis.domains.base import DomainRegistry
from pythonstan.analysis.domains.values import Value, ValueOperations, ValuePolicy
from pythonstan.analysis.pointer.kcfa.context import CallStringContext
from pythonstan.analysis.pointer.kcfa.object import AllocKind, AllocSite, ConstantObject
from pythonstan.compat.legacy_ai.value_domain import AbsVal
from pythonstan.frontend.source import source_constant

if TYPE_CHECKING:
    from pythonstan.analysis.pointer.kcfa.context import NativeContext
    from pythonstan.analysis.pointer.kcfa.object import AllocationSource


def finite_integer_transfer(
    expr: ast.expr,
    read_name: Callable[[str], AbsVal],
    statement: AllocationSource | None,
    context: NativeContext | None,
    *,
    max_pairs: int = 256,
    max_bits: int = 4096,
) -> AbsVal | None:
    registry: list[DomainRegistry] = []

    def operand(node: ast.expr) -> Value | None:
        if isinstance(node, ast.Constant):
            literal = source_constant(node)
            return (
                Value.constant(literal)
                if type(literal) is int and literal.bit_length() <= max_bits
                else None
            )
        if not isinstance(node, ast.Name):
            return None
        old = read_name(node.id)
        registry.append(old.registry)
        if (
            old.scalar_kinds is None
            or old.scalar_kinds - {"int"}
            or old.addr_top
            or any(
                not isinstance(obj, ConstantObject) or type(obj.value) is not int
                for obj in old.addrs
            )
        ):
            return None
        integer = old.get_int_lattice()
        if integer.is_bottom or integer.is_top:
            return None
        constants: Collection[int] | None = integer.get_consts()
        if constants is None:
            if integer.lo is None or integer.hi is None or integer.hi - integer.lo >= max_pairs:
                return None
            constants = tuple(
                value for value in range(integer.lo, integer.hi + 1) if integer.may_contain(value)
            )
        if len(constants) > max_pairs or any(value.bit_length() > max_bits for value in constants):
            return None
        return ValueOperations().join_all(Value.constant(value) for value in constants)

    ops = ValueOperations(ValuePolicy(max_scalar_pairs=max_pairs))
    if isinstance(expr, ast.UnaryOp) and isinstance(expr.op, (ast.UAdd, ast.USub)):
        value = operand(expr.operand)
        if value is None:
            return None
        result, errors = ops.scalar_binary(
            ast.Sub() if isinstance(expr.op, ast.USub) else ast.Add(), Value.constant(0), value
        )
        if errors:
            return None
    elif isinstance(expr, (ast.BinOp, ast.Compare)):
        operator: ast.operator | ast.cmpop
        if isinstance(expr, ast.Compare):
            if len(expr.ops) != 1 or len(expr.comparators) != 1:
                return None
            operator, left_node, right_node = expr.ops[0], expr.left, expr.comparators[0]
        else:
            operator, left_node, right_node = expr.op, expr.left, expr.right
        if not isinstance(
            operator,
            (ast.Add, ast.Sub, ast.Mult, ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE),
        ):
            return None
        left, right = operand(left_node), operand(right_node)
        if left is None or right is None:
            return None
        result, errors = ops.scalar_binary(operator, left, right)
        if errors:
            return None
    else:
        return None
    constants = result.exact_constants(max_pairs)
    if not constants or any(
        type(value) is int and value.bit_length() > max_bits for value in constants
    ):
        return None
    integers: list[int | bool] = []
    scalar = AbsVal.bottom()
    for constant in constants:
        if not isinstance(constant, int):
            return None
        integers.append(constant)
        component = (
            AbsVal.bool_true()
            if constant is True
            else AbsVal.bool_false()
            if constant is False
            else AbsVal.from_int(constant)
        )
        scalar = scalar.join(component)
    if registry:
        scalar = scalar.with_registry(registry[0])
    return scalar.with_addrs(
        frozenset(
            ConstantObject(
                context or CallStringContext(), AllocSite(statement, AllocKind.CONSTANT), value
            )
            for value in integers
        )
    )
