"""Registered one-shot API grants with disjoint token and money settlement."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from .json_data import JsonObject

if TYPE_CHECKING:
    from .resources import RateCard, RunLedger, _WorkScope

type ApiStatus = Literal["reserved", "dispatched", "settled", "aborted", "unknown"]


class ApiReservation:
    """A grant is authority only while registered in its original request."""

    def __init__(
        self,
        owner: RunLedger,
        identity: int,
        *,
        model: str,
        request_digest: str,
        input_tokens: int,
        output_tokens: int,
        money: int,
        cache_reads_allowed: bool,
        cache_writes_allowed: bool,
    ) -> None:
        card = owner.rate_card
        if card is None:
            raise ValueError("An API grant needs a frozen rate card")
        self._owner: RunLedger = owner
        self._identity: int = identity
        self._model: str = model
        self._request_digest: str = request_digest
        self._input_tokens: int = input_tokens
        self._output_tokens: int = output_tokens
        self._money: int = money
        self._cache_reads_allowed: bool = cache_reads_allowed
        self._cache_writes_allowed: bool = cache_writes_allowed
        self._card: RateCard = card
        self._scopes: tuple[_WorkScope, ...] = tuple(owner._work_scopes)
        self._status: ApiStatus = "reserved"
        self._reason: str | None = None
        self._usage: JsonObject | None = None
        self._cost_known: bool = False

    @property
    def identity(self) -> int:
        return self._identity

    @property
    def model(self) -> str:
        return self._model

    @property
    def request_digest(self) -> str:
        return self._request_digest

    @property
    def input_tokens(self) -> int:
        return self._input_tokens

    @property
    def output_tokens(self) -> int:
        return self._output_tokens

    @property
    def amount_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    @property
    def amount_money(self) -> int:
        return self._money

    @property
    def status(self) -> ApiStatus:
        return self._status

    @property
    def reason(self) -> str | None:
        return self._reason

    @property
    def cache_reads_allowed(self) -> bool:
        return self._cache_reads_allowed

    @property
    def cache_writes_allowed(self) -> bool:
        return self._cache_writes_allowed

    @property
    def cost_known(self) -> bool:
        return self._cost_known

    def _registered(self, owner: RunLedger | None = None) -> None:
        if owner is not None and owner is not self._owner:
            raise ValueError("API grant belongs to another request")
        if (
            not 0 <= self.identity < len(self._owner._api_reservations)
            or self._owner._api_reservations[self.identity] is not self
        ):
            raise ValueError("Unregistered API grant")

    def validate(
        self,
        *,
        model: str,
        request_digest: str,
        owner: RunLedger | None = None,
        cache_reads_allowed: bool | None = None,
        cache_writes_allowed: bool | None = None,
    ) -> None:
        with self._owner._lock:
            self._registered(owner)
            if self.status != "reserved" or self._owner.api_stopped:
                raise ValueError("API grant is no longer dispatchable")
            if model != self.model or request_digest != self.request_digest:
                raise ValueError("API grant does not match the exact request")
            if (
                cache_reads_allowed is not None and cache_reads_allowed != self.cache_reads_allowed
            ) or (
                cache_writes_allowed is not None
                and cache_writes_allowed != self.cache_writes_allowed
            ):
                raise ValueError("API grant does not match the exact request cache policy")
            if self._owner.rate_card is not self._card:
                raise ValueError("Frozen API pricing identity changed")
            if tuple(self._owner._work_scopes) != self._scopes or any(
                not scope.active for scope in self._scopes
            ):
                raise ValueError("API grant belongs to another quota scope")
            self._owner.checkpoint()

    def dispatch(self) -> None:
        with self._owner._lock:
            self.validate(model=self.model, request_digest=self.request_digest)
            self._status = "dispatched"

    def abort(self, reason: str) -> None:
        with self._owner._lock:
            self._registered()
            if self.status != "reserved":
                raise ValueError("Only an undispatched API grant can be cancelled without cost")
            self._owner.api_reserved_tokens -= self.amount_tokens
            self._owner.api_reserved_money -= self.amount_money
            self._status = "aborted"
            self._reason = reason

    def unknown(self, reason: str) -> None:
        with self._owner._lock:
            self._registered()
            if self.status in {"settled", "aborted"}:
                raise ValueError("A finalized API grant cannot become unknown")
            self._status = "unknown"
            self._reason = self.reason or reason
            self._owner.api_stopped = True

    def settle(
        self,
        *,
        input_tokens: int,
        output_tokens: int,
        cached_tokens: int | None = 0,
        reasoning_tokens: int | None = 0,
        cache_write_tokens: int | None = 0,
    ) -> None:
        """Usage details are subsets; neither cached nor reasoning adds tokens."""
        totals = (input_tokens, output_tokens)
        details = (cached_tokens, reasoning_tokens, cache_write_tokens)
        with self._owner._lock:
            self._registered()
            if self.status != "dispatched":
                raise ValueError("Only an outstanding dispatched API grant can settle")
            if (
                any(type(count) is not int or count < 0 for count in totals)
                or any(
                    count is not None and (type(count) is not int or count < 0) for count in details
                )
                or (cached_tokens or 0) + (cache_write_tokens or 0) > input_tokens
                or (reasoning_tokens or 0) > output_tokens
            ):
                self.unknown("invalid_usage")
                raise ValueError("Invalid API usage subsets")
            self._usage = {
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "cached_tokens": cached_tokens,
                "cache_write_tokens": cache_write_tokens,
                "reasoning_tokens": reasoning_tokens,
            }
            self._owner.api_observed_tokens += input_tokens + output_tokens
            money = self._reported_cost(
                input_tokens, output_tokens, cached_tokens, cache_write_tokens
            )
            if money is not None:
                self._cost_known = True
                self._owner.api_observed_money += money
            if (
                input_tokens > self.input_tokens
                or output_tokens > self.output_tokens
                or (money is not None and money > self.amount_money)
            ):
                self.unknown("usage_exceeds_reserved_capacity")
                raise ValueError("Reported API usage exceeds its reserved exact-request bound")
            if ((cached_tokens or 0) > 0 and not self.cache_reads_allowed) or (
                (cache_write_tokens or 0) > 0 and not self.cache_writes_allowed
            ):
                self.unknown("usage_violates_request_cache_policy")
                raise ValueError("Reported cache usage violates the exact request policy")
            if money is None:
                self.unknown("cache_cost_breakdown_unavailable")
                raise ValueError("Reported cache usage cannot be priced at the frozen rate card")
            self._owner.api_reserved_tokens -= self.amount_tokens
            self._owner.api_reserved_money -= self.amount_money
            self._owner.api_consumed_tokens += input_tokens + output_tokens
            self._owner.api_consumed_money += money
            self._status = "settled"

    def _reported_cost(
        self,
        input_tokens: int,
        output_tokens: int,
        cached_tokens: int | None,
        cache_write_tokens: int | None,
    ) -> int | None:
        if cache_write_tokens and self._card.cache_write_rate is None:
            return None
        if (
            cached_tokens is None
            and self.cache_reads_allowed
            and self._card.cached_input_rate != self._card.input_rate
        ) or (
            cache_write_tokens is None
            and self.cache_writes_allowed
            and self._card.cache_write_rate != self._card.input_rate
        ):
            return None
        return self._card.cost(
            input_tokens, output_tokens, cached_tokens or 0, cache_write_tokens or 0
        )

    def to_dict(self) -> JsonObject:
        return {
            "id": self.identity,
            "model": self.model,
            "request_digest": self.request_digest,
            "input_tokens_bound": self.input_tokens,
            "output_tokens_bound": self.output_tokens,
            "tokens_bound": self.amount_tokens,
            "money_bound_microusd": self.amount_money,
            "rate_card_digest": self._card.digest,
            "cache_reads_allowed": self.cache_reads_allowed,
            "cache_writes_allowed": self.cache_writes_allowed,
            "cost_known_at_frozen_card": self.cost_known,
            "status": self.status,
            "reason": self.reason,
            "usage": None if self._usage is None else dict(self._usage),
        }


def reserve_api(
    owner: RunLedger,
    *,
    model: str,
    request_digest: str,
    input_tokens: int,
    output_tokens: int,
    cache_reads_allowed: bool = True,
    cache_writes_allowed: bool = False,
) -> ApiReservation:
    """Atomically reserve a complete request before any SDK dispatch."""
    from .resources import ResourceStop

    if (
        not isinstance(model, str)
        or not model
        or not isinstance(request_digest, str)
        or not request_digest
        or any(type(count) is not int or count < 0 for count in (input_tokens, output_tokens))
        or type(cache_reads_allowed) is not bool
        or type(cache_writes_allowed) is not bool
    ):
        raise ValueError("Invalid API reservation request")
    with owner._lock:
        owner.api_reservation_attempts += 1
        owner.checkpoint()
        card = owner.rate_card
        if card is None or card.model != model:
            owner.api_denials["rate_card_unavailable"] += 1
            raise ResourceStop("rate_card_unavailable")
        if cache_writes_allowed and card.cache_write_rate is None:
            owner.api_denials["cache_write_price_unavailable"] += 1
            raise ResourceStop("cache_write_price_unavailable")
        input_rates = [card.input_rate]
        if cache_reads_allowed:
            input_rates.append(card.cached_input_rate)
        if cache_writes_allowed and card.cache_write_rate is not None:
            input_rates.append(card.cache_write_rate)
        money = (
            input_tokens * max(input_rates) + output_tokens * card.output_rate + 999999
        ) // 1000000
        reason: str | None = None
        if owner.api_stopped:
            reason = "api_stopped_after_unknown_charge"
        elif owner.api_calls >= owner.max_api_calls:
            reason = "api_call_budget"
        elif (
            owner.api_consumed_tokens + owner.api_reserved_tokens + input_tokens + output_tokens
            > owner.max_api_tokens
        ):
            reason = "api_token_budget"
        elif owner.api_consumed_money + owner.api_reserved_money + money > owner.max_api_microusd:
            reason = "api_money_budget"
        if reason is not None:
            owner.api_denials[reason] += 1
            raise ResourceStop(reason)
        grant = ApiReservation(
            owner,
            len(owner._api_reservations),
            model=model,
            request_digest=request_digest,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            money=money,
            cache_reads_allowed=cache_reads_allowed,
            cache_writes_allowed=cache_writes_allowed,
        )
        owner._api_reservations.append(grant)
        owner.api_calls += 1
        owner.api_reserved_tokens += grant.amount_tokens
        owner.api_reserved_money += grant.amount_money
        return grant
