"""Untrusted proposal records; none of these records can grant verification."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from pythonstan.analysis.contracts.identity import SemanticIdentity, SourceModule


@dataclass(frozen=True)
class CandidateProposal:
    status: str
    candidate: object | None
    provider: str
    work: int
    seconds: float
    diagnostics: tuple[str, ...] = ()


class CandidateProvider(Protocol):
    def propose(
        self,
        source: str,
        *,
        identity: SemanticIdentity | None = None,
        max_work: int | None = None,
    ) -> CandidateProposal: ...


class SourceBundle(Protocol):
    """Only source records are available to the proposal factory."""

    @property
    def modules(self) -> Sequence[SourceModule]: ...
