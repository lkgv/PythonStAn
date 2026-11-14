"""Semantic fact ownership, transaction and query-scope contracts."""

from __future__ import annotations

from dataclasses import replace
from threading import Thread

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
    FactPublication,
    FactSink,
    FactVersion,
    ProcedureFactHead,
    ProcedureProducer,
    ProcedureScope,
)
from pythonstan.analysis.contracts.resources import ResourceStop, RunLedger
from pythonstan.analysis.domains.values import Value, ValueOperations
from pythonstan.analysis.observations.procedures import (
    PreparedGeneration,
    ProcedureObservationStore,
    ProcedurePayload,
)
from pythonstan.analysis.observations.query_ledger import FactQuery, QueryLedger, ScopeSelector
from pythonstan.analysis.state.boundary import HostStateBoundary
from pythonstan.analysis.state.flow import ExceptionValue, RaiseFlow, ReturnFlow
from pythonstan.analysis.state.handles import StateHandle
from pythonstan.analysis.state.store import Frame, HeapObject, Store, Unit
from pythonstan.analysis.state.transform import PayloadTransformer


def service(work: int = 10000) -> ProcedureObservationStore:
    return ProcedureObservationStore(
        SemanticIdentity("source", "ir", "model", "policy", "profile"),
        IdentityEpoch(0),
        RunLedger(work, 0, 0, memory_reader=lambda: 0),
    )


def unit(value: int = 17) -> Unit:
    root = Store(cells={CellId("global:m:x"): Value.constant(value)})
    frame = Frame("m", "m.f", ContextId(()), FrameId("frame:f"))
    return Unit(
        UnitId("unit:f"),
        frame,
        (),
        root,
        outcomes=[ReturnFlow(StateHandle.capture(root), Value.constant(value))],
        revision=StateRevision(1),
        visits=1,
    )


def head(
    store: ProcedureObservationStore, generation: int = 1, *, kind: str = "unit"
) -> ProcedureFactHead:
    producer = ProcedureProducer(
        "unit" if kind == "unit" else "checked_summary",
        "unit:f",
        "unit:f",
        ProcedureScope("m", "m.f", "root-1"),
    )
    return ProcedureFactHead(
        producer, FactVersion(generation, StateRevision(1), store.epoch), store.identity
    )


def publish_unit(store: ProcedureObservationStore, producer: Unit) -> ProcedureFactHead:
    metadata = head(store, producer.visits)
    store.register_unit(producer)
    publication = store.capture_unit(metadata, producer)
    store.publish_generation(metadata.producer.owner, producer.visits, (publication,))
    return metadata


def test_actual_unit_borrow_does_not_copy_store(monkeypatch: pytest.MonkeyPatch) -> None:
    store, producer = service(), unit()

    def forbidden(*args: object, **kwargs: object) -> object:
        raise AssertionError("Unit observations must not deep-copy an already-owned graph")

    monkeypatch.setattr(PayloadTransformer, "visit", forbidden)
    metadata = publish_unit(store, producer)
    sink: FactSink[ProcedurePayload] = store
    assert sink is store
    record = store.current("m", "m.f")[0]
    assert record.covering and record.head == metadata
    assert record.payload.outcomes[0].store is producer.outcomes[0].store


def test_foreign_unit_and_unregistered_owner_are_rejected() -> None:
    store, producer = service(), unit()
    with pytest.raises(ValueError, match="registered"):
        store.capture_unit(head(store), producer)
    store.register_unit(producer)
    foreign = replace(producer)
    with pytest.raises(ValueError, match="registered"):
        store.capture_unit(head(store), foreign)
    with pytest.raises(ValueError, match="different Unit"):
        store.register_unit(foreign)
    assert store.current("m", "m.f") == ()


