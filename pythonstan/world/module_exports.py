"""Source-owned module names, distinct from builtin name lookup fallback."""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Protocol, override, runtime_checkable

from pythonstan.compat.host_iteration import contains_object
from pythonstan.compat.host_paths import filesystem_path
from pythonstan.ir.ir_statements import IRScope


@runtime_checkable
class _FilenameReader(Protocol):
    def __call__(self) -> object: ...


def _source_path(value: object) -> Path:
    raw = filesystem_path(value)
    if isinstance(raw, str):
        return Path(raw)
    raise TypeError("Module filename must be a string or text path")


class _Bindings(ast.NodeVisitor):
    def __init__(self) -> None:
        self.names: set[str] = set()

    @override
    def visit_Name(self, node: ast.Name) -> None:
        if isinstance(node.ctx, ast.Store):
            self.names.add(node.id)

    @override
    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self.names.add(alias.asname or alias.name.split(".")[0])

    @override
    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        for alias in node.names:
            if alias.name != "*":
                self.names.add(alias.asname or alias.name)

    @override
    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        if node.name:
            self.names.add(node.name)
        self.generic_visit(node)

    @override
    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        self.visit(node.annotation)
        if node.value is not None:
            self.visit(node.target)
            self.visit(node.value)

    @override
    def visit_FunctionDef(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        self.names.add(node.name)
        for expression in (
            *node.decorator_list,
            *node.args.defaults,
            *(v for v in node.args.kw_defaults if v is not None),
        ):
            self.visit(expression)
        for argument in (
            *node.args.posonlyargs,
            *node.args.args,
            *node.args.kwonlyargs,
            *(a for a in (node.args.vararg, node.args.kwarg) if a is not None),
        ):
            if argument.annotation is not None:
                self.visit(argument.annotation)
        if node.returns is not None:
            self.visit(node.returns)

    visit_AsyncFunctionDef = visit_FunctionDef

    @override
    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.names.add(node.name)
        for expression in (*node.decorator_list, *node.bases, *(k.value for k in node.keywords)):
            self.visit(expression)

    @override
    def visit_Lambda(self, node: ast.Lambda) -> None:
        for expression in (
            *node.args.defaults,
            *(v for v in node.args.kw_defaults if v is not None),
        ):
            self.visit(expression)

    def _comprehension(self, node: ast.AST) -> None:
        # Comprehension targets are local. Assignment expressions explicitly
        # bind in the containing scope, including those in later clauses.
        collector = _NamedExpressions()
        for child in ast.iter_child_nodes(node):
            collector.visit(child)
        self.names.update(collector.names)

    visit_ListComp = _comprehension
    visit_SetComp = _comprehension
    visit_DictComp = _comprehension
    visit_GeneratorExp = _comprehension


class _NamedExpressions(_Bindings):
    @override
    def visit_Name(self, node: ast.Name) -> None:
        pass

    @override
    def visit_NamedExpr(self, node: ast.NamedExpr) -> None:
        self.names.add(node.target.id)
        self.visit(node.value)


class _GlobalNames(ast.NodeVisitor):
    def __init__(self, names: set[str]) -> None:
        self.names = names

    @override
    def visit_Global(self, node: ast.Global) -> None:
        self.names.update(node.names)

    @override
    def visit_FunctionDef(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        pass

    visit_AsyncFunctionDef = visit_FunctionDef

    @override
    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        pass


def source_export_names(module: IRScope) -> object:
    """Conservative lexical ownership; values still come from real constraints."""
    cache_name = "_source_export_names"
    cached: object = getattr(module, cache_name, None)
    if cached is not None:
        return cached
    method_name = "get_filename"
    getter: object = getattr(module, method_name)
    if not isinstance(getter, _FilenameReader):
        raise TypeError("Module filename provider is not callable")
    filename = getter()
    try:
        tree = (
            ast.parse(_source_path(filename).read_text())
            if filename and _source_path(filename).is_file()
            else module.get_ast()
        )
    except (OSError, SyntaxError, UnicodeError):
        tree = module.get_ast()
    visitor = _Bindings()
    visitor.visit(tree)
    # Writes under an explicit global declaration in nested source scopes can
    # introduce exports after initialization. Merely reading a global cannot.
    for scope in ast.walk(tree):
        if not isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        local = _Bindings()
        globals_written: set[str] = set()

        selector = _GlobalNames(globals_written)
        for statement in scope.body:
            selector.visit(statement)
            local.visit(statement)
        visitor.names.update(local.names & globals_written)
    names = visitor.names | {
        "__name__",
        "__package__",
        "__file__",
        "__doc__",
        "__loader__",
        "__spec__",
        "__builtins__",
        "__annotations__",
    }
    exports = frozenset(name for name in names if name.isidentifier())
    setattr(module, cache_name, exports)
    return exports


def source_has_export(module: IRScope, name: str) -> bool:
    """Read lexical ownership through the original cache membership protocol."""
    return contains_object(source_export_names(module), name)
