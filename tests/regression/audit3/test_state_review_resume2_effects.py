"""New post-checkpoint effect application graph/alias witnesses."""

from __future__ import annotations

from pythonstan.analysis.contracts.identity import CellId, ObjectId
from pythonstan.analysis.domains.values import Value, ValueOperations
from pythonstan.analysis.state.effects import CellWrite, MetadataAspect, MetadataUpdate
from pythonstan.analysis.state.flow import NormalStmtFlow
from pythonstan.analysis.state.handles import StateHandle
from pythonstan.analysis.state.store import HeapObject, Store


def test_one_application_preserves_alias_between_two_written_metadata_owners() -> None:
    caller = Store(
        heap={
            ObjectId("A"): HeapObject("instance", metadata={"attachment": [Value.constant(1)]}),
            ObjectId("B"): HeapObject("instance", metadata={"attachment": [Value.constant(2)]}),
        }
    )
    shared = [Value.constant(0)]
    callee = Store(
        heap={
            ObjectId("A"): HeapObject("instance", metadata={"attachment": shared}),
            ObjectId("B"): HeapObject("instance", metadata={"attachment": shared}),
        },
        writes={
            MetadataUpdate(ObjectId(name), MetadataAspect.COMPONENT_ATTACHMENT)
            for name in ("A", "B")
        },
    )
    applied = caller.apply(NormalStmtFlow(StateHandle.capture(callee)), ValueOperations())
    a = applied.heap[ObjectId("A")].metadata["attachment"]
    b = applied.heap[ObjectId("B")].metadata["attachment"]
    assert a is b and a is not shared
    a[0] = Value.constant(9)
    assert b[0].exact_constants() == [9]
    assert shared[0].exact_constants() == [0]
    assert caller.heap[ObjectId("A")].metadata["attachment"][0].exact_constants() == [1]


def test_new_escaping_heap_object_root_backedge_targets_applied_owner() -> None:
    caller = Store(
        cells={CellId("x"): Value.constant(1), CellId("caller_local"): Value.constant(7)}
    )
    callee = Store(cells={CellId("x"): Value.constant(2)}, writes={CellWrite(CellId("x"))})
    callee.heap[ObjectId("new")] = HeapObject("instance", metadata={"entry": callee})
    applied = caller.apply(NormalStmtFlow(StateHandle.capture(callee)), ValueOperations())
    assert applied.heap[ObjectId("new")].metadata["entry"] is applied
    assert applied.cells[CellId("x")].exact_constants() == [2]
    assert applied.cells[CellId("caller_local")].exact_constants() == [7]
    applied.write_cell(CellId("x"), Value.constant(9))
    assert applied.heap[ObjectId("new")].metadata["entry"].cells[CellId("x")].exact_constants() == [
        9
    ]
    assert callee.cells[CellId("x")].exact_constants() == [2]
    assert caller.cells[CellId("x")].exact_constants() == [1]


def test_new_metadata_edge_to_an_untouched_existing_owner_is_not_silently_made_distinct() -> None:
    caller = Store(
        heap={
            ObjectId("A"): HeapObject("instance", metadata={"attachment": [Value.constant(0)]}),
            ObjectId("B"): HeapObject("instance", metadata={"attachment": [Value.constant(7)]}),
        }
    )
    callee = caller.copy()
    callee.heap[ObjectId("A")].metadata["attachment"] = callee.heap[ObjectId("B")].metadata[
        "attachment"
    ]
    callee.writes.add(MetadataUpdate(ObjectId("A"), MetadataAspect.COMPONENT_ATTACHMENT))
    applied = caller.apply(NormalStmtFlow(StateHandle.capture(callee)), ValueOperations())
    a, b = applied.heap[ObjectId("A")], applied.heap[ObjectId("B")]
    assert a.metadata["attachment"] is b.metadata["attachment"] or a.havoc or b.havoc
    assert caller.heap[ObjectId("A")].metadata["attachment"][0].exact_constants() == [0]


def test_no_write_application_preserves_caller_precision_and_local_aliases() -> None:
    shared = [Value.constant(3)]
    caller = Store(
        cells={CellId("x"): Value.constant(9), CellId("caller_local"): Value.constant(7)},
        heap={
            ObjectId("A"): HeapObject("instance", metadata={"attachment": shared}),
            ObjectId("B"): HeapObject("instance", metadata={"attachment": shared}),
        },
    )
    stale_callee = caller.copy()
    stale_callee.cells[CellId("x")] = Value.constant(0)
    stale_callee.cells.pop(CellId("caller_local"))
    applied = caller.apply(NormalStmtFlow(StateHandle.capture(stale_callee)), ValueOperations())
    assert applied.cells[CellId("x")].exact_constants() == [9]
    assert applied.cells[CellId("caller_local")].exact_constants() == [7]
    assert (
        applied.heap[ObjectId("A")].metadata["attachment"]
        is applied.heap[ObjectId("B")].metadata["attachment"]
    )
    assert not any(obj.havoc for obj in applied.heap.values())
