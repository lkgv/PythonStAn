"""Request-owned admission, held capacity and monotone resource observations.

Logical work is software fuel. Memory is sampled combined RSS, not an operating
system guarantee, and a stopped request never receives a fresh analysis budget.
"""

from __future__ import annotations

import math
import os
import time
from collections import Counter, defaultdict
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from threading import RLock, get_ident
from typing import Literal

from .cost_model import (
    COST_MODEL_DIGEST,
    COST_MODEL_VERSION,
    StructuralWorkMeter,
    visit_json_text,
)
from .json_data import JsonObject, JsonValue
from .resource_api import ApiReservation, reserve_api
from .work import SharedWorkLedger

type WorkStatus = Literal["reserved", "settled", "unknown"]


class ResourceStop(Exception):
    def __init__(self, reason: str) -> None:
        self.reason: str = reason
        super().__init__(reason)


def resident_bytes() -> int:
    with open("/proc/self/statm", encoding="ascii") as stream:
        return int(stream.read().split()[1]) * os.sysconf("SC_PAGE_SIZE")


def json_work(value: JsonValue) -> int:
    """Raw JSON visit count, rather than already rounded logical ticks."""
    if isinstance(value, str):
        total = 0

        def count(amount: int) -> None:
            nonlocal total
            total += amount

        visit_json_text(value, count)
        return total
    if isinstance(value, dict):
        return 1 + sum(json_work(key) + json_work(item) for key, item in value.items())
    if isinstance(value, list):
        return 1 + sum(json_work(item) for item in value)
    return 1


@dataclass(frozen=True)
class RateCard:
    """Caller-verified immutable rates in micro-USD per million tokens.

    This record does not verify a vendor or retrieve current prices. Live entry
    requires an independently verified card and exact-request token quotation.
    """

    model: str
    digest: str
    source_url: str
    input_rate: int
    cached_input_rate: int
    output_rate: int
    count_request_cost: int | None = None
    cache_write_rate: int | None = None

    def __post_init__(self) -> None:
        if not self.model or not self.digest or not self.source_url:
            raise ValueError("A rate card needs a model and frozen source identity")
        rates = (self.input_rate, self.cached_input_rate, self.output_rate)
        if any(type(rate) is not int or rate < 0 for rate in rates):
            raise ValueError("Invalid token rate")
        if self.count_request_cost is not None and (
            type(self.count_request_cost) is not int or self.count_request_cost < 0
        ):
            raise ValueError("Invalid token quotation cost")
        if self.cache_write_rate is not None and (
            type(self.cache_write_rate) is not int or self.cache_write_rate < 0
        ):
            raise ValueError("Invalid cache write rate")

    def cost(
        self,
        input_tokens: int,
        output_tokens: int,
        cached_tokens: int = 0,
        cache_write_tokens: int = 0,
    ) -> int:
        if (
            any(
                type(count) is not int or count < 0
                for count in (input_tokens, output_tokens, cached_tokens, cache_write_tokens)
            )
            or cached_tokens + cache_write_tokens > input_tokens
        ):
            raise ValueError("Invalid token quantities")
        if cache_write_tokens and self.cache_write_rate is None:
            raise ValueError("Cache write price is not verified")
        numerator = (
            (input_tokens - cached_tokens - cache_write_tokens) * self.input_rate
            + cached_tokens * self.cached_input_rate
            + cache_write_tokens * (self.cache_write_rate or 0)
            + output_tokens * self.output_rate
        )
        return (numerator + 999999) // 1000000


@dataclass
class _WorkScope:
    identity: int
    name: str
    limit: int
    deadline: float | None
    consumed: int = 0
    held: int = 0
    active: bool = True


