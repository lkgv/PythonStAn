"""Independent publication authority, finite shape, and paid-copy controls."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from types import MappingProxyType

import pytest

from pythonstan.analysis.contracts.identity import (
    CellId,
    ContextId,
    ObjectId,
    SourceModule,
    UnitId,
)
from pythonstan.analysis.contracts.resources import ResourceStop, RunLedger
from pythonstan.analysis.domains.ints import IntLattice
from pythonstan.analysis.domains.values import Value, ValueOperations
from pythonstan.analysis.engines.abstract import publication
from pythonstan.analysis.engines.abstract.driver import InterproceduralDriver
from pythonstan.analysis.state.flow import NormalStmtFlow
from pythonstan.analysis.state.handles import StateHandle
from pythonstan.analysis.state.store import HeapObject, Store
from pythonstan.analysis.state.transform import GraphPayload, PayloadTransformer


def source_for(store: Store) -> publication.PublicationInput:
    unit = publication.PublicationUnit(
        UnitId("root"),
        "subject",
        "subject",
        ContextId(()),
        1,
        1,
        True,
        "root",
        (NormalStmtFlow(StateHandle.capture(store)),),
    )
    return publication.PublicationInput((unit,), {}, (), (unit.key,), (), (), (), (), ())


def prepared(number: int = 7) -> publication.PreparedPublication:
    return publication.prepare_publication(
        source_for(Store(cells={CellId("global:subject:x"): Value.constant(number)})),
        ValueOperations(),
    )


def runtime() -> publication.PublicationRuntime:
    return publication.PublicationRuntime(
        0, 0, 0, {}, frozenset(), (), "python-core-v1", "converged"
    )


def export_shape(value: Value, heap: dict[ObjectId, HeapObject]) -> dict[str, object]:
    return dict(
        publication._export_value(value, {ref: obj.kind for ref, obj in heap.items()}, heap)
    )


def assert_omitted(record: dict[str, object], identities: set[str]) -> None:
    assert set(record["objects"]) == identities
    assert "items" not in record
    assert record["shape_projection"] == {"complete": False, "omitted_fields": ["items"]}


def test_cross_prepared_identity_rejected_even_when_values_match() -> None:
    first, second = prepared(7), prepared(7)
    paid = publication.export_publication_values(first)
    with pytest.raises(ValueError, match="origin"):
        publication.finalize_publication(second, runtime(), published_statistics={}, exported=paid)
    good = publication.finalize_publication(
        first, runtime(), published_statistics={}, exported=paid
    )
    assert good.snapshot.to_dict()["values"]["subject"]["x"]["constants"] == [7]


def test_equal_frozen_foreign_payload_is_not_issued_identity() -> None:
    original = prepared()
    paid = publication.export_publication_values(original)
    forged = replace(paid, values=MappingProxyType(dict(paid.values)))
    with pytest.raises(ValueError, match="genuine"):
        publication.finalize_publication(
            original, runtime(), published_statistics={}, exported=forged
        )


def test_retained_mapping_alias_exposes_snapshot_time_contract() -> None:
    # Fully typed caller-owned dictionaries are legal Mapping inputs; no cast or
    # frozen-dataclass bypass is needed to retain a mutable alias.
    rows = {"x": Value.constant(7)}
    original = replace(prepared(), values={"subject": rows})
    paid = publication.export_publication_values(original)
    rows["x"] = Value.constant(8)
    result = publication.finalize_publication(
        original, runtime(), published_statistics={}, exported=paid
    ).snapshot.to_dict()
    assert original.values["subject"]["x"].exact_constants() == [8]
    assert result["values"]["subject"]["x"]["constants"] == [7]
    # This records immutable issued-snapshot behavior. Whether origin mutation
    # must be rejected is a separate precondition/TCB question, not a free proof.


def test_prepare_denial_precedes_any_heap_graph_copy(monkeypatch: pytest.MonkeyPatch) -> None:
    state = Store(heap={ObjectId("obj"): HeapObject("list", metadata={"length": 0})})
    calls = 0

    def forbidden_copy(self: HeapObject) -> HeapObject:
        nonlocal calls
        calls += 1
        raise AssertionError("Unfunded heap copy")

    def deny(amount: int) -> None:
        raise ResourceStop("review_denial")

    monkeypatch.setattr(HeapObject, "copy", forbidden_copy)
    with pytest.raises(ResourceStop, match="review_denial"):
        publication.prepare_publication(source_for(state), ValueOperations(), visit=deny)
    assert calls == 0


def test_export_denial_precedes_value_materialization(monkeypatch: pytest.MonkeyPatch) -> None:
    original = prepared()
    calls = 0

    def forbidden(self: Value, *args: object, **kwargs: object) -> dict[str, object]:
        nonlocal calls
        calls += 1
        raise AssertionError("Unfunded value export")

    def deny(amount: int) -> None:
        raise ResourceStop("review_denial")

    monkeypatch.setattr(Value, "export", forbidden)
    with pytest.raises(ResourceStop, match="review_denial"):
        publication.export_publication_values(original, visit=deny)
    assert calls == 0


def test_bool_integer_reference_alternatives_cannot_share_exact_shape() -> None:
    left, right = ObjectId("left"), ObjectId("right")
    heap = {
        left: HeapObject("list", {0: Value.constant(True)}, metadata={"length": 1}),
        right: HeapObject("list", {0: Value.constant(1)}, metadata={"length": 1}),
    }
    assert_omitted(export_shape(Value(refs=frozenset(heap)), heap), {"left", "right"})


@pytest.mark.parametrize("fault", ["missing", "default", "havoc", "summary", "interval", "mixed"])
def test_uncertain_shape_components_never_claim_exact_items(fault: str) -> None:
    ref = ObjectId("object")
    obj = HeapObject("list", {0: Value.constant(7)}, metadata={"length": 1})
    value = Value.ref(ref)
    if fault == "missing":
        obj.maybe_missing.add(0)
    elif fault == "default":
        obj.default = Value.constant(9)
    elif fault == "havoc":
        obj.havoc = True
    elif fault == "summary":
        obj.metadata["summarized_keys"] = frozenset({0})
    elif fault == "interval":
        obj.metadata["length_value"] = Value(ints=IntLattice.from_interval(1, 2))
    else:
        value = replace(value, bools=frozenset({True}))
    assert_omitted(export_shape(value, {ref: obj}), {"object"})


def test_cycle_and_equal_nonsingleton_alternatives_are_distinguished() -> None:
    left, right = ObjectId("left"), ObjectId("right")
    heap = {
        left: HeapObject("tuple", {0: Value.constant(7)}, singleton=False, metadata={"length": 1}),
        right: HeapObject("tuple", {0: Value.constant(7)}, singleton=False, metadata={"length": 1}),
    }
    assert export_shape(Value(refs=frozenset(heap)), heap)["items"] == [7]
    heap[left].fields[0] = Value.ref(left)
    assert_omitted(export_shape(Value.ref(left), heap), {"left"})


@pytest.mark.parametrize("limit", [0, 10])
def test_shared_budget_stop_uses_bounded_controller_without_raw_error(limit: int) -> None:
    ledger = RunLedger(limit, 0, 0, memory_reader=lambda: 0)
    driver = InterproceduralDriver(
        (SourceModule("subject", "subject.py", "result=[(1,2)]\n"),), resource_ledger=ledger
    )
    result = driver.run().to_dict()
    assert result["solver_status"] == "budget_exhausted"
    assert result["coverage_status"] == "open" and result["values"] == {}
    assert result["diagnostics"] == [{"code": "publication_unavailable", "facts_omitted": True}]
    assert ledger.work.used <= limit


def test_stopped_ledger_never_retries_full_heap_copy(monkeypatch: pytest.MonkeyPatch) -> None:
    ledger = RunLedger(10000, 0, 0, memory_reader=lambda: 0)
    driver = InterproceduralDriver(
        (SourceModule("subject", "subject.py", "result=[(1,2)]\n"),), resource_ledger=ledger
    )
    driver.run()
    ledger.stop("original_first_cause")
    work = ledger.work.used

    def forbidden_copy(self: HeapObject) -> HeapObject:
        raise AssertionError("Stopped publication retried full heap copy")

    monkeypatch.setattr(HeapObject, "copy", forbidden_copy)
    record = driver.snapshot(0).to_dict()
    assert record["solver_status"] == "budget_exhausted" and record["values"] == {}
    assert record["obligations"] == [{"code": "budget:original_first_cause", "status": "open"}]
    assert ledger.work.used == work


def test_nested_store_graph_copy_is_repeatedly_admitted(monkeypatch: pytest.MonkeyPatch) -> None:
    state = Store(cells={CellId("global:subject:x"): Value.constant(7)})
    for index in range(3):
        state.heap[ObjectId(str(index))] = HeapObject("instance", metadata={"entry": state})
    original_walk = publication.walk_graph
    original_copy = HeapObject.copy
    original_charge = PayloadTransformer._charge
    funded: dict[int, list[int]] = {}
    raw = 0
    transform_work = 0
    copying = False
    copies = 0

    def visit(amount: int) -> None:
        nonlocal raw
        raw += amount

    def counted_walk(
        payload: GraphPayload,
        *,
        on_value: Callable[[Value], None] | None = None,
        on_store: Callable[[Store], None] | None = None,
        charge: Callable[[int], None] | None = None,
    ) -> int:
        before = raw
        work = original_walk(payload, on_value=on_value, on_store=on_store, charge=charge)
        funded.setdefault(id(payload), []).append(raw - before)
        return work

    def counted_charge(self: PayloadTransformer, amount: int = 1) -> None:
        nonlocal transform_work
        if copying:
            transform_work += amount
        original_charge(self, amount)

    def counted_copy(self: HeapObject) -> HeapObject:
        nonlocal copying, copies
        credit = funded[id(self)].pop(0)
        before = transform_work
        copying = True
        try:
            result = original_copy(self)
        finally:
            copying = False
        copies += 1
        assert credit >= transform_work - before > 0
        return result

    monkeypatch.setattr(publication, "walk_graph", counted_walk)
    monkeypatch.setattr(HeapObject, "copy", counted_copy)
    monkeypatch.setattr(PayloadTransformer, "_charge", counted_charge)
    publication.prepare_publication(source_for(state), ValueOperations(), visit=visit)
    assert copies > 0
    assert raw >= transform_work > 0
    assert all(not credits for credits in funded.values())
    diagnostic = {"copies": copies, "admitted_raw": raw, "internal_transform_work": transform_work}
    target = Path("artifacts/next/evidence/SEM_PTA_REVIEW_RESUME2/publication-copy-counts.json")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(diagnostic, indent=2) + "\n")


def test_shared_publication_graph_work_has_export_phase() -> None:
    ledger = RunLedger(10000, 0, 0, memory_reader=lambda: 0)
    driver = InterproceduralDriver(
        (SourceModule("subject", "subject.py", "result=[1]\n"),), resource_ledger=ledger
    )
    with ledger.phase("core"):
        driver.run()
    assert ledger.raw_structural_by_phase.get("export", {}).get("publication_export", 0) > 0
    assert ledger.raw_structural_by_phase.get("core", {}).get("publication_export", 0) == 0


def test_actual_shared_one_tick_denies_mid_graph_before_copy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = Store(cells={CellId("global:subject:x"): Value.constant(7)})
    state.heap[ObjectId("root")] = HeapObject("instance", metadata={"entry": state})
    ledger = RunLedger(1, 0, 0, memory_reader=lambda: 0)
    copies = 0

    def forbidden_copy(self: HeapObject) -> HeapObject:
        nonlocal copies
        copies += 1
        raise AssertionError("Partially funded graph reached copy")

    monkeypatch.setattr(HeapObject, "copy", forbidden_copy)
    with (
        pytest.raises(ResourceStop),
        ledger.structural_meter("review_graph_admission") as meter,
    ):
        publication.prepare_publication(source_for(state), ValueOperations(), visit=meter.visit)
    assert copies == 0 and ledger.work.used == 1
