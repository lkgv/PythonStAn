"""Independent Value-leaf and alias checks for metadata widening graphs."""

from __future__ import annotations

import pytest

from pythonstan.analysis.contracts.identity import CellId, ObjectId
from pythonstan.analysis.domains.values import Value, ValueOperations, ValuePolicy
from pythonstan.analysis.state.continuation import Continuation, GraphAttachmentCommand
from pythonstan.analysis.state.store import HeapObject, Store


def covers_small_int(value: Value, member: int) -> bool:
    part = value.ints
    return (
        value.top
        or "int" in value.top_sorts
        or not part.is_bottom
        and (
            member in part.consts
            if part.consts
            else (part.lo is None or part.lo <= member)
            and (part.hi is None or part.hi >= member)
            and (part.parity.value == "top" or member % 2 == (part.parity.value == "odd"))
        )
    )


def cyclic_metadata(value: int, *, continuation: bool) -> Store:
    backedges: list[object] = []
    graph = backedges, Value.constant(value)
    backedges.append(graph)
    payload = Continuation((GraphAttachmentCommand("cycle", (graph,)),)) if continuation else graph
    return Store(heap={ObjectId("O"): HeapObject("instance", metadata={"attachment": payload})})


@pytest.mark.parametrize("widen", (False, True))
@pytest.mark.parametrize("continuation", (False, True))
def test_metadata_tuple_cycle_keeps_updated_leaf_and_backedge(
    widen: bool, continuation: bool
) -> None:
    left = cyclic_metadata(0, continuation=continuation)
    right = cyclic_metadata(1, continuation=continuation)
    merged = left.join(right, ValueOperations(ValuePolicy(max_const_ints=1)), widen=widen)
    graph = merged.heap[ObjectId("O")].metadata["attachment"]
    if continuation:
        graph = graph.commands[0].payload[0]
    assert graph[0][0] is graph, "A tuple replacement must update cyclic metadata backedges"
    for leaf in (graph[1], graph[0][0][1]):
        assert all(covers_small_int(leaf, member) for member in (0, 1))
    assert not merged.heap[ObjectId("O")].havoc
    for original, expected in ((left, 0), (right, 1)):
        source = original.heap[ObjectId("O")].metadata["attachment"]
        if continuation:
            source = source.commands[0].payload[0]
        assert source[0][0] is source
        assert source[1].exact_constants() == [expected]


@pytest.mark.parametrize("widen", (False, True))
def test_nested_store_aliases_receive_shared_value_policy(widen: bool) -> None:
    def make(number: int) -> Store:
        value = Value.constant(number)
        nested = Store(
            cells={CellId("x"): value},
            heap={
                ObjectId("N"): HeapObject(
                    "list", {"x": value}, default=value, metadata={"length_value": value}
                )
            },
        )
        shared = {"nested": nested}
        return Store(
            heap={ObjectId("O"): HeapObject("instance", metadata={"attachment": [shared, shared]})}
        )

    left, right = make(0), make(1)
    result = left.join(right, ValueOperations(ValuePolicy(max_const_ints=1)), widen=widen)
    graph = result.heap[ObjectId("O")].metadata["attachment"]
    assert graph[0] is graph[1]
    nested = graph[0]["nested"]
    obj = nested.heap[ObjectId("N")]
    for leaf in (
        nested.cells[CellId("x")],
        obj.fields["x"],
        obj.default,
        obj.metadata["length_value"],
    ):
        assert all(covers_small_int(leaf, member) for member in (0, 1))
        if widen:
            assert leaf.ints.hi is None
    assert not result.heap[ObjectId("O")].havoc
    assert left.heap[ObjectId("O")].metadata["attachment"][0]["nested"].cells[
        CellId("x")
    ].exact_constants() == [0]
