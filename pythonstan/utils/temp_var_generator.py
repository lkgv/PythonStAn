"""Run-local AST temporary construction with explicit context ownership."""

from __future__ import annotations

import ast
import copy
from collections.abc import Iterable
from typing import TypeGuard

TEMP_VAR_TEMPLATE = "$tmp_%d"


def destructable(node: object) -> TypeGuard[ast.Tuple | ast.List]:
    return isinstance(node, (ast.Tuple, ast.List))


def update_ctx[NodeT: ast.AST](node: NodeT, ctx: ast.expr_context) -> NodeT:
    new_node = copy.deepcopy(node)
    if hasattr(node, "ctx"):
        ast.AST.__setattr__(new_node, "ctx", ctx)
    return new_node


class TempVarGenerator:
    next_idx: int
    template: str
    var_dict: dict[str, dict[str, ast.Name]]

    @staticmethod
    def get_ctx_name(ctx: ast.expr_context) -> str:
        return ctx.__class__.__name__

    def __init__(self, next_idx: int = 0, template: str = TEMP_VAR_TEMPLATE) -> None:
        self.reset(next_idx, template)

    def reset(self, next_idx: int = 0, template: str = TEMP_VAR_TEMPLATE) -> None:
        self.next_idx = next_idx
        self.template = template
        self.var_dict = {"Store": {}, "Load": {}, "Del": {}}

    def gen(
        self, idx: int | None = None, ctxs: Iterable[ast.expr_context] | None = None
    ) -> list[ast.Name]:
        if ctxs is None:
            ctxs = [ast.Load(), ast.Store()]
        if idx is None:
            idx = self.gen_idx()
        var_name = self.template % (idx,)
        result: list[ast.Name] = []
        for ctx in ctxs:
            context_name = self.get_ctx_name(ctx)
            variable = ast.Name(id=var_name, ctx=ctx)
            self.var_dict[context_name][var_name] = variable
            result.append(variable)
        return result

    def __call__(
        self, idx: int | None = None, ctxs: Iterable[ast.expr_context] | None = None
    ) -> list[ast.Name]:
        return self.gen(idx, ctxs)

    def gen_idx(self) -> int:
        idx = self.next_idx
        self.next_idx += 1
        return idx

    def get(self, var: ast.Name, ctx: ast.expr_context) -> ast.Name:
        if isinstance(var.ctx, ctx.__class__):
            return var
        context_name = self.get_ctx_name(ctx)
        if var.id not in self.var_dict[context_name]:
            self.var_dict[context_name][var.id] = ast.Name(id=var.id, ctx=ctx)
        return self.var_dict[context_name][var.id]
