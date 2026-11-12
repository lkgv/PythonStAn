"""Run-local work accounting shared by nested engines; no solver imports."""

from __future__ import annotations

from collections import Counter
from typing import Protocol

from .json_data import JsonObject, JsonValue


class WorkLedgerPort(Protocol):
    """Only preflight capacity and actual charge reach a shared caller budget."""

    @property
    def remaining(self) -> int: ...

    def consume(self, *, kind: str) -> bool: ...


class SharedWorkLedger:
    def __init__(self, limit: int | None = None) -> None:
        if limit is not None and (type(limit) is not int or limit < 0):
            raise ValueError("Global work limit must be a nonnegative integer or None")
        self.limit: int | None = limit
        self.used: int = 0
        self.by_kind: Counter[str] = Counter()
        self.denials: Counter[str] = Counter()

    @property
    def remaining(self) -> int:
        return max(0, self.limit - self.used) if self.limit is not None else 2**63 - 1

    def consume(self, *, kind: str = "abstract_transfer", amount: int = 1) -> bool:
        if type(amount) is not int or amount < 0 or not isinstance(kind, str) or not kind:
            raise ValueError("Invalid work charge")
        if amount > self.remaining:
            self.denials[kind] += 1
            return False
        self.used += amount
        self.by_kind[kind] += amount
        return True

    def snapshot(self) -> JsonObject:
        return {
            "limit": self.limit,
            "used": self.used,
            "remaining": self.remaining if self.limit is not None else None,
            "by_kind": dict[str, JsonValue](self.by_kind),
            "denials": dict[str, JsonValue](self.denials),
        }


__all__ = ["SharedWorkLedger", "WorkLedgerPort"]
