"""Explicit ownership/version checkpoints for live mutable analysis states."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from pythonstan.analysis.contracts.identity import (
    IdentityEpoch,
    ObjectId,
    StateOwnerId,
    StateRevision,
)

if TYPE_CHECKING:
    from pythonstan.analysis.domains.reabstract import IdentityArena

    from .store import Store


class StateOwnershipError(RuntimeError):
    """An internal state handle was paired with another mutation owner."""


@dataclass(frozen=True, slots=True)
class StateHandle:
    owner: StateOwnerId
    epoch: IdentityEpoch
    revision: StateRevision
    root: Store

    @classmethod
    def capture(cls, root: Store) -> StateHandle:
        return cls(
            StateOwnerId(f"store:{id(root)}"),
            IdentityEpoch(root.identity_epoch),
            root.state_revision,
            root,
        )

    def read(self) -> Store:
        """Read the owned work state without normalizing or creating facts."""
        if self.owner != StateOwnerId(f"store:{id(self.root)}"):
            raise StateOwnershipError("State handle has a different mutation owner")
        return self.root

    def is_current(self) -> bool:
        root = self.read()
        return self.epoch == root.identity_epoch and self.revision == root.state_revision


__all__ = ["StateHandle", "StateOwnershipError"]


@dataclass(frozen=True, slots=True)
class ObjectHandle:
    identity: ObjectId
    observed_epoch: IdentityEpoch

    def refresh(self, arena: IdentityArena) -> ObjectHandle:
        return ObjectHandle(arena.resolve(self.identity), arena.epoch)
