"""Immutable source obligations preserved by executable AST lowering.

These records describe missing semantic transfers. They neither replace AST
execution nor add a fabricated semantic top/call edge. AST dumps and source
fingerprints remain based on the actual source fields; the in-memory analysis
pipeline carries this metadata separately through lowering and IR construction.
"""

import ast
from collections.abc import Iterable
from dataclasses import dataclass
from typing import TypeGuard

from pythonstan.compat.host_iteration import iter_objects

_RESIDUAL_ATTRIBUTE = "_pythonstan_semantic_residuals"


@dataclass(frozen=True)
class FrontendResidual:
    code: str
    reason: str
    effects: tuple[str, ...]
    source_kind: str
    source_line: int


def _residual_tuple(values: tuple[object, ...]) -> TypeGuard[tuple[FrontendResidual, ...]]:
    return all(isinstance(value, FrontendResidual) for value in values)


def attach_frontend_residual[NodeT: ast.AST](
    node: NodeT, origin: ast.AST, code: str, reason: str, effects: Iterable[str]
) -> NodeT:
    """Attach an obligation to a generated statement without changing its AST."""
    line: object = getattr(origin, "lineno", 0)
    if not isinstance(line, int):
        raise TypeError("frontend residual source position must be an integer")
    residual = FrontendResidual(code, reason, tuple(effects), type(origin).__name__, line)
    current = get_frontend_residuals(node)
    if residual not in current:
        setattr(node, _RESIDUAL_ATTRIBUTE, current + (residual,))
    return node


def get_frontend_residuals(node: object) -> tuple[FrontendResidual, ...]:
    """Read this node's finite obligation payload without scanning nested scopes."""
    if not isinstance(node, ast.AST):
        return ()
    metadata: object = getattr(node, _RESIDUAL_ATTRIBUTE, ())
    if type(metadata) is tuple:
        values: tuple[object, ...] = metadata
        if not _residual_tuple(values):
            raise TypeError("frontend residual metadata must contain FrontendResidual values")
        return values
    iterator = iter_objects(metadata)
    result: list[FrontendResidual] = []
    while True:
        try:
            residual = next(iterator)
        except StopIteration:
            return tuple(result)
        if not isinstance(residual, FrontendResidual):
            raise TypeError("frontend residual metadata must contain FrontendResidual values")
        result.append(residual)
