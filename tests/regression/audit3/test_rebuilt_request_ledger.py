"""Fresh request-ledger witnesses rebuilt after the execution snapshot loss."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pytest

from pythonstan.analysis.contracts.json_data import require_number, require_object
from pythonstan.analysis.contracts.resources import (
    RateCard,
    ResourceStop,
    RunLedger,
    WorkReservation,
    json_work,
)


def ledger(limit: int = 100) -> RunLedger:
    return RunLedger(limit, 0, 0, memory_reader=lambda: 0)


def test_reserved_and_observed_work_have_a_single_disjoint_total() -> None:
    run = ledger(100)
    run.charge_work(7, "setup")
    grant = run.reserve_work(80, category="child")
    assert (run.work.used, run.work.reserved, run.remaining_work) == (7, 80, 13)
    grant.observe(12)
    grant.observe(12)
    assert (run.work.used, run.work.reserved, run.remaining_work) == (19, 68, 13)
    grant.settle(12)
    grant.settle(12)
    assert (run.work.used, run.work.reserved, run.remaining_work) == (19, 0, 81)
    assert run.work.by_kind == {"setup": 7, "child": 12}


def test_direct_shared_native_consumer_cannot_bypass_request_scopes() -> None:
    run = ledger(10)
    with run.work_scope(1, "native"):
        assert run.work.consume(kind="native_selected")
        assert not run.work.consume(kind="native_selected")
        assert run.remaining_work == 0
    assert run.stop_reason is None
    run.charge_work(1, "next_stage")
    assert run.work.used == 2


def test_refund_of_an_older_reservation_cannot_renew_a_later_scope() -> None:
    run = ledger(100)
    old = run.reserve_work(80, category="earlier")
    with run.work_scope(5, "current"):
        run.charge_work(5, "current")
        old.settle(0)
        assert run.remaining_work == 0
        with pytest.raises(ResourceStop, match="stage_max_work"):
            run.charge_work(1, "not_free")
    assert run.work.used == 5 and run.work.reserved == 0


def test_ordinary_grant_consumption_is_pinned_to_its_live_origin_scope() -> None:
    run = ledger()
    grant = run.reserve_work(10, category="parser")
    with (
        run.work_scope(0, "new_scope"),
        pytest.raises(ResourceStop, match="reservation_scope_mismatch"),
    ):
        grant.consume(1, owner=run)
    assert run.work.used == 0 and grant.held == 10
    grant.consume(1, owner=run)
    grant.settle(1)
    assert run.work.used == 1 and run.remaining_work == 99


def test_unknown_grants_cannot_refund_a_late_completion() -> None:
    run = ledger()
    grant = run.reserve_work(30, category="worker")
    grant.observe(7)
    grant.unknown("timeout")
    for settle in (grant.observe, grant.settle):
        with pytest.raises(ValueError, match="Unknown"):
            settle(8)
    assert (run.work.used, run.work.reserved, grant.held) == (7, 23, 23)


def test_foreign_and_forged_grants_do_not_change_a_ledger() -> None:
    run = ledger()
    other = ledger()
    grant = run.reserve_work(10, category="parser")
    with pytest.raises(ValueError, match="another request"):
        grant.consume(1, owner=other)
    forged = WorkReservation(run, grant.identity, grant.amount, grant.category)
    with pytest.raises(ValueError, match="Unregistered"):
        forged.observe(1)
    assert run.work.used == other.work.used == 0


def test_terminal_cleanup_is_bounded_inside_the_original_cap() -> None:
    run = ledger(64)
    grant = run.ensure_terminal_permit()
    assert grant.amount == grant.held == 64 and run.remaining_work == 0
    run.stop("max_seconds")
    run.stop("max_memory_mb")
    grant.consume_terminal(63)
    with pytest.raises(ResourceStop, match="terminal_capacity"):
        grant.consume_terminal(2)
    grant.consume_terminal(1)
    assert run.stop_reason == "max_seconds"
    assert run.work.used + run.work.reserved == 64


def test_successful_publication_releases_only_known_unused_cleanup_capacity() -> None:
    run = ledger(200)
    grant = run.ensure_terminal_permit()
    run.charge_work(10, "normal_publication")
    grant.settle(grant.observed)
    assert run.work.used == 10 and run.remaining_work == 190


def test_local_deadline_does_not_refresh_the_absolute_request_deadline() -> None:
    now = [0.0]
    run = RunLedger(100, 10, 0, clock=lambda: now[0], memory_reader=lambda: 0)
    with run.work_scope(100, "first", max_seconds=3):
        now[0] = 4
        with pytest.raises(ResourceStop, match="stage_max_seconds"):
            run.charge_work(1, "first")
    now[0] = 9
    with run.work_scope(100, "second", max_seconds=100):
        now[0] = 10
        with pytest.raises(ResourceStop, match="max_seconds"):
            run.charge_work(1, "second")
    assert run.stop_reason == "max_seconds" and run.work.used == 0


def test_structural_fuel_precharges_batches_and_never_transfers_credit() -> None:
    run = ledger(20)
    with run.work_scope(1, "first"):
        meter = run.structural_meter("walk")
        for _ in range(8):
            meter.visit()
        with pytest.raises(ResourceStop, match="stage_max_work"):
            meter.visit()
    with run.work_scope(0, "sibling"), pytest.raises(RuntimeError, match="another scope"):
        meter.visit()
    assert run.work.used == 1 and run.raw_structural["walk"] == 8


@pytest.mark.parametrize("payload", [{"a" * 2049: 0}, {"中" * 500: "\ud800"}, ["😀" * 512]])
def test_json_pricing_includes_wide_keys_values_and_escaped_surrogates(payload: object) -> None:
    from pythonstan.analysis.contracts.json_data import validate_json

    value = validate_json(payload)
    run = ledger(100)
    run.charge_json(value, "json")
    assert run.raw_structural["json"] == json_work(value)
    assert run.work.used == (json_work(value) + 7) // 8


def test_json_zero_fuel_is_denied_before_first_admitted_primitive() -> None:
    run = ledger(0)
    with pytest.raises(ResourceStop, match="max_work"):
        run.charge_json({"big": "x" * 100000}, "json")
    assert run.work.used == 0 and run.raw_structural["json"] == 0


def test_exclusive_timing_partitions_its_explicit_asof_boundary_across_threads() -> None:
    now = [0.0]
    run = RunLedger(100, 0, 0, clock=lambda: now[0], memory_reader=lambda: 0)
    now[0] = 2
    with run.phase("outer"):
        now[0] = 3
        with run.phase("inner"):
            now[0] = 5
        now[0] = 7
    now[0] = 9
    with ThreadPoolExecutor(max_workers=1) as pool:
        foreign = pool.submit(run.snapshot).result()
    assert foreign["elapsed_seconds"] == 7
    assert foreign["wall_observed_seconds"] == 9
    assert (
        sum(
            require_number(value)
            for value in require_object(foreign["exclusive_phase_seconds"]).values()
        )
        == 7
    )
    owned = run.snapshot()
    assert (
        sum(
            require_number(value)
            for value in require_object(owned["exclusive_phase_seconds"]).values()
        )
        == 9
    )


def test_sampled_memory_and_disabled_enforcement_are_distinct_observations() -> None:
    run = RunLedger(100, 0, 0, memory_reader=lambda: 20)
    run.checkpoint(force=True)
    assert run.memory_checks == 0 and run.memory_samples == 1
    assert run.remaining_memory_bytes is None
    assert run.memory_samples == 2
    bounded = RunLedger(100, 0, 1, memory_reader=lambda: 2 * 1024 * 1024)
    with pytest.raises(ResourceStop, match="max_memory_mb"):
        bounded.checkpoint(force=True)
    assert bounded.memory_checks == 1


def test_rate_card_uses_cached_and_reasoning_token_subsets_without_double_counting() -> None:
    card = RateCard("synthetic", "fixture-digest", "synthetic:test-only", 1000000, 500000, 2000000)
    assert card.cost(10, 4, 6) == 15
