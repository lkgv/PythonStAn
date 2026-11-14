"""Independent operational STATE application witnesses; no candidate order oracle."""

from __future__ import annotations

import ast

from pythonstan.analysis.contracts.identity import CellId, ContextId, FrameId, ObjectId
from pythonstan.analysis.domains.values import Value, ValueOperations
from pythonstan.analysis.engines.abstract.driver import InterproceduralDriver
from pythonstan.analysis.state.effects import HeapWrite, MetadataAspect, MetadataUpdate
from pythonstan.analysis.state.flow import Flow, NormalStmtFlow
from pythonstan.analysis.state.handles import StateHandle
from pythonstan.analysis.state.store import Frame, HeapObject, Store


def _frame(name: str = "activation") -> Frame:
    return Frame(
        "subject",
        "subject.generator",
        ContextId(()),
        FrameId(name),
        locals=frozenset({"private"}),
    )


def _generator(entry: Store, frame: Frame, expression: str = "private + live") -> HeapObject:
    return HeapObject(
        "generator",
        metadata={
            "entry": entry,
            "frame": frame,
            "body": tuple(ast.parse("yield " + expression).body),
            "stage": "created",
        },
    )


def _active() -> Store:
    return Store(
        cells={CellId("global:subject:live"): Value.constant(30)},
        heap={ObjectId("unrelated"): HeapObject("dict", {"x": Value.constant(17)})},
    )


def _resume(store: Store, identity: str) -> list[Flow]:
    driver = InterproceduralDriver(())
    return driver.transfer.continuations.resume(
        Value.ref(ObjectId(identity)), store, _frame("caller"), None, "next"
    )


def _values(store: Store, identity: str) -> set[object]:
    return {
        value
        for flow in _resume(store, identity)
        if flow.kind == "normal"
        for value in flow.value.exact_constants() or ()
    }


def _apply(caller: Store, callee: Store) -> Store:
    return caller.apply(NormalStmtFlow(StateHandle.capture(callee)), ValueOperations())


def test_distinct_snapshot_sharing_only_active_heap_keeps_private_cells() -> None:
    caller = _active()
    callee = caller.copy()
    frame = _frame()
    captured = Store(cells={frame.cell("private"): Value.constant(3)}, heap=callee.heap)
    callee.heap[ObjectId("G")] = _generator(captured, frame)
    applied = _apply(caller, callee)
    assert not applied.heap[ObjectId("G")].havoc
    assert applied.heap[ObjectId("G")].metadata["entry"] is not applied
    assert _values(applied, "G") == {33}
    assert frame.cell("private") not in caller.cells
    assert captured.cells[frame.cell("private")].exact_constants() == [3]


def test_distinct_captured_roots_keep_shared_private_cells_and_live_globals() -> None:
    caller = _active()
    frame = _frame()
    cells = {frame.cell("private"): Value.constant(3)}
    left = Store(cells=cells)
    right = Store(cells=cells)
    callee = caller.copy()
    callee.heap[ObjectId("G1")] = _generator(left, frame)
    callee.heap[ObjectId("G2")] = _generator(right, frame)
    applied = _apply(caller, callee)
    first = applied.heap[ObjectId("G1")].metadata["entry"]
    second = applied.heap[ObjectId("G2")].metadata["entry"]
    assert first is not second and first is not applied and second is not applied
    assert first.cells is second.cells and first.cells is not cells
    first.cells[frame.cell("private")] = Value.constant(8)
    assert _values(applied, "G1") == _values(applied, "G2") == {38}
    assert cells[frame.cell("private")].exact_constants() == [3]


def test_partial_deep_snapshot_mutation_has_explicit_both_owner_residuals() -> None:
    caller = _active()
    frame = _frame()
    captured = Store(cells={frame.cell("private"): Value.constant(3)})
    caller.heap[ObjectId("A")] = _generator(captured, frame)
    caller.heap[ObjectId("B")] = _generator(captured, frame)
    callee = caller.copy()
    callee.heap[ObjectId("A")].metadata["entry"].cells[frame.cell("private")] = Value.constant(9)
    callee.writes.add(MetadataUpdate(ObjectId("A"), MetadataAspect.GENERATOR_STATE))
    applied = _apply(caller, callee)
    for identity in ("A", "B"):
        assert applied.heap[ObjectId(identity)].havoc
        flows = _resume(applied, identity)
        assert any(flow.kind == "normal" and flow.value.top for flow in flows)
        assert any(flow.kind == "raise" for flow in flows)
    assert not applied.heap[ObjectId("unrelated")].havoc
    assert applied.read_slot(ObjectId("unrelated"), "x", ValueOperations())[
        0
    ].exact_constants() == [17]
    assert captured.cells[frame.cell("private")].exact_constants() == [3]


