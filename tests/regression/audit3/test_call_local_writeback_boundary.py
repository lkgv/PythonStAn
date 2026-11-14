"""Independent current-epoch local contribution and cost boundaries."""

from __future__ import annotations

import pytest

from pythonstan.analysis.contracts.cost_model import structural_ticks
from pythonstan.analysis.contracts.identity import CellId, ContextId, FrameId, ObjectId
from pythonstan.analysis.contracts.resources import ResourceStop, RunLedger
from pythonstan.analysis.domains.values import Value, ValueOperations
from pythonstan.analysis.engines.abstract.driver import InterproceduralDriver
from pythonstan.analysis.state.effects import CellWrite
from pythonstan.analysis.state.flow import NormalStmtFlow
from pythonstan.analysis.state.guards import MembershipGuard
from pythonstan.analysis.state.handles import StateHandle
from pythonstan.analysis.state.store import Frame, HeapObject, Store


def constants(value: Value) -> set[object]:
    return set(value.exact_constants() or ())


def test_local_join_preserves_disjoint_strong_bindings_and_default_apply() -> None:
    local, global_cell, nonlocal_cell, untouched, fresh = map(
        CellId, ("local", "global", "nonlocal", "untouched", "fresh")
    )
    caller = Store(
        cells={
            local: Value.constant(17),
            global_cell: Value.constant(0),
            nonlocal_cell: Value.constant(0),
            untouched: Value.constant(7),
        }
    )
    callee = Store(
        cells={
            local: Value.constant(19),
            global_cell: Value.constant(23),
            nonlocal_cell: Value.constant(29),
            fresh: Value.constant(31),
        },
        writes={CellWrite(local), CellWrite(global_cell), CellWrite(nonlocal_cell)},
    )
    outcome = NormalStmtFlow(StateHandle.capture(callee))
    ops = ValueOperations()

    applied = caller.apply(outcome, ops, weak_cells=frozenset({local, fresh}))

    assert constants(applied.cells[local]) == {17, 19}
    assert constants(applied.cells[global_cell]) == {23}
    assert constants(applied.cells[nonlocal_cell]) == {29}
    assert constants(applied.cells[untouched]) == {7}
    assert constants(applied.cells[fresh]) == {31}
    assert not applied.cells[fresh].unbound
    assert constants(caller.cells[local]) == {17}
    assert constants(callee.cells[local]) == {19}
    assert constants(caller.apply(outcome, ops).cells[local]) == {19}


def test_parameter_contribution_without_cell_write() -> None:
    cell = CellId("parameter")
    caller = Store(cells={cell: Value.constant(17)})
    callee = Store(cells={cell: Value.constant(19)})
    outcome = NormalStmtFlow(StateHandle.capture(callee))
    ops = ValueOperations()

    applied = caller.apply(outcome, ops, weak_cells=frozenset({cell}))

    assert constants(applied.cells[cell]) == {17, 19}
    assert not applied.writes
    assert constants(caller.apply(outcome, ops).cells[cell]) == {17}


def test_deleted_cell_keeps_caller_and_unbound_alternatives() -> None:
    cell = CellId("deleted")
    caller = Store(cells={cell: Value.constant(17)})
    callee = Store(writes={CellWrite(cell)})

    applied = caller.apply(
        NormalStmtFlow(StateHandle.capture(callee)),
        ValueOperations(),
        weak_cells=frozenset({cell}),
    )

    assert applied.cells[cell].unbound
    assert constants(applied.cells[cell].without_unbound()) == {17}


def test_unwritten_changed_cell_invalidates_its_guard() -> None:
    cell, other = CellId("condition"), CellId("other-condition")
    owner = ObjectId("dictionary")
    guard = MembershipGuard(owner, "key", True)
    caller = Store(
        cells={cell: Value.constant(True)},
        heap={owner: HeapObject("dict", {"key": Value.constant(1)})},
        guards={cell: guard, other: guard},
    )
    callee = Store(cells={cell: Value.constant(False)})

    applied = caller.apply(
        NormalStmtFlow(StateHandle.capture(callee)),
        ValueOperations(),
        weak_cells=frozenset({cell}),
    )

    assert applied.cells[cell].bools == frozenset({False, True})
    assert cell not in applied.guards
    assert applied.guards[other] == guard
    assert caller.guards[cell] == guard


def test_current_epoch_cell_keys_preserve_inputs() -> None:
    old, canonical = CellId("old-cell"), CellId("opaque-current-cell")
    caller = Store(cells={old: Value.constant(17)})
    callee = Store(cells={old: Value.constant(19)}, writes={CellWrite(old)})
    ops = ValueOperations()
    ops.arena.publish({}, cell_aliases={old: canonical})

    applied = caller.apply(
        NormalStmtFlow(StateHandle.capture(callee)),
        ops,
        weak_cells=frozenset({canonical}),
    )

    assert set(applied.cells) == {canonical}
    assert constants(applied.cells[canonical]) == {17, 19}
    assert applied.identity_epoch == ops.arena.epoch
    assert set(caller.cells) == {old}
    assert set(callee.cells) == {old}


