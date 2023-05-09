from typing import *
import ast

from .ir_statement import IRAbstractStmt
from .ir_scope import IRScope

__all__ = ["IRClass"]


class IRClass(IRScope, IRAbstractStmt):
    name: str
    bases: List[ast.expr]
    keywords: List[ast.keyword]
    decorator_list: List[ast.expr]
    ast_repr: ast.ClassDef

    cell_vars: Set[str]

    def __init__(self, cls: ast.ClassDef, cfg=None, prev_scope=None, cell_vars=None):
        super().__init__(cfg, prev_scope)
        self.name = cls.name
        self.bases = cls.bases
        self.keywords = cls.keywords
        self.decorator_list = cls.decorator_list
        self.ast_repr = ast.ClassDef(
            name=self.name,
            bases=self.bases,
            keywords=self.keywords,
            body=[],
            decorator_list=self.decorator_list)
        ast.copy_location(self.ast_repr, cls)
        if cell_vars is None:
            self.cell_vars = {*()}
        else:
            self.cell_vars = cell_vars

    def to_ast(self) -> ast.ClassDef:
        return self.ast_repr

    def set_cell_vars(self, cell_vars):
        self.cell_vars = cell_vars

    def add_cell_var(self, cell_var):
        self.cell_vars.add(cell_var)

    def __str__(self):
        names = list(map(lambda x: x.id, self.cell_vars))
        cell_comment = "# closure: (" + ', '.join(names) + ")\n    "
        return cell_comment + ast.unparse(self.to_ast())

    def get_name(self) -> str:
        return f'cls${self.class_def.name}'

    def __repr__(self) -> str:
        return str(self.class_def)