def test_forged_payload_and_cross_request_receipt_cannot_publish() -> None:
    store, producer = service(), unit()
    store.register_unit(producer)
    metadata = head(store)
    forged = FactPublication(metadata, ProcedurePayload(tuple(producer.outcomes)))
    before = store.revision
    with pytest.raises(ValueError, match="ownership"):
        store.publish_generation(metadata.producer.owner, 1, (forged,))
    assert store.revision == before
    genuine = store.capture_unit(metadata, producer)
    other = service()
    with pytest.raises(ValueError, match="ownership"):
        other.publish_generation(metadata.producer.owner, 1, (genuine,))
    assert other.current("m", "m.f") == ()


def test_detached_summary_graph_survives_later_caller_mutation() -> None:
    store = service()
    caller = Store(heap={ObjectId("box"): HeapObject("dict", {"value": Value.constant(1)})})
    flow = ReturnFlow(StateHandle.capture(caller), Value.ref(ObjectId("box")))
    metadata = head(store, kind="checked_summary")
    publication = store.capture_detached(metadata, (flow,))
    store.publish_generation(metadata.producer.owner, 1, (publication,), application_accepted=True)
    caller.write_slot(ObjectId("box"), "value", Value.constant(9), ValueOperations())
    record = store.current("m", "m.f")[0]
    assert record.covering
    assert record.payload.outcomes[0].store is not caller
    assert record.payload.outcomes[0].store.heap[ObjectId("box")].fields[
        "value"
    ].exact_constants() == [1]


def test_borrowed_root_mutation_invalidates_without_read_side_effect() -> None:
    store, producer = service(), unit()
    publish_unit(store, producer)
    producer.outcomes[0].store.write_cell(CellId("global:m:x"), Value.constant(9))
    before = store.revision
    record = store.current("m", "m.f")[0]
    assert record.head.materialization == "invalidated"
    assert record.reason == "borrowed_generation_changed"
    assert store.revision == before


def test_equal_outcomes_still_replace_producer_generation() -> None:
    store, producer = service(), unit()
    publish_unit(store, producer)
    producer.visits = 2  # The semantic Unit revision and outcome list stay equal.
    store.start_generation("unit:f", 2)
    assert not store.current("m", "m.f")[0].covering
    newer = store.capture_unit(head(store, 2), producer)
    store.publish_generation("unit:f", 2, (newer,), application_accepted=True)
    records = store.current("m", "m.f")
    assert len(records) == 1 and records[0].covering
    assert records[0].head.version.generation == 2
    assert records[0].head.version.revision == 1


def test_generation_replacement_drops_absent_old_subproducers() -> None:
    store = service()
    metadata = head(store, kind="checked_summary")
    flow = ReturnFlow(StateHandle.capture(Store()), Value.constant(1))
    first = store.capture_detached(metadata, (flow,))
    other_head = replace(
        metadata,
        producer=replace(
            metadata.producer, identity="unit:g", scope=ProcedureScope("m", "m.g", "root-1")
        ),
    )
    other = store.capture_detached(other_head, (flow,))
    store.publish_generation("unit:f", 1, (first, other), application_accepted=True)
    newer_head = replace(metadata, version=replace(metadata.version, generation=2))
    newer = store.capture_detached(newer_head, (flow,))
    store.publish_generation("unit:f", 2, (newer,), application_accepted=True)
    assert store.current("m", "m.g") == ()
    assert len(store.current("m", "m.f")) == 1


def test_failed_preparation_and_stale_commit_do_not_publish() -> None:
    store, producer = service(), unit()
    store.register_unit(producer)
    publication = store.capture_unit(head(store), producer)
    before = store.revision
    with store.resource_ledger.work_scope(0, "deny_fact_prepare"), pytest.raises(ResourceStop):
        store.prepare_generation("unit:f", 1, (publication,))
    assert store.revision == before and store.current("m", "m.f") == ()
    plan = store.prepare_generation("unit:f", 1, (publication,))
    with pytest.raises(ValueError, match="validated"):
        plan.commit()
    store.start_generation("another-owner", 1)
    with pytest.raises(ValueError, match="stale"):
        plan.validate()
    assert store.current("m", "m.f") == ()


