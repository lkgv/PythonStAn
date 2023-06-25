from typing import Set, Optional
import ast
from ast import stmt as Statement

from .ir_statement import IRAbstractStmt
from pythonstan.utils.var_collector import VarCollector
from pythonstan.utils.ast_rename import RenameTransformer

__all__ = ["IRYield"]


class IRYield(IRAbstractStmt):
    stmt: Statement
    value: ast.expr
    load_collector: VarCollector
    store_collector: VarCollector
    target: Optional[ast.expr]

    def __init__(self, stmt):
        self.stmt = stmt
        if isinstance(stmt, ast.Assign):
            assert isinstance(stmt.value, (ast.Yield, ast.YieldFrom))
            self.value = stmt.value.value
            self.target = stmt.targets[0]
        else:
            assert isinstance(stmt.value, (ast.Yield, ast.YieldFrom))
            self.value = stmt.value.value
        self.value = self.value
        ast.fix_missing_locations(self.stmt)
        self.load_collector = VarCollector("load")
        self.load_collector.visit(self.stmt)
        self.store_collector = VarCollector("store")
        self.store_collector.visit(self.stmt)

    def __str__(self):
        val_str = ast.unparse(self.value)
        return f"yield {val_str}"

    def get_loads(self) -> Set[str]:
        return self.load_collector.get_vars()

    def get_stores(self) -> Set[str]:
        return self.store_collector.get_vars()

    def rename(self, old_name, new_name, ctxs):
        renamer = RenameTransformer(old_name, new_name, ctxs)
        self.value = renamer.visit(self.value)

    def get_ast(self):
        return self.stmt
