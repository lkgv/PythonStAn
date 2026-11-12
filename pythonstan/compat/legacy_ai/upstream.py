"""Decode the two existing, externally supplied predecessor services.

This boundary observes convergence metadata and the actual Native state payload.
It does not validate a predecessor's analysis or grant it semantic completeness.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from pythonstan.analysis.pointer.kcfa.state import PointerAnalysisState

__all__ = ["LegacyProvider", "QueryState"]


@runtime_checkable
class _NullaryService(Protocol):
    def __call__(self) -> object: ...


@runtime_checkable
class _LookupService(Protocol):
    def __call__(self, name: str) -> object: ...


def _not_callable(value: object) -> TypeError:
    return TypeError(f"'{type(value).__name__}' object is not callable")


def _call_nullary(value: object) -> object:
    if not isinstance(value, _NullaryService):
        raise _not_callable(value)
    return value()


@dataclass(frozen=True)
class QueryState:
    """A checked state payload, retaining the exact predecessor state object."""

    _state: PointerAnalysisState


@dataclass(frozen=True)
class LegacyProvider:
    """A raw predecessor result/query, consumed only through finite services."""

    raw: object

    def get_statistics(self) -> dict[str, object]:
        # Keep the legacy existence lookup followed by the invocation lookup.
        # A descriptor may observe both accesses; do not cache it beforehand.
        name = "get_statistics"
        if not hasattr(self.raw, name):
            return {}
        service: object = getattr(self.raw, name)
        payload = _call_nullary(service)
        getter_name = "get"
        lookup: object = getattr(payload, getter_name)
        if not isinstance(lookup, _LookupService):
            raise _not_callable(lookup)
        # This driver consumes exactly this observation, not the entire opaque
        # statistics mapping. Other predecessor statistics remain untouched.
        return {"converged": lookup("converged")}

    def query(self) -> QueryState:
        name = "query"
        if hasattr(self.raw, name):
            service: object = getattr(self.raw, name)
            query = _call_nullary(service)
        else:
            query = self.raw
        state_name = "_state"
        payload: object = getattr(query, state_name, None)
        if payload is None:
            raise RuntimeError("KCFA solver query does not expose analysis state")
        if not isinstance(payload, PointerAnalysisState):
            raise TypeError("KCFA query state must implement the Native state contract")
        return QueryState(payload)
