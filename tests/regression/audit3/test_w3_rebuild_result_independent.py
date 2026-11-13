"""New ownership, selection and exhausted-publication API witnesses."""

from __future__ import annotations

from dataclasses import replace
from types import MappingProxyType

import pytest

from pythonstan import OutputSpec, ValueQuery
from pythonstan.analysis.contracts.json_data import JsonObject, require_array, require_object
from pythonstan.analysis.contracts.resources import ResourceStop, RunLedger
from pythonstan.analysis.domains.value_wire import decode_value, restore_value
from pythonstan.analysis.domains.values import Value, ValueOperations
from pythonstan.results.result import (
    AnalysisResult,
    PublishedSnapshot,
    QueryAnswer,
    _PublicationOwnership,
)


def _document() -> JsonObject:
    return {
        "actual_engine": "ai_primary",
        "solver_status": "converged",
        "coverage_status": "closed_under_profile",
        "proof_status": "not_requested",
        "effective_config": {"output": OutputSpec().to_dict()},
        "entry_contract": {"entry_coverage": "initialization_only"},
        "inventory": [{"module": "sample"}],
        "values": {"sample": {"result": Value.constant(17).export()}},
        "stats": {"work": 1},
        "calls": [],
        "returns": {},
        "effects": {},
        "outcomes": {},
        "obligations": [],
    }


def test_only_issued_receipt_exact_document_and_payload_identity_can_skip_a_copy() -> None:
    external = [17]
    foreign_payload = MappingProxyType({"constants": external})
    foreign_document = MappingProxyType({"payload": foreign_payload})
    legitimate = PublishedSnapshot.from_wire(_document())
    fake = PublishedSnapshot(
        foreign_document,
        "converged",
        "open",
        "not_requested",
        "ai_primary",
        _PublicationOwnership(foreign_document),
    )
    forged_by_replacement = replace(legitimate, document=foreign_document)
    for owner in (fake, forged_by_replacement):
        with pytest.raises(ValueError, match="ownership receipt"):
            QueryAnswer._from_published(
                ValueQuery("sample", "result"),
                "answered",
                foreign_payload,
                "flow_sensitive_may",
                "open",
                owner=owner,
                path=("payload",),
            )


def test_genuine_adoption_only_freezes_new_header_and_owns_later_mutable_exports() -> None:
    data = _document()
    data["payload"] = [1] * 5000
    owner = PublishedSnapshot.from_wire(data)
    payload = owner.document["payload"]
    visits: list[int] = []
    answer = QueryAnswer._from_published(
        ValueQuery("sample", "result"),
        "answered",
        payload,
        "flow_sensitive_may",
        "open",
        owner=owner,
        path=("payload",),
        visit=visits.append,
    )
    assert sum(visits) < 100
    exported = require_array(answer.value)
    assert len(exported) == 5000
    exported[0] = 99
    assert require_array(answer.value)[0] == 1
    with pytest.raises(ValueError, match="ownership receipt"):
        QueryAnswer._from_published(
            ValueQuery("sample", "result"),
            "answered",
            payload,
            "flow_sensitive_may",
            "open",
            owner=owner,
            path=("values",),
        )


def test_public_answer_factory_always_detaches_a_foreign_frozen_wrapper() -> None:
    values = [17]
    supplied = MappingProxyType({"values": values})
    answer = QueryAnswer.create(
        ValueQuery("sample", "result"), "answered", supplied, "flow_sensitive_may", "open"
    )
    values.append(99)
    assert answer.value == {"values": [17]}


def test_normal_success_releases_unused_reserve_but_failed_checked_publication_does_not() -> None:
    ledger = RunLedger(4096, 0, 0, memory_reader=lambda: 0)
    permit = ledger.ensure_terminal_permit()
    result = AnalysisResult(_document(), (), resource_ledger=ledger, terminal_permit=permit)
    assert result.solver_status == "converged"
    assert permit.status == "settled" and permit.observed == 0 and permit.held == 0
    assert ledger.work.used > 0 and ledger.work.reserved == 0
    failed = RunLedger(4096, 0, 0, memory_reader=lambda: 0)
    held = failed.ensure_terminal_permit()
    bad = _document()
    bad["actual_engine"] = 1
    with pytest.raises((TypeError, ValueError)):
        AnalysisResult(bad, (), resource_ledger=failed, terminal_permit=held)
    assert held.status == "reserved" and held.held == 128
    assert failed.work.used > 0 and failed.work.used + failed.work.reserved <= 4096