def test_forged_or_consumed_commit_ticket_is_rejected() -> None:
    store, producer = service(), unit()
    store.register_unit(producer)
    publication = store.capture_unit(head(store), producer)
    plan = store.prepare_generation("unit:f", 1, (publication,))
    forged = PreparedGeneration(
        store, plan._base_revision, plan._epoch, plan._owner, plan._generation
    )
    with pytest.raises(ValueError, match="Foreign"):
        forged.validate()
    plan.validate()
    plan.commit()
    with pytest.raises(ValueError, match="consumed"):
        plan.commit()


def test_dependency_version_changes_invalidate_observed_facts() -> None:
    store, producer = service(), unit()
    current = {"f": "v1"}
    store.dependency_check = lambda dep: current.get(dep.identity) == dep.version
    metadata = replace(head(store), dependencies=(VersionedDependency("function", "f", "v1"),))
    store.register_unit(producer)
    publication = store.capture_unit(metadata, producer)
    store.publish_generation("unit:f", 1, (publication,))
    assert store.current("m", "m.f")[0].covering
    before = store.revision
    current["f"] = "v2"
    assert store.current("m", "m.f")[0].reason == "dependency_invalidated"
    assert store.revision == before


def test_shared_host_epoch_preparation_preserves_real_unit_graph() -> None:
    store, producer = service(), unit()
    root = producer.outcomes[0].store
    root.heap[ObjectId("old")] = HeapObject("instance", metadata={"entry": root})
    producer.outcomes = [ReturnFlow(StateHandle.capture(root), Value.ref(ObjectId("old")))]
    publish_unit(store, producer)
    ops = ValueOperations()
    transform = PayloadTransformer(
        {ObjectId("old"): ObjectId("new")},
        ops,
        epoch=1,
        charge=lambda amount: store.resource_ledger.charge_work(amount, "test_host_epoch"),
    )
    host = transform.visit(HostStateBoundary({producer.key: producer}, {}, (), []))
    plan = store.prepare_epoch(transform, IdentityEpoch(1), units=host.units)
    before = store.revision
    plan.validate()
    assert store.epoch == 0 and store.revision == before
    plan.commit()
    record = store.current("m", "m.f")[0]
    assert record.head.materialization == "partial"  # Requires fresh entry-scope reanalysis.
    assert record.payload.outcomes[0].value.refs == frozenset({ObjectId("new")})
    mapped = host.units[producer.key].outcomes[0].store
    assert record.payload.outcomes[0].store is mapped
    assert mapped.heap[ObjectId("new")].metadata["entry"] is mapped
    assert root.heap[ObjectId("old")].metadata["entry"] is root
    assert not root.identity_epoch and store.epoch == 1


def test_epoch_without_admission_or_failed_prepare_leaves_old_publication() -> None:
    store, producer = service(), unit()
    publish_unit(store, producer)
    before = store.revision
    with pytest.raises(ValueError, match="admitted"):
        store.prepare_epoch(PayloadTransformer(epoch=1), IdentityEpoch(1))
    transform = PayloadTransformer(
        epoch=1, charge=lambda count: store.resource_ledger.charge_work(count, "test_epoch")
    )
    with store.resource_ledger.work_scope(0, "deny_epoch"), pytest.raises(ResourceStop):
        store.prepare_epoch(transform, IdentityEpoch(1), units={producer.key: producer})
    assert store.revision == before and store.epoch == 0
    assert store.current("m", "m.f")[0].covering


def test_query_catalog_distinguishes_missing_raise_only_lazy_unreachable() -> None:
    store, producer = service(), unit()
    root = Store()
    producer.outcomes = [
        RaiseFlow(
            StateHandle.capture(root), ExceptionValue(Value.constant("ValueError"), "ValueError")
        )
    ]
    metadata = publish_unit(store, producer)
    ledger = QueryLedger(store)
    query = FactQuery("returns", "m", "m.f")
    ledger.request(query)
    assert ledger.resolve(query).scope_status == "unmaterialized"
    selector = ScopeSelector("m", "m.f")
    ledger.declare(selector, (metadata.producer,), reachability="reachable", complete=True)
    answer = ledger.resolve(query)
    assert answer.covering and answer.completion == "raise_only"
    ledger.declare(selector, (), reachability="lazy", complete=True)
    assert ledger.resolve(query).scope_status == "lazy"
    with pytest.raises(ValueError, match="complete empty"):
        ledger.declare(selector, (), reachability="unreachable", complete=False)
    ledger.declare(selector, (), reachability="unreachable", complete=True)
    assert ledger.resolve(query).completion == "unreachable"


