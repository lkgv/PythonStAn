"""Guarantee metadata shared by engines; construction never grants a proof.

Runtime completions live in analysis.state.flow. The former unused generic
Outcome/ProcedureSummary hierarchy is retired rather than shadowing that union.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from .identity import SemanticIdentity

type CompletionKind = Literal["normal", "return", "raise", "break", "continue", "suspend"]
type ProofStatus = Literal["not_requested", "unknown", "verified", "refuted"]


@dataclass(frozen=True)
class PropertyProof:
    property_id: str
    status: ProofStatus
    identity: SemanticIdentity
    input_bound_digest: str
    closed_obligations: tuple[str, ...] = ()
    open_obligations: tuple[str, ...] = ()
    evidence: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "closed_obligations", tuple(self.closed_obligations))
        object.__setattr__(self, "open_obligations", tuple(self.open_obligations))
        object.__setattr__(self, "evidence", tuple(self.evidence))
        if self.status == "verified" and (self.open_obligations or not self.evidence):
            raise ValueError("verified property requires evidence and no open obligations")


@dataclass(frozen=True)
class GuaranteeLedger:
    """The actual snapshot's solver/coverage/proof status dimensions."""

    solver_status: str
    coverage_status: str
    profile: str
    properties: tuple[PropertyProof, ...] = ()
    open_obligations: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "properties", tuple(self.properties))
        object.__setattr__(self, "open_obligations", tuple(self.open_obligations))

    @property
    def proof_status(self) -> ProofStatus:
        requested = tuple(p for p in self.properties if p.status != "not_requested")
        if not requested:
            return "not_requested"
        if all(p.status == "verified" for p in requested):
            return "verified"
        if any(p.status == "refuted" for p in requested):
            return "refuted"
        return "unknown"
