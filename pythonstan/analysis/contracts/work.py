"""Run-local work accounting shared by nested engines; no solver imports."""

from collections import Counter


class SharedWorkLedger:
    def __init__(self, limit=None):
        if limit is not None and (type(limit) is not int or limit < 0):
            raise ValueError("Global work limit must be a nonnegative integer or None")
        self.limit = limit
        self.used = 0
        self.by_kind = Counter()
        self.denials = Counter()

    @property
    def remaining(self):
        return max(0, self.limit - self.used) if self.limit is not None else 2**63 - 1

    def consume(self, *, kind="abstract_transfer", amount=1):
        if type(amount) is not int or amount < 0 or not isinstance(kind, str) or not kind:
            raise ValueError("Invalid work charge")
        if amount > self.remaining:
            self.denials[kind] += 1
            return False
        self.used += amount
        self.by_kind[kind] += amount
        return True

    def snapshot(self):
        return {
            "limit": self.limit,
            "used": self.used,
            "remaining": self.remaining if self.limit is not None else None,
            "by_kind": dict(self.by_kind),
            "denials": dict(self.denials),
        }
