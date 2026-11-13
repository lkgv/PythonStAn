"""New independent W3 witnesses after the unpublished workspace was lost.

These cases do not stand in for the missing prior W3 fixture sources. Existing
W2 frozen manifests and numeric resource profiles are left unchanged.
"""

from __future__ import annotations

import hashlib
from dataclasses import replace
from pathlib import Path

import pytest

from pythonstan.analysis.composition.oracle import IncrementalCandidateOracle
from pythonstan.analysis.contracts.identity import CellId, ObjectId, SourceModule, UnitId
from pythonstan.analysis.contracts.json_data import JsonObject
from pythonstan.analysis.domains.reabstract import ReferenceKey
from pythonstan.analysis.domains.values import Value, ValueOperations
from pythonstan.analysis.engines.abstract.driver import InterproceduralDriver
from pythonstan.analysis.state.flow import FlowTail, NormalStmtFlow
from pythonstan.analysis.state.handles import StateHandle
from pythonstan.analysis.state.store import HeapObject, Store
from pythonstan.analysis.state.transform import payload_signature
from tests.regression.audit3 import semantic_oracle as oracle

PREREG = (
    Path(__file__).resolve().parents[3]
    / "docs/development/next/reviews/W3_REBUILD_preregistration_20261113.json"
)


def _driver() -> InterproceduralDriver:
    return InterproceduralDriver(
        (SourceModule("subject", "subject.py", "pass\n"),),
        {"resources": {"max_work": 100000, "max_seconds": 0, "max_memory_mb": 0}},
    )


def _alias_store(shared: bool) -> Store:
    left = [Value.constant(0)]
    right = left if shared else [Value.constant(0)]
    return Store(
        heap={
            ObjectId("A"): HeapObject("instance", metadata={"attachment": left}),
            ObjectId("B"): HeapObject("instance", metadata={"attachment": right}),
            ObjectId("C"): HeapObject("instance", metadata={"attachment": [Value.constant(7)]}),
        }
    )


@pytest.mark.parametrize("shared", [False, True])
def test_identical_alias_world_retains_precision(shared: bool) -> None:
    left = _alias_store(shared)
    right = _alias_store(shared)
    ops = ValueOperations()
    merged = left.join(right, ops)
    assert left.leq(right, ops) and right.leq(left, ops)
    assert left.leq(merged, ops) and right.leq(merged, ops)
    assert not any(obj.havoc for obj in merged.heap.values())
    a = merged.heap[ObjectId("A")].metadata["attachment"]
    b = merged.heap[ObjectId("B")].metadata["attachment"]
    assert (a is b) is shared


@pytest.mark.parametrize("reverse", [False, True])
def test_cross_owner_alias_disagreement_cannot_be_ordered_as_identical(reverse: bool) -> None:
    left, right = _alias_store(True), _alias_store(False)
    if reverse:
        left, right = right, left
    assert not left.leq(right, ValueOperations())


@pytest.mark.parametrize("reverse", [False, True])
def test_alias_disagreement_join_covers_both_without_poisoning_unrelated_owner(
    reverse: bool,
) -> None:
    left, right = _alias_store(True), _alias_store(False)
    if reverse:
        left, right = right, left
    ops = ValueOperations()
    merged = left.join(right, ops)
    assert merged.heap[ObjectId("A")].havoc
    assert merged.heap[ObjectId("B")].havoc
    assert not merged.heap[ObjectId("C")].havoc
    assert left.leq(merged, ops) and right.leq(merged, ops)
    assert merged.heap[ObjectId("C")].metadata["attachment"][0].exact_constants() == [7]
    assert left.heap[ObjectId("A")].metadata["attachment"][0].exact_constants() == [0]


def _tail_worlds() -> tuple[NormalStmtFlow, NormalStmtFlow]:
    attached = Store(cells={CellId("x"): Value.constant(0)})
    detached = attached.copy()
    return (
        NormalStmtFlow(StateHandle.capture(attached), FlowTail(resume_target={"root": attached})),
        NormalStmtFlow(
            StateHandle.capture(detached), FlowTail(resume_target={"root": detached.copy()})
        ),
    )


