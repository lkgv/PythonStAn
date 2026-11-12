"""Engine-neutral semantic identities; no solver or mutable global state."""

from __future__ import annotations

import json
from dataclasses import dataclass
from hashlib import sha256
from typing import NewType

from .json_data import FrozenJsonValue, freeze_json, thaw_json, validate_json


def stable_digest(value: object) -> str:
    """Digest a JSON-compatible immutable identity description."""
    encoded = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode("utf-8")
    return sha256(encoded).hexdigest()


@dataclass(frozen=True)
class SourceId:
    module: str
    digest: str
    filename: str = "<source>"
    original_bytes_digest: str | None = None
    encoding: str = "utf-8"
    import_root_digest: str = ""


@dataclass(frozen=True)
class RegionId:
    source: SourceId
    qualname: str
    node_id: str
    span: tuple[int, int, int, int] | None = None


# These identities are opaque at runtime. Their existing display strings are
# retained, while static checking prevents an object from becoming a cell/unit
# merely because all three used to be represented by ``str``.
ObjectId = NewType("ObjectId", str)
CellId = NewType("CellId", str)
ProgramPointId = NewType("ProgramPointId", str)
UnitId = NewType("UnitId", str)
FrameId = NewType("FrameId", str)
ContextId = NewType("ContextId", tuple[ProgramPointId, ...])
PolicyId = NewType("PolicyId", str)
IdentityEpoch = NewType("IdentityEpoch", int)
StateOwnerId = NewType("StateOwnerId", str)
StateRevision = NewType("StateRevision", int)
ContinuationId = NewType("ContinuationId", str)


@dataclass(frozen=True)
class VersionedDependency:
    kind: str
    identity: str
    version: str
    negative: bool = False


@dataclass(frozen=True)
class SemanticIdentity:
    source_digest: str
    ir_digest: str
    model_digest: str
    policy_digest: str
    profile: str
    state_version: str = "0"
    python_version: str = "3.12"
    dependencies: tuple[VersionedDependency, ...] = ()

    @property
    def digest(self) -> str:
        return stable_digest(
            {
                "source": self.source_digest,
                "ir": self.ir_digest,
                "model": self.model_digest,
                "policy": self.policy_digest,
                "profile": self.profile,
                "state": self.state_version,
                "python": self.python_version,
                "dependencies": [
                    (d.kind, d.identity, d.version, d.negative) for d in self.dependencies
                ],
            }
        )


@dataclass(frozen=True)
class CallKey:
    target: str
    context: tuple[str, ...]
    identity: SemanticIdentity
    scenario: str


@dataclass(frozen=True)
class SourceModule:
    """Nonexecuting, decoded source snapshot passed to an engine."""

    module: str
    filename: str
    source: str
    digest: str = ""
    is_package: bool = False
    original_bytes_digest: str | None = None
    encoding: str = "utf-8"
    import_root_digest: str = ""

    def __post_init__(self) -> None:
        if not self.module or not isinstance(self.source, str):
            raise ValueError("SourceModule requires a module and decoded source")
        actual = sha256(self.source.encode("utf-8")).hexdigest()
        if self.digest and self.digest != actual:
            raise ValueError("source digest does not match SourceModule.source")
        object.__setattr__(self, "digest", actual)

    @property
    def identity(self) -> SourceId:
        return SourceId(
            self.module,
            self.digest,
            self.filename,
            self.original_bytes_digest,
            self.encoding,
            self.import_root_digest,
        )

    @property
    def code(self) -> str:
        return self.source


@dataclass(frozen=True)
class EntryScenario:
    """One independent initialization cache and optional explicit call root.

    Arguments are immutable JSON abstract input descriptions, not concrete
    samples implicitly promoted to universal entry coverage.
    """

    root_module: str
    entry_qualname: str | None = None
    arguments: tuple[FrozenJsonValue, ...] = ()
    scenario_id: str = ""
    coverage: str = "initialization_only"

    def __post_init__(self) -> None:
        if not self.root_module:
            raise ValueError("entry scenario needs a root module")
        args = freeze_json(validate_json(self.arguments))
        if not isinstance(args, tuple):
            raise TypeError("Entry arguments must freeze to an immutable sequence")
        object.__setattr__(self, "arguments", args)
        if not self.scenario_id:
            object.__setattr__(
                self,
                "scenario_id",
                stable_digest(
                    {
                        "module": self.root_module,
                        "qualname": self.entry_qualname,
                        "arguments": thaw_json(args),
                        "coverage": self.coverage,
                    }
                )[:24],
            )

    @property
    def module(self) -> str:
        return self.root_module

    @property
    def qualname(self) -> str | None:
        return self.entry_qualname
