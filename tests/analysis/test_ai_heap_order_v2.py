"""Heap growth must invalidate convergence; narrowing must retain live spills."""

from itertools import product

import pytest

from pythonstan.analysis.pointer.ai.api import AnalysisBudget
from pythonstan.analysis.pointer.ai.heap import (
    AIAddr, AbsObj, ContainerSlots, FieldStore, ObjKind,
)
from pythonstan.analysis.pointer.ai.solver import AISolver, SolverConfig, TraceKey, WorkItem
from pythonstan.analysis.pointer.ai.state import AIState
from pythonstan.analysis.pointer.ai.value_domain import AbsVal


def value(*names):
    return AbsVal.from_strs(frozenset(names)) if names else AbsVal.bottom()


def test_container_payload_growth_is_not_below_previous_heap():
    address = AIAddr("list-site", "context")
    before = AbsObj(address, ObjKind.LIST, container_slots=ContainerSlots())
    after = before.with_container_slots(ContainerSlots().set_index(0, value("callback")))
    assert not after.leq(before)
    assert not AIState(heap={address: after}).leq(AIState(heap={address: before}))


def test_join_retains_possible_empty_path_and_narrow_refines_it():
    empty = ContainerSlots()
    nonempty = ContainerSlots().set_index(0, value("callback"))
    joined = empty.join(nonempty)
    assert joined.may_be_empty
    assert empty.leq(joined)
    assert nonempty.leq(joined)
    assert not joined.leq(nonempty)
    assert not joined.narrow(nonempty).may_be_empty


@pytest.mark.parametrize("trace_partitioning,widen", product((False, True), repeat=2))
def test_solver_reprocesses_growth_only_in_container_payload(trace_partitioning, widen):
    budget = AnalysisBudget()
    solver = AISolver(
        scope_ir=object(), initial_state=AIState(), pta_query=object(), budget=budget,
        config=SolverConfig(enable_trace_partitioning=trace_partitioning, loop_unroll=0),
    )
    address = AIAddr("list-site", "context")
    initial = AIState(heap={address: AbsObj(address, ObjKind.LIST,
                                          container_slots=ContainerSlots())})
    incoming = AIState(heap={address: AbsObj(address, ObjKind.LIST,
        container_slots=ContainerSlots().set_index(0, value("callback")))})
    item = WorkItem(block_id=5, trace_key=TraceKey.initial())
    if widen:
        solver.loop_heads.add(item.block_id)
    assert solver._update_state_for_item(item, initial)
    assert solver._update_state_for_item(item, incoming)
    merged = solver._get_state_for_work_item(item)
    assert incoming.leq(merged)
    assert merged.heap[address].container_slots.get_index(0).str_const == {"callback"}


def test_container_narrow_recovers_value_spilled_at_an_untracked_index():
    precise = ContainerSlots(max_tracked=1).set_index(0, value("callback"))
    widened = ContainerSlots(elem_bucket=value("callback"), may_be_empty=True,
                             max_tracked=1)
    assert precise.leq(widened)
    narrowed = widened.narrow(precise)
    assert precise.leq(narrowed)
    assert narrowed.leq(widened)
    assert narrowed.get_index(0).str_const == {"callback"}


def test_fieldstore_narrow_recovers_value_spilled_at_an_untracked_key():
    precise = FieldStore().strong_set_const("callback", value("handler"))
    widened = FieldStore(default_bucket=value("handler"), max_keys=1)
    assert precise.leq(widened)
    narrowed = widened.narrow(precise)
    assert precise.leq(narrowed)
    assert narrowed.leq(widened)
    assert narrowed.get_const("callback").str_const == {"handler"}


def test_container_join_widen_and_narrow_laws_with_tracked_and_spilled_values():
    # Vary representation and shape independently. In particular an empty
    # tracked map with a wildcard bucket must cover a precise incoming index.
    states = []
    for default, precise, empty in product((value(), value("a"), value("a", "b")),
                                           (value(), value("b")), (False, True)):
        states.append(ContainerSlots(elem_bucket=default,
                                     tracked_indices={0: precise},
                                     may_be_empty=empty, max_tracked=1))
        states.append(ContainerSlots(elem_bucket=default,
                                     tracked_indices={1: precise},
                                     may_be_empty=empty, max_tracked=1))
    for left, right in product(states, repeat=2):
        joined = left.join(right)
        widened = left.widen(right)
        assert left.leq(joined) and right.leq(joined)
        assert left.leq(widened) and right.leq(widened)
        if right.leq(left):
            narrowed = left.narrow(right)
            assert right.leq(narrowed)
            assert narrowed.leq(left)