def test_outcome_equations_distinguish_root_to_tail_alias_worlds() -> None:
    left, right = _tail_worlds()
    left.store.write_cell(CellId("x"), Value.constant(9))
    right.store.write_cell(CellId("x"), Value.constant(9))
    assert left.resume_target["root"].cells[CellId("x")].exact_constants() == [9]
    assert right.resume_target["root"].cells[CellId("x")].exact_constants() == [0]
    # Compare equal pre-write contents, where the distinction is only topology.
    before_left, before_right = _tail_worlds()
    assert not _driver()._outcomes_equal([before_left], [before_right])


def test_compaction_does_not_delete_a_distinct_tail_alias_world() -> None:
    left, right = _tail_worlds()
    assert len(_driver().compact([left, right])) == 2


@pytest.mark.parametrize("explicit_cell_alias", [False, True])
def test_object_quotient_preserves_cell_namespace_and_literal_reference_like_key(
    explicit_cell_alias: bool,
) -> None:
    driver = _driver()
    arena = driver.ops.arena
    if explicit_cell_alias:
        arena.publish({}, cell_aliases={CellId("A"): CellId("C")})
    source = Store(
        cells={CellId("A"): Value.ref(ObjectId("A"))},
        heap={ObjectId("A"): HeapObject("dict", {("ref", "A"): Value.constant(3)})},
    )
    before = payload_signature(source)
    (mapped,) = driver.reabstract_addresses({ObjectId("A"): ObjectId("S")}, (source,))
    wanted_cell = CellId("C") if explicit_cell_alias else CellId("A")
    assert set(mapped.cells) == {wanted_cell}
    assert mapped.cells[wanted_cell].refs == frozenset({ObjectId("S")})
    assert mapped.heap[ObjectId("S")].fields[("ref", "A")].exact_constants() == [3]
    assert payload_signature(source) == before


def _host_oracle() -> tuple[InterproceduralDriver, IncrementalCandidateOracle]:
    driver = _driver()
    service = IncrementalCandidateOracle(
        source_digest=driver.source_digest,
        profile=driver.semantic_profile,
        policy_digest=driver.policy_digest,
        model_digest=driver.model_digest,
        ops=driver.ops,
    )
    service.bind(driver)
    driver.install_oracle(service, incremental=service)
    return driver, service


def test_owned_object_transaction_migrates_existing_cell_aliases_and_late_copy_edges() -> None:
    driver, service = _host_oracle()
    service.publish_cell(CellId("A"), Value.ref(ObjectId("A")))
    service.register_copy(CellId("A"), CellId("out"))
    service.candidates(CellId("A"), UnitId("consumer"))
    driver.ops.arena.publish({}, cell_aliases={CellId("A"): CellId("C")})
    driver.reabstract_addresses({ObjectId("A"): ObjectId("S")})
    assert service.candidates(CellId("C")).values.refs == frozenset({ObjectId("S")})
    assert (CellId("C"), UnitId("consumer")) in service._subscriptions
    service.publish_cell(CellId("C"), Value.ref(ObjectId("T")))
    assert service.candidates(CellId("out")).values.refs == frozenset(
        {ObjectId("S"), ObjectId("T")}
    )


@pytest.mark.parametrize("nested", [False, True])
def test_oracle_remaps_explicit_reference_keys_but_not_literal_lookalikes(nested: bool) -> None:
    driver, service = _host_oracle()
    old_key = ("nested", ReferenceKey(ObjectId("K"))) if nested else ReferenceKey(ObjectId("K"))
    new_key = ("nested", ReferenceKey(ObjectId("T"))) if nested else ReferenceKey(ObjectId("T"))
    service.publish_field(ObjectId("A"), old_key, Value.constant(42))
    service.publish_field(ObjectId("A"), ("ref", "K"), Value.constant(7))
    driver.reabstract_addresses({ObjectId("A"): ObjectId("S"), ObjectId("K"): ObjectId("T")})
    assert service.kernel.value(("field", ObjectId("S"), new_key)).exact_constants() == [42]
    assert service.kernel.value(("field", ObjectId("S"), ("ref", "K"))).exact_constants() == [7]
    assert ("field", ObjectId("S"), old_key) not in service.kernel.facts


