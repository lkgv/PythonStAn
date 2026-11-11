"""Immutable source obligations preserved by executable AST lowering.

These records describe missing semantic transfers. They neither replace AST
execution nor add a fabricated semantic top/call edge. AST dumps and source
fingerprints remain based on the actual source fields; the in-memory analysis
pipeline carries this metadata separately through lowering and IR construction.
"""
from dataclasses import dataclass
from typing import Tuple
import ast


@dataclass(frozen=True)
class FrontendResidual:
    code: str
    reason: str
    effects: Tuple[str, ...]
    source_kind: str
    source_line: int


def attach_frontend_residual(node, origin, code, reason, effects):
    """Attach an obligation to a generated statement without changing its AST."""
    residual = FrontendResidual(code, reason, tuple(effects), type(origin).__name__,
                                getattr(origin, "lineno", 0))
    current = get_frontend_residuals(node)
    if residual not in current:
        node._pythonstan_semantic_residuals = current + (residual,)
    return node


def get_frontend_residuals(node):
    """Read only metadata on this node, never scan nested/unexecuted scopes."""
    if not isinstance(node, ast.AST):
        return ()
    return tuple(getattr(node, "_pythonstan_semantic_residuals", ()))
