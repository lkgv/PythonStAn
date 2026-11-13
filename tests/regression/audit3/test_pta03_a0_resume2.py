"""Selected method bounds and flat wrappers, before adding policy table ports."""

from __future__ import annotations

import pytest

from pythonstan.analysis.pointer.kcfa.context import (
    CallStringContext,
    HybridContext,
    NativeBaseContext,
    ObjectContext,
    ParamContext,
    ReceiverContext,
    SummaryContext,
    TypeContext,
    ordered_binding_token,
)
from pythonstan.analysis.pointer.kcfa.context_selector import ContextPolicy, ContextSelector
from pythonstan.analysis.pointer.kcfa.protocol_context import (
    KeywordPartition,
    ProtocolKeyContext,
    apply_native_protocol_partition,
    split_native_protocol_context,
)

from .test_pta03_transition_resume2 import site


def broad_caller(selector: ContextSelector) -> NativeBaseContext:
    empty = selector.empty_context()
    calls = (site("first()"), site("second()"), site("third()"))
    if isinstance(empty, CallStringContext):
        return CallStringContext(calls, 3)
    if isinstance(empty, ObjectContext):
        return ObjectContext(("one", "two", "three"), 3)
    if isinstance(empty, TypeContext):
        return TypeContext(("one", "two", "three"), 3)
    if isinstance(empty, ReceiverContext):
        return ReceiverContext(("one", "two", "three"), 3)
    if isinstance(empty, ParamContext):
        return ParamContext(tuple(ordered_binding_token((str(i),)) for i in range(3)), 3)
    assert isinstance(empty, HybridContext)
    return HybridContext(calls, ("one", "two", "three"), 3, 3)


@pytest.mark.parametrize("policy", list(ContextPolicy))
def test_every_registered_policy_rebinds_to_its_own_declared_shape(policy: ContextPolicy) -> None:
    selector = ContextSelector(policy)
    empty = selector.empty_context()
    selected = selector.select_call_context(
        site("current()"), broad_caller(selector), params=ordered_binding_token(("left", "right"))
    )
    assert type(selected) is type(empty)
    if isinstance(selected, CallStringContext) and isinstance(empty, CallStringContext):
        assert selected.k == empty.k and len(selected.call_sites) <= empty.k
    elif isinstance(selected, HybridContext) and isinstance(empty, HybridContext):
        assert selected.call_k == empty.call_k and selected.obj_depth == empty.obj_depth
        assert len(selected.call_sites) <= empty.call_k
        assert len(selected.alloc_sites) <= empty.obj_depth
    else:
        assert isinstance(selected, (ObjectContext, TypeContext, ReceiverContext, ParamContext))
        assert isinstance(empty, (ObjectContext, TypeContext, ReceiverContext, ParamContext))
        assert selected.depth == empty.depth


@pytest.mark.parametrize("policy", list(ContextPolicy))
def test_summary_marker_survives_selection_without_protocol(policy: ContextPolicy) -> None:
    selector = ContextSelector(policy)
    selected = selector.select_call_context(
        site("current()"), SummaryContext(broad_caller(selector))
    )
    assert isinstance(selected, SummaryContext)
    assert selected.inner is not None
    assert not isinstance(selected.inner, (SummaryContext, ProtocolKeyContext))


@pytest.mark.parametrize("policy", list(ContextPolicy))
def test_protocol_and_summary_are_flat_after_policy_selection(policy: ContextPolicy) -> None:
    selector = ContextSelector(policy)
    partition = KeywordPartition(site("dispatch()"), None, "summary", None)
    wrapped = apply_native_protocol_partition(SummaryContext(broad_caller(selector)), partition)
    selected = selector.select_call_context(site("current()"), wrapped)
    assert isinstance(selected, SummaryContext)
    assert isinstance(selected.inner, ProtocolKeyContext)
    assert selected.inner.partition == partition
    assert not isinstance(selected.inner.inner, (SummaryContext, ProtocolKeyContext))
    base, actual_partition = split_native_protocol_context(selected)
    assert isinstance(base, SummaryContext) and actual_partition == partition


def test_larger_selected_bound_keeps_available_call_history_without_inventing_more() -> None:
    prior = site("prior()")
    current = site("current()")
    selected = ContextSelector(ContextPolicy.CALL_3).select_call_context(
        current, HybridContext((prior,), (), 1, 1)
    )
    assert isinstance(selected, CallStringContext)
    assert selected.k == 3 and selected.call_sites == (prior, current)


def test_invalid_summary_without_a_base_is_rejected() -> None:
    with pytest.raises(ValueError):
        ContextSelector(ContextPolicy.CALL_2).select_call_context(
            site("current()"), SummaryContext(None)
        )


@pytest.mark.parametrize("hybrid", [False, True])
def test_registered_raw_ir_call_history_is_not_dropped(hybrid: bool) -> None:
    old = site("old()").statement
    current = site("current()").statement
    caller = HybridContext((old,), (), 1, 1) if hybrid else CallStringContext((old,), 2)
    selected = ContextSelector(ContextPolicy.CALL_2).select_call_context(current, caller)
    assert isinstance(selected, CallStringContext)
    assert selected.call_sites == (old, current)
