from typing import Set
import ast
from ast import stmt as Statement

from .ir_statement import IRAbstractStmt
from pythonstan.utils.var_collector import VarCollector
from pythonstan.utils.ast_rename import RenameTransformer

__all__ = ["IRAnno"]


class IRAnno(IRAbstractStmt):
    target: ast.expr
    anno: ast.expr
    stmt: Statement
    store_collector: VarCollector
    load_collector: VarCollector

    def __init__(self, stmt: ast.AnnAssign):
        self.set_stmt(stmt)

    def __str__(self):
        tgt_str = ast.unparse(self.target)
        ann_str = ast.unparse(self.anno)
        return f"{tgt_str} : {ann_str}"

    def set_stmt(self, stmt: ast.AnnAssign):
        self.target = stmt.target
        self.anno = stmt.annotation
        self.stmt = stmt
        ast.fix_missing_locations(self.stmt)
        self.collector_reset()
        self.collect_from_stmt(stmt)

    def get_ast(self) -> Statement:
        return self.stmt

    def get_target(self) -> ast.expr:
        return self.target

    def get_anno(self) -> ast.expr:
        return self.anno

    def collector_reset(self):
        self.store_collector = VarCollector("store")
        self.load_collector = VarCollector("load")

    def collect_from_stmt(self, stmt: Statement):
        self.store_collector.visit(self.target)
        self.load_collector.visit(self.anno)

    def get_stores(self) -> Set[str]:
        return self.store_collector.get_vars()

    def get_loads(self) -> Set[str]:
        return self.load_collector.get_vars()

    def rename(self, old_name, new_name, ctxs):
        renamer = RenameTransformer(old_name, new_name, ctxs)
        self.stmt = renamer.visit(self.stmt)
        if isinstance(ast.Load(), ctxs):
            self.anno = renamer.visit(self.anno)
        if isinstance(ast.Store(), ctxs):
            self.target = renamer.visit(self.target)
