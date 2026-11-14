"""Semantic callbacks may update facts, but cannot publish stale covering views."""

from __future__ import annotations

from dataclasses import replace

import pytest

from pythonstan.analysis.contracts.identity import VersionedDependency
from pythonstan.analysis.contracts.procedure_facts import ProcedureScope
from pythonstan.analysis.domains.values import Value
from pythonstan.analysis.observations.query_ledger import FactQuery, QueryLedger, ScopeSelector
from pythonstan.analysis.state.flow import ReturnFlow
from pythonstan.analysis.state.handles import StateHandle
from pythonstan.analysis.state.store import Store

from .test_query_resume2_q0 import head, service


def test_dependency_reentry_adds_owner_without_iterating_a_mutating_dictionary() -> None:
    store = service()
    store.dependency_check = lambda dependency: True
    metadata = replace(
        head(store, kind="checked_summary"),
        dependencies=(VersionedDependency("function", "f", "v1"),),
    )
    flow = ReturnFlow(StateHandle.capture(Store()), Value.constant(17))
    first = store.capture_detached(metadata, (flow,))
    store.publish_generation("unit:f", 1, (first,), application_accepted=True)
    other_head = replace(
        metadata,
        producer=replace(
            metadata.producer,
            owner="summary:g",
            identity="summary:g",
            scope=ProcedureScope("m", "m.g", "root-1"),
        ),
        dependencies=(),
    )
    other = store.capture_detached(other_head, (flow,))
    fired = False

    def dependency_current(dependency: VersionedDependency) -> bool:
        nonlocal fired
        if not fired:
            fired = True
            store.publish_generation("summary:g", 1, (other,), application_accepted=True)
        return True

    store.dependency_check = dependency_current
    result = store.current("m", "m.f")
    assert fired and result and not result[0].covering
    assert result[0].reason == "publication_changed_during_read"
    assert store.current("m", "m.g")[0].covering


def test_generation_prepare_cannot_roll_back_a_reentrant_newer_generation() -> None:
    store = service()
    store.dependency_check = lambda dependency: True
    metadata = replace(
        head(store, kind="checked_summary"),
        dependencies=(VersionedDependency("function", "f", "v1"),),
    )
    old = store.capture_detached(
        metadata, (ReturnFlow(StateHandle.capture(Store()), Value.constant(17)),)
    )
    newer_head = replace(metadata, version=replace(metadata.version, generation=2), dependencies=())
    newer = store.capture_detached(
        newer_head, (ReturnFlow(StateHandle.capture(Store()), Value.constant(19)),)
    )
    fired = False

    def dependency_current(dependency: VersionedDependency) -> bool:
        nonlocal fired
        if not fired:
            fired = True
            store.publish_generation("unit:f", 2, (newer,), application_accepted=True)
        return True

    store.dependency_check = dependency_current
    with pytest.raises(ValueError, match="changed"):
        store.prepare_generation("unit:f", 1, (old,))
    current = store.current("m", "m.f")[0]
    assert current.covering and current.head.version.generation == 2
    assert current.payload.outcomes[0].value.exact_constants() == [19]


def test_catalog_reentry_cannot_return_an_old_complete_query_contract() -> None:
    store = service()
    store.dependency_check = lambda dependency: True
    metadata = replace(
        head(store, kind="checked_summary"),
        dependencies=(VersionedDependency("function", "f", "v1"),),
    )
    fact = store.capture_detached(
        metadata, (ReturnFlow(StateHandle.capture(Store()), Value.constant(17)),)
    )
    store.publish_generation("unit:f", 1, (fact,), application_accepted=True)
    ledger = QueryLedger(store)
    query = FactQuery("returns", "m", "m.f")
    selector = ScopeSelector("m", "m.f")
    ledger.request(query)
    ledger.declare(selector, (metadata.producer,), complete=True)
    fired = False

    def dependency_current(dependency: VersionedDependency) -> bool:
        nonlocal fired
        if not fired:
            fired = True
            ledger.declare(selector, (), supported=False)
        return True

    store.dependency_check = dependency_current
    answer = ledger.resolve(query)
    assert fired and not answer.covering and answer.status == "unavailable"
    assert answer.reason == "query_catalog_changed_during_read"
    assert ledger.resolve(query).scope_status == "unsupported"
