"""Order/read/apply ports must not publish into borrowed stale roots."""

from __future__ import annotations

import pytest

from pythonstan.analysis.contracts.identity import CellId, ObjectId
from pythonstan.analysis.domains.values import Value, ValueOperations
from pythonstan.analysis.state.flow import NormalStmtFlow
from pythonstan.analysis.state.handles import StateHandle
from pythonstan.analysis.state.store import HeapObject, Store
from pythonstan.analysis.state.transform import payload_signature


def stale_graph() -> tuple[Store, ValueOperations]:
    root = Store(cells={CellId("value"): Value.ref(ObjectId("before"))})
    root.heap[ObjectId("before")] = HeapObject(
        "generator", {"value": Value.constant(7)}, metadata={"entry": root}
    )
    ops = ValueOperations()
    ops.arena.publish(ops.arena.prepare({ObjectId("before"): ObjectId("after")}))
    return root, ops


@pytest.mark.parametrize("operation", ["read", "leq", "join", "apply"])
def test_stale_observation_preserves_input_roots(operation: str) -> None:
    left, ops = stale_graph()
    right = left.copy()
    left_signature = payload_signature(left)
    right_signature = payload_signature(right)
    left_heap = left.heap
    right_heap = right.heap
    left_cells = left.cells
    if operation == "read":
        value, missing = left.read_slot(ObjectId("before"), "value", ops)
        assert value.exact_constants() == [7]
        assert not missing
    elif operation == "leq":
        assert left.leq(right, ops)
    elif operation == "join":
        joined = left.join(right, ops)
        assert joined.heap[ObjectId("after")].metadata["entry"] is joined
    else:
        applied = left.apply(NormalStmtFlow(StateHandle.capture(right)), ops)
        assert applied.identity_epoch == ops.arena.epoch
    assert payload_signature(left) == left_signature
    assert payload_signature(right) == right_signature
    assert left.heap is left_heap
    assert right.heap is right_heap
    assert left.cells is left_cells
    assert left.identity_epoch == right.identity_epoch == 0
    assert left.state_revision == right.state_revision == 0


def test_explicit_normalization_publishes_once_and_is_idempotent() -> None:
    root, ops = stale_graph()
    assert root.ensure_current(ops) is root
    assert root.identity_epoch == ops.arena.epoch
    assert root.state_revision == 1
    assert root.heap[ObjectId("after")].metadata["entry"] is root
    signature = payload_signature(root)
    assert root.ensure_current(ops) is root
    assert payload_signature(root) == signature