class WorkReservation:
    """One registered grant; public fields cannot rewrite its accounting state."""

    def __init__(self, owner: RunLedger, identity: int, amount: int, category: str) -> None:
        self._owner: RunLedger = owner
        self._identity: int = identity
        self._amount: int = amount
        self._category: str = category
        self._observed: int = 0
        self._held: int = amount
        self._status: WorkStatus = "reserved"
        self._reason: str | None = None
        self._scopes: tuple[_WorkScope, ...] = tuple(owner._work_scopes)

    @property
    def identity(self) -> int:
        return self._identity

    @property
    def amount(self) -> int:
        return self._amount

    @property
    def category(self) -> str:
        return self._category

    @property
    def observed(self) -> int:
        return self._observed

    @property
    def held(self) -> int:
        return self._held

    @property
    def status(self) -> WorkStatus:
        return self._status

    @property
    def reason(self) -> str | None:
        return self._reason

    def _validate(self, owner: RunLedger | None = None) -> None:
        if owner is not None and owner is not self._owner:
            raise ValueError("Work reservation belongs to another request")
        if (
            not 0 <= self.identity < len(self._owner._work_reservations)
            or self._owner._work_reservations[self.identity] is not self
        ):
            raise ValueError("Unregistered work reservation")

    def observe(self, total: int) -> None:
        with self._owner._lock:
            self._validate()
            if type(total) is not int or not self.observed <= total <= self.amount:
                raise ValueError("Nonmonotone or excessive work observation")
            if self.status == "unknown":
                raise ValueError("Unknown work cannot be refunded by a late observation")
            if self.status == "settled":
                if total != self.observed:
                    raise ValueError("Work reservation already settled")
                return
            delta = total - self.observed
            self._owner.work.settle_reserved(delta, consumed=delta, kind=self.category)
            for scope in self._scopes:
                scope.held -= delta
                scope.consumed += delta
            self._observed = total
            self._held -= delta

    def consume(self, amount: int, *, owner: RunLedger) -> None:
        """Admit new work, unlike retrospective known-usage settlement."""
        with self._owner._lock:
            self._validate(owner)
            if tuple(self._owner._work_scopes) != self._scopes or any(
                not scope.active for scope in self._scopes
            ):
                raise ResourceStop("reservation_scope_mismatch")
            if self.status != "reserved":
                raise ValueError("Work grant is no longer active")
            if type(amount) is not int or amount < 0:
                raise ValueError("Invalid ordinary grant charge")
            self._owner.attempted[self.category] += amount
            self._owner.checkpoint()
            if amount > self.held:
                raise ResourceStop("stage_max_work")
            self.observe(self.observed + amount)

    def settle(self, total: int) -> None:
        with self._owner._lock:
            self.observe(total)
            if self.status == "settled":
                return
            self._owner.work.settle_reserved(self.held, consumed=0, kind=self.category)
            for scope in self._scopes:
                scope.held -= self.held
            self._held = 0
            self._status = "settled"

    def consume_terminal(self, amount: int) -> None:
        with self._owner._lock:
            self._validate()
            if self.category != "terminal_publication" or self.status != "reserved":
                raise ValueError("Only a reserved terminal grant permits cleanup")
            if type(amount) is not int or amount < 0:
                raise ValueError("Invalid terminal publication charge")
            if amount > self.held:
                raise ResourceStop("terminal_capacity")
            self.observe(self.observed + amount)

    def unknown(self, reason: str) -> None:
        with self._owner._lock:
            self._validate()
            if self.status == "settled":
                raise ValueError("Settled work cannot become unknown")
            self._status = "unknown"
            self._reason = self._reason or reason

    def to_dict(self) -> JsonObject:
        return {
            "id": self.identity,
            "category": self.category,
            "amount": self.amount,
            "observed": self.observed,
            "reserved": self.held,
            "status": self.status,
            "reason": self.reason,
            "scope_ids": [scope.identity for scope in self._scopes],
        }


