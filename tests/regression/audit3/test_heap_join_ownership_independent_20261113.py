"""Standalone HeapObject joins must own every retained suspension graph."""

from __future__ import annotations

import pytest

from pythonstan.analysis.contracts.identity import CellId, ContextId, FrameId, ObjectId
from pythonstan.analysis.domains.values import Value, ValueOperations
from pythonstan.analysis.state.store import Frame, HeapObject, Store
from pythonstan.analysis.state.suspensions import suspension_states


def activation(number: int, *, cycle: bool) -> tuple[HeapObject, Store, Frame]:
    root = Store(cells={CellId("payload"): Value.constant(number)})
    frame = Frame("subject", "gen", ContextId(str(number)), FrameId(str(number)))
    owner = HeapObject("generator", metadata={"entry": root, "frame": frame, "stage": "created"})
    if cycle:
        root.heap[ObjectId("owner")] = owner
    return owner, root, frame


@pytest.mark.parametrize("cycle", (False, True))
def test_retained_left_and_right_activations_are_mutably_detached(cycle: bool) -> None:
    left, left_root, left_frame = activation(11, cycle=cycle)
    right, right_root, right_frame = activation(29, cycle=cycle)
    joined = left.join(right, ValueOperations())
    states = suspension_states(joined.metadata.record)
    assert len(states) == 2
    roots = []
    for state in states:
        assert state.entry is not None and state.frame is not None
        root = state.entry.read()
        assert root is not left_root and root is not right_root
        assert state.frame is not left_frame and state.frame is not right_frame
        if cycle:
            assert root.heap[ObjectId("owner")] is joined
        roots.append(root)
    assert {tuple(root.cells[CellId("payload")].exact_constants()) for root in roots} == {
        (11,),
        (29,),
    }
    for root in roots:
        root.write_cell(CellId("payload"), Value.constant(101))
    assert left_root.cells[CellId("payload")].exact_constants() == [11]
    assert right_root.cells[CellId("payload")].exact_constants() == [29]
    left_root.write_cell(CellId("payload"), Value.constant(5))
    right_root.write_cell(CellId("payload"), Value.constant(7))
    assert all(root.cells[CellId("payload")].exact_constants() == [101] for root in roots)
    assert not joined.havoc


def test_shared_entry_root_alias_is_preserved_across_both_activations() -> None:
    shared = Store(cells={CellId("payload"): Value.constant(17)})
    left_frame = Frame("subject", "gen", ContextId("left"), FrameId("left"))
    right_frame = Frame("subject", "gen", ContextId("right"), FrameId("right"))
    left = HeapObject("generator", metadata={"entry": shared, "frame": left_frame})
    right = HeapObject("generator", metadata={"entry": shared, "frame": right_frame})
    joined = left.join(right, ValueOperations())
    states = suspension_states(joined.metadata.record)
    assert len(states) == 2
    assert all(state.entry is not None for state in states)
    first, second = (state.entry.read() for state in states)
    assert first is second and first is not shared
    first.write_cell(CellId("payload"), Value.constant(31))
    assert second.cells[CellId("payload")].exact_constants() == [31]
    assert shared.cells[CellId("payload")].exact_constants() == [17]
    assert not joined.havoc
