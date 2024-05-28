import ast
from ast import stmt as Statement

from .ir_assign import IRAssign
from pythonstan.utils.var_collector import VarCollector

__all__ = ["IRLoadAttr"]


# lval = rval( obj.attr )
class IRLoadAttr(IRAssign):
    lval: ast.Name
    obj: ast.Name
    attr: str
    stmt: Statement
    store_collector: VarCollector
    load_collector: VarCollector

    def __init__(self, stmt: ast.Assign):
        assert len(stmt.targets) == 1 and isinstance(stmt.targets[0], ast.Name)
        assert isinstance(stmt.value, ast.Attribute)
        assert isinstance(stmt.value.value, ast.Name)
        super().__init__(stmt)
        self.obj = stmt.value.value
        self.attr = stmt.value.attr

    def get_lval(self) -> ast.Name:
        return self.lval

    def get_obj(self) -> ast.Name:
        return self.obj

    def get_attr(self) -> str:
        return self.attr