def test_late_in_place_cell_alias_change_aborts_before_object_or_oracle_publication(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    driver, service = _host_oracle()
    service.publish_cell(CellId("A"), Value.ref(ObjectId("A")))
    original_kernel = service.kernel
    original_units, original_functions = driver.units, driver.functions
    original_check = InterproceduralDriver._check_limits
    mutated = False

    def late_check(self: InterproceduralDriver, force: bool = False) -> None:
        nonlocal mutated
        original_check(self, force=force)
        if self is driver and force and self._in_reabstract and not mutated:
            self.ops.arena.cell_aliases[CellId("A")] = CellId("C")
            mutated = True

    monkeypatch.setattr(InterproceduralDriver, "_check_limits", late_check)
    with pytest.raises((ValueError, RuntimeError)):
        driver.reabstract_addresses({ObjectId("A"): ObjectId("S")})
    assert mutated
    assert driver.ops.arena.aliases == {}
    assert driver.ops.arena.cell_aliases == {CellId("A"): CellId("C")}
    assert driver.units is original_units and driver.functions is original_functions
    assert service.kernel is original_kernel
    assert driver.work > 0


def test_late_oracle_generation_change_is_not_partially_published(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    driver, service = _host_oracle()
    service.publish_cell(CellId("A"), Value.ref(ObjectId("A")))
    original_kernel = service.kernel
    original_check = InterproceduralDriver._check_limits
    mutated = False

    def late_check(self: InterproceduralDriver, force: bool = False) -> None:
        nonlocal mutated
        original_check(self, force=force)
        if self is driver and force and self._in_reabstract and not mutated:
            service.kernel.add_fact(("cell", CellId("late")), Value.constant(9))
            mutated = True

    monkeypatch.setattr(InterproceduralDriver, "_check_limits", late_check)
    with pytest.raises((ValueError, RuntimeError)):
        driver.reabstract_addresses({ObjectId("A"): ObjectId("S")})
    assert mutated and service.kernel is original_kernel
    assert driver.ops.arena.aliases == {}
    assert service.kernel.value(("cell", CellId("late"))).exact_constants() == [9]


def test_join_of_cross_referencing_source_roots_still_covers_each_input_alias_world() -> None:
    left = Store(cells={CellId("x"): Value.constant(0)})
    right = Store(cells={CellId("x"): Value.constant(1)})
    left.heap[ObjectId("A")] = HeapObject("instance", metadata={"entry": right})
    right.heap[ObjectId("A")] = HeapObject("instance", metadata={"entry": left})
    ops = ValueOperations()
    merged = left.join(right, ops)
    assert left.leq(merged, ops) and right.leq(merged, ops)
    if merged.heap[ObjectId("A")].metadata["entry"] is merged:
        assert merged.heap[ObjectId("A")].havoc
    assert left.cells[CellId("x")].exact_constants() == [0]
    assert right.cells[CellId("x")].exact_constants() == [1]


def test_ordinary_external_store_alias_is_not_coalesced_or_unnecessarily_havoced() -> None:
    external = Store(cells={CellId("x"): Value.constant(7)})
    left = Store(
        cells={CellId("x"): Value.constant(0)},
        heap={ObjectId("A"): HeapObject("instance", metadata={"entry": external})},
    )
    right = Store(
        cells={CellId("x"): Value.constant(1)},
        heap={ObjectId("A"): HeapObject("instance", metadata={"entry": external})},
    )
    ops = ValueOperations()
    merged = left.join(right, ops)
    assert left.leq(merged, ops) and right.leq(merged, ops)
    assert not merged.heap[ObjectId("A")].havoc
    target = merged.heap[ObjectId("A")].metadata["entry"]
    assert target is not merged and target is not external
    merged.write_cell(CellId("x"), Value.constant(9))
    assert target.cells[CellId("x")].exact_constants() == [7]
    assert external.cells[CellId("x")].exact_constants() == [7]


def _semantic_cases() -> list[dict[str, oracle.Json]]:
    prereg = oracle.object_value(oracle.decode(PREREG.read_text()))
    return [oracle.object_value(value) for value in oracle.array_value(prereg["semantic_cases"])]


@pytest.mark.parametrize("case", _semantic_cases(), ids=lambda case: case["id"])
def test_finite_constructor_protocol_matches_frozen_concrete_prefix(
    case: dict[str, oracle.Json],
) -> None:
    source = oracle.string_value(case["source"])
    assert hashlib.sha256(source.encode()).hexdigest() == case["source_sha256"]
    concrete = oracle.run_worker(oracle.CONCRETE_WORKER, {"source": source})
    assert concrete.get("process_status") == "completed"
    assert concrete["result"] == case["expected"]
    observed = oracle.run_worker(
        oracle.ANALYZER_WORKER,
        {
            "source": source,
            "resources": {"max_work": 10000, "max_seconds": 8, "max_memory_mb": 1536},
        },
        timeout=15,
    )
    assert observed.get("process_status") == "completed", observed
    answer = oracle.object_value(observed["answer"])
    assert (
        answer["status"] == "answered" and observed["coverage_status"] == "closed_under_profile"
    ), observed
    assert oracle.exact_scalar(oracle.object_value(answer["value"]), [case["expected"]]), observed


def test_existing_frozen_bytes_boundary_is_not_narrowed_to_an_opaque_reference() -> None:
    manifest = oracle.load_manifest()
    cases = [oracle.object_value(value) for value in oracle.array_value(manifest["cases"])]
    case = next(value for value in cases if value["id"] == "bytes_iteration_open")
    result = oracle.check_analyzer(
        case,
        {"max_work": 10000, "max_seconds": 8, "max_memory_mb": 1536, "external_seconds": 15},
    )
    assert result["passed"], result


def test_wire_projection_preserves_possible_unbound_even_when_both_labels_are_omitted() -> None:
    from pythonstan.analysis.domains.value_wire import restore_value

    original = replace(Value.constant(1), unbound=True)
    document = original.export()
    document.pop("may_unbound")
    document.pop("types")
    document["projection"] = {
        "complete": False,
        "omitted_fields": ["may_unbound", "types"],
    }
    restored = restore_value(document)
    assert not restored.complete
    assert restored.value.unbound
    assert ValueOperations().leq(original, restored.value)


def test_wire_dual_constant_contradiction_is_rejected_or_explicitly_covering() -> None:
    from pythonstan.analysis.domains.value_wire import restore_value

    document = Value.constant(1).export()
    document["tagged_constants"] = [{"type": "int", "value": 2}]
    try:
        restored = restore_value(document)
    except (TypeError, ValueError):
        return
    assert not restored.complete
    assert ValueOperations().leq(Value.constant(1), restored.value)
    assert ValueOperations().leq(Value.constant(2), restored.value)


def test_wire_missing_top_facet_is_not_claimed_complete() -> None:
    from pythonstan.analysis.domains.value_wire import restore_value

    document = Value.unknown().export()
    document.pop("top")
    try:
        restored = restore_value(document)
    except (TypeError, ValueError):
        return
    assert not restored.complete
    assert ValueOperations().leq(Value.unknown(), restored.value)


@pytest.mark.parametrize("container", ["items", "slots"])
def test_wire_future_major_version_is_rejected_inside_reference_shape(container: str) -> None:
    from pythonstan.analysis.contracts.value_wire import decode_value_wire

    future = Value.constant(3).export()
    future["value_schema_version"] = 2
    document = Value.ref(ObjectId("A")).export()
    document[container] = [future] if container == "items" else {"field": future}
    with pytest.raises(ValueError, match="version"):
        decode_value_wire(document)


def test_wire_decode_owns_foreign_mutable_input() -> None:
    from pythonstan.analysis.contracts.json_data import require_array
    from pythonstan.analysis.contracts.value_wire import decode_value_wire

    document = Value.constant(1).export()
    decoded = decode_value_wire(document)
    require_array(document["constants"]).append(2)
    assert decoded.document["constants"] == [1]


def test_wire_scalar_union_roundtrip_preserves_every_component() -> None:
    from pythonstan.analysis.contracts.identity import ProgramPointId
    from pythonstan.analysis.domains.ints import IntLattice, Parity
    from pythonstan.analysis.domains.relations import PartitionToken
    from pythonstan.analysis.domains.strings import StrLattice
    from pythonstan.analysis.domains.value_wire import restore_value

    original = Value(
        ints=IntLattice.from_interval(None, 7, Parity.ODD),
        strings=StrLattice(
            frozenset({"literal"}),
            frozenset({"pre"}),
            frozenset({"tail"}),
            False,
            frozenset({("x", "y")}),
        ),
        bools=frozenset({False}),
        floats=frozenset({3.5, float("inf")}),
        none=True,
        refs=frozenset({ObjectId("ref")}),
        unbound=True,
        relations=frozenset(
            {
                PartitionToken(
                    ProgramPointId("call"), "str", "read", ObjectId("handler-id"), True, "display"
                )
            }
        ),
    )
    restored = restore_value(original.export())
    assert restored.complete
    assert ValueOperations().leq(original, restored.value)
    assert restored.value.relations == original.relations
    assert restored.reference_info["freshness"] == "not_exported"


@pytest.mark.parametrize("invalid", [-1, True, 0.5])
def test_invalid_ledger_charges_and_reservations_have_no_side_effects(invalid: object) -> None:
    from pythonstan.analysis.contracts.resources import RunLedger

    ledger = RunLedger(10, 0, 0, memory_reader=lambda: 0)
    before = ledger.snapshot()
    with pytest.raises(ValueError):
        ledger.charge_work(invalid, "invalid")
    with pytest.raises(ValueError):
        ledger.reserve_work(invalid, category="invalid")
    after = ledger.snapshot()
    for key in ("work", "attempted_work", "reservation_attempted_work", "work_reservations"):
        assert after[key] == before[key]


def test_cumulative_child_usage_is_not_double_charged_and_unused_known_work_is_released() -> None:
    from pythonstan.analysis.contracts.resources import RunLedger

    ledger = RunLedger(10, 0, 0, memory_reader=lambda: 0)
    grant = ledger.reserve_work(8, category="worker")
    grant.observe(3)
    grant.observe(3)
    assert (ledger.work.used, ledger.work.reserved) == (3, 5)
    grant.settle(5)
    assert (ledger.work.used, ledger.work.reserved, ledger.remaining_work) == (5, 0, 5)
    assert ledger.work.by_kind["worker"] == 5


def test_unknown_child_work_cannot_be_refunded_by_late_observation_or_settlement() -> None:
    from pythonstan.analysis.contracts.resources import RunLedger

    ledger = RunLedger(10, 0, 0, memory_reader=lambda: 0)
    grant = ledger.reserve_work(8, category="worker")
    grant.observe(3)
    grant.unknown("timeout")
    for late in (lambda: grant.observe(4), lambda: grant.settle(3)):
        with pytest.raises(ValueError):
            late()
    assert (ledger.work.used, ledger.work.reserved, grant.held) == (3, 5, 5)
    assert grant.status == "unknown" and grant.reason == "timeout"


def test_refund_of_older_reservation_does_not_refresh_a_later_local_work_scope() -> None:
    from pythonstan.analysis.contracts.resources import ResourceStop, RunLedger

    ledger = RunLedger(100, 0, 0, memory_reader=lambda: 0)
    older = ledger.reserve_work(80, category="earlier")
    with ledger.work_scope(5, "later"):
        ledger.charge_work(5, "later")
        older.settle(0)
        with pytest.raises(ResourceStop, match="stage_max_work"):
            ledger.charge_work(1, "unfunded")
    assert ledger.work.used == 5 and ledger.work.reserved == 0
    assert ledger.stop_reason is None


def test_preallocated_grant_cannot_consume_new_work_in_an_unfunded_sibling_scope() -> None:
    from pythonstan.analysis.contracts.resources import ResourceStop, RunLedger

    ledger = RunLedger(20, 0, 0, memory_reader=lambda: 0)
    grant = ledger.reserve_work(10, category="cpu")
    with ledger.work_scope(0, "later_zero"), pytest.raises(ResourceStop, match="scope"):
        grant.consume(1, owner=ledger)
    assert ledger.work.used == 0 and grant.held == ledger.work.reserved == 10


def test_grant_registry_and_owner_are_checked_before_consumption() -> None:
    from pythonstan.analysis.contracts.resources import RunLedger, WorkReservation

    ledger = RunLedger(10, 0, 0, memory_reader=lambda: 0)
    other = RunLedger(10, 0, 0, memory_reader=lambda: 0)
    real = ledger.reserve_work(5, category="cpu")
    forged = WorkReservation(ledger, real.identity, real.amount, real.category)
    with pytest.raises(ValueError):
        forged.consume(1, owner=ledger)
    with pytest.raises(ValueError):
        real.consume(1, owner=other)
    assert ledger.work.used == other.work.used == 0
    assert ledger.work.reserved == 5 and other.work.reserved == 0


@pytest.mark.parametrize(
    "attribute", ["amount", "identity", "observed", "held", "status", "category"]
)
def test_public_grant_properties_cannot_forge_accounting_state(attribute: str) -> None:
    from pythonstan.analysis.contracts.resources import RunLedger

    ledger = RunLedger(10, 0, 0, memory_reader=lambda: 0)
    grant = ledger.reserve_work(5, category="cpu")
    with pytest.raises(AttributeError):
        setattr(grant, attribute, 0)
    assert grant.amount == grant.held == ledger.work.reserved == 5


def test_real_ledger_meter_has_no_unpaid_first_batch_or_cross_scope_credit() -> None:
    from pythonstan.analysis.contracts.resources import ResourceStop, RunLedger

    empty = RunLedger(0, 0, 0, memory_reader=lambda: 0)
    with pytest.raises(ResourceStop, match="max_work"):
        empty.structural_meter("walk").visit()
    ledger = RunLedger(10, 0, 0, memory_reader=lambda: 0)
    with ledger.structural_meter("walk") as meter:
        meter.visit()
        with (
            ledger.work_scope(0, "child"),
            pytest.raises((RuntimeError, ResourceStop), match="scope"),
        ):
            meter.visit()
    assert ledger.work.used == 1 and ledger.raw_structural["walk"] == 1


def test_real_ledger_meter_denies_ninth_raw_visit_without_debit_or_fake_visit() -> None:
    from pythonstan.analysis.contracts.resources import ResourceStop, RunLedger

    ledger = RunLedger(1, 0, 0, memory_reader=lambda: 0)
    with ledger.structural_meter("walk") as meter:
        meter.visit(8)
        with pytest.raises(ResourceStop, match="max_work"):
            meter.visit()
    assert ledger.work.used == 1 and ledger.raw_structural["walk"] == 8


def test_wide_json_keys_and_surrogates_receive_declared_text_work() -> None:
    from pythonstan.analysis.contracts.resources import RunLedger, json_work

    small = RunLedger(100, 0, 0, memory_reader=lambda: 0)
    wide = RunLedger(100, 0, 0, memory_reader=lambda: 0)
    small.charge_json({"a": "x"}, "json")
    payload = {"中" * 4000: "\ud800"}
    wide.charge_json(payload, "json")
    assert wide.work.used > small.work.used
    assert wide.raw_structural["json"] == json_work(payload)
    assert wide.work.used == (json_work(payload) + 7) // 8


def test_exclusive_phase_time_and_foreign_snapshot_have_an_honest_as_of_boundary() -> None:
    from threading import Thread

    from pythonstan.analysis.contracts.json_data import require_number, require_object
    from pythonstan.analysis.contracts.resources import RunLedger

    clock = [0.0]
    ledger = RunLedger(100, 0, 0, clock=lambda: clock[0], memory_reader=lambda: 0)
    clock[0] = 1.0
    with ledger.phase("setup"):
        clock[0] = 3.0
        with ledger.phase("nested"):
            clock[0] = 4.0
        clock[0] = 6.0
    clock[0] = 7.0
    rows: list[JsonObject] = []

    def inspect() -> None:
        rows.append(ledger.snapshot())

    worker = Thread(target=inspect)
    worker.start()
    worker.join(timeout=2)
    assert not worker.is_alive() and len(rows) == 1
    snapshot = rows[0]
    elapsed = require_number(snapshot["elapsed_seconds"])
    exclusive = require_object(snapshot["exclusive_phase_seconds"])
    assert sum(require_number(value) for value in exclusive.values()) == elapsed
    assert require_number(snapshot["wall_observed_seconds"]) >= elapsed
    assert snapshot["timing_scope"] == (
        "Exclusive phases partition owner-timing-as-of elapsed_seconds; inclusive phases overlap."
    )


def test_disabled_memory_limit_is_distinct_from_observing_rss() -> None:
    from pythonstan.analysis.contracts.resources import ResourceStop, RunLedger

    observed = RunLedger(10, 0, 0, memory_reader=lambda: 2 * 1024 * 1024)
    observed.checkpoint(force=True)
    assert observed.memory_samples >= 1 and observed.memory_checks == 0
    enforced = RunLedger(10, 0, 1, memory_reader=lambda: 2 * 1024 * 1024)
    with pytest.raises(ResourceStop, match="max_memory_mb"):
        enforced.checkpoint(force=True)
    assert enforced.memory_samples >= 1 and enforced.memory_checks >= 1
