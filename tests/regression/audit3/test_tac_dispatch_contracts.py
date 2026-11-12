"""Actual AST dispatch retains iterator effects, statement identity and root scope."""

from __future__ import annotations

import ast
from collections.abc import Iterator
from typing import Self, override

import pytest

from pythonstan.analysis.transform.three_address import ThreeAddressTransformer
from pythonstan.utils.temp_var_generator import TempVarGenerator


class _OneShotPair:
    def __init__(self, prefix: object, value: ast.expr) -> None:
        self.rows = (prefix, value)
        self.index = 0
        self.iterations = 0

    def __iter__(self) -> Self:
        self.iterations += 1
        if self.iterations != 1:
            raise RuntimeError("The pair iterator was restarted")
        return self

    def __next__(self) -> object:
        if self.index == len(self.rows):
            raise StopIteration
        row = self.rows[self.index]
        self.index += 1
        return row


class _ObservedPrefix(list[ast.stmt]):
    def __init__(self, statement: ast.stmt) -> None:
        super().__init__([statement])
        self.iterations = 0

    @override
    def __iter__(self) -> Iterator[ast.stmt]:
        self.iterations += 1
        return super().__iter__()


class _OverrideDispatch(ThreeAddressTransformer):
    def __init__(self, prefix: object) -> None:
        super().__init__()
        self.prefix = prefix
        self.pair: _OneShotPair | None = None

    @override
    def visit(self, node: ast.AST) -> object:
        if isinstance(node, ast.Name):
            self.pair = _OneShotPair(self.prefix, node)
            return self.pair
        return super().visit(node)


def test_expression_dispatch_consumes_foreign_pair_and_prefix_once() -> None:
    statement = ast.Pass()
    prefix = _ObservedPrefix(statement)
    transformer = _OverrideDispatch(prefix)
    name = ast.Name(id="value", ctx=ast.Load())
    statements, value = transformer.split_expr(name)
    assert transformer.pair is not None
    assert transformer.pair.iterations == 1
    assert prefix.iterations == 1
    assert statements == [statement]
    assert statements[0] is statement
    assert value is name


def test_expression_dispatch_keeps_supported_generator_prefix_identity() -> None:
    statement = ast.Pass()
    observed: list[str] = []

    def prefix() -> Iterator[ast.stmt]:
        observed.append("start")
        yield statement
        observed.append("stop")

    transformer = _OverrideDispatch(prefix())
    statements, value = transformer.split_expr(ast.Name(id="value", ctx=ast.Load()))
    assert observed == ["start", "stop"]
    assert statements[0] is statement
    assert isinstance(value, ast.Name)


def test_temporary_generation_keeps_context_specific_cached_names() -> None:
    generator = TempVarGenerator(next_idx=3, template="$local_%d")
    load, store = generator()
    assert load.id == store.id == "$local_3"
    assert isinstance(load.ctx, ast.Load)
    assert isinstance(store.ctx, ast.Store)
    assert generator.get(load, ast.Load()) is load
    assert generator.get(load, ast.Store()) is store
    assert generator.next_idx == 4


def test_expression_root_explicitly_rejects_missing_statement_context() -> None:
    transformer = ThreeAddressTransformer()
    with pytest.raises(TypeError, match="statement-prefix context"):
        transformer.visit(ast.parse("value + 1", mode="eval"))
