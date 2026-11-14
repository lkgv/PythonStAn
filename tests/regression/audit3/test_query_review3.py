"""Independent facts ownership and scope regressions; no fake semantic Units."""

from __future__ import annotations

from dataclasses import replace

import pytest

from pythonstan.analysis.contracts.identity import (
    CellId,
    ContextId,
    FrameId,
    IdentityEpoch,
    ObjectId,
    SemanticIdentity,
    StateRevision,
    UnitId,
    VersionedDependency,
)
from pythonstan.analysis.contracts.procedure_facts import (
    FactVersion,
    ProcedureFactHead,
    ProcedureProducer,
    ProcedureScope,
)
from pythonstan.analysis.contracts.resources import RunLedger
from pythonstan.analysis.domains.values import Value, ValueOperations
from pythonstan.analysis.observations.procedures import ProcedureObservationStore
from pythonstan.analysis.observations.query_ledger import FactQuery, QueryLedger, ScopeSelector
from pythonstan.analysis.state.boundary import HostStateBoundary
from pythonstan.analysis.state.flow import ExceptionValue, RaiseFlow, ReturnFlow
from pythonstan.analysis.state.handles import StateHandle
from pythonstan.analysis.state.store import Frame, HeapObject, Store, Unit
from pythonstan.analysis.state.transform import PayloadTransformer


def setup() -> tuple[ProcedureObservationStore, Unit, ProcedureFactHead]:
    ledger = RunLedger(10000, 0, 0, memory_reader=lambda: 0)
    store = ProcedureObservationStore(
        SemanticIdentity("src-review3", "ir-review3", "model", "policy", "python-core-v1"),
        IdentityEpoch(0),
        ledger,
    )
    root = Store(cells={CellId("global:m:value"): Value.constant(17)})
    frame = Frame("m", "m.answer", ContextId(()), FrameId("frame:review3"))
    unit = Unit(
        UnitId("unit:review3"),
        frame,
        (),
        root,
        outcomes=[ReturnFlow(StateHandle.capture(root), Value.constant(17))],
        revision=StateRevision(1),
        visits=1,
    )
    producer = ProcedureProducer(
        "unit", "unit:review3", unit.key, ProcedureScope("m", "m.answer", "scenario:root")
    )
    head = ProcedureFactHead(
        producer, FactVersion(1, unit.revision, IdentityEpoch(0)), store.identity
    )
    store.register_unit(unit)
    return store, unit, head


def publish(store: ProcedureObservationStore, unit: Unit, head: ProcedureFactHead) -> None:
    captured = store.capture_unit(head, unit)
    store.publish_generation(head.producer.owner, 1, (captured,))


def test_closed_whole_inventory_is_not_proof_of_an_unknown_module() -> None:
    store, _, _ = setup()
    ledger = QueryLedger(store)
    query = FactQuery("returns", "unloaded")
    ledger.request(query)
    ledger.close_inventory()
    answer = ledger.resolve(query)
    assert answer.status == "unavailable" and not answer.covering


def test_unreachable_catalog_cannot_hide_a_new_real_return() -> None:
    store, unit, head = setup()
    ledger = QueryLedger(store)
    query = FactQuery("returns", "m", "m.answer")
    ledger.request(query)
    ledger.declare(ScopeSelector("m", "m.answer"), (), reachability="unreachable", complete=True)
    assert ledger.resolve(query).covering
    publish(store, unit, head)
    answer = ledger.resolve(query)
    assert not answer.covering and answer.scope_status != "unreachable"


def test_borrow_changed_after_validation_cannot_become_covering() -> None:
    store, unit, head = setup()
    fact = store.capture_unit(head, unit)
    plan = store.prepare_generation(head.producer.owner, 1, (fact,))
    plan.validate()
    unit.outcomes[0].store.write_cell(CellId("global:m:value"), Value.constant(19))
    try:
        plan.commit()
    except ValueError:
        assert store.current("m", "m.answer") == ()
    else:
        records = store.current("m", "m.answer")
        assert records and not any(record.covering for record in records)


def test_dependency_changed_after_validation_cannot_become_covering() -> None:
    store, unit, head = setup()
    versions = {"handler": "v1"}
    store.dependency_check = lambda dependency: (
        versions.get(dependency.identity) == dependency.version
    )
    head = replace(head, dependencies=(VersionedDependency("handler", "handler", "v1"),))
    fact = store.capture_unit(head, unit)
    plan = store.prepare_generation(head.producer.owner, 1, (fact,))
    plan.validate()
    versions["handler"] = "v2"
    try:
        plan.commit()
    except ValueError:
        assert store.current("m", "m.answer") == ()
    else:
        records = store.current("m", "m.answer")
        assert records and not any(record.covering for record in records)


def test_invalid_sibling_generation_cannot_publish_an_accepted_prefix() -> None:
    store, unit, head = setup()
    first = store.capture_unit(head, unit)
    bad = replace(first, head=replace(head, producer=replace(head.producer, owner="foreign")))
    before = store.revision
    with pytest.raises(ValueError):
        store.publish_generation(head.producer.owner, 1, (first, bad))
    assert store.revision == before and store.current("m", "m.answer") == ()


