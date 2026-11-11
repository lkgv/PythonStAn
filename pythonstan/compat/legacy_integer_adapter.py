"""Native representation projection onto the one canonical scalar implementation."""
import ast
from pythonstan.compat.legacy_ai.value_domain import AbsVal
from pythonstan.analysis.domains.values import Value,ValueOperations,ValuePolicy
from pythonstan.analysis.pointer.kcfa.object import ConstantObject,AllocKind,AllocSite
from pythonstan.analysis.pointer.kcfa.context import CallStringContext


def finite_integer_transfer(expr,read_name,statement,context,*,max_pairs=256,max_bits=4096):
    registry=[]
    def operand(node):
        if isinstance(node,ast.Constant):return Value.constant(node.value) if type(node.value) is int and node.value.bit_length()<=max_bits else None
        if not isinstance(node,ast.Name):return None
        old=read_name(node.id);registry.append(old.registry)
        if old.scalar_kinds is None or old.scalar_kinds-{'int'} or old.addr_top or any(not isinstance(obj,ConstantObject) or type(obj.value) is not int for obj in old.addrs):return None
        integer=old.get_int_lattice()
        if integer.is_bottom or integer.is_top:return None
        constants=integer.get_consts()
        if constants is None:
            if integer.lo is None or integer.hi is None or integer.hi-integer.lo>=max_pairs:return None
            constants=tuple(value for value in range(integer.lo,integer.hi+1) if integer.may_contain(value))
        if len(constants)>max_pairs or any(value.bit_length()>max_bits for value in constants):return None
        return ValueOperations().join_all(Value.constant(value) for value in constants)
    ops=ValueOperations(ValuePolicy(max_scalar_pairs=max_pairs))
    if isinstance(expr,ast.UnaryOp) and isinstance(expr.op,(ast.UAdd,ast.USub)):
        value=operand(expr.operand)
        if value is None:return None
        result,errors=ops.scalar_binary(ast.Sub() if isinstance(expr.op,ast.USub) else ast.Add(),Value.constant(0),value)
        if errors:return None
    elif isinstance(expr,(ast.BinOp,ast.Compare)):
        if isinstance(expr,ast.Compare):
            if len(expr.ops)!=1 or len(expr.comparators)!=1:return None
            op,left,right=expr.ops[0],expr.left,expr.comparators[0]
        else:op,left,right=expr.op,expr.left,expr.right
        if not isinstance(op,(ast.Add,ast.Sub,ast.Mult,ast.Eq,ast.NotEq,ast.Lt,ast.LtE,ast.Gt,ast.GtE)):return None
        left,right=operand(left),operand(right)
        if left is None or right is None:return None
        result,errors=ops.scalar_binary(op,left,right)
        if errors:return None
    else:return None
    constants=result.exact_constants(max_pairs)
    if not constants or any(type(value) is int and value.bit_length()>max_bits for value in constants):return None
    scalar=AbsVal.bottom()
    for value in constants:scalar=scalar.join(AbsVal.bool_true() if value is True else AbsVal.bool_false() if value is False else AbsVal.from_int(value))
    if registry:scalar=scalar.with_registry(registry[0])
    return scalar.with_addrs(frozenset(ConstantObject(context or CallStringContext(),AllocSite(statement,AllocKind.CONSTANT),value) for value in constants))
