"""Actual ownership, paid reads and bounded terminal-publication controls."""

from __future__ import annotations

import io
import json
from concurrent.futures import ThreadPoolExecutor
from types import MappingProxyType

import pytest

from pythonstan import OutputSpec, ValueQuery
from pythonstan.analysis.contracts.json_data import JsonObject, require_object
from pythonstan.analysis.contracts.resources import ResourceStop, RunLedger
from pythonstan.analysis.domains.value_wire import decode_value, restore_value
from pythonstan.analysis.domains.values import Value, ValueOperations
from pythonstan.results.result import (
    AnalysisResult,
    PublishedSnapshot,
    QueryAnswer,
    _PublicationOwnership,
)


def _data() -> JsonObject:
    return {
        "actual_engine": "ai_primary",
        "solver_status": "converged",
        "coverage_status": "closed_under_profile",
        "proof_status": "not_requested",
        "effective_config": {"output": OutputSpec().to_dict()},
        "entry_contract": {"entry_coverage": "initialization_only"},
        "inventory": [{"module": "sample"}],
        "values": {"sample": {"result": Value.constant(17).export()}},
        "calls": [],
        "outcomes": {},
        "returns": {},
        "effects": {},
        "obligations": [],
        "stats": {"work": 1},
    }


def test_normal_publication_and_each_mutable_read_are_paid_and_owned() -> None:
    ledger = RunLedger(10000, 0, 0)
    data = _data()
    result = AnalysisResult(data, (ValueQuery("sample", "result"),), resource_ledger=ledger)
    first = ledger.work.used
    assert first > 0 and ledger.raw_structural["result_publication"] > 0
    answer = result.query(ValueQuery("sample", "result"))
    assert ledger.work.used > first
    value = require_object(answer.value)
    cost = ledger.work.used
    value["constants"] = [99]
    assert require_object(answer.value)["constants"] == [17]
    assert ledger.work.used > cost
    require_object(require_object(data["values"])["sample"])["result"] = Value.constant(3).export()
    assert require_object(result.query(ValueQuery("sample", "result")).value)["constants"] == [17]


def test_public_answer_constructor_never_adopts_a_foreign_frozen_wrapper() -> None:
    external = [17]
    wrapper = MappingProxyType({"constants": external})
    answer = QueryAnswer.create(
        ValueQuery("sample", "result"), "answered", wrapper, "flow_sensitive_may", "open"
    )
    external.append(99)
    assert answer.value == {"constants": [17]}


def test_freely_constructed_receipt_cannot_authorize_mutable_borrow() -> None:
    payload = MappingProxyType({"constants": [17]})
    document = MappingProxyType({"payload": payload})
    fake = PublishedSnapshot(
        document,
        "converged",
        "open",
        "not_requested",
        "ai_primary",
        _PublicationOwnership(document),
    )
    with pytest.raises(ValueError, match="ownership receipt"):
        QueryAnswer._from_published(
            ValueQuery("sample", "result"),
            "answered",
            payload,
            "flow_sensitive_may",
            "open",
            owner=fake,
            path=("payload",),
        )


def test_actual_receipt_is_bound_to_payload_identity_and_exact_path() -> None:
    owner = PublishedSnapshot.from_wire(_data())
    payload = require_object(_data()["values"])
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


def test_selection_excludes_large_unwanted_payload_before_paid_copy() -> None:
    data = _data()
    require_object(data["values"])["excluded"] = {"large": {"text": "x" * 100000}}
    ledger = RunLedger(100000, 0, 0)
    result = AnalysisResult(data, ("values",), resource_ledger=ledger)
    with ledger.work_scope(64, "small_projection"):
        output = result.to_dict(OutputSpec(views=("values",), modules=("sample",), max_items=1))
    answers = require_object(output["answers"])
    selected = require_object(require_object(answers["values"])["value"])
    assert set(selected) == {"sample"}
    assert not require_object(output["projection"])["truncated"]


def test_field_projection_marks_omission_and_preserves_covering_boundness() -> None:
    data = _data()
    original = Value(ints=Value.constant(17).ints, unbound=True)
    require_object(require_object(data["values"])["sample"])["result"] = original.export()
    result = AnalysisResult(data, (ValueQuery("sample", "result"),))
    projected = result.to_dict(OutputSpec(views=(), fields=("constants",)))
    answer = next(iter(require_object(projected["answers"]).values()))
    wire = require_object(require_object(answer)["value"])
    assert require_object(projected["projection"])["truncated"]
    assert require_object(wire["projection"])["complete"] is False
    restored = restore_value(wire)
    assert not restored.complete
    assert ValueOperations().leq(original, restored.retained_value)
    with pytest.raises(ValueError, match="Incomplete"):
        decode_value(wire)


@pytest.mark.parametrize("limit", [0, 2, 64, 4096])
def test_terminal_publication_is_bounded_typed_and_preserves_first_cause(limit: int) -> None:
    ledger = RunLedger(limit, 0, 0)
    permit = ledger.ensure_terminal_permit()
    ledger.stop("max_work")
    result = AnalysisResult.from_terminal(
        _data(),
        (ValueQuery("sample", "result"),),
        resource_ledger=ledger,
        terminal_permit=permit,
        reason="later_failure",
    )
    answer = result.query(ValueQuery("sample", "result"))
    if limit == 4096:
        assert require_object(answer.value)["constants"] == [17]
    else:
        assert answer.status == "unavailable" and answer.value is None
    raw = result.raw_snapshot()
    assert ledger.stop_reason == "max_work"
    assert require_object(raw["resource_ledger"])["stop_reason"] == "max_work"
    assert require_object(raw["projection"])["truncated"]
    assert ledger.work.used + ledger.work.reserved <= limit


