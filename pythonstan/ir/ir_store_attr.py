import ast
from ast import stmt as Statement

from .ir_assign import IRAssign
from pythonstan.utils.var_collector import VarCollector

__all__ = ["IRStoreAttr"]


class IRStoreAttr(IRAssign):
    lval: ast.expr
    rval: ast.expr
    obj: ast.expr
    attr: str
    stmt: Statement
    store_collector: VarCollector
    load_collector: VarCollector

    def __init__(self, stmt: ast.Assign):
        target = stmt.targets[0]
        assert isinstance(target, ast.Attribute)
        super().__init__(stmt)
        self.obj = target.value
        self.attr = target.attr

    def get_obj(self) -> ast.expr:
        return self.obj

    def get_attr(self) -> str:
        return self.attr
