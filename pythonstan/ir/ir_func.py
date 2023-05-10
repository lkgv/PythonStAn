import ast
from typing import *

from .ir_scope import IRScope
from .ir_statement import IRStatement

__all__ = ["IRFunc"]


class IRFunc(IRScope, IRStatement):
    name: str
    args: ast.arguments
    decorator_list: List[ast.expr]
    returns: ast.expr
    type_comment: str
    ast: ast.stmt
    is_async: bool

    cell_vars: Set[str]

    def __init__(self, fn: ast.stmt, cfg, defs=None, cell_vars=None):
        super().__init__(cfg, defs)
        self.name = fn.name
        self.args = fn.args
        self.decorator_list = fn.decorator_list
        self.returns = fn.returns
        self.type_comment = fn.type_comment
        self.ast = fn
        if isinstance(fn, ast.AsyncFunctionDef):
            self.is_async = False
        else:
            self.is_async = True
        if cell_vars is None:
            self.cell_vars = {*()}
        else:
            self.cell_vars = cell_vars

    def get_ast(self) -> ast.stmt:
        return self.ast

    def set_cell_vars(self, cell_vars):
        self.cell_vars = cell_vars

    def add_cell_var(self, cell_var):
        self.cell_vars.add(cell_var)

    def get_stores(self) -> Set[str]:
        return {*()}

    def get_loads(self) -> Set[str]:
        return {*()}

    def get_dels(self) -> Set[str]:
        return {*()}

    def get_name(self) -> str:
        if self.is_async:
            return f'<async function {self.name}>'
        else:
            return f'<function {self.name}'

    def __repr__(self) -> str:
        decrs = ', '.join([ast.unparse(decr) for decr in self.decorator_list])
        args = ast.unparse(self.args)
        if self.returns is not None:
            rets = f' -> {ast.unparse(self.returns)}'
        else:
            rets = ''
        if len(decrs) > 0:
            fn_repr = f'fn [{decrs}] {self.name}({args}){rets}'
        else:
            fn_repr = f'fn {self.name}({args}){rets}'
        if self.is_async:
            fn_repr = f'async {fn_repr}'
        return fn_repr

    def __str__(self):
        return self.__repr__()
