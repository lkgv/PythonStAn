"""Independent mutation witnesses for STATE graph and namespace boundaries."""

from __future__ import annotations

import pytest

from pythonstan.analysis.composition.oracle import IncrementalInclusionStore
from pythonstan.analysis.contracts.identity import CellId, ContextId, FrameId, ObjectId
from pythonstan.analysis.domains.reabstract import ReferenceKey
from pythonstan.analysis.domains.values import Value, ValueOperations
from pythonstan.analysis.engines.abstract.driver import InterproceduralDriver
from pythonstan.analysis.state.flow import FlowTail, NormalStmtFlow
from pythonstan.analysis.state.handles import StateHandle
from pythonstan.analysis.state.store import Frame, HeapObject, Store


def _alias_world(shared: bool) -> Store:
    root = Store()
    common: list[Value] = [Value.constant(0)]
    for name in ("A", "B"):
        obj = HeapObject("instance")
        obj.metadata.attach_graph("component", common if shared else [Value.constant(0)])
        root.heap[ObjectId(name)] = obj
    root.heap[ObjectId("C")] = HeapObject("dict", {"payload": Value.constant(17)})
    return root


def test_metadata_alias_partition_is_observable_after_write() -> None:
    shared, separate = _alias_world(True), _alias_world(False)
    ops = ValueOperations()
    assert not shared.leq(separate, ops)
    assert not separate.leq(shared, ops)


def test_join_alias_disagreement_havocs_only_affected_owners() -> None:
    shared, separate = _alias_world(True), _alias_world(False)
    ops = ValueOperations()
    joined = shared.join(separate, ops)
    assert joined.heap[ObjectId("A")].havoc
    assert joined.heap[ObjectId("B")].havoc
    assert not joined.heap[ObjectId("C")].havoc
    assert joined.heap[ObjectId("C")].fields["payload"].exact_constants() == [17]
    assert shared.leq(joined, ops) and separate.leq(joined, ops)


def test_join_same_root_preserves_metadata_root_and_input_ownership() -> None:
    root = Store(cells={CellId("x"): Value.constant(0)})
    root.heap[ObjectId("A")] = HeapObject("generator", metadata={"entry": root})
    result = root.join(root, ValueOperations())
    assert result.heap[ObjectId("A")].metadata["entry"] is result
    result.write_cell(CellId("x"), Value.constant(9))
    assert root.cells[CellId("x")].exact_constants() == [0]
    assert result.heap[ObjectId("A")].metadata["entry"].cells[CellId("x")].exact_constants() == [9]


def test_join_preanchors_both_source_roots_before_visiting_left_metadata() -> None:
    left = Store(cells={CellId("x"): Value.constant(0)})
    right = Store(cells={CellId("x"): Value.constant(1)})
    left.heap[ObjectId("A")] = HeapObject("generator", metadata={"entry": right})
    right.heap[ObjectId("A")] = HeapObject("generator", metadata={"entry": right})
    result = left.join(right, ValueOperations())
    assert result.heap[ObjectId("A")].metadata["entry"] is result
    assert result.heap[ObjectId("A")].havoc
    result.write_cell(CellId("x"), Value.constant(9))
    assert right.cells[CellId("x")].exact_constants() == [1]


def test_join_opposite_input_roots_does_not_publish_new_exact_root_alias() -> None:
    left = Store(cells={CellId("x"): Value.constant(0)})
    right = Store(cells={CellId("x"): Value.constant(1)})
    left.heap[ObjectId("A")] = HeapObject("generator", metadata={"entry": right})
    right.heap[ObjectId("A")] = HeapObject("generator", metadata={"entry": left})
    ops = ValueOperations()
    result = left.join(right, ops)
    assert result.heap[ObjectId("A")].havoc
    assert left.leq(result, ops) and right.leq(result, ops)
    result.write_cell(CellId("x"), Value.constant(9))
    assert left.cells[CellId("x")].exact_constants() == [0]
    assert right.cells[CellId("x")].exact_constants() == [1]
    # Runtime consumers must take the declared residual before consulting the
    # retained entry/frame representative; the incomplete fixture has no frame.
    driver = InterproceduralDriver(())
    frame = Frame("subject", "subject", ContextId(()), FrameId("subject"))
    flows = driver.transfer.continuations.resume(
        Value.ref(ObjectId("A")), result, frame, None, "next"
    )
    assert any(flow.kind == "normal" and flow.value.top for flow in flows)
    assert any(flow.kind == "raise" for flow in flows)


def test_completion_equality_keeps_cross_tail_root_alias_worlds() -> None:
    driver = InterproceduralDriver(())
    root = Store(cells={CellId("x"): Value.constant(0)})
    other = root.copy()
    attached = NormalStmtFlow(StateHandle.capture(root), FlowTail(resume_target={"root": root}))
    detached = NormalStmtFlow(
        StateHandle.capture(other), FlowTail(resume_target={"root": root.copy()})
    )
    assert not driver._outcomes_equal([attached], [detached])
    assert len(driver.compact([attached, detached])) == 2


def test_current_object_alias_does_not_rename_same_spelling_cell() -> None:
    ops = ValueOperations()
    source = Store(
        cells={CellId("A"): Value.ref(ObjectId("A"))}, heap={ObjectId("A"): HeapObject("dict")}
    )
    ops.arena.publish({ObjectId("A"): ObjectId("S")})
    current = source.current_view(ops)
    assert set(current.cells) == {CellId("A")}
    assert current.cells[CellId("A")].refs == frozenset({ObjectId("S")})
    assert set(source.heap) == {ObjectId("A")}


def test_oracle_migrates_explicit_cell_aliases_with_object_transaction() -> None:
    ops = ValueOperations()
    kernel = IncrementalInclusionStore(ops)
    kernel.add_fact(("cell", CellId("A")), Value.ref(ObjectId("A")))
    ops.arena.publish({}, cell_aliases={CellId("A"): CellId("C")})
    prepared = kernel.prepare_reabstract({ObjectId("A"): ObjectId("S")}, ops)
    assert prepared["kernel"].value(("cell", CellId("C"))).refs == frozenset({ObjectId("S")})


def test_oracle_remaps_reference_keys_but_not_literal_tuples() -> None:
    ops = ValueOperations()
    kernel = IncrementalInclusionStore(ops)
    kernel.add_fact(("field", ObjectId("A"), ReferenceKey(ObjectId("K"))), Value.constant(42))
    kernel.add_fact(("field", ObjectId("A"), ("ref", "K")), Value.constant(17))
    prepared = kernel.prepare_reabstract(
        {ObjectId("A"): ObjectId("S"), ObjectId("K"): ObjectId("T")}, ops
    )
    current = prepared["kernel"]
    assert current.value(
        ("field", ObjectId("S"), ReferenceKey(ObjectId("T")))
    ).exact_constants() == [42]
    assert current.value(("field", ObjectId("S"), ("ref", "K"))).exact_constants() == [17]


def test_late_cell_alias_edit_aborts_host_publication(monkeypatch: pytest.MonkeyPatch) -> None:
    driver = InterproceduralDriver(())
    original = driver._check_limits

    def check(force: bool = False) -> None:
        if force:
            driver.ops.arena.cell_aliases[CellId("A")] = CellId("C")
        original(force)

    monkeypatch.setattr(driver, "_check_limits", check)
    with pytest.raises(ValueError, match="identity|arena|stale"):
        driver.reabstract_addresses({ObjectId("A"): ObjectId("S")})
    assert driver.ops.arena.aliases == {}
    assert driver.ops.arena.epoch == 0
    assert driver.ops.arena.cell_aliases == {CellId("A"): CellId("C")}
