"""Collect normalized variable names under selected AST expression contexts."""

import ast
from typing import override


class VarCollector(ast.NodeVisitor):
    var_map: dict[str, int]
    ctx: tuple[type[ast.expr_context], ...]
    next_idx: int

    def __init__(self, ctx: str = "") -> None:
        self.reset(ctx)

    def reset(self, ctx: str = "") -> None:
        self.next_idx = 0
        self.var_map = {}
        if ctx == "store":
            self.ctx = (ast.Store,)
        elif ctx == "no_store":
            self.ctx = (ast.Load, ast.Del)
        elif ctx == "load":
            self.ctx = (ast.Load,)
        elif ctx == "del":
            self.ctx = (ast.Del,)
        else:
            self.ctx = (ast.Store, ast.Load, ast.Del)

    def find(self, name: object) -> int | None:
        if isinstance(name, ast.Name):
            return self.var_map.get(name.id, None)
        if isinstance(name, str):
            return self.var_map.get(name, None)
        return None

    def get_vars(self) -> set[str]:
        return set(self.var_map.keys())

    def size(self) -> int:
        return len(self.var_map)

    @override
    def visit_Name(self, node: ast.Name) -> None:
        if isinstance(node.ctx, self.ctx) and node.id not in self.var_map:
            self.var_map[node.id] = self.next_idx
            self.next_idx += 1
        self.generic_visit(node)

    @override
    def visit_alias(self, node: ast.alias) -> None:
        if isinstance(ast.Store(), self.ctx) and node.name != "*" and node.name not in self.var_map:
            self.var_map[node.name] = self.next_idx
            self.next_idx += 1
        self.generic_visit(node)

    @override
    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        if isinstance(ast.Store(), self.ctx) and node.name not in self.var_map:
            self.var_map[node.name] = self.next_idx
            self.next_idx += 1

    @override
    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        if isinstance(ast.Store(), self.ctx) and node.name not in self.var_map:
            self.var_map[node.name] = self.next_idx
            self.next_idx += 1

    @override
    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        if isinstance(ast.Store(), self.ctx) and node.name not in self.var_map:
            self.var_map[node.name] = self.next_idx
            self.next_idx += 1

    @override
    def visit_arg(self, node: ast.arg) -> None:
        if isinstance(ast.Store(), self.ctx) and node.arg not in self.var_map:
            self.var_map[node.arg] = self.next_idx
            self.next_idx += 1