def test_driver_classifies_bindings_and_separates_identity_namespaces() -> None:
    driver = InterproceduralDriver(())
    frame = Frame(
        "subject",
        "f",
        ContextId(()),
        FrameId("f"),
        locals=frozenset({"x", "g", "n"}),
        closure=(("n", CellId("outer-n")),),
        globals=frozenset({"g"}),
        nonlocals=frozenset({"n"}),
    )
    raw, canonical = frame.cell("x"), CellId("opaque-current-local")
    driver.ops.arena.publish(
        {ObjectId(raw): ObjectId("object-summary")},
        cell_aliases={raw: canonical},
    )

    assert driver._call_local_writeback_cells(frame, driver.ops) == frozenset({canonical})
    assert frame.binding("g").scope == "global"
    assert frame.binding("n").scope == "nonlocal"


def test_classification_uses_existing_structural_cost_model() -> None:
    ledger = RunLedger(50, 0, 0, memory_reader=lambda: 0)
    driver = InterproceduralDriver((), resource_ledger=ledger)
    frame = Frame(
        "subject",
        "f",
        ContextId(()),
        FrameId("f"),
        locals=frozenset(f"p{i}" for i in range(9)),
    )
    before = ledger.work.used

    selected = driver._call_local_writeback_cells(frame, driver.ops)

    assert len(selected) == 9
    assert ledger.raw_structural["call_local_writeback_work"] == 27
    assert ledger.work.used - before == structural_ticks(27) == 4


def test_joined_refs_keep_heap_owners_and_new_root_backedge() -> None:
    cell = CellId("selected")
    first, second = ObjectId("first"), ObjectId("second")
    caller = Store(
        cells={cell: Value.ref(first)},
        heap={first: HeapObject("instance", {"value": Value.constant(17)})},
    )
    callee = caller.copy()
    callee.cells[cell] = Value.ref(second)
    callee.heap[second] = HeapObject("instance", metadata={"entry": callee})

    applied = caller.apply(
        NormalStmtFlow(StateHandle.capture(callee)),
        ValueOperations(),
        weak_cells=frozenset({cell}),
    )

    assert applied.cells[cell].refs == frozenset({first, second})
    assert set(applied.heap) == {first, second}
    assert applied.heap[second].metadata["entry"] is applied
    assert caller.cells[cell].refs == frozenset({first})
    assert callee.cells[cell].refs == frozenset({second})
    assert callee.heap[second].metadata["entry"] is callee


def test_identical_immutable_value_keeps_guard_without_facet_visit() -> None:
    cell = CellId("unchanged")
    owner = ObjectId("dictionary")
    value = Value.constant(True)
    guard = MembershipGuard(owner, "key", True)
    caller = Store(cells={cell: value}, guards={cell: guard})
    callee = Store(cells={cell: value})

    def reject_visit(_amount: int) -> None:
        raise AssertionError("An identical immutable Value requires no join admission")

    applied = caller.apply(
        NormalStmtFlow(StateHandle.capture(callee)),
        ValueOperations(),
        weak_cells=frozenset({cell}),
        visit=reject_visit,
    )

    assert applied.cells[cell] is value
    assert applied.guards[cell] == guard


@pytest.mark.parametrize("written", [False, True])
def test_join_admits_inputs_and_output_once(written: bool) -> None:
    cell = CellId("selected")
    caller = Store(cells={cell: Value.constant(17)})
    callee = Store(
        cells={cell: Value.constant(19)},
        writes={CellWrite(cell)} if written else set(),
    )
    ledger = RunLedger(2, 0, 0, memory_reader=lambda: 0)

    with ledger.structural_meter("independent_local_join") as meter:
        applied = caller.apply(
            NormalStmtFlow(StateHandle.capture(callee)),
            ValueOperations(),
            weak_cells=frozenset({cell}),
            visit=meter.visit,
        )

    assert constants(applied.cells[cell]) == {17, 19}
    assert ledger.raw_structural["independent_local_join"] == 13
    assert ledger.work.used == structural_ticks(13) == 2


@pytest.mark.parametrize("written", [False, True])
def test_output_admission_denial_does_not_publish_changed_roots(written: bool) -> None:
    cell = CellId("selected")
    caller = Store(cells={cell: Value.constant(17)})
    callee = Store(
        cells={cell: Value.constant(19)},
        writes={CellWrite(cell)} if written else set(),
    )
    ledger = RunLedger(1, 0, 0, memory_reader=lambda: 0)

    with pytest.raises(ResourceStop), ledger.structural_meter("independent_local_join") as meter:
        caller.apply(
            NormalStmtFlow(StateHandle.capture(callee)),
            ValueOperations(),
            weak_cells=frozenset({cell}),
            visit=meter.visit,
        )

    assert ledger.work.used == 1
    assert constants(caller.cells[cell]) == {17}
    assert constants(callee.cells[cell]) == {19}
