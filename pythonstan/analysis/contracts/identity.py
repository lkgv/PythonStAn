"""Engine-neutral semantic identities; no solver or mutable global state."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from .wire import freeze_snapshot, thaw_snapshot


def stable_digest(value: object) -> str:
    """Digest a JSON-compatible immutable identity description."""
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"),
                         ensure_ascii=False, allow_nan=False).encode("utf-8")
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


@dataclass(frozen=True)
class ObjectId:
    arena: str
    allocation: str
    context: tuple[str, ...] = ()
    kind: str = "object"

    def __str__(self) -> str:
        return f"{self.arena}:{self.kind}:{self.allocation}:{'/'.join(self.context)}"


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
        return stable_digest({
            "source": self.source_digest, "ir": self.ir_digest,
            "model": self.model_digest, "policy": self.policy_digest,
            "profile": self.profile, "state": self.state_version,
            "python": self.python_version,
            "dependencies": [(d.kind, d.identity, d.version, d.negative)
                             for d in self.dependencies],
        })


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
        return SourceId(self.module, self.digest, self.filename,
                        self.original_bytes_digest, self.encoding,
                        self.import_root_digest)

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
    arguments: tuple[object, ...] = ()
    scenario_id: str = ""
    coverage: str = "initialization_only"

    def __post_init__(self) -> None:
        if not self.root_module:
            raise ValueError("entry scenario needs a root module")
        args = freeze_snapshot(self.arguments)
        object.__setattr__(self, "arguments", args)
        if not self.scenario_id:
            object.__setattr__(self, "scenario_id", stable_digest({
                "module": self.root_module, "qualname": self.entry_qualname,
                "arguments": thaw_snapshot(args), "coverage": self.coverage,
            })[:24])

    @property
    def module(self) -> str:
        return self.root_module

    @property
    def qualname(self) -> str | None:
        return self.entry_qualname
