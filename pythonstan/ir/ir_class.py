import ast
from typing import *

from .ir_scope import IRScope
from .ir_statement import IRStatement

__all__ = ["IRClass"]


class IRClass(IRScope, IRStatement):
    name: str
    bases: List[ast.expr]
    keywords: List[ast.keyword]
    decorator_list: List[ast.expr]
    ast: ast.ClassDef

    cell_vars: Set[str]

    def __init__(self, cls: ast.ClassDef, cfg=None, defs=None, cell_vars=None):
        super().__init__(cfg, defs)
        self.name = cls.name
        self.bases = cls.bases
        self.keywords = cls.keywords
        self.decorator_list = cls.decorator_list
        self.ast_repr = cls
        if cell_vars is None:
            self.cell_vars = {*()}
        else:
            self.cell_vars = cell_vars

    def set_cell_vars(self, cell_vars):
        self.cell_vars = cell_vars

    def add_cell_var(self, cell_var):
        self.cell_vars.add(cell_var)

    def get_ast(self) -> ast.ClassDef:
        return self.ast

    def get_stores(self) -> Set[str]:
        return {*()}

    def get_loads(self) -> Set[str]:
        return {*()}

    def get_dels(self) -> Set[str]:
        return {*()}

    def __repr__(self) -> str:
        decrs = ', '.join([ast.unparse(decr) for decr in self.decorator_list])
        bases = ', '.join([ast.unparse(base) for base in self.bases])
        kws = ', '.join([ast.unparse(kw) for kw in self.keywords])
        if len(decrs) > 0:
            cls_repr = f'class [{decrs}] {self.name}'
        else:
            cls_repr = f'class {self.name}'
        if len(bases) > 0:
            if len(kws) > 0:
                cls_repr = f'{cls_repr}({bases}, {kws})'
            else:
                cls_repr = f'{cls_repr}({bases})'
        elif len(kws) > 0:
            cls_repr = f'{cls_repr}({kws})'
        return cls_repr

    def __str__(self) -> str:
        return self.__repr__()

    def get_name(self) -> str:
        return f'<class {self.name}>'
