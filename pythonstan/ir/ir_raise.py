from typing import Set, Optional
import ast

from .ir_statement import IRAbstractStmt
from pythonstan.utils.var_collector import VarCollector
from pythonstan.utils.ast_rename import RenameTransformer

__all__ = ["IRRaise"]


class IRRaise(IRAbstractStmt):
    exc: Optional[ast.expr]
    cause: Optional[ast.expr]
    stmt: ast.Raise
    load_collector: VarCollector

    def __init__(self, stmt: ast.Raise):
        self.stmt = stmt
        ast.fix_missing_locations(self.stmt)
        self.exc = stmt.exc
        self.cause = stmt.cause
        self.load_collector = VarCollector("load")
        self.load_collector.visit(self.stmt)

    def __str__(self):
        return ast.unparse(self.stmt)

    def get_loads(self) -> Set[str]:
        return self.load_collector.get_vars()

    def rename(self, old_name, new_name, ctxs):
        renamer = RenameTransformer(old_name, new_name, ctxs)
        self.stmt = renamer.visit(self.stmt)
        if self.exc is not None:
            self.exc = renamer.visit(self.exc)
        if self.cause is not None:
            self.cause = renamer.visit(self.cause)

    def get_ast(self):
        return self.stmt
