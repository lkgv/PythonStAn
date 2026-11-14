"""Real producer lifecycle integration independent of debug publication."""

from __future__ import annotations

from pythonstan.analysis.contracts.identity import ContextId, ProgramPointId, StateRevision
from pythonstan.analysis.domains.values import Value
from pythonstan.analysis.observations.driver_bridge import (
    DriverProcedureBridge,
    SummaryProcedureExit,
)
from pythonstan.analysis.observations.query_ledger import FactQuery
from pythonstan.analysis.state.flow import ReturnFlow
from pythonstan.analysis.state.handles import StateHandle
from pythonstan.analysis.state.store import Unit

from .test_query_resume2_q0 import service, unit


def bridge() -> DriverProcedureBridge:
    owner = service()
    return DriverProcedureBridge(
        owner.identity, owner.epoch, owner.resource_ledger, (FactQuery("returns"),)
    )


def start(owner: DriverProcedureBridge, producer_value: int = 17) -> Unit:
    producer = unit(producer_value)
    producer.visits = 0
    producer.revision = StateRevision(0)
    owner.register(producer, "root-1")
    owner.begin(producer)
    producer.visits = 1
    producer.revision = StateRevision(1)
    return producer


def test_actual_unit_borrow_and_explicit_catalog_completion() -> None:
    owner = bridge()
    producer = start(owner)
    owner.finish(producer, pending=False)
    rows, metadata = owner.finalize({}, producer_catalog_complete=True)
    actual = next(row for row in rows if row.head.producer.kind == "unit")
    assert actual.payload.outcomes[0] is producer.outcomes[0]
    assert actual.covering
    assert metadata["queries"][0]["coverage_status"] == "closed_under_profile"
    _, incomplete = owner.finalize({}, producer_catalog_complete=False)
    assert incomplete["queries"][0]["coverage_status"] == "open"


def test_equal_outcomes_replace_the_real_visit_generation() -> None:
    owner = bridge()
    producer = start(owner)
    owner.finish(producer, pending=False)
    same_outcomes = producer.outcomes
    owner.begin(producer)
    producer.visits += 1
    owner.finish(producer, pending=False)
    row = owner.store.current("m", "m.f")[0]
    assert producer.outcomes is same_outcomes
    assert row.head.version.generation == 2 and row.covering


def test_pending_producer_retains_only_noncovering_old_generation() -> None:
    owner = bridge()
    producer = start(owner)
    owner.finish(producer, pending=False)
    owner.begin(producer)
    producer.visits += 1
    owner.pending(producer)
    owner.finish(producer, pending=True)
    rows, metadata = owner.finalize({}, producer_catalog_complete=False)
    assert rows and not rows[0].covering
    assert metadata["queries"][0]["status"] == "unavailable"


def test_accepted_summary_owns_poststate_and_waits_for_caller_completion() -> None:
    owner = bridge()
    producer = start(owner)
    poststate = producer.outcomes[0].store.copy()
    result = ReturnFlow(StateHandle.capture(poststate), Value.constant(19))
    owner.accepted(
        (
            SummaryProcedureExit(
                "m", "m.handler", ContextId(()), ProgramPointId("m:call"), (result,)
            ),
        )
    )
    assert not owner.store.current("m", "m.handler")
    owner.finish(producer, pending=False)
    row = owner.store.current("m", "m.handler")[0]
    assert row.covering and row.payload.outcomes[0].store is not poststate
    assert row.payload.outcomes[0].value == Value.constant(19)


def test_abandoned_visit_discards_staged_summary_on_next_begin() -> None:
    owner = bridge()
    producer = start(owner)
    owner.accepted(
        (
            SummaryProcedureExit(
                "m", "m.handler", ContextId(()), ProgramPointId("m:call"), tuple(producer.outcomes)
            ),
        )
    )
    owner.pending(producer)
    owner.finish(producer, pending=True)
    owner.begin(producer)
    producer.visits += 1
    owner.finish(producer, pending=False)
    assert not owner.store.current("m", "m.handler")


def test_stale_only_bulk_has_no_positive_fact_availability() -> None:
    owner = bridge()
    producer = start(owner)
    owner.finish(producer, pending=False)
    owner.begin(producer)
    producer.visits += 1
    owner.finish(producer, pending=True)
    _, metadata = owner.finalize({}, producer_catalog_complete=False)
    assert metadata["queries"][0]["status"] == "unavailable"
    assert metadata["queries"][0]["has_positive_facts"] is False


def test_pure_suspend_is_an_outcome_without_a_return_fact() -> None:
    from pythonstan.analysis.state.flow import SuspendFlow

    owner = bridge()
    owner.requests = (FactQuery("returns"), FactQuery("outcomes"))
    producer = start(owner)
    producer.outcomes = [SuspendFlow(producer.outcomes[0].state, Value.constant(17))]
    owner.finish(producer, pending=False)
    _, metadata = owner.finalize({}, producer_catalog_complete=False)
    assert metadata["queries"][0]["has_positive_facts"] is False
    assert metadata["queries"][1]["has_positive_facts"] is True