def test_projection_selects_before_copy_and_omission_roundtrip_is_covering() -> None:
    data = _document()
    original = replace(Value.constant(17), unbound=True)
    require_object(require_object(data["values"])["sample"])["result"] = original.export()
    require_object(data["values"])["excluded"] = {"large": {"text": "x" * 1000000}}
    ledger = RunLedger(100000, 0, 0, memory_reader=lambda: 0)
    result = AnalysisResult(
        data, (ValueQuery("sample", "result"), "values"), resource_ledger=ledger
    )
    with ledger.work_scope(64, "small_only"):
        projected = result.to_dict(
            OutputSpec(views=("values",), modules=("sample",), fields=("constants",), max_items=1)
        )
    assert require_object(projected["projection"])["truncated"]
    answers = require_object(projected["answers"])
    for answer in answers.values():
        row = require_object(answer)
        if require_object(row["query"])["kind"] == "value":
            wire = require_object(row["value"])
            assert require_object(wire["projection"])["complete"] is False
            fragment = restore_value(wire)
            assert not fragment.complete
            assert ValueOperations().leq(original, fragment.retained_value)
            with pytest.raises(ValueError, match="[Ii]ncomplete"):
                decode_value(wire)


def test_ordinary_normal_read_denial_is_not_laundered_as_a_terminal_fallback() -> None:
    ledger = RunLedger(4096, 0, 0, memory_reader=lambda: 0)
    result = AnalysisResult(_document(), (ValueQuery("sample", "result"),), resource_ledger=ledger)
    answer = result.query(ValueQuery("sample", "result"))
    with ledger.work_scope(0, "zero"), pytest.raises(ResourceStop, match="stage_max_work"):
        _ = answer.value
    assert ledger.stop_reason is None


@pytest.mark.parametrize("limit", [0, 2, 64, 4096])
def test_terminal_publication_preserves_first_cause_and_never_copies_unrequested_bulk(
    limit: int,
) -> None:
    ledger = RunLedger(limit, 0, 0, memory_reader=lambda: 0)
    permit = ledger.ensure_terminal_permit()
    ledger.stop("max_seconds")
    data = _document()
    data["effective_config"] = {"bulk_omitted": "x" * 1000000}
    data["proof"] = {"bulk_omitted": [0] * 100000}
    result = AnalysisResult.from_terminal(
        data,
        (ValueQuery("sample", "result"),),
        resource_ledger=ledger,
        terminal_permit=permit,
        reason="later_reason",
    )
    raw = result.raw_snapshot()
    assert require_object(raw["resource_ledger"])["stop_reason"] == "max_seconds"
    assert ledger.stop_reason == "max_seconds"
    for key in ("effective_config", "proof"):
        assert "bulk_omitted" not in require_object(raw.get(key, {}))
    assert require_object(raw["projection"])["truncated"]
    answer = result.query(ValueQuery("sample", "result"))
    if answer.status == "answered":
        assert require_object(answer.value)["constants"] == [17]
    else:
        assert answer.status == "unavailable" and answer.value is None
    assert ledger.work.used + ledger.work.reserved <= limit


def test_exhausted_saved_terminal_mapping_getters_remain_bounded_and_typed() -> None:
    ledger = RunLedger(512, 0, 0, memory_reader=lambda: 0)
    permit = ledger.ensure_terminal_permit()
    ledger.stop("max_seconds")
    result = AnalysisResult.from_terminal(
        _document(),
        (ValueQuery("sample", "result"),),
        resource_ledger=ledger,
        terminal_permit=permit,
    )
    saved = result.query(ValueQuery("sample", "result"))
    for _ in range(513):
        _ = saved.value
        if saved.status == "unavailable":
            break
    else:
        raise AssertionError("A funded fresh export must consume a positive tick")
    assert saved["query"] == {"kind": "value"}
    mapped = dict(saved)
    assert mapped["status"] == "unavailable" and mapped["value"] is None
    assert saved.to_dict()["value"] is None
    assert ledger.stop_reason == "max_seconds"
    assert ledger.work.used + ledger.work.reserved <= 512


def test_zero_fuel_fixed_controller_does_not_resolve_or_retain_unbounded_query_registration() -> (
    None
):
    ledger = RunLedger(0, 0, 0, memory_reader=lambda: 0)
    permit = ledger.ensure_terminal_permit()
    ledger.stop("max_work")
    queries = tuple(ValueQuery("sample", "v" + str(index)) for index in range(257))
    result = AnalysisResult.from_terminal(
        None, queries, resource_ledger=ledger, terminal_permit=permit
    )
    assert result._controller_only
    assert result._queries == () and result._query_keys == frozenset()
    answer = result.query(queries[-1])
    assert answer.status == "unavailable" and answer.value is None
    assert ledger.work.used == ledger.work.reserved == 0
