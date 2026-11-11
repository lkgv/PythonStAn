"""Generic completion, summary and guarantee records shared by engines.

These records express contracts; constructing one never grants a proof.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Generic, Literal, Mapping, TypeVar

from .identity import SemanticIdentity, VersionedDependency

V = TypeVar("V")
S = TypeVar("S")
H = TypeVar("H")

CompletionKind = Literal["normal", "return", "raise", "break", "continue", "suspend"]


@dataclass(frozen=True)
class Outcome(Generic[V, S]):
    """One correlated exit; payload absence is explicit.

    A full abstract value denoting Python None is a present payload. Normal
    statement continuation, break and continue have no payload. Expression
    evaluation may use a normal outcome with a present value. Unreachable
    states are represented by the state domain, never by an empty env.
    """

    kind: CompletionKind
    state: S
    value: V | None = None
    value_present: bool = False
    continuation: str | None = None
    pending_cleanup: tuple[str, ...] = ()
    guard: str | None = None

    def __post_init__(self) -> None:
        if self.kind not in ("normal", "return", "raise", "break", "continue", "suspend"):
            raise ValueError(f"unknown completion kind: {self.kind}")
        if self.kind in ("return", "raise", "suspend") and not self.value_present:
            raise ValueError(f"{self.kind} requires an explicit full value payload")
        if self.kind in ("break", "continue") and self.value_present:
            raise ValueError(f"{self.kind} cannot carry a value payload")


@dataclass(frozen=True)
class Effects(Generic[V]):
    reads: tuple[object, ...] = ()
    writes: tuple[object, ...] = ()
    calls: tuple[object, ...] = ()
    exceptions: tuple[object, ...] = ()
    allocations: tuple[object, ...] = ()
    havoc: tuple[object, ...] = ()
    obligations: tuple[str, ...] = ()


@dataclass(frozen=True)
class UnknownEffects:
    origin: str
    return_sorts: tuple[str, ...] = ()
    reachable_objects: tuple[str, ...] = ()
    cells: tuple[str, ...] = ()
    globals: tuple[str, ...] = ()
    may_invoke_callbacks: bool = True
    may_raise: bool = True
    may_suspend: bool = False
    unbounded_heap: bool = False


@dataclass(frozen=True)
class FreshTemplate:
    template_id: str
    kind: str
    fields: tuple[tuple[object, object], ...] = ()
    relation: tuple[object, ...] = ()


@dataclass(frozen=True)
class FrameMap:
    preserved_locations: tuple[str, ...] = ()
    may_written_locations: tuple[str, ...] = ()
    identity_mapping: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class CoverageRecord:
    status: Literal["closed_under_profile", "open", "unsupported", "no_entry"]
    profile: str
    entry_contract: str
    obligations: tuple[str, ...] = ()
    input_bound_digest: str = ""


@dataclass(frozen=True)
class DerivationRecord:
    origin: str = "solver"
    checker: str | None = None
    evidence: tuple[str, ...] = ()
    checked_obligations: tuple[str, ...] = ()


@dataclass(frozen=True)
class ProcedureSummary(Generic[V, H]):
    identity: SemanticIdentity
    input_bound: object
    outcomes: tuple[Outcome[V, H], ...]
    prefix_effects: Effects[V]
    coverage: CoverageRecord
    relation: tuple[object, ...] = ()
    fresh: tuple[FreshTemplate, ...] = ()
    frame: FrameMap = field(default_factory=FrameMap)
    dependencies: tuple[VersionedDependency, ...] = ()
    derivation: DerivationRecord = field(default_factory=DerivationRecord)


@dataclass(frozen=True)
class PropertyProof:
    property_id: str
    status: Literal["not_requested", "unknown", "verified", "refuted"]
    identity: SemanticIdentity
    input_bound_digest: str
    closed_obligations: tuple[str, ...] = ()
    open_obligations: tuple[str, ...] = ()
    evidence: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.status == "verified" and (self.open_obligations or not self.evidence):
            raise ValueError("verified property requires evidence and no open obligations")


@dataclass(frozen=True)
class GuaranteeLedger:
    solver_status: str
    coverage_status: str
    profile: str
    properties: tuple[PropertyProof, ...] = ()
    open_obligations: tuple[str, ...] = ()

    @property
    def proof_status(self) -> str:
        requested = tuple(p for p in self.properties if p.status != "not_requested")
        if not requested:
            return "not_requested"
        if all(p.status == "verified" for p in requested):
            return "verified"
        if any(p.status == "refuted" for p in requested):
            return "refuted"
        return "unknown"
