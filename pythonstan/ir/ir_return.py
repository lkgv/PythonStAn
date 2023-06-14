from typing import Set
import ast
from ast import stmt as Statement

from .ir_statement import IRAbstractStmt
from pythonstan.utils.var_collector import VarCollector
from pythonstan.utils.ast_rename import RenameTransformer

__all__ = ["IRReturn"]


class IRReturn(IRAbstractStmt):
    value: ast.expr
    load_collector: VarCollector

    def __init__(self, value):
        self.value = value
        ast.fix_missing_locations(self.value)
        self.load_collector = VarCollector("load")
        self.load_collector.visit(self.value)

    def __str__(self):
        val_str = ast.unparse(self.value)
        return f"return {val_str}"

    def get_loads(self) -> Set[str]:
        return self.load_collector.get_vars()

    def rename(self, old_name, new_name, ctxs):
        renamer = RenameTransformer(old_name, new_name, ctxs)
        self.value = renamer.visit(self.value)

    def get_ast(self):
        return self.value
