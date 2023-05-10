import ast
from typing import *

from .ir_scope import IRScope
from .ir_statement import IRStatement

__all__ = ["IRModule"]


class IRModule(IRScope, IRStatement):
    name: str
    filename: str
    ast: ast.Module

    def __init__(self, module: ast.Module, cfg, name="", filename=None, defs=None):
        super().__init__(cfg, defs)
        self.name = name
        if filename is None:
            self.filename = "None"
        else:
            self.filename = filename
        self.ast = module

    def get_name(self) -> str:
        return f'<module \'{self.name}\' from \'{self.filename}\'>'

    def get_ast(self) -> ast.AST:
        return self.ast

    def get_stores(self) -> Set[str]:
        return {*()}

    def get_loads(self) -> Set[str]:
        return {*()}

    def get_dels(self) -> Set[str]:
        return {*()}

    def __str__(self) -> str:
        return self.get_name()

    def __repr__(self) -> str:
        return self.get_name()
