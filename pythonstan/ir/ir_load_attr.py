import ast
from ast import stmt as Statement

from .ir_assign import IRAssign
from pythonstan.utils.var_collector import VarCollector

__all__ = ["IRLoadAttr"]


class IRLoadAttr(IRAssign):
    lval: ast.expr
    rval: ast.expr
    obj: ast.expr
    attr: str
    stmt: Statement
    store_collector: VarCollector
    load_collector: VarCollector

    def __init__(self, stmt: ast.Assign):
        assert isinstance(stmt.value, ast.Attribute)
        super().__init__(stmt)
        self.obj = stmt.value.value
        self.attr = stmt.value.attr

    def get_obj(self) -> ast.expr:
        return self.obj

    def get_attr(self) -> str:
        return self.attr
