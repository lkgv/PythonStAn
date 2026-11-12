"""Compiler-style class-private identifiers for the owned AST execution model.

String literals and keyword-call labels stay literal. Declared display names
remain available separately from the compiler's mangled binding names.
"""

from __future__ import annotations

import ast


def mangle(private: str | None, name: str) -> str:
    if not private or not name or not name.startswith("__") or name.endswith("__") or "." in name:
        return name
    prefix = private.lstrip("_")
    return "_" + prefix + name if prefix else name


def source_declared_name(node: ast.AST) -> str | None:
    """Read the compiler-owned original declaration name from an AST extension."""
    value: object = vars(node).get("_source_declared_name")
    if value is None or isinstance(value, str):
        return value
    raise TypeError("AST declaration-name metadata must be text")


def source_annotation_text(node: ast.AST) -> str | None:
    """Read annotation text preserved before private identifier lowering."""
    value: object = vars(node).get("_source_annotation_text")
    if value is None or isinstance(value, str):
        return value
    raise TypeError("AST annotation metadata must be text")


def _is_lowered(tree: ast.AST) -> bool:
    value: object = vars(tree).get("_private_names_lowered", False)
    if not isinstance(value, bool):
        raise TypeError("AST private-name lowering marker must be boolean")
    return value


def lower_private_names[NodeT: ast.AST](tree: NodeT) -> NodeT:
    if _is_lowered(tree):
        return tree
    pending: list[tuple[ast.AST, str | None]] = [(tree, None)]
    while pending:
        node, private = pending.pop()
        if isinstance(node, ast.ClassDef):
            original = node.name
            changed = mangle(private, original)
            if changed != original:
                vars(node)["_source_declared_name"] = original
                node.name = changed
            # Header expressions are evaluated in the enclosing private scope;
            # the body starts the class's own lexical private scope.
            pending.extend(
                (child, private)
                for child in [
                    *node.decorator_list,
                    *node.bases,
                    *node.keywords,
                    *node.type_params,
                ]
            )
            pending.extend((child, original) for child in node.body)
            continue
        # Preserve the compiler's original annotation text before identifiers
        # in that expression are lowered. Future annotations store that text.
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            annotations = [
                arg.annotation
                for arg in (*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs)
            ]
            annotations.extend(
                [
                    node.returns,
                    node.args.vararg.annotation if node.args.vararg else None,
                    node.args.kwarg.annotation if node.args.kwarg else None,
                ]
            )
            for annotation in annotations:
                if annotation is not None and source_annotation_text(annotation) is None:
                    vars(annotation)["_source_annotation_text"] = ast.unparse(annotation)
        elif isinstance(node, ast.AnnAssign) and source_annotation_text(node.annotation) is None:
            vars(node.annotation)["_source_annotation_text"] = ast.unparse(node.annotation)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            original = node.name
            changed = mangle(private, original)
            if changed != original:
                vars(node)["_source_declared_name"] = original
                node.name = changed
        if isinstance(node, ast.Name):
            node.id = mangle(private, node.id)
        elif isinstance(node, ast.Attribute):
            node.attr = mangle(private, node.attr)
        elif isinstance(node, ast.arg):
            node.arg = mangle(private, node.arg)
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            node.names = [mangle(private, name) for name in node.names]
        elif isinstance(node, (ast.ExceptHandler, ast.MatchAs, ast.MatchStar)) and node.name:
            node.name = mangle(private, node.name)
        elif isinstance(node, ast.MatchMapping) and node.rest:
            node.rest = mangle(private, node.rest)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                alias.name = mangle(private, alias.name)
                if alias.asname:
                    alias.asname = mangle(private, alias.asname)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                node.module = mangle(private, node.module)
            for alias in node.names:
                alias.name = mangle(private, alias.name)
                if alias.asname:
                    alias.asname = mangle(private, alias.asname)
        pending.extend((child, private) for child in ast.iter_child_nodes(node))
    vars(tree)["_private_names_lowered"] = True
    return tree
