"""Synthetic fee authority is request-bound and cannot be renewed by failure."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pytest

from pythonstan.analysis.contracts.resource_api import ApiReservation
from pythonstan.analysis.contracts.resources import RateCard, ResourceStop, RunLedger


def ledger() -> RunLedger:
    return RunLedger(
        1000,
        0,
        0,
        memory_reader=lambda: 0,
        max_api_calls=4,
        max_api_tokens=20,
        max_api_microusd=20,
        rate_card=RateCard(
            "synthetic", "frozen-fixture-rate", "synthetic:test-only", 1000000, 500000, 1000000
        ),
    )


def reserve(run: RunLedger, digest: str = "body-a") -> ApiReservation:
    return run.reserve_api(
        model="synthetic", request_digest=digest, input_tokens=3, output_tokens=7
    )


def test_fee_and_token_settlement_are_disjoint_and_release_only_unused_upper_bound() -> None:
    run = ledger()
    first, second = reserve(run), reserve(run, "body-b")
    assert (run.api_reserved_tokens, run.api_reserved_money) == (20, 20)
    first.dispatch()
    first.settle(input_tokens=3, output_tokens=2, cached_tokens=1, reasoning_tokens=1)
    assert (run.api_consumed_tokens, run.api_consumed_money) == (5, 5)
    assert (run.api_reserved_tokens, run.api_reserved_money) == (10, 10)
    second.abort("not_dispatched")
    assert (run.api_reserved_tokens, run.api_reserved_money) == (0, 0)
    assert run.api_calls == 2


def test_unknown_usage_retains_full_reservation_and_stops_previously_reserved_sibling() -> None:
    run = ledger()
    first, sibling = reserve(run), reserve(run, "body-b")
    first.dispatch()
    first.unknown("timeout")
    with pytest.raises(ValueError, match="no longer dispatchable"):
        sibling.dispatch()
    with pytest.raises(ValueError, match="outstanding dispatched"):
        first.settle(input_tokens=1, output_tokens=1)
    assert run.api_consumed_tokens == 0 and run.api_reserved_tokens == 20
    assert run.api_reserved_money == 20 and first.status == "unknown"


def test_grant_registry_owner_body_model_and_pricing_identity_cannot_be_substituted() -> None:
    run = ledger()
    grant = reserve(run)
    with pytest.raises(ValueError, match="exact request"):
        grant.validate(model="synthetic", request_digest="changed-body")
    with pytest.raises(ValueError, match="exact request"):
        grant.validate(model="other", request_digest="body-a")
    with pytest.raises(ValueError, match="another request"):
        grant.validate(model="synthetic", request_digest="body-a", owner=ledger())
    forged = ApiReservation(
        run,
        grant.identity,
        model="synthetic",
        request_digest="body-a",
        input_tokens=3,
        output_tokens=7,
        money=10,
        cache_reads_allowed=True,
        cache_writes_allowed=False,
    )
    with pytest.raises(ValueError, match="Unregistered"):
        forged.dispatch()
    run.rate_card = RateCard("synthetic", "new-rate", "synthetic:changed", 1, 1, 1)
    with pytest.raises(ValueError, match="pricing identity"):
        grant.dispatch()
    assert run.api_consumed_money == 0 and run.api_reserved_money == 10


def test_api_scope_change_does_not_spend_an_older_admission() -> None:
    run = ledger()
    grant = reserve(run)
    with run.work_scope(0, "new_stage"), pytest.raises(ValueError, match="quota scope"):
        grant.dispatch()
    assert grant.status == "reserved"
    grant.dispatch()
    with pytest.raises(ValueError, match="no longer dispatchable"):
        grant.dispatch()


@pytest.mark.parametrize("amount", [-1, True])
def test_invalid_reservation_does_not_mutate_attempts_or_financial_capacity(amount: int) -> None:
    run = ledger()
    before = run.snapshot()["api"]
    with pytest.raises(ValueError):
        run.reserve_api(
            model="synthetic", request_digest="body", input_tokens=amount, output_tokens=2
        )
    assert run.snapshot()["api"] == before


def test_simultaneous_reservations_cannot_oversubscribe_tokens_or_money() -> None:
    run = ledger()

    def attempt(index: int) -> str:
        try:
            reserve(run, f"body-{index}")
            return "reserved"
        except ResourceStop:
            return "denied"

    with ThreadPoolExecutor(max_workers=4) as pool:
        statuses = list(pool.map(attempt, range(4)))
    assert statuses.count("reserved") == 2 and statuses.count("denied") == 2
    assert run.api_reserved_tokens == run.api_reserved_money == 20
    assert run.api_calls == 2 and run.api_reservation_attempts == 4


def test_invalid_subsets_and_excess_usage_are_not_silently_billed_as_zero() -> None:
    run = ledger()
    grant = reserve(run)
    grant.dispatch()
    with pytest.raises(ValueError, match="subsets"):
        grant.settle(input_tokens=2, output_tokens=1, cached_tokens=2, cache_write_tokens=1)
    assert grant.status == "unknown" and run.api_reserved_money == 10
    other = ledger()
    excess = reserve(other)
    excess.dispatch()
    with pytest.raises(ValueError, match="exceeds"):
        excess.settle(input_tokens=4, output_tokens=7)
    assert other.api_observed_tokens == 11 and excess.status == "unknown"
    assert other.api_reserved_tokens == 10


def test_undispatched_abort_cannot_erase_a_dispatched_or_known_charge() -> None:
    run = ledger()
    grant = reserve(run)
    grant.dispatch()
    with pytest.raises(ValueError, match="undispatched"):
        grant.abort("not_free")
    grant.settle(input_tokens=3, output_tokens=2)
    with pytest.raises(ValueError, match="finalized"):
        grant.unknown("not_unknown")
    assert run.api_consumed_money == 5 and run.api_reserved_money == 0


def test_missing_model_prices_block_before_any_financial_debit() -> None:
    run = RunLedger(100, 0, 0, max_api_calls=1, max_api_tokens=10, max_api_microusd=10)
    with pytest.raises(ResourceStop, match="rate_card_unavailable"):
        reserve(run)
    assert run.api_calls == run.api_reserved_money == run.api_reserved_tokens == 0


def test_money_reservation_uses_the_worst_verified_cached_rate() -> None:
    run = RunLedger(
        100,
        0,
        0,
        max_api_calls=1,
        max_api_tokens=10,
        max_api_microusd=100,
        rate_card=RateCard(
            "synthetic", "high-cached", "synthetic:test-only", 1000000, 2000000, 1000000
        ),
    )
    grant = reserve(run)
    assert grant.amount_money == 13
    grant.dispatch()
    grant.settle(input_tokens=3, output_tokens=7, cached_tokens=3)
    assert run.api_consumed_money == 13 and run.api_reserved_money == 0


def test_missing_reasoning_breakdown_keeps_known_whole_output_billing() -> None:
    run = ledger()
    grant = reserve(run)
    grant.dispatch()
    grant.settle(input_tokens=3, output_tokens=2, cached_tokens=1, reasoning_tokens=None)
    assert grant.status == "settled" and grant.cost_known
    assert run.api_consumed_tokens == 5 and run.api_consumed_money == 5
    assert grant.to_dict()["usage"]["reasoning_tokens"] is None


def test_missing_price_dependent_cache_details_keeps_whole_usage_and_held_cost() -> None:
    run = ledger()
    grant = reserve(run)
    grant.dispatch()
    with pytest.raises(ValueError, match="cannot be priced"):
        grant.settle(input_tokens=3, output_tokens=2, cached_tokens=None, reasoning_tokens=None)
    assert run.api_observed_tokens == 5 and run.api_consumed_tokens == 0
    assert grant.status == "unknown" and not grant.cost_known
    assert run.api_reserved_money == 10 and run.api_consumed_money == 0
    assert run.snapshot()["api"]["observed_cost_at_frozen_card_microusd"] is None


def test_exact_no_cache_request_can_price_whole_usage_without_optional_details() -> None:
    run = ledger()
    grant = run.reserve_api(
        model="synthetic",
        request_digest="cache-explicit-without-breakpoints",
        input_tokens=3,
        output_tokens=7,
        cache_reads_allowed=False,
        cache_writes_allowed=False,
    )
    grant.dispatch()
    grant.settle(
        input_tokens=3,
        output_tokens=2,
        cached_tokens=None,
        cache_write_tokens=None,
        reasoning_tokens=None,
    )
    assert grant.status == "settled" and grant.cost_known
    assert run.api_consumed_money == run.api_consumed_tokens == 5