def test_epoch_uses_the_actual_prepared_registered_owner() -> None:
    store, unit, head = setup()
    root = unit.outcomes[0].store
    root.heap[ObjectId("before")] = HeapObject("instance", metadata={"entry": root})
    unit.outcomes = [ReturnFlow(StateHandle.capture(root), Value.ref(ObjectId("before")))]
    publish(store, unit, head)
    transform = PayloadTransformer(
        {ObjectId("before"): ObjectId("after")},
        ValueOperations(),
        epoch=1,
        charge=lambda count: store.resource_ledger.charge_work(count, "review3_epoch"),
    )
    boundary = transform.visit(HostStateBoundary({unit.key: unit}, {}, (), []))
    plan = store.prepare_epoch(transform, IdentityEpoch(1), units=boundary.units)
    plan.validate()
    plan.commit()
    record = store.current("m", "m.answer")[0]
    assert record.payload.outcomes[0] is boundary.units[unit.key].outcomes[0]
    assert record.payload.outcomes[0].value.refs == frozenset({ObjectId("after")})
    assert record.head.materialization == "partial" and not record.covering


def test_unrelated_well_typed_unit_cannot_impersonate_an_epoch_remap() -> None:
    store, original, head = setup()
    publish(store, original, head)
    # This is a different genuine Unit with the same key, scope and revision.
    # It was never produced from the registered old Unit by the transformer.
    alien_root = Store(identity_epoch=1)
    alien = Unit(
        original.key,
        original.frame,
        (),
        alien_root,
        outcomes=[ReturnFlow(StateHandle.capture(alien_root), Value.constant(99))],
        revision=original.revision,
        visits=original.visits,
    )
    transform = PayloadTransformer(
        epoch=1,
        charge=lambda count: store.resource_ledger.charge_work(count, "review3_epoch"),
    )
    before = store.revision
    with pytest.raises(ValueError):
        store.prepare_epoch(transform, IdentityEpoch(1), units={alien.key: alien})
    assert store.epoch == 0 and store.revision == before
    assert store.current("m", "m.answer")[0].payload.outcomes[0].value.exact_constants() == [17]


def test_completion_facts_do_not_depend_on_debug_events() -> None:
    store, unit, head = setup()
    root = unit.entry
    unit.outcomes.append(
        RaiseFlow(
            StateHandle.capture(root), ExceptionValue(Value.constant("ValueError"), "ValueError")
        )
    )
    publish(store, unit, head)
    ledger = QueryLedger(store)
    query = FactQuery("returns", "m", "m.answer")
    ledger.request(query)
    ledger.declare(
        ScopeSelector("m", "m.answer"), (head.producer,), complete=True, reachability="reachable"
    )
    answer = ledger.resolve(query)
    assert answer.covering and answer.completion == "mixed"
    assert {flow.kind for row in answer.observations for flow in row.payload.outcomes} == {
        "return",
        "raise",
    }
    assert len(answer.observations) == 1


def test_dependency_reentry_cannot_leave_a_covering_old_generation() -> None:
    store, _, unit_head = setup()
    producer = replace(unit_head.producer, kind="checked_summary", owner="summary:review3")
    metadata = replace(
        unit_head,
        producer=producer,
        dependencies=(VersionedDependency("handler", "summary-handler", "v1"),),
    )
    store.dependency_check = lambda dependency: True
    old = store.capture_detached(
        metadata, (ReturnFlow(StateHandle.capture(Store()), Value.constant(17)),)
    )
    newer_head = replace(metadata, version=replace(metadata.version, generation=2))
    newer_value = ValueOperations().join(Value.constant(17), Value.constant(19))
    newer = store.capture_detached(
        newer_head, (ReturnFlow(StateHandle.capture(Store()), newer_value),)
    )
    store.publish_generation(producer.owner, 1, (old,), application_accepted=True)
    ledger = QueryLedger(store)
    query = FactQuery("returns", "m", "m.answer")
    ledger.request(query)
    ledger.declare(ScopeSelector("m", "m.answer"), (producer,), complete=True)
    pending = True

    def refresh(dependency: VersionedDependency) -> bool:
        nonlocal pending
        if pending:
            pending = False
            store.publish_generation(producer.owner, 2, (newer,), application_accepted=True)
        return True

    store.dependency_check = refresh
    answer = ledger.resolve(query)
    assert store.current("m", "m.answer")[0].head.version.generation == 2
    assert not answer.covering or all(
        row.head.version.generation == 2 for row in answer.observations
    )


def test_failed_last_summary_acceptance_revokes_staged_commit() -> None:
    store, _, unit_head = setup()
    producer = replace(unit_head.producer, kind="checked_summary", owner="summary:rejected")
    head = replace(unit_head, producer=producer)
    fact = store.capture_detached(
        head, (ReturnFlow(StateHandle.capture(Store()), Value.constant(17)),)
    )
    plan = store.prepare_generation(producer.owner, 1, (fact,))
    plan.validate(application_accepted=True)
    with pytest.raises(ValueError, match="accepted"):
        plan.validate(application_accepted=False)
    with pytest.raises(ValueError):
        plan.commit()
    assert store.current("m", "m.answer") == ()
