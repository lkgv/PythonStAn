"""Narrow optional may-oracle capabilities without concrete engine/state imports."""

from __future__ import annotations

from collections.abc import Callable, Container, Hashable, Mapping
from dataclasses import dataclass
from typing import Literal, Protocol

from .identity import CellId, ObjectId, ProgramPointId, SourceModule, UnitId
from .json_data import JsonObject


@dataclass(frozen=True)
class CandidateAnswer[ValueT]:
    values: ValueT
    coverage: Literal["partial"]
    version: int
    provenance: tuple[str, ...]
    identity: str

    def permits_exclusion(self) -> Literal[False]:
        return False


class CandidateOraclePort[ValueT](Protocol):
    """Candidate-only replacements do not promise lifecycle or publication hooks."""

    def candidates(
        self, cell: CellId, consumer: UnitId | None = None
    ) -> CandidateAnswer[ValueT]: ...

    def statistics(self) -> JsonObject: ...


class OracleHostPort[OperationsT](Protocol):
    """Only identity, work accounting, and dependency notification reach an oracle."""

    @property
    def source_digest(self) -> str: ...

    @property
    def policy_digest(self) -> str: ...

    @property
    def model_digest(self) -> str: ...

    @property
    def semantic_profile(self) -> str: ...

    @property
    def modules(self) -> Mapping[str, SourceModule]: ...

    @property
    def ops(self) -> OperationsT: ...

    @property
    def max_work(self) -> int: ...

    @property
    def work(self) -> int: ...

    @property
    def unit_ids(self) -> Container[UnitId]: ...

    def charge(self, amount: int, category: str) -> None: ...

    def enqueue(self, key: UnitId, reason: str) -> None: ...


class IncrementalOraclePort[ValueT, OperationsT, PreparedT](CandidateOraclePort[ValueT], Protocol):
    """Explicit additional capabilities for a run-local incremental oracle."""

    def bind(
        self,
        driver: OracleHostPort[OperationsT],
        *,
        source_digest: str | None = None,
        profile: str | None = None,
        policy_digest: str | None = None,
    ) -> None: ...

    def publish_cell(self, cell: CellId, value: ValueT, origin: str = "ai") -> bool: ...

    def publish_field(
        self, objid: ObjectId, key: Hashable, value: ValueT, origin: str = "ai"
    ) -> bool: ...

    def observe_call(self, caller: str, callee: str, site: ProgramPointId) -> None: ...

    def prepare_reabstract(
        self,
        mapping: Mapping[ObjectId, ObjectId],
        ops: OperationsT | None = None,
        charge: Callable[[int], None] | None = None,
    ) -> PreparedT: ...

    def commit_reabstract(self, prepared: PreparedT) -> None: ...
