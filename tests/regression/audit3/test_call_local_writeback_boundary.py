"""Independent current-epoch local contribution and cost boundaries."""

from __future__ import annotations

import pytest

from pythonstan.analysis.contracts.cost_model import structural_ticks
from pythonstan.analysis.contracts.identity import CellId, ContextId, FrameId, ObjectId
from pythonstan.analysis.contracts.resources import ResourceStop, RunLedger
from pythonstan.analysis.domains.values import Value, ValueOperations
from pythonstan.analysis.engines.abstract.driver import InterproceduralDriver
from pythonstan.analysis.state.effects import CellWrite, Havoc, remap_effect
from pythonstan.analysis.state.flow import NormalStmtFlow
from pythonstan.analysis.state.guards import MembershipGuard
from pythonstan.analysis.state.handles import StateHandle
from pythonstan.analysis.state.store import Frame, HeapObject, Store
from pythonstan.analysis.state.transform import payload_signature


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
    assert applied.writes == {CellWrite(cell, weak=True)}
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

    visits: list[int] = []

    applied = caller.apply(
        NormalStmtFlow(StateHandle.capture(callee)),
        ValueOperations(),
        weak_cells=frozenset({cell}),
        visit=visits.append,
    )

    assert applied.cells[cell] is value
    assert applied.guards[cell] == guard
    assert sum(visits) == 4
    assert CellWrite(cell, weak=True) in applied.writes


@pytest.mark.parametrize("written", [False, True])
def test_join_admits_inputs_and_output_once(written: bool) -> None:
    cell = CellId("selected")
    caller = Store(cells={cell: Value.constant(17)})
    callee = Store(
        cells={cell: Value.constant(19)},
        writes={CellWrite(cell)} if written else set(),
    )
    ledger = RunLedger(3, 0, 0, memory_reader=lambda: 0)

    with ledger.structural_meter("independent_local_join") as meter:
        applied = caller.apply(
            NormalStmtFlow(StateHandle.capture(callee)),
            ValueOperations(),
            weak_cells=frozenset({cell}),
            visit=meter.visit,
        )

    assert constants(applied.cells[cell]) == {17, 19}
    # Value facets still cost 13; explicit provenance/index bookkeeping is additional.
    expected_raw = 19 if written else 17
    assert ledger.raw_structural["independent_local_join"] == expected_raw
    assert ledger.work.used == structural_ticks(expected_raw) == 3


@pytest.mark.parametrize("written", [False, True])
@pytest.mark.parametrize("budget", [1, 2])
def test_contribution_admission_denial_preserves_roots(written: bool, budget: int) -> None:
    cell = CellId("selected")
    caller = Store(cells={cell: Value.constant(17)})
    callee = Store(
        cells={cell: Value.constant(19)},
        writes={CellWrite(cell)} if written else set(),
    )
    ledger = RunLedger(budget, 0, 0, memory_reader=lambda: 0)

    with pytest.raises(ResourceStop), ledger.structural_meter("independent_local_join") as meter:
        caller.apply(
            NormalStmtFlow(StateHandle.capture(callee)),
            ValueOperations(),
            weak_cells=frozenset({cell}),
            visit=meter.visit,
        )

    assert ledger.work.used == budget
    assert constants(caller.cells[cell]) == {17}
    assert constants(callee.cells[cell]) == {19}


def test_identical_contribution_crosses_the_next_call_boundary() -> None:
    cell = CellId("captured")
    shared = Value.constant(17)
    middle_pre = Store(cells={cell: shared})
    inner_post = Store(cells={cell: shared})
    middle = middle_pre.apply(
        NormalStmtFlow(StateHandle.capture(inner_post)),
        ValueOperations(),
        weak_cells=frozenset({cell}),
    )
    assert middle.cells[cell] is shared
    assert middle.writes == {CellWrite(cell, weak=True)}

    outer = Store(cells={cell: Value.constant(19)})
    result = outer.apply(NormalStmtFlow(StateHandle.capture(middle)), ValueOperations())

    assert constants(result.cells[cell]) == {17, 19}
    assert CellWrite(cell, weak=True) in result.writes
    assert constants(outer.cells[cell]) == {19}


@pytest.mark.parametrize("reverse", [False, True])
def test_strong_weak_and_havoc_same_cell_have_one_final_join(reverse: bool) -> None:
    cell = CellId("selected")
    writes = [CellWrite(cell), CellWrite(cell, weak=True), Havoc(cells=frozenset({cell}))]
    if reverse:
        writes.reverse()
    caller = Store(cells={cell: Value.constant(17)})
    callee = Store(cells={cell: Value.constant(19)}, writes=set(writes))

    result = caller.apply(NormalStmtFlow(StateHandle.capture(callee)), ValueOperations())

    assert constants(result.cells[cell]) == {17, 19}
    assert set(writes) <= result.writes
    assert constants(caller.cells[cell]) == {17}
    assert constants(callee.cells[cell]) == {19}


def test_isolated_weak_keys_do_not_materialize_cells_or_unbound() -> None:
    cell, absent = CellId("retained"), CellId("absent")
    caller = Store(cells={cell: Value.constant(17)})
    result = caller.apply(
        NormalStmtFlow(StateHandle.capture(Store())),
        ValueOperations(),
        weak_cells=frozenset({cell, absent}),
    )

    assert constants(result.cells[cell]) == {17}
    assert not result.cells[cell].unbound
    assert absent not in result.cells
    assert not result.writes

    created = Store().apply(
        NormalStmtFlow(StateHandle.capture(Store(cells={cell: Value.constant(19)}))),
        ValueOperations(),
        weak_cells=frozenset({cell}),
    )
    assert constants(created.cells[cell]) == {19}
    assert not created.cells[cell].unbound
    assert not created.writes


def test_weak_provenance_survives_copy_remap_and_signature() -> None:
    old, canonical = CellId("old-cell"), CellId("canonical-cell")
    weak = CellWrite(old, weak=True)
    strong = CellWrite(old)
    source = Store(cells={old: Value.constant(17)}, writes={weak})

    assert source.copy().writes == {weak}
    assert remap_effect(weak, {}, {old: canonical}) == CellWrite(canonical, weak=True)
    assert payload_signature(weak) != payload_signature(strong)

    ops = ValueOperations()
    ops.arena.publish({}, cell_aliases={old: canonical})
    mapped = source.current_view(ops)
    assert mapped.writes == {CellWrite(canonical, weak=True)}
    assert source.writes == {weak}


def test_strong_and_weak_atoms_remain_in_ascending_effect_sets() -> None:
    cell = CellId("selected")
    strong, weak = CellWrite(cell), CellWrite(cell, weak=True)
    left = Store(cells={cell: Value.constant(17)}, writes={strong})
    right = Store(cells={cell: Value.constant(19)}, writes={weak})
    ops = ValueOperations()

    joined = left.join(right, ops)

    assert joined.writes == {strong, weak}
    assert left.leq(joined, ops)
    assert right.leq(joined, ops)
