import ast
from ast import stmt as Statement

from .ir_assign import IRAssign
from pythonstan.utils.var_collector import VarCollector

__all__ = ["IRStoreAttr"]


# lval (obj.attr) = rval
class IRStoreAttr(IRAssign):
    rval: ast.Name
    obj: ast.Name
    attr: str
    stmt: Statement
    store_collector: VarCollector
    load_collector: VarCollector

    def __init__(self, stmt: ast.Assign):
        assert len(stmt.targets) == 1
        target = stmt.targets[0]
        assert isinstance(target, ast.Attribute)
        assert isinstance(target.value, ast.Name)
        assert isinstance(stmt.value, ast.Name)
        super().__init__(stmt)
        self.obj = target.value
        self.attr = target.attr

    def get_rval(self) -> ast.Name:
        return self.rval

    def get_obj(self) -> ast.Name:
        return self.obj

    def get_attr(self) -> str:
        return self.attr
