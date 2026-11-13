"""Lexical binding classification, independent of an analysis engine."""

from __future__ import annotations

import ast
from typing import NamedTuple, override

type BindingScopeNode = (
    ast.Module | ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda | ast.ClassDef
)


class BindingClassification(NamedTuple):
    locals: frozenset[str]
    globals: frozenset[str]
    nonlocals: frozenset[str]
    has_yield: bool


def classify_bindings(node: BindingScopeNode) -> BindingClassification:
    local: set[str] = set()
    global_names: set[str] = set()
    nonlocal_names: set[str] = set()
    has_yield = False
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
        args = node.args
        local.update(argument.arg for argument in (*args.posonlyargs, *args.args, *args.kwonlyargs))
        if args.vararg is not None:
            local.add(args.vararg.arg)
        if args.kwarg is not None:
            local.add(args.kwarg.arg)

    class Scan(ast.NodeVisitor):
        @override
        def visit_Global(self, current: ast.Global) -> None:
            global_names.update(current.names)

        @override
        def visit_Nonlocal(self, current: ast.Nonlocal) -> None:
            nonlocal_names.update(current.names)

        @override
        def visit_Name(self, current: ast.Name) -> None:
            if isinstance(current.ctx, (ast.Store, ast.Del)):
                local.add(current.id)

        @override
        def visit_FunctionDef(self, current: ast.FunctionDef) -> None:
            local.add(current.name)

        @override
        def visit_AsyncFunctionDef(self, current: ast.AsyncFunctionDef) -> None:
            local.add(current.name)

        @override
        def visit_ClassDef(self, current: ast.ClassDef) -> None:
            local.add(current.name)

        @override
        def visit_Lambda(self, current: ast.Lambda) -> None:
            pass

        def _comprehension(
            self, current: ast.ListComp | ast.SetComp | ast.DictComp | ast.GeneratorExp
        ) -> None:
            # Targets belong to the implicit scope. NamedExpr targets in the
            # remaining expressions still bind in this enclosing scope.
            for generator in current.generators:
                self.visit(generator.iter)
                for condition in generator.ifs:
                    self.visit(condition)
            if isinstance(current, ast.DictComp):
                self.visit(current.key)
                self.visit(current.value)
            else:
                self.visit(current.elt)

        @override
        def visit_ListComp(self, current: ast.ListComp) -> None:
            self._comprehension(current)

        @override
        def visit_SetComp(self, current: ast.SetComp) -> None:
            self._comprehension(current)

        @override
        def visit_DictComp(self, current: ast.DictComp) -> None:
            self._comprehension(current)

        @override
        def visit_GeneratorExp(self, current: ast.GeneratorExp) -> None:
            self._comprehension(current)

        @override
        def visit_Import(self, current: ast.Import) -> None:
            for alias in current.names:
                local.add(alias.asname or alias.name.split(".")[0])

        @override
        def visit_ImportFrom(self, current: ast.ImportFrom) -> None:
            for alias in current.names:
                if alias.name != "*":
                    local.add(alias.asname or alias.name)

        @override
        def visit_ExceptHandler(self, current: ast.ExceptHandler) -> None:
            if current.name is not None:
                local.add(current.name)
            for statement in current.body:
                self.visit(statement)

        @override
        def visit_Yield(self, current: ast.Yield) -> None:
            nonlocal has_yield
            has_yield = True
            if current.value is not None:
                self.visit(current.value)

        @override
        def visit_YieldFrom(self, current: ast.YieldFrom) -> None:
            nonlocal has_yield
            has_yield = True
            self.visit(current.value)

    scan = Scan()
    if isinstance(node, ast.Lambda):
        scan.visit(node.body)
    else:
        for statement in node.body:
            scan.visit(statement)
    return BindingClassification(
        frozenset(local - global_names - nonlocal_names),
        frozenset(global_names),
        frozenset(nonlocal_names),
        has_yield,
    )
