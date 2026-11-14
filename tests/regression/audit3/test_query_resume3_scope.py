"""Fresh scope catalog boundaries after procedure publication changes."""

from __future__ import annotations

from dataclasses import replace

from pythonstan.analysis.contracts.identity import CellId
from pythonstan.analysis.contracts.procedure_facts import ProcedureScope
from pythonstan.analysis.domains.values import Value
from pythonstan.analysis.observations.query_ledger import FactQuery, QueryLedger, ScopeSelector
from pythonstan.analysis.state.flow import ReturnFlow
from pythonstan.analysis.state.handles import StateHandle
from pythonstan.analysis.state.store import Store

from .test_query_resume2_q0 import head, publish_unit, service, unit


def test_new_producer_invalidates_a_previously_complete_empty_inventory() -> None:
    store = service()
    ledger = QueryLedger(store)
    query = FactQuery("returns")
    ledger.request(query)
    ledger.close_inventory()
    assert ledger.resolve(query).covering
    publish_unit(store, unit())
    result = ledger.resolve(query)
    assert result.status == "unavailable" and not result.covering


def test_explicit_known_empty_module_can_answer_empty_returns() -> None:
    ledger = QueryLedger(service())
    query = FactQuery("returns", "m")
    ledger.request(query)
    ledger.declare(ScopeSelector("m", "m", "module"), (), complete=True)
    ledger.close_inventory()
    result = ledger.resolve(query)
    assert result.status == "answered" and result.covering and result.scope_status == "empty"


def test_new_uncatalogued_summary_cannot_be_omitted_by_a_closed_bulk_answer() -> None:
    store = service()
    first = publish_unit(store, unit())
    ledger = QueryLedger(store)
    query = FactQuery("returns")
    specific = FactQuery("returns", "m", "m.f")
    ledger.request(query)
    ledger.declare(ScopeSelector("m", "m.f"), (first.producer,), complete=True)
    ledger.close_inventory()
    assert ledger.resolve(query).covering
    metadata = head(store, kind="checked_summary")
    metadata = replace(
        metadata,
        producer=replace(
            metadata.producer,
            owner="summary:g",
            identity="summary:g",
            scope=ProcedureScope("m", "m.g", "root-1"),
        ),
    )
    root = Store(cells={CellId("global:m:x"): Value.constant(99)})
    fact = store.capture_detached(
        metadata, (ReturnFlow(StateHandle.capture(root), Value.constant(99)),)
    )
    store.publish_generation("summary:g", 1, (fact,), application_accepted=True)
    assert ledger.resolve(specific).covering
    result = ledger.resolve(query)
    assert result.status == "unavailable" and not result.covering


def test_refreshing_catalog_and_inventory_covers_new_scope() -> None:
    store = service()
    producer = unit()
    metadata = publish_unit(store, producer)
    ledger = QueryLedger(store)
    query = FactQuery("returns")
    ledger.request(query)
    ledger.close_inventory()
    ledger.declare(ScopeSelector("m", "m.f"), (metadata.producer,), complete=True)
    ledger.close_inventory()
    result = ledger.resolve(query)
    assert result.covering and len(result.observations) == 1
