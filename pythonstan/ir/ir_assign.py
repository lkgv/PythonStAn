from typing import Set
import ast
from ast import stmt as Statement

from .ir_statement import IRAbstractStmt
from pythonstan.utils.var_collector import VarCollector
from pythonstan.utils.ast_rename import RenameTransformer

__all__ = ["IRAssign"]


class IRAssign(IRAbstractStmt):
    lval: ast.Name
    rval: ast.expr
    stmt: Statement
    store_collector: VarCollector
    load_collector: VarCollector

    def __init__(self, stmt: ast.Assign):
        self.set_stmt(stmt)

    def __str__(self):
        lstr = ast.unparse(self.lval)
        rstr = ast.unparse(self.rval)
        return f"{lstr} = {rstr}"

    def set_stmt(self, stmt: ast.Assign):
        assert isinstance(stmt.targets[0], ast.Name)
        self.lval = stmt.targets[0]
        self.rval = stmt.value
        self.stmt = stmt
        ast.fix_missing_locations(self.stmt)
        self.collector_reset()
        self.collect_from_stmt(stmt)

    def get_ast(self) -> Statement:
        return self.stmt

    def get_lval(self) -> ast.Name:
        return self.lval

    def get_rval(self) -> ast.expr:
        return self.rval

    def collector_reset(self):
        self.store_collector = VarCollector("store")
        self.load_collector = VarCollector("load")

    def collect_from_stmt(self, stmt: Statement):
        self.store_collector.visit(self.lval)
        self.load_collector.visit(self.rval)

    def get_stores(self) -> Set[str]:
        return self.store_collector.get_vars()

    def get_loads(self) -> Set[str]:
        return self.load_collector.get_vars()

    def rename(self, old_name, new_name, ctxs):
        renamer = RenameTransformer(old_name, new_name, ctxs)
        self.stmt = renamer.visit(self.stmt)
        if isinstance(ast.Load(), ctxs):
            self.rval = renamer.visit(self.rval)
        if isinstance(ast.Store(), ctxs):
            self.lval = renamer.visit(self.lval)
