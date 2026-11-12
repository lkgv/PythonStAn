"""Rename matching normalized AST names without changing unrelated nodes."""

import ast
import copy
from typing import override

__all__ = ["RenameTransformer"]


class RenameTransformer(ast.NodeTransformer):
    old_name: str
    new_name: str
    ctxs: tuple[type[ast.AST], ...]

    def __init__(self, old_name: str, new_name: str, ctxs: tuple[type[ast.AST], ...]) -> None:
        self.old_name = old_name
        self.new_name = new_name
        self.ctxs = ctxs

    @override
    def visit_Name(self, node: ast.Name) -> ast.Name:
        name = node
        if isinstance(node.ctx, self.ctxs) and name.id == self.old_name:
            name = copy.deepcopy(node)
            name.id = self.new_name
        return name

    @override
    def visit_alias(self, node: ast.alias) -> ast.alias:
        alias = node
        if isinstance(ast.Store(), self.ctxs) and (
            alias.asname == self.old_name or alias.asname is None and alias.name == self.old_name
        ):
            alias = copy.deepcopy(node)
            alias.asname = self.new_name
        return alias
