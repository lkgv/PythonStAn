"""A cached predicate stays false; its old heap refinement must be invalidated."""

from __future__ import annotations

import ast

from pythonstan.analysis.contracts.identity import CellId, ContextId, FrameId, ObjectId
from pythonstan.analysis.domains.values import Value, ValueOperations
from pythonstan.analysis.engines.abstract.driver import InterproceduralDriver
from pythonstan.analysis.state.guards import MembershipGuard
from pythonstan.analysis.state.store import Frame, HeapObject, Store


def test_write_through_named_heap_alias_keeps_cached_false_branch_reachable() -> None:
    frame = Frame("subject", "subject", ContextId(()), FrameId("module"), is_module=True)
    shared = HeapObject("dict")
    state = Store(
        cells={
            frame.cell("condition"): Value.constant(False),
            frame.cell("container"): Value.ref(ObjectId("H")),
        },
        heap={ObjectId("H"): shared, ObjectId("new"): shared},
        guards={frame.cell("condition"): MembershipGuard(ObjectId("H"), "x", True)},
    )
    state.write_slot(ObjectId("new"), "x", Value.constant(11), ValueOperations())
    driver = InterproceduralDriver(())
    command = ast.parse("if condition:\n    result=0\nelse:\n    result=container['x']\n").body[0]
    flows = driver.transfer.statement(command, state, frame)
    values = {
        value
        for flow in flows
        if flow.kind == "normal"
        for value in flow.store.cells.get(frame.cell("result"), Value.bottom()).exact_constants()
        or ()
    }
    assert values == {11}
    assert state.cells[CellId("global:subject:condition")].exact_constants() == [False]


def test_shared_fields_map_invalidates_alias_guard_but_keeps_disjoint_guard() -> None:
    frame = Frame("subject", "subject", ContextId(()), FrameId("module"), is_module=True)
    fields: dict[str, Value] = {}
    state = Store(
        cells={
            frame.cell("condition"): Value.constant(False),
            frame.cell("container"): Value.ref(ObjectId("H")),
        },
        heap={
            ObjectId("H"): HeapObject("dict", fields),
            ObjectId("new"): HeapObject("dict", fields),
            ObjectId("disjoint"): HeapObject("dict", {"keep": Value.constant(17)}),
        },
        guards={
            frame.cell("condition"): MembershipGuard(ObjectId("H"), "x", True),
            CellId("unrelated_guard"): MembershipGuard(ObjectId("disjoint"), "keep", True),
        },
    )
    state.write_slot(ObjectId("new"), "x", Value.constant(11), ValueOperations())
    driver = InterproceduralDriver(())
    command = ast.parse("if condition:\n    result=0\nelse:\n    result=container['x']\n").body[0]
    flows = driver.transfer.statement(command, state, frame)
    assert {
        value
        for flow in flows
        if flow.kind == "normal"
        for value in flow.store.cells.get(frame.cell("result"), Value.bottom()).exact_constants()
        or ()
    } == {11}
    assert state.guards[CellId("unrelated_guard")] == MembershipGuard(
        ObjectId("disjoint"), "keep", True
    )
