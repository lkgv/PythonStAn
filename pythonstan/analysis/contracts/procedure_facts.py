"""Pure procedure-fact identities and the finite semantic publication port.

These records describe observed solver scopes. They are not proof certificates
and do not depend on solver states, result views, or optional debug observers.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

from .identity import (
    ContextId,
    IdentityEpoch,
    ProgramPointId,
    SemanticIdentity,
    StateRevision,
    VersionedDependency,
)

type ProducerKind = Literal["unit", "native", "checked_summary"]
type ScopeKind = Literal["module", "procedure"]
type Materialization = Literal["complete", "partial", "unmaterialized", "lazy", "invalidated"]


def _text(value: str, name: str) -> None:
    if type(value) is not str or not value:
        raise ValueError(name + " must be a nonempty string")


@dataclass(frozen=True, slots=True)
class ProcedureScope:
    module: str
    qualname: str
    scenario: str
    context: ContextId = ContextId(())
    site: ProgramPointId | None = None
    kind: ScopeKind = "procedure"

    def __post_init__(self) -> None:
        _text(self.module, "module")
        _text(self.qualname, "qualname")
        _text(self.scenario, "scenario")
        if type(self.context) is not tuple or any(
            type(point) is not str or not point for point in self.context
        ):
            raise ValueError("Scope context must contain source point strings")
        if self.site is not None:
            _text(self.site, "source site")
        if self.kind not in {"module", "procedure"}:
            raise ValueError("Unknown procedure scope kind")
        if self.kind == "module" and self.qualname != self.module:
            raise ValueError("Module scope must name its module")
        if self.kind == "procedure" and not self.qualname.startswith(self.module + "."):
            raise ValueError("Procedure scope must belong to its module")


@dataclass(frozen=True, slots=True)
class ProcedureProducer:
    kind: ProducerKind
    owner: str
    identity: str
    scope: ProcedureScope

    def __post_init__(self) -> None:
        if self.kind not in {"unit", "native", "checked_summary"}:
            raise ValueError("Unknown semantic fact producer")
        _text(self.owner, "generation owner")
        _text(self.identity, "producer identity")


@dataclass(frozen=True, slots=True)
class FactVersion:
    generation: int
    revision: StateRevision
    epoch: IdentityEpoch

    def __post_init__(self) -> None:
        for name, value in (
            ("generation", self.generation),
            ("revision", self.revision),
            ("epoch", self.epoch),
        ):
            if type(value) is not int or value < 0:
                raise ValueError(name + " must be a nonnegative integer")


@dataclass(frozen=True, slots=True)
class ProcedureFactHead:
    producer: ProcedureProducer
    version: FactVersion
    identity: SemanticIdentity
    materialization: Materialization = "complete"
    dependencies: tuple[VersionedDependency, ...] = ()

    def __post_init__(self) -> None:
        if self.materialization not in {
            "complete",
            "partial",
            "unmaterialized",
            "lazy",
            "invalidated",
        }:
            raise ValueError("Unknown fact materialization state")


@dataclass(frozen=True, slots=True)
class FactPublication[Payload]:
    head: ProcedureFactHead
    payload: Payload


class FactSink[Payload](Protocol):
    def publish_generation(
        self,
        owner: str,
        generation: int,
        facts: tuple[FactPublication[Payload], ...],
        *,
        application_accepted: bool | None = None,
    ) -> None: ...


__all__ = [
    "FactPublication",
    "FactSink",
    "FactVersion",
    "Materialization",
    "ProcedureFactHead",
    "ProcedureProducer",
    "ProcedureScope",
    "ProducerKind",
    "ScopeKind",
]
