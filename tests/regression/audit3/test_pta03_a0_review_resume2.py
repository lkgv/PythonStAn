"""Independent exact-source-history tails for selected method bounds."""

from __future__ import annotations

import ast

from pythonstan.analysis.pointer.kcfa.context import (
    CallSite,
    CallStringContext,
    FiniteContextAtom,
    HybridContext,
    ObjectContext,
    ParamContext,
    SummaryContext,
    ordered_binding_token,
)
from pythonstan.analysis.pointer.kcfa.context_selector import ContextPolicy, ContextSelector
from pythonstan.analysis.pointer.kcfa.object import AbstractObject
from pythonstan.analysis.pointer.kcfa.protocol_context import (
    KeywordPartition,
    ProtocolKeyContext,
    apply_native_protocol_partition,
)
from pythonstan.ir.ir_statements import IRCall
from tests.pointer.kcfa.builders import make_site


def call(name: str) -> CallSite:
    expression = ast.parse(name + "()").body[0]
    assert isinstance(expression, ast.Expr) and isinstance(expression.value, ast.Call)
    return CallSite(IRCall(expression.value), "independent")


def test_object_bound_shrink_preserves_only_current_source() -> None:
    old = ObjectContext(("first", "second", "third"), 3)
    current = call("fourth")
    selected = ContextSelector(ContextPolicy.OBJ_1).select_call_context(current, old)
    assert isinstance(selected, ObjectContext)
    assert selected.depth == 1 and selected.alloc_sites == (current,)
    assert old.depth == 3 and old.alloc_sites == ("first", "second", "third")


def test_object_bound_growth_keeps_only_history_that_exists() -> None:
    old = ObjectContext(("available",), 1)
    receiver = AbstractObject(CallStringContext((), 0), make_site(name="receiver"))
    selected = ContextSelector(ContextPolicy.OBJ_3).select_call_context(call("next"), old, receiver)
    assert isinstance(selected, ObjectContext)
    assert selected.depth == 3 and len(selected.alloc_sites) == 2
    assert selected.alloc_sites[0] == "available"
    atom = selected.alloc_sites[1]
    assert isinstance(atom, FiniteContextAtom)
    assert atom.kind == "object" and atom.source is receiver.alloc_site and atom.origins == ()


def test_parameter_shrink_without_new_token_keeps_latest_bindings() -> None:
    first = ordered_binding_token(("discard",))
    latest = ordered_binding_token(("right", "left", "left"), {"named": "right"})
    old = ParamContext((first, latest), 3)
    selected = ContextSelector(ContextPolicy.PARAM_1).select_call_context(call("next"), old)
    assert isinstance(selected, ParamContext)
    assert selected.depth == 1 and selected.params == (latest,)
    assert latest.bindings == (
        ("pos:0", "right"),
        ("pos:1", "left"),
        ("pos:2", "left"),
        ("kw:named", "right"),
    )
    assert old.params == (first, latest)


def test_parameter_reselection_preserves_duplicate_arguments_and_token_order() -> None:
    prior = ordered_binding_token(("before",))
    latest = ordered_binding_token(("a", "a", "b"), {"x": "b"})
    old = ParamContext((ordered_binding_token(("discard",)), prior), 3)
    selected = ContextSelector(ContextPolicy.PARAM_2).select_call_context(
        call("next"), old, params=latest
    )
    assert isinstance(selected, ParamContext)
    assert selected.depth == 2 and selected.params == (prior, latest)
    assert latest.bindings[:3] == (("pos:0", "a"), ("pos:1", "a"), ("pos:2", "b"))


def test_cross_family_parameter_policy_does_not_invent_argument_history() -> None:
    old = CallStringContext((call("before"),), 3)
    selected = ContextSelector(ContextPolicy.PARAM_3).select_call_context(call("next"), old)
    assert isinstance(selected, ParamContext)
    assert selected.depth == 3 and selected.params == ()


def test_raw_ir_history_growth_retains_exact_two_existing_statements() -> None:
    first, second, current = (call(name).statement for name in ("first", "second", "current"))
    old = HybridContext((first, second), (), 2, 1)
    selected = ContextSelector(ContextPolicy.CALL_3).select_call_context(current, old)
    assert isinstance(selected, CallStringContext)
    assert selected.k == 3 and selected.call_sites == (first, second, current)
    assert all(
        actual is expected
        for actual, expected in zip(selected.call_sites, (first, second, current), strict=True)
    )


def test_cross_family_summary_protocol_are_flat_and_keep_new_call_only() -> None:
    old = ObjectContext(("not-call-history",), 3)
    token = KeywordPartition(call("protocol"), None, "summary", None)
    wrapped = apply_native_protocol_partition(SummaryContext(old), token)
    current = call("next")
    selected = ContextSelector(ContextPolicy.CALL_2).select_call_context(current, wrapped)
    assert isinstance(selected, SummaryContext)
    assert isinstance(selected.inner, ProtocolKeyContext)
    assert selected.inner.partition is token
    base = selected.inner.inner
    assert isinstance(base, CallStringContext)
    assert base.k == 2 and base.call_sites == (current,)
    assert not isinstance(base, (SummaryContext, ProtocolKeyContext))


def test_hybrid_callee_rebinds_call_and_object_bounds_independently() -> None:
    old = HybridContext((call("one"), call("two"), call("three")), ("available-object",), 3, 1)
    current = call("now")
    selected = ContextSelector(ContextPolicy.HYBRID_CALL1_OBJ2).select_call_context(current, old)
    assert isinstance(selected, HybridContext)
    assert selected.call_k == 1 and selected.obj_depth == 2
    assert selected.call_sites == (current,)
    assert selected.alloc_sites == ("available-object", None)
    assert len(old.call_sites) == 3 and old.obj_depth == 1


def test_independent_zero_heap_projection_does_not_mutate_selected_method_history() -> None:
    selector = ContextSelector(
        ContextPolicy.CALL_3, heap_context_policy="1-cfa", heap_context_depth=0
    )
    prior, current = call("prior"), call("current")
    method = selector.select_call_context(current, CallStringContext((prior,), 1))
    assert isinstance(method, CallStringContext)
    allocated = selector.select_alloc_context(method, make_site())
    assert isinstance(allocated, CallStringContext)
    assert allocated.k == 0 and allocated.call_sites == ()
    assert method.k == 3 and method.call_sites == (prior, current)
