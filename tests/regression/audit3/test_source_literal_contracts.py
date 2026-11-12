"""Checked AST constants preserve native literal kinds and reject live payloads."""

from __future__ import annotations

import ast

import pytest

from pythonstan.frontend.source import SourceLiteral, source_constant


def external_constant(payload: object) -> ast.Constant:
    """Model the raw CPython/optimizer AST field beyond the scalar ctor stub."""
    node = ast.Constant(value=None)
    vars(node)["value"] = payload
    return node


@pytest.mark.parametrize(
    "value", (None, False, 3, 1.5, 2j, "text", b"bytes", Ellipsis, (1, b"x"), frozenset((1, "x")))
)
def test_source_literal_boundary_preserves_supported_native_payload(value: SourceLiteral) -> None:
    actual = source_constant(external_constant(value))
    assert type(actual) is type(value) and actual == value


@pytest.mark.parametrize("value", ([], {"unsupported": 1}, object()))
def test_raw_ast_boundary_rejects_three_invalid_payload_kinds(value: object) -> None:
    with pytest.raises(TypeError, match="unsupported payload"):
        source_constant(external_constant(value))


def test_nested_optimized_constant_validates_every_member() -> None:
    assert source_constant(external_constant((None, frozenset((b"x", 2j))))) == (
        None,
        frozenset((b"x", 2j)),
    )
    with pytest.raises(TypeError, match="unsupported payload"):
        source_constant(external_constant((1, {"live": object()})))
