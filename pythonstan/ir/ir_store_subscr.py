import ast
from ast import stmt as Statement

from .ir_assign import IRAssign
from pythonstan.utils.var_collector import VarCollector

__all__ = ["IRStoreSubscr"]


class IRStoreSubscr(IRAssign):
    lval: ast.expr
    rval: ast.expr
    obj: ast.expr
    slice: ast.expr
    stmt: Statement
    store_collector: VarCollector
    load_collector: VarCollector

    def __init__(self, stmt: ast.Assign):
        target = stmt.targets[0]
        assert isinstance(target, ast.Subscript)
        super().__init__(stmt)
        self.obj = target.value
        self.slice = target.slice

    def get_obj(self) -> ast.expr:
        return self.obj

    def get_attr(self) -> ast.expr:
        return self.slice

    def has_slice(self) -> bool:
        return isinstance(self.slice, ast.Slice)