def test_empty_bulk_needs_closed_inventory_and_projection_is_separate() -> None:
    store = service()
    ledger = QueryLedger(store)
    query = FactQuery("returns")
    assert ledger.resolve(query).status == "not_requested"
    ledger.request(query)
    assert ledger.resolve(query).status == "unavailable"
    ledger.close_inventory()
    answer = ledger.resolve(query)
    assert answer.covering and answer.scope_status == "empty"
    excluded = ledger.resolve(query, visible=False)
    assert excluded.semantic_complete and not excluded.projection_complete
    assert excluded.scope_status == "excluded" and excluded.coverage_status == "open"
    truncated = ledger.resolve(query, truncated=True)
    assert truncated.scope_status == "truncated"
    with pytest.raises(ValueError, match="cover"):
        truncated.require_covering()


def test_missing_expected_or_partial_producer_is_not_closed_empty() -> None:
    store, producer = service(), unit()
    metadata = publish_unit(store, producer)
    ledger = QueryLedger(store)
    query = FactQuery("returns", "m", "m.f")
    ledger.request(query)
    selector = ScopeSelector("m", "m.f")
    missing = replace(metadata.producer, identity="missing-call")
    ledger.declare(selector, (metadata.producer, missing), complete=True)
    assert ledger.resolve(query).scope_status == "partial"
    ledger.declare(selector, (metadata.producer,), complete=True)
    assert ledger.resolve(query).covering
    store.start_generation("unit:f", 2)
    assert ledger.resolve(query).scope_status == "partial"
    ledger.declare(selector, (), complete=True, supported=False)
    assert ledger.resolve(query).scope_status == "unsupported"


def test_foreign_writer_and_zero_budget_cannot_publish_semantic_facts() -> None:
    store = service()
    errors: list[str] = []

    def foreign() -> None:
        try:
            store.start_generation("foreign", 1)
        except ValueError as error:
            errors.append(str(error))

    worker = Thread(target=foreign)
    worker.start()
    worker.join()
    assert errors and "owner" in errors[0] and store.revision == 0
    empty = service(0)
    with pytest.raises(ResourceStop):
        empty.capture_detached(head(empty, kind="checked_summary"), ())
    assert empty.revision == 0


def test_checked_summary_staging_cannot_commit_failed_or_unaccepted_application() -> None:
    store = service()
    metadata = head(store, kind="checked_summary")
    flow = ReturnFlow(StateHandle.capture(Store()), Value.constant(17))
    publication = store.capture_detached(metadata, (flow,))
    plan = store.prepare_generation("unit:f", 1, (publication,))
    before = store.revision
    for accepted in (None, False):
        with pytest.raises(ValueError, match="whole accepted"):
            plan.validate(application_accepted=accepted)
    assert store.revision == before and store.current("m", "m.f") == ()
    plan.validate(application_accepted=True)
    plan.commit()
    assert store.current("m", "m.f")[0].covering


def test_closed_inventory_does_not_make_unknown_module_a_complete_empty_scope() -> None:
    ledger = QueryLedger(service())
    ledger.close_inventory()
    unknown = FactQuery("returns", "missing")
    ledger.request(unknown)
    assert ledger.resolve(unknown).status == "unavailable"


def test_old_unreachable_catalog_cannot_hide_new_reached_procedure_facts() -> None:
    store = service()
    ledger = QueryLedger(store)
    query = FactQuery("returns", "m", "m.f")
    ledger.request(query)
    ledger.declare(ScopeSelector("m", "m.f"), (), reachability="unreachable", complete=True)
    assert ledger.resolve(query).covering
    publish_unit(store, unit())
    answer = ledger.resolve(query)
    assert not answer.covering and answer.status == "unavailable"