class RunLedger:
    """Single request identity shared by preparation, engines and publication."""

    def __init__(
        self,
        max_work: int,
        max_seconds: float,
        max_memory_mb: float,
        *,
        started_at: float | None = None,
        clock: Callable[[], float] = time.monotonic,
        memory_reader: Callable[[], int] = resident_bytes,
        max_api_calls: int = 0,
        max_api_tokens: int = 0,
        max_api_microusd: int = 0,
        rate_card: RateCard | None = None,
    ) -> None:
        if type(max_work) is not int or max_work < 0:
            raise ValueError("Invalid request work cap")
        if any(not math.isfinite(value) or value < 0 for value in (max_seconds, max_memory_mb)):
            raise ValueError("Invalid request resource limit")
        if any(
            type(value) is not int or value < 0
            for value in (max_api_calls, max_api_tokens, max_api_microusd)
        ):
            raise ValueError("Invalid API resource limit")
        self._lock: RLock = RLock()
        self._owner_thread: int = get_ident()
        self.clock: Callable[[], float] = clock
        self.memory_reader: Callable[[], int] = memory_reader
        self.started_at: float = clock() if started_at is None else started_at
        if not math.isfinite(self.started_at):
            raise ValueError("Invalid request start time")
        self.deadline: float | None = self.started_at + max_seconds if max_seconds else None
        self.memory_cap: int | None = int(max_memory_mb * 1024 * 1024) if max_memory_mb else None
        self.memory_used: int = 0
        self.peak_memory: int = 0
        self.memory_checks: int = 0
        self.memory_samples: int = 0
        self.child_memory: int = 0
        self._last_memory_work: int = -64
        self.stop_reason: str | None = None
        self._last_work_denial: str = "max_work"
        self.attempted: Counter[str] = Counter()
        self.reserve_attempts: Counter[str] = Counter()
        self.scope_denials: Counter[str] = Counter()
        self.raw_structural: Counter[str] = Counter()
        self.raw_structural_by_phase: dict[str, Counter[str]] = {}
        self._work_reservations: list[WorkReservation] = []
        self._work_scopes: list[_WorkScope] = []
        self._scope_records: list[_WorkScope] = []
        self._stack: list[str] = ["request_prefix"]
        self._time_at: float = self.started_at
        self.phase_seconds: defaultdict[str, float] = defaultdict(float)
        self.inclusive_phase_seconds: defaultdict[str, float] = defaultdict(float)
        self.phase_attempts: Counter[str] = Counter()
        self.work: SharedWorkLedger = SharedWorkLedger(max_work, admission=self._admit_work)
        self.max_api_calls: int = max_api_calls
        self.max_api_tokens: int = max_api_tokens
        self.max_api_microusd: int = max_api_microusd
        self.rate_card: RateCard | None = rate_card
        self.api_calls: int = 0
        self.api_reservation_attempts: int = 0
        self.api_denials: Counter[str] = Counter()
        self.api_stopped: bool = False
        self.api_observed_tokens: int = 0
        self.api_observed_money: int = 0
        self.api_consumed_tokens: int = 0
        self.api_reserved_tokens: int = 0
        self.api_consumed_money: int = 0
        self.api_reserved_money: int = 0
        self._api_reservations: list[ApiReservation] = []

    def reserve_api(
        self,
        *,
        model: str,
        request_digest: str,
        input_tokens: int,
        output_tokens: int,
        cache_reads_allowed: bool = True,
        cache_writes_allowed: bool = False,
    ) -> ApiReservation:
        return reserve_api(
            self,
            model=model,
            request_digest=request_digest,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_reads_allowed=cache_reads_allowed,
            cache_writes_allowed=cache_writes_allowed,
        )

    @property
    def remaining_work(self) -> int:
        return min(
            [
                self.work.remaining,
                *(max(0, scope.limit - scope.consumed - scope.held) for scope in self._work_scopes),
            ]
        )

    @property
    def remaining_seconds(self) -> float | None:
        deadlines = ([self.deadline] if self.deadline is not None else []) + [
            scope.deadline for scope in self._work_scopes if scope.deadline is not None
        ]
        return None if not deadlines else max(0.0, min(deadlines) - self.clock())

    @property
    def remaining_memory_bytes(self) -> int | None:
        with self._lock:
            self._sample_memory()
            return None if self.memory_cap is None else max(0, self.memory_cap - self.memory_used)

    def stop(self, reason: str) -> None:
        with self._lock:
            self.stop_reason = self.stop_reason or reason

    def _sample_memory(self) -> None:
        self.memory_used = self.memory_reader() + self.child_memory
        self.peak_memory = max(self.peak_memory, self.memory_used)
        self.memory_samples += 1
        self.memory_checks += self.memory_cap is not None
        self._last_memory_work = self.work.used
        if self.memory_cap is not None and self.memory_used > self.memory_cap:
            self.stop("max_memory_mb")
            raise ResourceStop(self.stop_reason or "max_memory_mb")

    def checkpoint(self, force: bool = False) -> None:
        with self._lock:
            if self.stop_reason is not None:
                raise ResourceStop(self.stop_reason)
            now = self.clock()
            if self.deadline is not None and now >= self.deadline:
                self.stop("max_seconds")
                raise ResourceStop(self.stop_reason or "max_seconds")
            if any(
                scope.deadline is not None and now >= scope.deadline for scope in self._work_scopes
            ):
                self.scope_denials["stage_max_seconds"] += 1
                raise ResourceStop("stage_max_seconds")
            if force or self.work.used - self._last_memory_work >= 64:
                self._sample_memory()

    def _admit_work(self, amount: int, category: str, commit: Callable[[], bool]) -> bool:
        with self._lock:
            self.attempted[category] += amount
            try:
                self.checkpoint()
                if amount > self.work.remaining:
                    self.stop("max_work")
                    raise ResourceStop(self.stop_reason or "max_work")
                if amount > self.remaining_work:
                    self.scope_denials["stage_max_work"] += 1
                    raise ResourceStop("stage_max_work")
            except ResourceStop as error:
                self._last_work_denial = error.reason
                return False
            accepted = commit()
            if accepted:
                for scope in self._work_scopes:
                    scope.consumed += amount
            return accepted

    def charge_work(self, amount: int, category: str) -> None:
        with self._lock:
            if type(amount) is not int or amount < 0:
                raise ValueError("Invalid attempted work")
            if not self.work.consume(kind=category, amount=amount):
                raise ResourceStop(self._last_work_denial)

    def record_structural(self, category: str, amount: int) -> None:
        if type(amount) is not int or amount < 0:
            raise ValueError("Invalid admitted structural visit count")
        with self._lock:
            self.raw_structural[category] += amount
            phase = self.raw_structural_by_phase.setdefault(self._stack[-1], Counter[str]())
            phase[category] += amount

    def structural_meter(
        self, category: str, *, charge: Callable[[int], None] | None = None
    ) -> StructuralWorkMeter:
        origin = (get_ident(), tuple(scope.identity for scope in self._work_scopes))

        def admit(amount: int, selected: str) -> None:
            with self._lock:
                current = (get_ident(), tuple(scope.identity for scope in self._work_scopes))
                if current != origin:
                    raise ResourceStop("structural_scope_mismatch")
                if charge is None:
                    self.charge_work(amount, selected)
                else:
                    charge(amount)

        return StructuralWorkMeter(
            admit,
            self.record_structural,
            category,
            scope=lambda: (get_ident(), tuple(scope.identity for scope in self._work_scopes)),
        )

    def charge_json(
        self, value: JsonValue, category: str, *, charge: Callable[[int], None] | None = None
    ) -> None:
        with self.structural_meter(category, charge=charge) as meter:

            def visit(node: JsonValue) -> None:
                if isinstance(node, str):
                    visit_json_text(node, meter.visit)
                else:
                    meter.visit()
                    if isinstance(node, dict):
                        for key, child in node.items():
                            visit_json_text(key, meter.visit)
                            visit(child)
                    elif isinstance(node, list):
                        for child in node:
                            visit(child)

            visit(value)

    def _reserve_work(self, amount: int, category: str, *, cleanup: bool) -> WorkReservation:
        if type(amount) is not int or amount < 0 or not isinstance(category, str) or not category:
            raise ValueError("Invalid work reservation")
        with self._lock:
            self.reserve_attempts[category] += amount
            if not cleanup:
                self.checkpoint()
            if amount > self.remaining_work:
                if amount > self.work.remaining:
                    self.stop("max_work")
                    raise ResourceStop(self.stop_reason or "max_work")
                raise ResourceStop("stage_max_work")
            permit = WorkReservation(self, len(self._work_reservations), amount, category)
            self.work.reserve(amount)
            for scope in self._work_scopes:
                scope.held += amount
            self._work_reservations.append(permit)
            return permit

    def reserve_work(self, amount: int, *, category: str) -> WorkReservation:
        return self._reserve_work(amount, category, cleanup=False)

    def ensure_terminal_permit(
        self, existing: WorkReservation | None = None, *, amount: int = 128
    ) -> WorkReservation:
        if type(amount) is not int or amount < 0:
            raise ValueError("Invalid terminal reservation")
        with self._lock:
            if existing is not None:
                existing._validate(self)
                if existing.category != "terminal_publication":
                    raise ValueError("Not a terminal publication grant")
                if existing.status == "reserved":
                    return existing
            return self._reserve_work(
                min(amount, self.remaining_work), "terminal_publication", cleanup=True
            )

    def record_child_memory(self, amount: int) -> None:
        if type(amount) is not int or amount < 0:
            raise ValueError("Invalid child memory observation")
        with self._lock:
            self.child_memory = max(self.child_memory, amount)
            self._sample_memory()

    def release_child_memory(self) -> None:
        with self._lock:
            self.child_memory = 0

    @contextmanager
    def work_scope(self, limit: int, name: str, *, max_seconds: float = 0) -> Iterator[None]:
        if get_ident() != self._owner_thread:
            raise ValueError("Only the request owner can change quota scopes")
        if type(limit) is not int or limit < 0 or not name:
            raise ValueError("Invalid local work scope")
        if not math.isfinite(max_seconds) or max_seconds < 0:
            raise ValueError("Invalid local deadline")
        with self._lock:
            scope = _WorkScope(
                len(self._scope_records),
                name,
                limit,
                self.clock() + max_seconds if max_seconds else None,
            )
            self._scope_records.append(scope)
            self._work_scopes.append(scope)
        try:
            yield
        finally:
            with self._lock:
                if not self._work_scopes or self._work_scopes[-1] is not scope:
                    raise RuntimeError("Local quota scopes must close in order")
                self._work_scopes.pop()
                scope.active = False

    def _touch_time(self) -> None:
        now = self.clock()
        self.phase_seconds[self._stack[-1]] += max(0.0, now - self._time_at)
        self._time_at = max(self._time_at, now)

    @contextmanager
    def phase(self, name: str) -> Iterator[None]:
        if get_ident() != self._owner_thread or not name:
            raise ValueError("Only the request owner can change timing phases")
        with self._lock:
            self._touch_time()
            self._stack.append(name)
            self.phase_attempts[name] += 1
            started = self.clock()
        try:
            self.checkpoint()
            yield
        finally:
            with self._lock:
                self._touch_time()
                self.inclusive_phase_seconds[name] += max(0.0, self.clock() - started)
                self._stack.pop()

    def snapshot(self) -> JsonObject:
        with self._lock:
            if get_ident() == self._owner_thread:
                self._touch_time()
            return {
                "schema_version": 1,
                "cost_model": {"version": COST_MODEL_VERSION, "digest": COST_MODEL_DIGEST},
                "raw_structural_visits": dict[str, JsonValue](self.raw_structural),
                "raw_structural_by_phase": {
                    name: dict[str, JsonValue](counts)
                    for name, counts in self.raw_structural_by_phase.items()
                },
                "work": self.work.snapshot(),
                "attempted_work": dict[str, JsonValue](self.attempted),
                "reservation_attempted_work": dict[str, JsonValue](self.reserve_attempts),
                "work_reservations": [permit.to_dict() for permit in self._work_reservations],
                "stage_denials": dict[str, JsonValue](self.scope_denials),
                "work_scopes": [
                    {
                        "id": scope.identity,
                        "name": scope.name,
                        "limit": scope.limit,
                        "consumed": scope.consumed,
                        "reserved": scope.held,
                        "active": scope.active,
                        "deadline": scope.deadline,
                    }
                    for scope in self._scope_records
                ],
                "deadline": self.deadline,
                "elapsed_seconds": max(0.0, self._time_at - self.started_at),
                "wall_observed_seconds": max(0.0, self.clock() - self.started_at),
                "exclusive_phase_seconds": dict[str, JsonValue](self.phase_seconds),
                "inclusive_phase_seconds": dict[str, JsonValue](self.inclusive_phase_seconds),
                "timing_scope": "Exclusive phases partition owner-timing-as-of elapsed_seconds; inclusive phases overlap.",
                "phase_attempts": dict[str, JsonValue](self.phase_attempts),
                "memory": {
                    "cap_bytes": self.memory_cap,
                    "observed_bytes": self.memory_used,
                    "peak_sampled_bytes": self.peak_memory,
                    "enforcement": "sampled_combined_rss",
                    "enforcement_checks": self.memory_checks,
                    "samples": self.memory_samples,
                },
                "stop_reason": self.stop_reason,
                "api": {
                    "calls": self.api_calls,
                    "reservation_attempts": self.api_reservation_attempts,
                    "denials": dict[str, JsonValue](self.api_denials),
                    "stopped_after_unknown_charge": self.api_stopped,
                    "observed_tokens": self.api_observed_tokens,
                    "observed_cost_at_frozen_card_microusd": (
                        None
                        if any(
                            permit.status == "unknown" and not permit.cost_known
                            for permit in self._api_reservations
                        )
                        else self.api_observed_money
                    ),
                    "known_observed_cost_subtotal_microusd": self.api_observed_money,
                    "settlement_scope": "Consumed means settled exact-at-card reports; observed totals include unsettled reports. Unknown pricing is null, never inferred as zero.",
                    "consumed_tokens": self.api_consumed_tokens,
                    "reserved_tokens": self.api_reserved_tokens,
                    "consumed_microusd": self.api_consumed_money,
                    "reserved_microusd": self.api_reserved_money,
                    "rate_card_digest": None if self.rate_card is None else self.rate_card.digest,
                    "quotation_scope": "Caller-verified exact request; no token-count network service is invoked by this ledger.",
                    "reservations": [permit.to_dict() for permit in self._api_reservations],
                },
            }


__all__ = [
    "ApiReservation",
    "RateCard",
    "ResourceStop",
    "RunLedger",
    "WorkReservation",
    "json_work",
    "resident_bytes",
]
