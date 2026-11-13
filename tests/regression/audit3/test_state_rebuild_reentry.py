"""Owned suspension tails and live object identities across publication."""

from __future__ import annotations

import ast

import pytest

from pythonstan.analysis.contracts.identity import ContextId, FrameId, ObjectId
from pythonstan.analysis.domains.values import Value, ValueOperations
from pythonstan.analysis.engines.abstract.driver import InterproceduralDriver
from pythonstan.analysis.state.flow import Flow, NormalStmtFlow, SuspendFlow
from pythonstan.analysis.state.handles import ObjectHandle, StateHandle, StateOwnershipError
from pythonstan.analysis.state.store import Frame, HeapObject, Store


def test_stale_object_handle_must_refresh_before_reading_current_heap() -> None:
    ops = ValueOperations()
    root = Store(heap={ObjectId("A"): HeapObject("dict")})
    handle = ObjectHandle(ObjectId("A"), ops.arena.epoch)
    assert handle.read_current(root, ops.arena) is root.heap[ObjectId("A")]
    ops.arena.publish({ObjectId("A"): ObjectId("S")})
    with pytest.raises(StateOwnershipError, match="stale"):
        handle.read_current(root, ops.arena)
    root.ensure_current(ops)
    with pytest.raises(StateOwnershipError, match="stale"):
        handle.read_current(root, ops.arena)
    assert handle.refresh(ops.arena).read_current(root, ops.arena) is root.heap[ObjectId("S")]


def test_block_appends_suspended_tail_once(monkeypatch: pytest.MonkeyPatch) -> None:
    driver = InterproceduralDriver(())
    body = ast.parse("a=1\nb=2\nc=3\n").body
    root = Store()
    frame = Frame("subject", "subject", ContextId(()), FrameId("subject"))
    seen: list[int] = []

    def statement(
        node: ast.stmt, store: Store, frame: Frame, *, publish: bool = True
    ) -> list[Flow]:
        seen.append(node.lineno)
        return [SuspendFlow(StateHandle.capture(store), Value.constant(0))]

    monkeypatch.setattr(driver.transfer, "statement", statement)
    result = driver.transfer.continuations.block(
        body, [NormalStmtFlow(StateHandle.capture(root))], frame
    )
    assert seen == [1]
    assert len(result) == 1 and result[0].kind == "suspend"
    assert result[0].continuation.commands == tuple(body[1:])
