from typing import Set
import ast
from ast import stmt as Statement

from .ir_statement import IRAbstractStmt
from pythonstan.utils.var_collector import VarCollector
from pythonstan.utils.ast_rename import RenameTransformer

__all__ = ["IRPass"]


class IRPass(IRAbstractStmt):
    pass_ast: ast.Pass

    def __init__(self, pass_ast=None):
        if pass_ast is None:
            self.pass_ast = ast.Pass()
        else:
            self.pass_ast = pass_ast

    def __str__(self):
        return "pass"

    def get_ast(self):
        return self.pass_ast
