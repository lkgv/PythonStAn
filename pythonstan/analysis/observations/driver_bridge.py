"""Actual solver producer lifetimes and paid procedure-query finalization.

This adapter observes real Units and accepted finite summary applications. It
never creates a Unit for a summary, and never treats debug events as semantics.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from pythonstan.analysis.contracts.identity import (
    ContextId,
    IdentityEpoch,
    ObjectId,
    ProgramPointId,
    SemanticIdentity,
    StateRevision,
    UnitId,
)
from pythonstan.analysis.contracts.json_data import JsonObject, JsonValue
from pythonstan.analysis.contracts.procedure_facts import (
    FactPublication,
    FactVersion,
    ProcedureFactHead,
    ProcedureProducer,
    ProcedureScope,
)
from pythonstan.analysis.contracts.resources import RunLedger
from pythonstan.analysis.state.flow import Flow
from pythonstan.analysis.state.store import FunctionInfo, Unit

from .procedures import ObservationView, ProcedureObservationStore, ProcedurePayload
from .query_ledger import FactQuery, QueryLedger, Reachability, ScopeSelector


@dataclass(frozen=True, slots=True)
class SummaryProcedureExit:
    """A finite source procedure exit from one accepted provider application."""

    module: str
    qualname: str
    context: ContextId
    site: ProgramPointId
    outcomes: tuple[Flow, ...]


def procedure_requests(queries: JsonValue) -> tuple[FactQuery, ...]:
    if not isinstance(queries, list):
        return ()
    selected: list[FactQuery] = []
    for query in queries:
        if not isinstance(query, dict):
            continue
        if query.get("kind") in {"returns", "outcomes"}:
            selected.append(FactQuery("returns" if query["kind"] == "returns" else "outcomes"))
        elif query.get("kind") == "value" and query.get("point") == "function_exit":
            module, symbol = query.get("module"), query.get("symbol")
            if isinstance(module, str) and isinstance(symbol, str):
                selected.append(FactQuery("returns", module, module + "." + symbol))
    return tuple(selected)


class DriverProcedureBridge:
    def __init__(
        self,
        identity: SemanticIdentity,
        epoch: IdentityEpoch,
        resource_ledger: RunLedger,
        requests: tuple[FactQuery, ...],
    ) -> None:
        self.store = ProcedureObservationStore(identity, epoch, resource_ledger)
        self.requests = requests
        self._unit_producers: dict[UnitId, ProcedureProducer] = {}
        self._summary_producers: dict[str, tuple[ProcedureProducer, ...]] = {}
        self._staged: dict[ProcedureProducer, FactPublication[ProcedurePayload]] = {}
        self._active: Unit | None = None
        self._lazy: set[ScopeSelector] = set()
        self._unsupported: set[ScopeSelector] = set()

    @staticmethod
    def summary_owner(unit: Unit) -> str:
        return unit.key + ":accepted_procedures"

    def register(self, unit: Unit, scenario: str) -> None:
        self.store.register_unit(unit)
        scope = ProcedureScope(
            unit.frame.module,
            unit.frame.qualname,
            scenario,
            unit.frame.context,
            kind="module" if unit.is_module else "procedure",
        )
        self._unit_producers[unit.key] = ProcedureProducer("unit", unit.key, unit.key, scope)

    def pending(self, unit: Unit) -> None:
        generation = unit.visits + 1
        self.store.start_generation(unit.key, generation)
        self.store.start_generation(self.summary_owner(unit), generation)

    def begin(self, unit: Unit) -> None:
        self.pending(unit)
        self._active = unit
        self._staged = {}

    def accepted(self, exits: Sequence[SummaryProcedureExit]) -> None:
        unit = self._active
        if unit is None:
            raise ValueError("Accepted procedure facts require an active real caller")
        owner = self.summary_owner(unit)
        for record in exits:
            self.store.resource_ledger.charge_work(1, "procedure_summary_batch")
            scope = ProcedureScope(
                record.module,
                record.qualname,
                self._unit_producers[unit.key].scope.scenario,
                record.context,
                record.site,
            )
            producer = ProcedureProducer(
                "checked_summary",
                owner,
                unit.key + ":" + record.site + ":" + record.qualname,
                scope,
            )
            head = ProcedureFactHead(
                producer,
                FactVersion(unit.visits, StateRevision(unit.visits), self.store.epoch),
                self.store.identity,
            )
            previous = self._staged.get(producer)
            outcomes = record.outcomes
            if previous is not None:
                self.store.resource_ledger.charge_work(
                    len(previous.payload.outcomes) + len(outcomes), "procedure_summary_edges"
                )
                outcomes = (*previous.payload.outcomes, *outcomes)
            self._staged[producer] = self.store.capture_detached(head, outcomes)

    def finish(self, unit: Unit, *, pending: bool) -> None:
        # A recursive/current caller may have queued its next entry while this
        # visit ran. Its old generation must remain noncovering until that visit.
        if not pending:
            producer = self._unit_producers[unit.key]
            head = ProcedureFactHead(
                producer,
                FactVersion(unit.visits, unit.revision, self.store.epoch),
                self.store.identity,
            )
            fact = self.store.capture_unit(head, unit)
            self.store.publish_generation(unit.key, unit.visits, (fact,))
            owner = self.summary_owner(unit)
            facts = tuple(self._staged.values())
            self.store.resource_ledger.charge_work(len(facts), "procedure_summary_batch")
            self.store.publish_generation(owner, unit.visits, facts, application_accepted=True)
            self._summary_producers[owner] = tuple(fact.head.producer for fact in facts)
        self._active = None
        self._staged = {}

    def lazy(self, info: FunctionInfo) -> None:
        self.store.resource_ledger.charge_work(1, "procedure_lazy_scope")
        self._lazy.add(ScopeSelector(info.module, info.qualname))

    def unsupported(self, info: FunctionInfo) -> None:
        self.unsupported_scope(info.module, info.qualname)

    def unsupported_scope(self, module: str, qualname: str) -> None:
        self.store.resource_ledger.charge_work(1, "procedure_unsupported_scope")
        self._unsupported.add(ScopeSelector(module, qualname))

    def epoch_committed(self) -> None:
        # Accepted facts from a suspended transfer belong to its old epoch.
        self._staged = {}

    def finalize(
        self,
        functions: Mapping[ObjectId, FunctionInfo],
        *,
        producer_catalog_complete: bool,
    ) -> tuple[tuple[ObservationView, ...], JsonObject]:
        """Finalize under the owner's stable producer lifetime.

        The host supplies an explicit complete call-production contract. A
        converged counter or an empty observation collection is insufficient.
        """
        ledger = QueryLedger(self.store)
        for query in self.requests:
            ledger.request(query)
        catalog: dict[ScopeSelector, list[ProcedureProducer]] = {}
        for producer in self._unit_producers.values():
            self.store.resource_ledger.charge_work(1, "procedure_catalog")
            catalog.setdefault(ScopeSelector.of(producer.scope), []).append(producer)
        for producers in self._summary_producers.values():
            for producer in producers:
                self.store.resource_ledger.charge_work(1, "procedure_catalog")
                catalog.setdefault(ScopeSelector.of(producer.scope), []).append(producer)
        for info in functions.values():
            self.store.resource_ledger.charge_work(1, "procedure_catalog")
            catalog.setdefault(ScopeSelector(info.module, info.qualname), [])
        for selector, scope_producers in catalog.items():
            reachability: Reachability
            supported = selector not in self._unsupported
            if scope_producers:
                reachability = "reachable"
            elif selector in self._lazy:
                reachability = "lazy"
            elif producer_catalog_complete and supported:
                reachability = "unreachable"
            else:
                reachability = "unknown"
            ledger.declare(
                selector,
                tuple(scope_producers),
                reachability=reachability,
                complete=producer_catalog_complete,
                supported=supported,
            )
        if producer_catalog_complete:
            ledger.close_inventory()
        rows: list[JsonValue] = []
        # A bulk request also authorizes the corresponding concrete single view.
        queries = list(self.requests)
        for query in self.requests:
            if query.qualname is None:
                for selector in catalog:
                    self.store.resource_ledger.charge_work(1, "procedure_query_scope")
                    if query.view == "returns" and selector.kind == "module":
                        continue
                    queries.append(FactQuery(query.view, selector.module, selector.qualname))
        observations = tuple(
            row
            for selector in catalog
            for row in self.store.current(selector.module, selector.qualname)
            if row.head.producer.scope.kind == selector.kind
        )
        for query in queries:
            resolution = ledger.resolve(query)
            has_positive_facts = False
            for record in observations:
                self.store.resource_ledger.charge_work(1, "procedure_query_positive_facts")
                scope = record.head.producer.scope
                if (
                    record.head.materialization == "invalidated"
                    or query.module is not None
                    and query.module != scope.module
                    or query.qualname is not None
                    and query.qualname != scope.qualname
                    or query.view == "returns"
                    and scope.kind == "module"
                ):
                    continue
                for outcome in record.payload.outcomes:
                    self.store.resource_ledger.charge_work(1, "procedure_query_positive_facts")
                    if query.view == "outcomes" or outcome.kind in {"return", "raise"}:
                        has_positive_facts = True
            rows.append(
                {
                    "view": query.view,
                    "module": query.module,
                    "qualname": query.qualname,
                    "status": resolution.status,
                    "scope_status": resolution.scope_status,
                    "coverage_status": resolution.coverage_status,
                    "completion": resolution.completion,
                    "reason": resolution.reason,
                    "has_positive_facts": has_positive_facts,
                }
            )
        return observations, {"version": 1, "queries": rows}
