"""Summarized suspended owners preserve activation-state correlation."""

from __future__ import annotations

import pytest

from pythonstan.analysis.contracts.identity import CellId, ContextId, FrameId, ObjectId
from pythonstan.analysis.domains.values import Value, ValueOperations, ValuePolicy
from pythonstan.analysis.state.handles import StateHandle
from pythonstan.analysis.state.metadata import GeneratorMeta, Metadata
from pythonstan.analysis.state.store import Frame, HeapObject, Store
from pythonstan.analysis.state.transform import remap_store

from .domain_oracles import concrete_result, public_answer, require_members


@pytest.mark.parametrize("operation", ["next", "send", "close", "throw"])
@pytest.mark.parametrize("cap", [1, 2])
def test_correlated_summary_public_protocol_coverage(operation: str, cap: int) -> None:
    prefix = (
        "log=''\n"
        "def gen(value):\n"
        "    global log\n"
        "    try:\n"
        "        sent=yield value\n"
        "        yield sent\n"
        "    except ValueError:\n"
        "        yield value+10\n"
        "    finally:\n"
        "        log=log+'F'\n"
        "a=gen(3)\nb=gen(7)\n"
        "if flag:\n    g=a\nelse:\n    g=b\n"
    )
    suffixes = {
        "next": "result=next(g)\n",
        "send": "first=next(g)\nresult=g.send(9)\n",
        "close": "first=next(g)\ng.close()\nresult=log\n",
        "throw": "first=next(g)\nresult=g.throw(ValueError())\n",
    }
    source = prefix + suffixes[operation]
    expected = [concrete_result(source, {"flag": branch}) for branch in (False, True)]
    actual = public_answer(
        source,
        bindings={"flag": {"type": "bool", "top": True}},
        abstract={"max_addr_set_size": cap, "solver_options": {"max_traces_per_block": 1}},
    )
    require_members(actual, expected)


def test_correlated_graph_records_preserve_distinct_frames_and_entry_roots() -> None:
    from pythonstan.analysis.state.suspensions import suspension_states

    root = Store(cells={CellId("before"): Value.constant(0)})
    for name in ("A", "B"):
        root.heap[ObjectId(name)] = HeapObject(
            "generator",
            metadata=Metadata.from_record(
                "generator",
                GeneratorMeta(
                    function=ObjectId("function:" + name),
                    frame=Frame("m", "f", ContextId(()), FrameId("frame:" + name)),
                    entry=StateHandle.capture(root),
                    body=(),
                    stage="created" if name == "A" else "suspended",
                ),
            ),
        )
    ops = ValueOperations()
    merged = remap_store(root, {ObjectId("A"): ObjectId("S"), ObjectId("B"): ObjectId("S")}, ops)
    target = merged.heap[ObjectId("S")]
    assert not target.singleton
    assert not target.havoc
    assert isinstance(target.metadata.record, GeneratorMeta)
    states = suspension_states(target.metadata.record)
    assert {(state.frame.identity, state.stage) for state in states if state.frame is not None} == {
        (FrameId("frame:A"), "created"),
        (FrameId("frame:B"), "suspended"),
    }
    assert all(state.entry is not None and state.entry.read() is merged for state in states)
    clone = merged.copy()
    assert isinstance(clone.heap[ObjectId("S")].metadata.record, GeneratorMeta)
    assert all(
        state.entry is not None and state.entry.read() is clone
        for state in suspension_states(clone.heap[ObjectId("S")].metadata.record)
    )


def test_nested_metadata_value_widening_preserves_graph_alias_and_input() -> None:
    first_shared: list[object] = []
    # Explicit component adapters admit this recursive literal graph.
    first_shared.extend([Value.constant(0), first_shared])
    second_shared: list[object] = []
    second_shared.extend([Value.constant(1), second_shared])
    first = Store(heap={ObjectId("A"): HeapObject("instance", metadata={"graph": first_shared})})
    second = Store(heap={ObjectId("A"): HeapObject("instance", metadata={"graph": second_shared})})
    ops = ValueOperations(ValuePolicy(max_const_ints=1))
    joined = first.join(second, ops, widen=True)
    graph = joined.heap[ObjectId("A")].metadata["graph"]
    assert isinstance(graph, list)
    assert graph[1] is graph
    assert isinstance(graph[0], Value)
    assert graph[0].ints.may_contain(50)
    assert isinstance(first_shared[0], Value)
    assert first_shared[0].exact_constants() == [0]
    assert first.leq(joined, ops)
    assert second.leq(joined, ops)


def test_suspension_state_capacity_is_explicit_bounded_residual() -> None:
    from pythonstan.analysis.state.suspensions import suspension_states

    ops = ValueOperations()
    joined = Store()
    for value in range(40):
        entry = Store(cells={CellId("local:frame:input"): Value.constant(value)})
        state = GeneratorMeta(
            frame=Frame("m", "f", ContextId(()), FrameId("frame")),
            entry=StateHandle.capture(entry),
            stage="created",
            body=(),
        )
        fresh = Store(
            heap={
                ObjectId("G"): HeapObject(
                    "generator", metadata=Metadata.from_record("generator", state)
                )
            }
        )
        joined = joined.join(fresh, ops)
    target = joined.heap[ObjectId("G")]
    assert isinstance(target.metadata.record, GeneratorMeta)
    assert target.metadata.record.overflow
    assert target.havoc
    assert len(suspension_states(target.metadata.record)) <= 32
    assert "suspension_state_capacity_exceeded" in joined.obligations


def test_standalone_heap_join_detaches_right_suspension_graph() -> None:
    from pythonstan.analysis.state.suspensions import suspension_states

    roots: list[Store] = []
    owners: list[HeapObject] = []
    for name, initial in (("left", 1), ("right", 2)):
        root = Store(cells={CellId("input"): Value.constant(initial)})
        owner = HeapObject(
            "generator",
            metadata=Metadata.from_record(
                "generator",
                GeneratorMeta(
                    frame=Frame("m", "f", ContextId(()), FrameId(name)),
                    entry=StateHandle.capture(root),
                    body=(),
                    stage="created",
                ),
            ),
        )
        root.heap[ObjectId("G")] = owner
        roots.append(root)
        owners.append(owner)
    joined = owners[0].join(owners[1], ValueOperations())
    assert isinstance(joined.metadata.record, GeneratorMeta)
    states = suspension_states(joined.metadata.record)
    assert len(states) == 2
    assert all(state.entry is not None for state in states)
    detached_roots = [state.entry.read() for state in states if state.entry is not None]
    assert all(root is not roots[0] and root is not roots[1] for root in detached_roots)
    assert all(root.heap[ObjectId("G")] is joined for root in detached_roots)
    for root in roots:
        root.write_cell(CellId("input"), Value.constant(99))
    assert [root.cells[CellId("input")].exact_constants() for root in detached_roots] == [[1], [2]]
