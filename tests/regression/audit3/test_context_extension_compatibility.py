"""Context selection retains open context inputs before finite policy projection."""

from __future__ import annotations

import ast
from dataclasses import dataclass
from typing import override

import pytest

from pythonstan.analysis.pointer.kcfa.context import (
    AbstractContext,
    CallSite,
    CallStringContext,
    SummaryContext,
)
from pythonstan.analysis.pointer.kcfa.context_selector import ContextPolicy, ContextSelector
from pythonstan.analysis.pointer.kcfa.protocol_context import (
    KeywordPartition,
    ProtocolKeyContext,
    split_protocol_context,
)
from pythonstan.ir.ir_statements import IRCall


@dataclass(frozen=True)
class _ExtensionContext(AbstractContext[int]):
    call_sites: tuple[CallSite, ...]
    token: int = 0

    @override
    def to_string(self) -> str:
        return f"extension:{self.token}"

    @override
    def is_empty(self) -> bool:
        return not self.call_sites

    @override
    def append(self, call_site: int) -> _ExtensionContext:
        return _ExtensionContext(self.call_sites, call_site)

    @override
    def __hash__(self) -> int:
        return hash((self.call_sites, self.token))


def _site(name: str) -> CallSite:
    statement = ast.parse(f"{name}()").body[0]
    assert isinstance(statement, ast.Expr)
    assert isinstance(statement.value, ast.Call)
    return CallSite(IRCall(statement.value), name)


@pytest.mark.parametrize("policy", list(ContextPolicy))
def test_custom_root_projects_using_selected_policy(policy: ContextPolicy) -> None:
    history = _site("history")
    operation = _site("operation")
    extension = _ExtensionContext((history,))
    before = dict(vars(extension))
    selector = ContextSelector(policy)
    expected_call = selector.select_call_context(operation, None)
    empty = selector.empty_context()
    heap_input = (
        CallStringContext((history,) if empty.k else (), empty.k)
        if isinstance(empty, CallStringContext)
        else empty
    )
    expected_heap = selector.select_alloc_context(heap_input, operation)
    assert selector.select_call_context(operation, extension) == expected_call
    assert selector.select_alloc_context(extension, operation) == expected_heap
    assert vars(extension) == before


@pytest.mark.parametrize("policy", list(ContextPolicy))
def test_custom_wrapped_root_preserves_keyword_and_summary(policy: ContextPolicy) -> None:
    history = _site("history")
    operation = _site("operation")
    extension = _ExtensionContext((history,))
    partition = KeywordPartition(operation, None, "summary", None)
    wrapped = SummaryContext(ProtocolKeyContext(extension, partition))
    selector = ContextSelector(policy)
    call_result = selector.select_call_context(operation, wrapped)
    heap_result = selector.select_alloc_context(wrapped, operation)
    for result in (call_result, heap_result):
        base, actual_partition = split_protocol_context(result)
        assert isinstance(base, SummaryContext)
        assert actual_partition is partition