def test_saved_terminal_answer_exhausts_to_unavailable_without_internal_exception() -> None:
    ledger = RunLedger(4096, 0, 0)
    permit = ledger.ensure_terminal_permit()
    ledger.stop("max_seconds")
    result = AnalysisResult.from_terminal(
        _data(), (ValueQuery("sample", "result"),), resource_ledger=ledger, terminal_permit=permit
    )
    saved = result.query(ValueQuery("sample", "result"))
    for _ in range(ledger.work.limit + 1):
        value = saved.value
        if saved.status == "unavailable":
            assert value is None
            break
    else:
        raise AssertionError("Each funded fresh read must consume at least one tick")
    assert saved.to_dict()["value"] is None
    assert ledger.stop_reason == "max_seconds"
    assert ledger.work.used + ledger.work.reserved <= ledger.work.limit


def test_normal_local_stage_read_denial_is_not_hidden_by_terminal_fallback() -> None:
    ledger = RunLedger(10000, 0, 0)
    result = AnalysisResult(_data(), (ValueQuery("sample", "result"),), resource_ledger=ledger)
    answer = result.query(ValueQuery("sample", "result"))
    with ledger.work_scope(0, "no_read"), pytest.raises(ResourceStop, match="stage_max_work"):
        _ = answer.value
    assert ledger.stop_reason is None


def test_normal_success_releases_only_known_unused_terminal_capacity() -> None:
    ledger = RunLedger(4096, 0, 0)
    permit = ledger.ensure_terminal_permit()
    result = AnalysisResult(_data(), (), resource_ledger=ledger, terminal_permit=permit)
    assert result.solver_status == "converged"
    assert permit.status == "settled" and permit.held == 0 and permit.observed == 0
    assert ledger.work.reserved == 0 and ledger.work.used > 0


def test_serialization_and_write_are_additional_paid_operations() -> None:
    ledger = RunLedger(10000, 0, 0)
    result = AnalysisResult(_data(), (ValueQuery("sample", "result"),), resource_ledger=ledger)
    before = ledger.work.used
    data = json.loads(result.serialize())
    assert ledger.work.used > before
    assert ledger.raw_structural["result_serialization"] > 0
    assert data["solver_status"] == "converged"
    stream = io.StringIO()
    result.write(stream)
    assert json.loads(stream.getvalue()) == data
    assert ledger.raw_structural["result_write"] > 0


def test_zero_fuel_controller_does_not_register_unbounded_query_scope() -> None:
    ledger = RunLedger(0, 0, 0)
    queries = tuple(ValueQuery("sample", f"result{index}") for index in range(257))
    result = AnalysisResult.from_terminal(None, queries, resource_ledger=ledger, reason="max_work")
    assert result._queries == () and result._query_keys == frozenset()
    assert result.query(queries[-1]).status == "unavailable"
    assert ledger.work.used == ledger.work.reserved == 0
    assert ledger.raw_structural == {}


def test_a_late_write_deadline_never_appends_a_second_fallback_document() -> None:
    clock = [0.0]
    ledger = RunLedger(10000, 1, 0, clock=lambda: clock[0])
    result = AnalysisResult(_data(), (), resource_ledger=ledger)

    class Destination:
        def __init__(self) -> None:
            self.text = ""
            self.calls = 0

        def write(self, text: str) -> int:
            self.calls += 1
            self.text += text
            clock[0] = 2.0
            return len(text)

    destination = Destination()
    with pytest.raises(ResourceStop, match="max_seconds"):
        result.write(destination)
    assert destination.calls == 1
    assert json.loads(destination.text)["solver_status"] == "converged"
    assert ledger.stop_reason == "max_seconds"


def test_funded_terminal_can_serialize_and_write_after_the_original_stop() -> None:
    ledger = RunLedger(4096, 0, 0)
    permit = ledger.ensure_terminal_permit()
    ledger.stop("max_seconds")
    result = AnalysisResult.from_terminal(
        _data(), (ValueQuery("sample", "result"),), resource_ledger=ledger, terminal_permit=permit
    )
    serialized = json.loads(result.serialize())
    assert serialized["solver_status"] == "budget_exhausted"
    assert serialized["coverage_status"] == "open"
    text = result.serialize("text")
    assert "solver=budget_exhausted; coverage=open" in text
    stream = io.StringIO()
    result.write(stream)
    assert json.loads(stream.getvalue())["coverage_status"] == "open"
    assert ledger.stop_reason == "max_seconds"
    assert ledger.work.used + ledger.work.reserved <= ledger.work.limit


def test_foreign_thread_public_reads_charge_same_ledger_without_execution_authority() -> None:
    def create() -> tuple[AnalysisResult, RunLedger]:
        ledger = RunLedger(2000, 1, 0)
        result = AnalysisResult(_data(), (ValueQuery("sample", "result"),), resource_ledger=ledger)
        return result, ledger

    with ThreadPoolExecutor(max_workers=1) as executor:
        result, ledger = executor.submit(create).result()
    before = ledger.work.used
    assert require_object(result.query(ValueQuery("sample", "result")).value)["constants"] == [17]
    assert result.to_dict()["solver_status"] == "converged"
    assert json.loads(result.serialize())["solver_status"] == "converged"
    assert ledger.work.used > before
    assert ledger.work.used + ledger.work.reserved <= 2000
    with pytest.raises(ValueError), ledger.work_scope(0, "foreign_execution"):
        raise AssertionError("A reader gained execution scope authority")
