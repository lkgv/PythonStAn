"""Versioned, standard-library-only inspector boundary values.

Local integer references are scoped by the enclosing SnapshotRef. Labels and
origins are descriptions, never identities. Encoded snapshots own all bytes;
each decode returns a fresh tree with no connection to a live analyzer.
"""
from dataclasses import dataclass, field
import hashlib
import json
import math
from typing import Any, Mapping, Optional, Tuple

SCHEMA_MAJOR = 1
SCHEMA_MINOR = 0
REFERENCE_STATES = frozenset(("resolved", "omitted_by_capture", "outside_scope",
    "not_captured", "missing_in_engine", "unknown"))


class InspectorError(ValueError):
    """An invalid request or untrusted artifact, not an engine failure."""


def canonical_bytes(value: Any) -> bytes:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True,
                          separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (ValueError, TypeError, UnicodeError, RecursionError, OverflowError):
        raise InspectorError("value cannot be encoded as supported UTF-8 JSON") from None


def digest(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


@dataclass(frozen=True)
class SnapshotRef:
    run_id: str
    snapshot_id: str


@dataclass(frozen=True)
class EntityRef:
    snapshot: SnapshotRef
    local_id: int


@dataclass(frozen=True)
class CaptureRequest:
    views: Tuple[str, ...] = ("node_points", "ctx_points", "pfg_edges", "call_edges")
    max_entities: int = 100000
    max_rows: int = 1000000
    max_bytes: int = 64000000
    deadline_seconds: float = 10.0
    scope: str = "all_current_state"

    def __post_init__(self):
        if (type(self.views) is not tuple or len(self.views) > 32
                or any(type(v) is not str or not 0 < len(v) <= 64 for v in self.views)
                or len(set(self.views)) != len(self.views)):
            raise InspectorError("views must be a bounded tuple of distinct names")
        if type(self.scope) is not str or not 0 < len(self.scope) <= 128:
            raise InspectorError("capture scope must be bounded text")
        for name in ("max_entities", "max_rows", "max_bytes"):
            if type(getattr(self, name)) is not int or getattr(self, name) < 1:
                raise InspectorError("capture limits must be positive integers")
        if (type(self.deadline_seconds) not in (int, float)
                or not math.isfinite(self.deadline_seconds) or self.deadline_seconds <= 0):
            raise InspectorError("capture deadline must be positive and finite")


@dataclass(frozen=True)
class QueryLimits:
    max_rows: int = 1000
    max_visited: int = 100000
    max_bytes: int = 1000000
    max_depth: int = 8
    deadline_seconds: float = 2.0

    def __post_init__(self):
        for name in ("max_rows", "max_visited", "max_bytes", "max_depth"):
            if type(getattr(self, name)) is not int or getattr(self, name) < 1:
                raise InspectorError("query limits must be positive integers")
        if (type(self.deadline_seconds) not in (int, float)
                or not math.isfinite(self.deadline_seconds) or self.deadline_seconds <= 0):
            raise InspectorError("query deadline must be positive and finite")


@dataclass(frozen=True)
class QueryRequest:
    kind: str
    args: Mapping[str, Any] = field(default_factory=dict)
    claim: str = ""
    limits: QueryLimits = field(default_factory=QueryLimits)
    cursor: Optional[str] = None


@dataclass(frozen=True)
class SealedSnapshot:
    encoded: bytes

    def __post_init__(self):
        if type(self.encoded) is not bytes:
            raise InspectorError("sealed snapshot requires owned immutable bytes")

    @classmethod
    def seal(cls, data: Mapping[str, Any]):
        return cls(canonical_bytes(data))

    @property
    def data(self):
        return json.loads(self.encoded)

    @property
    def ref(self):
        value = self.data["snapshot"]
        return SnapshotRef(value["run_id"], value["snapshot_id"])

    @property
    def sha256(self):
        return hashlib.sha256(self.encoded).hexdigest()
