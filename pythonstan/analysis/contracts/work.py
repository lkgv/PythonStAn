"""Run-local work accounting shared by nested engines; no solver imports."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from typing import Protocol

from .json_data import JsonObject, JsonValue


class WorkLedgerPort(Protocol):
    """Only preflight capacity and actual charge reach a shared caller budget."""

    @property
    def remaining(self) -> int: ...

    def consume(self, *, kind: str) -> bool: ...


class SharedWorkLedger:
    def __init__(
        self,
        limit: int | None = None,
        *,
        admission: Callable[[int, str, Callable[[], bool]], bool] | None = None,
    ) -> None:
        if limit is not None and (type(limit) is not int or limit < 0):
            raise ValueError("Global work limit must be a nonnegative integer or None")
        self.limit: int | None = limit
        self.used: int = 0
        self.reserved: int = 0
        self.by_kind: Counter[str] = Counter()
        self.denials: Counter[str] = Counter()
        self._admission = admission

    @property
    def remaining(self) -> int:
        return (
            max(0, self.limit - self.used - self.reserved) if self.limit is not None else 2**63 - 1
        )

    def consume(self, *, kind: str = "abstract_transfer", amount: int = 1) -> bool:
        if type(amount) is not int or amount < 0 or not isinstance(kind, str) or not kind:
            raise ValueError("Invalid work charge")

        def commit() -> bool:
            if amount > self.remaining:
                self.denials[kind] += 1
                return False
            self.used += amount
            self.by_kind[kind] += amount
            return True

        return commit() if self._admission is None else self._admission(amount, kind, commit)

    def reserve(self, amount: int) -> None:
        if type(amount) is not int or not 0 <= amount <= self.remaining:
            raise ValueError("Invalid work reservation")
        self.reserved += amount

    def settle_reserved(self, amount: int, *, consumed: int, kind: str) -> None:
        if (
            type(amount) is not int
            or type(consumed) is not int
            or not 0 <= consumed <= amount <= self.reserved
        ):
            raise ValueError("Invalid reservation settlement")
        self.reserved -= amount
        self.used += consumed
        self.by_kind[kind] += consumed

    def tighten_limit(self, limit: int | None) -> None:
        if limit is not None and (type(limit) is not int or limit < 0):
            raise ValueError("Invalid work limit")
        if self.limit is not None and (limit is None or limit > self.limit):
            raise ValueError("A shared work limit cannot be enlarged")
        if limit is not None and limit < self.used + self.reserved:
            raise ValueError("A work limit cannot undercut already consumed or held capacity")
        self.limit = limit

    def snapshot(self) -> JsonObject:
        return {
            "limit": self.limit,
            "used": self.used,
            "reserved": self.reserved,
            "remaining": self.remaining if self.limit is not None else None,
            "by_kind": dict[str, JsonValue](self.by_kind),
            "denials": dict[str, JsonValue](self.denials),
        }


__all__ = ["SharedWorkLedger", "WorkLedgerPort"]
