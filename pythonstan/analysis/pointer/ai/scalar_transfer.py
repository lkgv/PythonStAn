"""Bounded finite transfer for exact builtin integer operations.

Only the represented integer set is evaluated, never user objects or target
source. An absent/large/foreign operand returns None so callers retain their
conservative fallback and open semantic obligation. Bounds affect precision,
not which concrete values are silently dropped.
"""
import ast
import operator
from .value_domain import AbsVal
from pythonstan.analysis.pointer.kcfa.object import ConstantObject, AllocKind, AllocSite
from pythonstan.analysis.pointer.kcfa.context import CallStringContext

_OPERATIONS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
               ast.Eq: operator.eq, ast.NotEq: operator.ne, ast.Lt: operator.lt,
               ast.LtE: operator.le, ast.Gt: operator.gt, ast.GtE: operator.ge}


def finite_integer_transfer(expr, read_name, statement, context, *, max_pairs=256, max_bits=4096):
    operand_registries = []
    def integers(node):
        if isinstance(node, ast.Constant):
            return {node.value} if type(node.value) is int and node.value.bit_length() <= max_bits else None
        if not isinstance(node, ast.Name):
            return None
        value = read_name(node.id)
        operand_registries.append(value.registry)
        if value.scalar_kinds is None or value.scalar_kinds - {"int"}:
            # Neither a legacy untagged constraint product nor one arm of a
            # mixed union establishes complete builtin-integer input coverage.
            return None
        if value.addr_top or any(not isinstance(obj, ConstantObject) or type(obj.value) is not int for obj in value.addrs):
            return None
        integer = value.get_int_lattice()
        if integer.is_bottom or integer.is_top:
            return None
        # Avoid enumerating unbounded intervals, or trusting only their known
        # witnesses when additional integer values are represented.
        if integer.get_consts() is not None:
            constants = integer.get_consts()
        elif integer.lo is not None and integer.hi is not None and integer.hi-integer.lo < max_pairs:
            constants = tuple(n for n in range(integer.lo, integer.hi+1) if integer.may_contain(n))
        else:
            return None
        if len(constants) > max_pairs or any(type(n) is not int or n.bit_length()>max_bits for n in constants):
            return None
        return set(constants)

    if isinstance(expr, ast.UnaryOp) and isinstance(expr.op, (ast.UAdd, ast.USub)):
        values = integers(expr.operand)
        if values is None:
            return None
        answers = {n if isinstance(expr.op, ast.UAdd) else -n for n in values}
    elif isinstance(expr, (ast.BinOp, ast.Compare)):
        if isinstance(expr, ast.Compare):
            if len(expr.ops) != 1 or len(expr.comparators) != 1:
                return None
            op, left_node, right_node = expr.ops[0], expr.left, expr.comparators[0]
        else:
            op, left_node, right_node = expr.op, expr.left, expr.right
        function = _OPERATIONS.get(type(op))
        left, right = integers(left_node), integers(right_node)
        if function is None or left is None or right is None or len(left)*len(right)>max_pairs:
            return None
        answers = {function(a,b) for a in left for b in right}
    else:
        return None
    if not answers or any(type(n) is int and n.bit_length()>max_bits for n in answers):
        return None
    if all(type(n) is bool for n in answers):
        scalar = AbsVal.bool_true() if answers == {True} else AbsVal.bool_false() if answers == {False} else AbsVal.bool_top()
    else:
        scalar = AbsVal.from_ints(frozenset(answers))
    witnesses = frozenset(ConstantObject(context or CallStringContext(), AllocSite(statement, AllocKind.CONSTANT), value=n) for n in answers)
    if operand_registries:
        scalar = scalar.with_registry(operand_registries[0])
    return scalar.with_addrs(witnesses)
