"""Requested semantic scope, producer coverage and output visibility.

An empty export never proves an empty analysis scope. Completeness requires an
explicit closed catalog and all expected current producer observations. Debug
observation enablement and event retention are intentionally absent here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pythonstan.analysis.contracts.cost_model import visit_json_text
from pythonstan.analysis.contracts.identity import IdentityEpoch
from pythonstan.analysis.contracts.procedure_facts import (
    ProcedureProducer,
    ProcedureScope,
    ScopeKind,
)

from .procedures import ObservationView, ProcedureObservationStore

type QueryView = Literal["returns", "outcomes"]
type Reachability = Literal["reachable", "unreachable", "lazy", "unknown"]
type QueryStatus = Literal["answered", "unavailable", "not_requested"]
type QueryScopeStatus = Literal[
    "complete",
    "empty",
    "unreachable",
    "lazy",
    "partial",
    "invalidated",
    "unmaterialized",
    "unsupported",
    "excluded",
    "truncated",
    "not_requested",
]
type CompletionKnowledge = Literal[
    "normal", "raise_only", "suspended", "mixed", "no_completion", "unreachable", "unknown"
]


@dataclass(frozen=True, slots=True)
class ScopeSelector:
    module: str
    qualname: str
    kind: ScopeKind = "procedure"

    @classmethod
    def of(cls, scope: ProcedureScope) -> ScopeSelector:
        return cls(scope.module, scope.qualname, scope.kind)


@dataclass(frozen=True, slots=True)
class FactQuery:
    view: QueryView
    module: str | None = None
    qualname: str | None = None

    def __post_init__(self) -> None:
        if self.view not in {"returns", "outcomes"}:
            raise ValueError("Unknown semantic query view")
        if self.module is not None and (type(self.module) is not str or not self.module):
            raise ValueError("Query module must be a nonempty string")
        if self.qualname is not None and (
            self.module is None or type(self.qualname) is not str or not self.qualname
        ):
            raise ValueError("A procedure query requires its module and qualified name")


@dataclass(frozen=True, slots=True)
class QueryResolution:
    query: FactQuery
    status: QueryStatus
    scope_status: QueryScopeStatus
    observations: tuple[ObservationView, ...] = ()
    completion: CompletionKnowledge = "unknown"
    semantic_complete: bool = False
    projection_complete: bool = True
    reason: str | None = None

    @property
    def covering(self) -> bool:
        return self.status == "answered" and self.semantic_complete and self.projection_complete

    @property
    def coverage_status(self) -> str:
        return "closed_under_profile" if self.covering else "open"

    def require_covering(self) -> tuple[ObservationView, ...]:
        if not self.covering:
            raise ValueError("Query observations do not cover the requested scope")
        return self.observations


@dataclass(frozen=True, slots=True)
class _Catalog:
    epoch: IdentityEpoch
    observation_revision: int
    expected: tuple[ProcedureProducer, ...]
    reachability: Reachability
    complete: bool
    supported: bool


class QueryLedger:
    def __init__(self, observations: ProcedureObservationStore) -> None:
        self.observations = observations
        self._requested: set[FactQuery] = set()
        self._catalog: dict[ScopeSelector, _Catalog] = {}
        self._inventory_epoch: IdentityEpoch | None = None
        self._inventory_revision: int | None = None
        self._catalog_revision = 0
        self._inventory_catalog_revision: int | None = None

    def _admit_query(self, query: FactQuery) -> None:
        ledger = self.observations.resource_ledger
        ledger.charge_work(1, "query_scope")
        with ledger.structural_meter("query_scope") as meter:
            for text in (query.view, query.module, query.qualname):
                if text is not None:
                    visit_json_text(text, meter.visit)

    def request(self, query: FactQuery) -> None:
        self.observations.check_writer()
        self._admit_query(query)
        self._requested.add(query)

    def declare(
        self,
        selector: ScopeSelector,
        expected: tuple[ProcedureProducer, ...],
        *,
        reachability: Reachability = "unknown",
        complete: bool = False,
        supported: bool = True,
    ) -> None:
        self.observations.check_writer()
        self._admit_query(FactQuery("outcomes", selector.module, selector.qualname))
        if selector.kind not in {"module", "procedure"} or reachability not in {
            "reachable",
            "unreachable",
            "lazy",
            "unknown",
        }:
            raise ValueError("Unknown catalog scope")
        if reachability == "unreachable" and (not complete or expected):
            raise ValueError("Unreachable scope needs a complete empty producer contract")
        selected: list[ProcedureProducer] = []
        seen: set[ProcedureProducer] = set()
        ledger = self.observations.resource_ledger
        for producer in expected:
            ledger.charge_work(1, "query_scope_producer")
            with ledger.structural_meter("query_scope_producer") as meter:
                for text in (producer.owner, producer.identity, producer.scope.scenario):
                    visit_json_text(text, meter.visit)
                for point in producer.scope.context:
                    visit_json_text(point, meter.visit)
                if producer.scope.site is not None:
                    visit_json_text(producer.scope.site, meter.visit)
            if ScopeSelector.of(producer.scope) != selector or producer in seen:
                raise ValueError("Catalog contains a duplicate or foreign producer")
            selected.append(producer)
            seen.add(producer)
        self._catalog[selector] = _Catalog(
            self.observations.epoch,
            self.observations.revision,
            tuple(selected),
            reachability,
            complete,
            supported,
        )
        self._catalog_revision += 1

    def close_inventory(self) -> None:
        # The integration owner must establish this from the actual source and
        # complete entry contract; absence of declarations never calls it here.
        self.observations.check_writer()
        self.observations.resource_ledger.charge_work(1, "query_inventory_close")
        self._inventory_epoch = self.observations.epoch
        self._inventory_revision = self.observations.revision
        self._inventory_catalog_revision = self._catalog_revision

    def _requested_query(self, query: FactQuery) -> bool:
        return (
            query in self._requested
            or FactQuery(query.view) in self._requested
            or (query.module is not None and FactQuery(query.view, query.module) in self._requested)
        )

    @staticmethod
    def _completion(records: tuple[ObservationView, ...]) -> CompletionKnowledge:
        kinds = {outcome.kind for record in records for outcome in record.payload.outcomes}
        normal = bool(kinds & {"normal", "return"})
        raised = "raise" in kinds
        suspended = "suspend" in kinds
        if sum((normal, raised, suspended)) > 1:
            return "mixed"
        if normal:
            return "normal"
        if raised:
            return "raise_only"
        if suspended:
            return "suspended"
        return "no_completion"

    def _scope(
        self, query: FactQuery, selector: ScopeSelector, catalog: _Catalog
    ) -> QueryResolution:
        if catalog.epoch != self.observations.epoch:
            return QueryResolution(
                query, "unavailable", "invalidated", reason="catalog_epoch_changed"
            )
        if (
            catalog.reachability in {"unreachable", "lazy"}
            and catalog.observation_revision != self.observations.revision
        ):
            # An explicit reachability declaration can supersede retained old
            # observations, but not a later publication. A new producer may
            # have reached a scope that the old entry contract excluded.
            return QueryResolution(
                query, "unavailable", "invalidated", reason="reachability_catalog_changed"
            )
        if not catalog.supported:
            return QueryResolution(
                query, "unavailable", "unsupported", reason="producer_capability_missing"
            )
        if catalog.reachability == "lazy":
            return QueryResolution(query, "unavailable", "lazy", reason="body_not_executed")
        if catalog.reachability == "unreachable":
            return QueryResolution(
                query, "answered", "unreachable", completion="unreachable", semantic_complete=True
            )
        records = tuple(
            row
            for row in self.observations.current(selector.module, selector.qualname)
            if row.head.producer.scope.kind == selector.kind
        )
        if not records:
            return QueryResolution(
                query, "unavailable", "unmaterialized", reason="no_procedure_observation"
            )
        if any(row.head.materialization == "invalidated" for row in records):
            return QueryResolution(
                query,
                "unavailable",
                "invalidated",
                records,
                reason="producer_observation_invalidated",
            )
        if (
            not catalog.complete
            or {row.head.producer for row in records} != set(catalog.expected)
            or any(not row.covering for row in records)
        ):
            return QueryResolution(
                query, "unavailable", "partial", records, reason="producer_scope_incomplete"
            )
        return QueryResolution(
            query,
            "answered",
            "complete",
            records,
            self._completion(records),
            semantic_complete=True,
        )

    def resolve(
        self, query: FactQuery, *, visible: bool = True, truncated: bool = False
    ) -> QueryResolution:
        self._admit_query(query)
        source_catalog_revision = self._catalog_revision
        if not self._requested_query(query):
            return QueryResolution(query, "not_requested", "not_requested")
        candidates: list[tuple[ScopeSelector, _Catalog]] = []
        module_known = query.module is None
        for selector, catalog in self._catalog.items():
            self.observations.resource_ledger.charge_work(1, "query_catalog_read")
            if selector.module == query.module:
                module_known = True
            if (
                (query.module is None or query.module == selector.module)
                and (query.qualname is None or query.qualname == selector.qualname)
                and (query.view == "outcomes" or selector.kind == "procedure")
            ):
                candidates.append((selector, catalog))
        if not candidates:
            complete_empty = query.qualname is None and self._inventory_current() and module_known
            result = QueryResolution(
                query,
                "answered" if complete_empty else "unavailable",
                "empty" if complete_empty else "unmaterialized",
                completion="no_completion" if complete_empty else "unknown",
                semantic_complete=complete_empty,
                reason=None if complete_empty else "query_scope_not_materialized",
            )
        else:
            scopes = [self._scope(query, selector, catalog) for selector, catalog in candidates]
            unavailable = next((scope for scope in scopes if not scope.covering), None)
            if unavailable is not None:
                result = unavailable
            elif query.qualname is not None:
                result = scopes[0]
            elif not self._inventory_current():
                result = QueryResolution(
                    query, "unavailable", "partial", reason="inventory_not_closed"
                )
            else:
                records = tuple(record for scope in scopes for record in scope.observations)
                result = QueryResolution(
                    query,
                    "answered",
                    "complete",
                    records,
                    self._completion(records),
                    semantic_complete=True,
                )
        if self._catalog_revision != source_catalog_revision:
            result = QueryResolution(
                query,
                "unavailable",
                "invalidated",
                result.observations,
                reason="query_catalog_changed_during_read",
            )
        if not visible or truncated:
            return QueryResolution(
                query,
                "unavailable",
                "excluded" if not visible else "truncated",
                result.observations,
                result.completion,
                result.semantic_complete,
                False,
                "output_selector_excluded" if not visible else "output_records_truncated",
            )
        return result

    def _inventory_current(self) -> bool:
        return (
            self._inventory_epoch == self.observations.epoch
            and self._inventory_revision == self.observations.revision
            and self._inventory_catalog_revision == self._catalog_revision
        )
