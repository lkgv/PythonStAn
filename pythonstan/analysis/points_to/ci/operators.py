import ast
from ast import operator as Operator

from .lattice.value import Value
from .solver_interface import SolverInterface


class Operators:
    '''
    OP:
    class ast.Add
    class ast.Sub
    class ast.Mult
    class ast.Div
    class ast.FloorDiv
    class ast.Mod
    class ast.Pow
    class ast.LShift¶
    class ast.RShift
    class ast.BitOr
    class ast.BitXor
    class ast.BitAnd
    class ast.MatMult
    '''

    @classmethod
    def bin_op(cls, op: Operator, left: Value, right: Value, c: SolverInterface) -> Value:
        if isinstance(op, ast.Add):
            return cls.add(left, right)
        elif isinstance(op, ast.Sub):
            return cls.sub(left, right)
        elif isinstance(op, ast.Mult):
            return cls.mult(left, right)
        elif isinstance(op, ast.Div):
            return cls.div(left, right)
        elif isinstance(op, ast.FloorDiv):
            return cls.floor_div(left, right)
        elif isinstance(op, ast.Mod):
            return cls.mod(left, right)
            ...
        else:
            return Value.make_none()


    @classmethod
    def unary_op(cls, op: ast.operator, s: State) -> Value:
        ...

    @staticmethod
    def add(left: Value, right: Value) -> Value:
        if left.is_maybe_int and right.is_maybe_int():
            ...

        ...

    @staticmethod
    def sub(left: Value, right: Value) -> Value:
        ...

    @staticmethod
    def mult(left: Value, right: Value) -> Value:
        ...

    @staticmethod
    def div(left: Value, right: Value) -> Value:
        ...

    @staticmethod
    def floor_div(left: Value, right: Value) -> Value:
        ...

    @staticmethod
    def mod(left: Value, right: Value) -> Value:
        ...

    @staticmethod
    def pow(left: Value, right: Value) -> Value:
        ...