def test_one_written_shared_source_field_map_guards_both_distinct_caller_owners() -> None:
    caller = _active()
    caller.heap[ObjectId("A")] = HeapObject("dict", {"x": Value.constant(1)})
    caller.heap[ObjectId("B")] = HeapObject("dict", {"x": Value.constant(2)})
    shared = {"x": Value.constant(9)}
    callee = caller.copy()
    callee.heap[ObjectId("A")].fields = shared
    callee.heap[ObjectId("B")].fields = shared
    callee.writes.add(HeapWrite(ObjectId("A"), "x"))
    applied = _apply(caller, callee)
    for identity in ("A", "B"):
        assert applied.heap[ObjectId(identity)].havoc
        assert applied.read_slot(ObjectId(identity), "x", ValueOperations())[0].top
    assert not applied.heap[ObjectId("unrelated")].havoc
    assert caller.heap[ObjectId("A")].fields["x"].exact_constants() == [1]
    assert caller.heap[ObjectId("B")].fields["x"].exact_constants() == [2]


def test_two_written_source_owners_do_not_last_write_one_caller_alias() -> None:
    caller = _active()
    shared = HeapObject("dict", {"x": Value.constant(1)})
    caller.heap[ObjectId("A")] = shared
    caller.heap[ObjectId("B")] = shared
    callee = caller.copy()
    for identity, value in (("A", 8), ("B", 9)):
        callee.heap[ObjectId(identity)] = HeapObject("dict", {"x": Value.constant(value)})
        callee.writes.add(HeapWrite(ObjectId(identity), "x"))
    applied = _apply(caller, callee)
    for identity in ("A", "B"):
        assert applied.heap[ObjectId(identity)].havoc
        assert applied.read_slot(ObjectId(identity), "x", ValueOperations())[0].top
    assert not applied.heap[ObjectId("unrelated")].havoc
    assert shared.fields["x"].exact_constants() == [1]


def test_unequal_frozen_frame_retains_its_own_binding_for_new_escape() -> None:
    caller = _active()
    old_frame, new_frame = _frame("old"), _frame("new")
    old_entry = Store(cells={old_frame.cell("private"): Value.constant(7)})
    caller.heap[ObjectId("old")] = _generator(old_entry, old_frame, "private")
    callee = caller.copy()
    # An unwritten old metadata representative is stale; caller remains its
    # authority. That must not identify unequal immutable Frame primitives.
    callee.heap[ObjectId("old")].metadata["frame"] = new_frame
    new_entry = Store(
        cells={
            new_frame.cell("private"): Value.constant(8),
            old_frame.cell("private"): Value.constant(7),
        }
    )
    callee.heap[ObjectId("new")] = _generator(new_entry, new_frame, "private")
    applied = _apply(caller, callee)
    assert applied.heap[ObjectId("old")].metadata["frame"].identity == old_frame.identity
    assert applied.heap[ObjectId("new")].metadata["frame"].identity == new_frame.identity
    assert _values(applied, "old") == {7}
    assert _values(applied, "new") == {8}


def test_new_named_owner_keeps_active_private_heap_alias_under_later_write() -> None:
    caller = _active()
    frame = _frame()
    private = HeapObject("dict", {"x": Value.constant(7)})
    caller.heap[ObjectId("H")] = private
    entry = Store(
        cells={frame.cell("private"): Value.ref(ObjectId("H"))},
        heap={ObjectId("H"): private},
    )
    caller.heap[ObjectId("old")] = _generator(entry, frame, "private['x']")
    callee = caller.copy()
    callee.heap[ObjectId("new")] = (
        callee.heap[ObjectId("old")].metadata["entry"].heap[ObjectId("H")]
    )
    applied = _apply(caller, callee)
    assert applied.heap[ObjectId("new")] is applied.heap[ObjectId("H")]
    assert not applied.heap[ObjectId("old")].havoc
    assert _values(applied, "old") == {7}
    applied.write_slot(ObjectId("new"), "x", Value.constant(11), ValueOperations())
    assert _values(applied, "old") == {11}
    assert private.fields["x"].exact_constants() == [7]
