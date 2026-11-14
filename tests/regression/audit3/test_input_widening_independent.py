"""Independent bounded input precision, termination and admission controls."""

from __future__ import annotations

import pytest

from pythonstan.analysis.contracts.cost_model import structural_ticks
from pythonstan.analysis.contracts.identity import CellId, ContextId, FrameId, ObjectId, UnitId
from pythonstan.analysis.contracts.resources import ResourceStop, RunLedger
from pythonstan.analysis.domains.ints import IntLattice
from pythonstan.analysis.domains.values import Value, ValueOperations, ValuePolicy
from pythonstan.analysis.engines.abstract.driver import InterproceduralDriver
from pythonstan.analysis.state.store import Frame, HeapObject, Store


def test_default_widen_is_unchanged_and_input_policy_retains_finite_members() -> None:
    ops = ValueOperations()
    old, new = Value.constant(17), Value.constant(19)

    ordinary = ops.widen(old, new)
    bounded = ops.widen_input(old, new)

    assert not ordinary.ints.is_enumerable()
    assert ordinary.ints.lo == 17 and ordinary.ints.hi is None
    assert set(bounded.exact_constants() or ()) == {17, 19}
    assert ops.leq(old, bounded) and ops.leq(new, bounded)


@pytest.mark.parametrize(
    "sequence",
    [
        tuple(range(30)),
        tuple(-number for number in range(30)),
        tuple(number if number % 2 else -number for number in range(30)),
    ],
    ids=["increasing", "decreasing", "both_bounds"],
)
def test_strict_growth_crosses_cap_and_stabilizes_with_cover(
    sequence: tuple[int, ...],
) -> None:
    ops = ValueOperations(ValuePolicy(max_const_ints=3))
    value = Value.bottom()
    interval_seen = False
    recent: list[Value] = []
    for number in sequence:
        incoming = Value.constant(number)
        previous = value
        value = ops.widen_input(previous, incoming)
        assert ops.leq(previous, value) and ops.leq(incoming, value)
        if value.ints.is_enumerable():
            assert not interval_seen
            assert len(value.ints.consts) <= 3
        else:
            interval_seen = True
        recent.append(value)
    assert interval_seen
    assert recent[-1] == recent[-2] == recent[-3]
    assert all(value.ints.may_contain(number) for number in sequence)


def test_existing_interval_and_oversized_first_input_use_original_widen() -> None:
    ops = ValueOperations(ValuePolicy(max_const_ints=2))
    interval = Value(ints=IntLattice.from_interval(17, 19))
    finite = Value.constant(23)
    oversized = Value(ints=IntLattice.from_consts(frozenset({1, 2, 3})))

    assert ops.widen_input(interval, finite).ints == ops.widen(interval, finite).ints
    assert not ops.widen_input(interval, finite).ints.is_enumerable()
    assert ops.widen_input(Value.bottom(), oversized).ints == ops.widen(
        Value.bottom(), oversized
    ).ints


def test_other_value_facets_keep_the_original_widening_policy() -> None:
    ops = ValueOperations()
    old = ops.join(Value.constant(17), Value.constant("before"))
    old = ops.join(old, Value.ref(ObjectId("old")))
    new = ops.join(Value.constant(19), Value.constant("after"))
    new = ops.join(new, Value.ref(ObjectId("new")))
    ordinary, bounded = ops.widen(old, new), ops.widen_input(old, new)

    assert bounded.strings == ordinary.strings
    assert bounded.refs == ordinary.refs == frozenset({ObjectId("old"), ObjectId("new")})
    assert bounded.top_sorts == ordinary.top_sorts
    assert bounded.unbound == ordinary.unbound
    assert bounded.relations == ordinary.relations
    assert ops.leq(old, bounded) and ops.leq(new, bounded)


def test_store_policy_reaches_heap_and_nested_cyclic_metadata_without_mutation() -> None:
    ops = ValueOperations()
    cell, owner = CellId("parameter"), ObjectId("owner")
    left = Store(cells={cell: Value.constant(17)})
    right = Store(cells={cell: Value.constant(19)})
    for store, number in ((left, 17), (right, 19)):
        store.heap[owner] = HeapObject(
            "instance",
            {"field": Value.constant(number)},
            default=Value.constant(number),
            metadata={"payload": {"value": Value.constant(number)}, "entry": store},
        )

    merged = left.join(right, ops, value_merge=ops.widen_input)

    assert set(merged.cells[cell].exact_constants() or ()) == {17, 19}
    assert set(merged.heap[owner].fields["field"].exact_constants() or ()) == {17, 19}
    assert set(merged.heap[owner].default.exact_constants() or ()) == {17, 19}
    payload = merged.heap[owner].metadata["payload"]
    assert isinstance(payload, dict)
    observed = payload["value"]
    assert isinstance(observed, Value)
    assert set(observed.exact_constants() or ()) == {17, 19}
    assert merged.heap[owner].metadata["entry"] is merged
    assert left.heap[owner].metadata["entry"] is left
    assert right.heap[owner].metadata["entry"] is right
    assert left.cells[cell].exact_constants() == [17]
    assert right.cells[cell].exact_constants() == [19]


@pytest.mark.parametrize("budget", [1, 2])
def test_request_input_widening_is_paid_and_denial_does_not_publish(budget: int) -> None:
    ledger = RunLedger(budget, 0, 0, memory_reader=lambda: 0)
    driver = InterproceduralDriver((), resource_ledger=ledger)
    frame = Frame("subject", "f", ContextId(()), FrameId("f"), locals=frozenset({"value"}))
    key, cell = UnitId("f"), frame.cell("value")
    original = Store(cells={cell: Value.constant(17)})
    incoming = Store(cells={cell: Value.constant(19)})
    driver._request(key, frame, (), original, False, None)
    driver.units[key].visits = 3

    if budget == 1:
        with pytest.raises(ResourceStop):
            driver._request(key, frame, (), incoming, False, None)
        assert driver.units[key].entry is original
        assert original.cells[cell].exact_constants() == [17]
        assert ledger.work.used <= budget
    else:
        driver._request(key, frame, (), incoming, False, None)
        assert set(driver.units[key].entry.cells[cell].exact_constants() or ()) == {17, 19}
        assert ledger.raw_structural["entry_widen_work"] == 13
        assert ledger.work.used == structural_ticks(13) == 2
    assert incoming.cells[cell].exact_constants() == [19]
