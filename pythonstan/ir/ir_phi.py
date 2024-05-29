import ast
from ast import stmt as Statement
from typing import List, Optional

from .ir_assign import IRAssign
from pythonstan.utils.var_collector import VarCollector

__all__ = ["IRLoadAttr"]


# lval = rval( obj.attr )
class IRPhi(IRAssign):
    lval: ast.Name
    items: List[Optional[ast.Name]]
    store_collector: VarCollector
    load_collector: VarCollector

    def __init__(self, lval: ast.Name, items: List[Optional[ast.Name]]):
        self.lval = lval
        self.items
        assert len(stmt.targets) == 1 and isinstance(stmt.targets[0], ast.Name)
        assert isinstance(stmt.value, ast.Attribute)
        assert isinstance(stmt.value.value, ast.Name)

    def get_lval(self) -> ast.Name:
        return self.lval

    def get_obj(self) -> ast.Name:
        return self.obj

    def get_attr(self) -> str:
        return self.attr
