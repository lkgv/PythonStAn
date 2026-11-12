"""Owned scalar descriptions at the passive native capture boundary.

These records contain snapshot-local references and scalar descriptions only.
The module uses the standard library so offline readers need no analyzer import.
"""

from typing import NotRequired, Protocol, TypedDict

from .contract import CaptureRequest

type CaptureScalar = None | bool | int | float | str


class CaptureReference(TypedDict):
    ref: int | None
    status: str
    reason: NotRequired[str]


class EntityDescription(TypedDict):
    id: int
    kind: str
    label: str
    origin: dict[str, CaptureScalar]
    attributes: dict[str, CaptureScalar]
    links: dict[str, CaptureReference]


class EntityLinkProjection(Protocol):
    """The single capture operation used by the audited identity projection."""

    def link(self, record: EntityDescription, name: str, obj: object) -> None: ...


class OwnerCoverage(TypedDict):
    fields: dict[str, str]
    evidence: NotRequired[str]


class ViewCoverage(TypedDict):
    registry: str
    owners: dict[str, OwnerCoverage]
    fields: NotRequired[dict[str, str]]
    enumerated_owners: NotRequired[int]
    enumerated_rows: NotRequired[int]


class CaptureView(TypedDict):
    """Named output relation and its explicitly scoped capture coverage."""

    version: int
    fidelity: str
    dimensions: list[str]
    operations: list[str]
    coverage: ViewCoverage
    rows: list[dict[str, object]]
    semantic_version: NotRequired[str]
    scope: NotRequired[str]
    authority: NotRequired[str]
    owner_fields: NotRequired[list[str]]
    dependencies: NotRequired[list[str]]
    diagnostics: NotRequired[list[str]]
    call_kind_encoding: NotRequired[str]
    capture: NotRequired[dict[str, object]]


class CaptureProjection(EntityLinkProjection, Protocol):
    """The actual shared budget and entity registry used by relation exporters."""

    request: "CaptureRequest"
    stopped: str | None
    bytes_used: int

    def available(self, size: int = 0) -> bool: ...
    def entity(self, obj: object) -> int | None: ...
    def omit(self, view: str, reason: str, owner: int | None = None) -> None: ...
    def row(self, target: list[dict[str, object]], row: dict[str, object]) -> bool: ...


class TraceMappingCoverage(TypedDict):
    registry: str
    examined: int
    mapped: int
    unmapped: int
    reason_counts: dict[str, int]
    registered_refs: NotRequired[int]
    unexamined: NotRequired[int]


class TraceReferenceMapping(TypedDict):
    version: int
    semantic_version: str
    scope: str
    rows: list[dict[str, object]]
    coverage: TraceMappingCoverage
