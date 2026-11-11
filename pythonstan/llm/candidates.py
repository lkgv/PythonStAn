"""Untrusted proposal records; none of these records can grant verification."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class CandidateProposal:
    status: str
    candidate: object | None
    provider: str
    work: int
    seconds: float
    diagnostics: tuple[str, ...] = ()


class CandidateProvider(Protocol):
    def propose(self, source: str, *, identity=None, max_work=None) -> CandidateProposal:
        ...
