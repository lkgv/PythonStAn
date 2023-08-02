from typing import Set, Optional
import ast
from ast import stmt as Statement

from .ir_statement import IRAbstractStmt
from pythonstan.utils.var_collector import VarCollector
from pythonstan.utils.ast_rename import RenameTransformer

__all__ = ["IRReturn"]


class IRReturn(IRAbstractStmt):
    value: Optional[str]
    stmt: ast.stmt
    load_collector: VarCollector

    def __init__(self, stmt: ast.Return):
        ast.fix_missing_locations(stmt)
        if stmt.value is not None:
            assert isinstance(stmt.value, ast.Name), "Return value of IR should be ast.Name or None!"
            self.value = stmt.value.id
        else:
            self.value = None
        self.stmt = stmt
        self.load_collector = VarCollector("load")
        self.load_collector.visit(self.stmt)

    def __str__(self):
        return ast.unparse(self.stmt)

    def get_loads(self) -> Set[str]:
        return self.load_collector.get_vars()

    def get_value(self) -> Optional[str]:
        return self.value

    def rename(self, old_name, new_name, ctxs):
        renamer = RenameTransformer(old_name, new_name, ctxs)
        self.value = renamer.visit(self.stmt)

    def get_ast(self):
        return self.value
