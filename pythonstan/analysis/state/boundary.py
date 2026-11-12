"""Engine-neutral boundary records, retaining canonical state graph anchors."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from pythonstan.analysis.contracts.identity import CellId, ObjectId, StateRevision, UnitId
from pythonstan.analysis.domains.values import Value

from .continuation import AttachmentValue, CompletionKind, Continuation
from .effects import WriteEffect
from .guards import MembershipGuard
from .keys import FieldKey
from .metadata import Metadata
from .store import FunctionInfo, HeapObject, Store, Unit

if TYPE_CHECKING:
    from .transform import GraphPayload


@dataclass(init=False)
class BoundaryObject(HeapObject):
    """A flat engine view reuses the canonical heap shape and join algorithm."""

    _runtime_root: HeapObject | None = field(default=None, repr=False, compare=False)

    def __init__(
        self,
        kind: str,
        fields: dict[FieldKey, Value],
        default: Value,
        maybe_missing: set[FieldKey],
        singleton: bool,
        class_ids: frozenset[ObjectId],
        metadata: Metadata | Mapping[str, object],
        havoc: bool,
        _runtime_root: HeapObject | None = None,
    ) -> None:
        super().__init__(
            kind, fields, default, maybe_missing, singleton, class_ids, metadata, havoc
        )
        self._runtime_root = _runtime_root


@dataclass
class BoundaryState:
    cells: dict[CellId, Value]
    heap: dict[ObjectId, BoundaryObject]
    initialized: dict[str, str]
    writes: set[WriteEffect]
    events: tuple[tuple[str, str], ...]
    obligations: frozenset[str]
    completion: CompletionKind = "normal"
    value: Value | None = None
    exception: str | None = None
    continuation: Continuation = field(default_factory=Continuation)
    cleanups: Continuation = field(default_factory=Continuation)
    resume_target: AttachmentValue = None
    havoc_namespaces: frozenset[str] = frozenset()
    havoc_imports: bool = False
    guards: dict[str, MembershipGuard] = field(default_factory=dict)
    identity_epoch: int = 0
    _store_root: Store | None = field(default=None, repr=False, compare=False)
    state_revision: StateRevision = StateRevision(0)

    def record_write(self, effect: WriteEffect) -> None:
        self.writes.add(effect)
        self.state_revision = StateRevision(self.state_revision + 1)


@dataclass
class HostStateBoundary:
    """The actual typed root bundle prepared by a host address transaction."""

    units: dict[UnitId, Unit]
    functions: dict[ObjectId, FunctionInfo]
    extras: tuple[GraphPayload, ...]
    events: list[tuple[str, str]]


__all__ = ["BoundaryObject", "BoundaryState", "HostStateBoundary"]
