"""Callee policy transitions needed by the planned finite method policy table."""

from __future__ import annotations

import ast

from pythonstan.analysis.pointer.kcfa.context import CallSite, CallStringContext
from pythonstan.analysis.pointer.kcfa.context_selector import ContextPolicy, ContextSelector
from pythonstan.ir.ir_statements import IRCall


def site(expression: str) -> CallSite:
    node = ast.parse(expression).body[0]
    assert isinstance(node, ast.Expr) and isinstance(node.value, ast.Call)
    return CallSite(IRCall(node.value), "subject")


def test_selected_two_call_context_restarts_at_its_declared_bound() -> None:
    current = site("current()")
    selected = ContextSelector(ContextPolicy.CALL_2).select_call_context(
        current, CallStringContext((), 0)
    )
    assert isinstance(selected, CallStringContext)
    assert selected.k == 2
    assert selected.call_sites == (current,)


def test_selected_one_call_context_does_not_inherit_callers_larger_bound() -> None:
    history = (site("first()"), site("second()"), site("third()"))
    current = site("current()")
    selected = ContextSelector(ContextPolicy.CALL_1).select_call_context(
        current, CallStringContext(history, 3)
    )
    assert isinstance(selected, CallStringContext)
    assert selected.k == 1
    assert selected.call_sites == (current,)


def test_homogeneous_two_call_context_positive_control() -> None:
    previous = site("previous()")
    current = site("current()")
    selected = ContextSelector(ContextPolicy.CALL_2).select_call_context(
        current, CallStringContext((previous,), 2)
    )
    assert isinstance(selected, CallStringContext)
    assert selected.k == 2
    assert selected.call_sites == (previous, current)
