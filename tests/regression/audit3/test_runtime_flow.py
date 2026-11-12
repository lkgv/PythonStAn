"""Actual completion distinctions and state ownership through live consumers."""

from __future__ import annotations

from pathlib import Path

import pytest

from pythonstan.analysis.contracts.identity import CellId
from pythonstan.analysis.domains.values import Value
from pythonstan.analysis.semantics.control import resume_pending
from pythonstan.analysis.state.flow import (
    BreakFlow,
    ContinueFlow,
    ExceptionValue,
    Flow,
    FlowTail,
    NormalExprFlow,
    NormalStmtFlow,
    RaiseFlow,
    ReturnFlow,
    SuspendFlow,
    flow_payload,
    make_completion,
)
from pythonstan.analysis.state.handles import StateHandle, StateOwnershipError
from pythonstan.analysis.state.store import Store

from .flow_exhaustiveness import check_real_flow_exhaustiveness


def test_expression_statement_and_return_none_have_distinct_payloads() -> None:
    state = StateHandle.capture(Store())
    expression = NormalExprFlow(state, Value.constant(None))
    statement = NormalStmtFlow(state)
    returned = ReturnFlow(state, Value.constant(None))
    assert expression.form == "expression" and expression.value.exact_constants() == [None]
    assert statement.form == "statement" and flow_payload(statement) is None
    assert not hasattr(statement, "value")
    assert returned.value.exact_constants() == [None]
    with pytest.raises(ValueError):
        make_completion(state, "return", None, None, FlowTail())


def test_actual_cleanup_handler_preserves_all_variants_and_uses_cleanup_state() -> None:
    old, current = Store(), Store()
    value = Value.constant("payload")
    held = StateHandle.capture(old)
    pending: tuple[Flow, ...] = (
        NormalExprFlow(held, value),
        NormalStmtFlow(held),
        ReturnFlow(held, value),
        RaiseFlow(held, ExceptionValue(value, "ValueError")),
        BreakFlow(held),
        ContinueFlow(held),
        SuspendFlow(held, value),
    )
    cleanup = NormalStmtFlow(StateHandle.capture(current))
    for completion in pending:
        resumed = resume_pending(completion, cleanup)
        assert type(resumed) is type(completion)
        assert resumed.store is current
        assert flow_payload(resumed) == flow_payload(completion)
    override = RaiseFlow(StateHandle.capture(current), ExceptionValue(value, "TypeError"))
    assert resume_pending(pending[2], override) is override


def test_state_handle_reads_are_pure_and_writes_invalidate_revision() -> None:
    store = Store()
    handle = StateHandle.capture(store)
    before = (store.state_revision, dict(store.cells), set(store.writes))
    assert handle.read() is store and handle.is_current()
    assert (store.state_revision, store.cells, store.writes) == before
    store.write_cell(CellId("global:subject:x"), Value.constant(1))
    assert not handle.is_current()
    assert StateHandle.capture(store).is_current()
    forged = StateHandle(handle.owner, handle.epoch, handle.revision, Store())
    with pytest.raises(StateOwnershipError):
        forged.read()


def test_real_completion_union_rejects_an_unhandled_variant() -> None:
    result = check_real_flow_exhaustiveness(Path(__file__).resolve().parents[3])
    assert result.positive_status == 0, result.positive_output
    assert result.negative_status != 0, result.negative_output
    assert result.handler_rejected, result.negative_output


def test_summary_dependency_detects_revision_scope_policy_and_epoch_changes() -> None:
    from pythonstan.analysis.contracts.identity import (
        IdentityEpoch,
        PolicyId,
        StateRevision,
        UnitId,
    )
    from pythonstan.analysis.state.dependencies import SummaryDependency

    dependency = SummaryDependency(
        UnitId("callee"), StateRevision(3), "source", PolicyId("policy"), IdentityEpoch(2)
    )
    assert not dependency.needs_refresh(
        revision=StateRevision(3),
        input_scope="source",
        policy=PolicyId("policy"),
        epoch=IdentityEpoch(2),
    )
    assert dependency.needs_refresh(
        revision=StateRevision(4),
        input_scope="source",
        policy=PolicyId("policy"),
        epoch=IdentityEpoch(2),
    )
    assert dependency.needs_refresh(
        revision=StateRevision(3),
        input_scope="changed",
        policy=PolicyId("policy"),
        epoch=IdentityEpoch(2),
    )
    assert dependency.needs_refresh(
        revision=StateRevision(3),
        input_scope="source",
        policy=PolicyId("changed"),
        epoch=IdentityEpoch(2),
    )
    assert dependency.needs_refresh(
        revision=StateRevision(3),
        input_scope="source",
        policy=PolicyId("policy"),
        epoch=IdentityEpoch(3),
    )


def test_real_transfer_publishes_expression_statement_and_return_none_variants() -> None:
    import ast

    from pythonstan.analysis.contracts.identity import ContextId, FrameId, SourceModule
    from pythonstan.analysis.engines.abstract.driver import InterproceduralDriver
    from pythonstan.analysis.state.store import Frame

    driver = InterproceduralDriver({"subject": SourceModule("subject", "subject.py", "x=None\n")})
    frame = Frame("subject", "subject", ContextId(()), FrameId("subject"), is_module=True)
    nodes = ast.parse("x=None\nreturn None\n").body
    expression = driver.transfer.expr(ast.Constant(None), Store(), frame)[0]
    statement = driver.transfer.statement(nodes[0], Store(), frame)[0]
    returned = driver.transfer.statement(nodes[1], Store(), frame)[0]
    assert isinstance(expression, NormalExprFlow) and expression.value.exact_constants() == [None]
    assert isinstance(statement, NormalStmtFlow) and flow_payload(statement) is None
    assert isinstance(returned, ReturnFlow) and returned.value.exact_constants() == [None]
