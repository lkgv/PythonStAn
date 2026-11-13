"""New synthetic-only API accounting probes; no SDK, network, or credentials."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pytest

from pythonstan.analysis.contracts.json_data import JsonObject, require_object
from pythonstan.analysis.contracts.resource_api import ApiReservation
from pythonstan.analysis.contracts.resources import RateCard, ResourceStop, RunLedger


def _ledger(
    *, calls: int = 4, tokens: int = 100, money: int = 1000, card: RateCard | None = None
) -> RunLedger:
    return RunLedger(
        1000,
        0,
        0,
        memory_reader=lambda: 0,
        max_api_calls=calls,
        max_api_tokens=tokens,
        max_api_microusd=money,
        rate_card=card
        or RateCard(
            "fixture-model", "synthetic-fixed-card", "synthetic:test-only", 1000000, 500000, 2000000
        ),
    )


def _reserve(
    ledger: RunLedger, digest: str = "body-a", *, cache_writes_allowed: bool = False
) -> ApiReservation:
    return ledger.reserve_api(
        model="fixture-model",
        request_digest=digest,
        input_tokens=10,
        output_tokens=4,
        cache_writes_allowed=cache_writes_allowed,
    )


def _api(ledger: RunLedger) -> JsonObject:
    return require_object(ledger.snapshot()["api"])


def test_cached_reasoning_and_cache_write_are_subsets_not_extra_tokens_or_fees() -> None:
    ledger = _ledger(
        card=RateCard(
            "fixture-model",
            "synthetic-explicit-write-price",
            "synthetic:test-only",
            1000000,
            500000,
            2000000,
            cache_write_rate=1000000,
        )
    )
    grant = _reserve(ledger, cache_writes_allowed=True)
    assert grant.amount_tokens == 14 and grant.amount_money == 18
    grant.dispatch()
    grant.settle(
        input_tokens=10, output_tokens=4, cached_tokens=6, reasoning_tokens=2, cache_write_tokens=2
    )
    state = _api(ledger)
    assert state["consumed_tokens"] == state["observed_tokens"] == 14
    assert state["consumed_microusd"] == 15
    assert state["reserved_tokens"] == state["reserved_microusd"] == 0
    with pytest.raises(ValueError):
        grant.settle(input_tokens=10, output_tokens=4)
    assert _api(ledger)["consumed_microusd"] == 15


def test_cache_write_usage_without_a_verified_write_rate_cannot_settle_as_known_cost() -> None:
    ledger = _ledger()
    grant = _reserve(ledger)
    grant.dispatch()
    with pytest.raises(ValueError):
        grant.settle(input_tokens=10, output_tokens=4, cache_write_tokens=2)
    state = _api(ledger)
    assert grant.status == "unknown" and state["stopped_after_unknown_charge"] is True
    assert state["reserved_tokens"] == 14 and state["reserved_microusd"] == 18
    assert state["consumed_microusd"] == 0


def test_higher_explicit_write_price_is_reserved_and_settled_for_its_own_subset() -> None:
    ledger = _ledger(
        card=RateCard(
            "fixture-model",
            "synthetic-high-write-price",
            "synthetic:test-only",
            1000000,
            500000,
            2000000,
            cache_write_rate=3000000,
        )
    )
    grant = _reserve(ledger, cache_writes_allowed=True)
    assert grant.amount_tokens == 14 and grant.amount_money == 38
    grant.dispatch()
    grant.settle(
        input_tokens=10, output_tokens=4, cached_tokens=6, reasoning_tokens=2, cache_write_tokens=2
    )
    state = _api(ledger)
    # 2 uncached + 6 cached at 1/2 + 2 writes at 3 + 4 outputs at 2.
    assert (
        state["consumed_microusd"]
        == state["observed_cost_at_frozen_card_microusd"]
        == state["known_observed_cost_subtotal_microusd"]
        == 19
    )
    assert state["consumed_tokens"] == 14
    assert state["reserved_tokens"] == state["reserved_microusd"] == 0


def test_disallowed_cache_reads_cannot_be_priced_as_a_known_no_cache_request() -> None:
    card = RateCard(
        "fixture-model", "synthetic-high-cache", "synthetic:test-only", 1000000, 2000000, 2000000
    )
    ledger = _ledger(card=card)
    grant = ledger.reserve_api(
        model="fixture-model",
        request_digest="explicit-no-cache-body",
        input_tokens=10,
        output_tokens=4,
        cache_reads_allowed=False,
        cache_writes_allowed=False,
    )
    assert grant.amount_money == 18
    grant.dispatch()
    with pytest.raises(ValueError):
        grant.settle(input_tokens=10, output_tokens=4, cached_tokens=6)
    state = _api(ledger)
    assert grant.status == "unknown" and state["stopped_after_unknown_charge"] is True
    assert state["reserved_tokens"] == 14 and state["reserved_microusd"] == 18
    assert state["consumed_microusd"] == 0


@pytest.mark.parametrize("mutation", ["model", "digest", "owner", "registry", "rate_card"])
def test_dispatch_authority_is_bound_to_actual_request_issuer_and_pricing(mutation: str) -> None:
    ledger = _ledger()
    grant = _reserve(ledger)
    candidate = grant
    expected_model, expected_digest, owner = "fixture-model", "body-a", ledger
    if mutation == "model":
        expected_model = "other-model"
    elif mutation == "digest":
        expected_digest = "body-b"
    elif mutation == "owner":
        owner = _ledger()
    elif mutation == "registry":
        candidate = ApiReservation(
            ledger,
            grant.identity,
            model=grant.model,
            request_digest=grant.request_digest,
            input_tokens=grant.input_tokens,
            output_tokens=grant.output_tokens,
            money=grant.amount_money,
            cache_reads_allowed=grant.cache_reads_allowed,
            cache_writes_allowed=grant.cache_writes_allowed,
        )
    else:
        ledger.rate_card = RateCard(
            "fixture-model", "synthetic-fixed-card", "synthetic:test-only", 1000000, 500000, 2000000
        )
    with pytest.raises(ValueError):
        candidate.validate(model=expected_model, request_digest=expected_digest, owner=owner)
    assert grant.status == "reserved"
    assert _api(ledger)["consumed_tokens"] == 0
    assert _api(ledger)["reserved_tokens"] == 14


def test_unknown_dispatched_request_stops_existing_and_future_grants_without_late_refund() -> None:
    ledger = _ledger()
    first, second = _reserve(ledger, "a"), _reserve(ledger, "b")
    first.dispatch()
    first.unknown("timeout")
    with pytest.raises(ValueError):
        second.dispatch()
    with pytest.raises(ResourceStop, match="api_stopped_after_unknown_charge"):
        _reserve(ledger, "c")
    with pytest.raises(ValueError):
        first.settle(input_tokens=1, output_tokens=1)
    state = _api(ledger)
    assert state["stopped_after_unknown_charge"] is True
    assert state["reserved_tokens"] == 28 and state["reserved_microusd"] == 36
    assert state["consumed_tokens"] == state["consumed_microusd"] == 0
    assert first.reason == "timeout" and first.status == "unknown"


def test_only_known_undispatched_abort_releases_reserved_capacity() -> None:
    ledger = _ledger(calls=1)
    grant = _reserve(ledger)
    grant.abort("not_dispatched")
    state = _api(ledger)
    assert state["calls"] == 1 and state["reserved_tokens"] == state["reserved_microusd"] == 0
    with pytest.raises(ValueError):
        grant.dispatch()
    with pytest.raises(ResourceStop, match="api_call_budget"):
        _reserve(ledger, "retry")
    dispatched = _reserve(_ledger())
    dispatched.dispatch()
    with pytest.raises(ValueError):
        dispatched.abort("cannot_undo_network")


@pytest.mark.parametrize(
    "limit,reason",
    [("calls", "api_call_budget"), ("tokens", "api_token_budget"), ("money", "api_money_budget")],
)
def test_each_declared_api_cap_is_admitted_before_registering_a_request(
    limit: str, reason: str
) -> None:
    ledger = _ledger(
        calls=0 if limit == "calls" else 4,
        tokens=13 if limit == "tokens" else 100,
        money=17 if limit == "money" else 1000,
    )
    with pytest.raises(ResourceStop, match=reason):
        _reserve(ledger)
    state = _api(ledger)
    assert state["calls"] == 0 and state["reservation_attempts"] == 1
    assert state["reserved_tokens"] == state["reserved_microusd"] == 0
    assert state["consumed_tokens"] == state["consumed_microusd"] == 0
    assert state["reservations"] == []


@pytest.mark.parametrize("invalid", [-1, True, 0.5])
def test_invalid_token_bounds_do_not_rewrite_attempt_or_fee_counters(invalid: object) -> None:
    ledger = _ledger()
    before = _api(ledger)
    with pytest.raises(ValueError):
        ledger.reserve_api(
            model="fixture-model", request_digest="body", input_tokens=invalid, output_tokens=4
        )
    assert _api(ledger) == before


def test_observed_usage_over_bound_is_not_refunded_or_mislabeled_as_success() -> None:
    ledger = _ledger()
    grant = _reserve(ledger)
    grant.dispatch()
    with pytest.raises(ValueError):
        grant.settle(input_tokens=11, output_tokens=4)
    state = _api(ledger)
    assert grant.status == "unknown" and grant.reason == "usage_exceeds_reserved_capacity"
    assert state["observed_tokens"] == 15
    assert state["reserved_tokens"] == 14 and state["reserved_microusd"] == 18
    assert state["stopped_after_unknown_charge"] is True


@pytest.mark.parametrize(
    "details",
    [
        {"cached_tokens": 11},
        {"reasoning_tokens": 5},
        {"cached_tokens": 6, "cache_write_tokens": 5},
    ],
)
def test_impossible_usage_subset_does_not_release_unresolved_request(
    details: dict[str, int],
) -> None:
    ledger = _ledger()
    grant = _reserve(ledger)
    grant.dispatch()
    with pytest.raises(ValueError):
        grant.settle(input_tokens=10, output_tokens=4, **details)
    assert grant.status == "unknown"
    assert _api(ledger)["reserved_tokens"] == 14
    assert _api(ledger)["reserved_microusd"] == 18


def test_concurrent_reservations_cannot_overbook_shared_token_or_money_capacity() -> None:
    ledger = _ledger(calls=10, tokens=42, money=54)

    def attempt(index: int) -> bool:
        try:
            _reserve(ledger, "body-" + str(index))
            return True
        except ResourceStop:
            return False

    with ThreadPoolExecutor(max_workers=8) as workers:
        accepted = list(workers.map(attempt, range(10)))
    state = _api(ledger)
    assert sum(accepted) == 3
    assert state["calls"] == 3 and state["reservation_attempts"] == 10
    assert state["reserved_tokens"] == 42 and state["reserved_microusd"] == 54


def test_more_expensive_cached_rate_is_reserved_conservatively() -> None:
    card = RateCard(
        "fixture-model", "synthetic-high-cache", "synthetic:test-only", 1000000, 2000000, 2000000
    )
    ledger = _ledger(card=card)
    grant = _reserve(ledger)
    assert grant.amount_money == 28
    grant.dispatch()
    grant.settle(input_tokens=10, output_tokens=4, cached_tokens=6)
    assert _api(ledger)["consumed_microusd"] == 24
    assert _api(ledger)["reserved_microusd"] == 0


def test_preallocated_api_request_cannot_dispatch_in_a_different_quota_scope() -> None:
    ledger = _ledger()
    grant = _reserve(ledger)
    with ledger.work_scope(0, "different"), pytest.raises(ValueError, match="scope"):
        grant.dispatch()
    assert grant.status == "reserved" and _api(ledger)["consumed_tokens"] == 0


def test_expired_global_deadline_prevents_dispatch_of_an_existing_grant() -> None:
    clock = [0.0]
    ledger = RunLedger(
        1000,
        1,
        0,
        clock=lambda: clock[0],
        memory_reader=lambda: 0,
        max_api_calls=1,
        max_api_tokens=100,
        max_api_microusd=1000,
        rate_card=RateCard(
            "fixture-model", "synthetic", "synthetic:test-only", 1000000, 500000, 2000000
        ),
    )
    grant = _reserve(ledger)
    clock[0] = 2.0
    with pytest.raises(ResourceStop, match="max_seconds"):
        grant.dispatch()
    assert grant.status == "reserved" and _api(ledger)["consumed_tokens"] == 0
    grant.abort("never_dispatched")
    assert _api(ledger)["reserved_tokens"] == 0
