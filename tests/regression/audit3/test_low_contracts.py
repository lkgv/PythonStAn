"""Actual low-contract codec, budget, and detached publication obligations."""

from __future__ import annotations

from collections.abc import Mapping, MutableMapping
from dataclasses import fields
from typing import get_type_hints

import pytest

from pythonstan.analysis.composition.oracle import IncrementalCandidateOracle
from pythonstan.analysis.contracts.budgets import AnalysisBudget, validate_budget_options
from pythonstan.analysis.contracts.engine import EngineSnapshot
from pythonstan.analysis.contracts.identity import (
    CellId,
    EntryScenario,
    ObjectId,
    SourceModule,
    UnitId,
)
from pythonstan.analysis.contracts.json_data import JsonObject, require_object
from pythonstan.analysis.contracts.oracle import CandidateAnswer
from pythonstan.analysis.contracts.wire import freeze_snapshot, thaw_snapshot, value_snapshot
from pythonstan.analysis.domains.values import Value
from pythonstan.analysis.engines.abstract.driver import InterproceduralDriver


def test_all_historical_budget_fields_are_projected_and_validated_without_coercion() -> None:
    defaults = AnalysisBudget().to_dict()
    expected = {
        "max_iterations": 100,
        "max_stmt_visits": 50,
        "max_recursion_depth": 3,
        "strict_exceptions": False,
        "max_addr_set_size": 50,
        "max_field_keys": 20,
        "max_const_strings": 10,
        "max_ho_tokens": 20,
        "enable_narrowing": True,
        "narrowing_iterations": 2,
        "enable_value_partition": True,
        "max_value_partitions": 4,
        "value_partition_str_cap": 4,
        "max_const_ints": 4,
        "max_tuple_len_exact": 8,
        "max_tuple_alts": 4,
        "max_star_expand_len": 8,
        "max_star_expand_alts": 4,
        "max_dict_parts": 20,
        "max_dict_key_const_strs": 10,
        "max_dict_key_const_ints": 10,
        "max_dict_key_tuple_len": 4,
        "max_dict_key_tuple_alts": 2,
        "enable_dict_identity_keys": True,
        "max_dict_identity_keys": 10,
    }
    assert len(defaults) == 25
    assert defaults == expected
    assert set(defaults) == {field.name for field in fields(AnalysisBudget)}
    assert validate_budget_options({"max_iterations": 0}) == {"max_iterations": 0}
    assert validate_budget_options({"enable_narrowing": False}) == {"enable_narrowing": False}
    with pytest.raises(ValueError, match="type int"):
        validate_budget_options({"max_iterations": True})
    with pytest.raises(ValueError, match="supported range"):
        validate_budget_options({"max_const_strings": 0})
    with pytest.raises(ValueError, match="Unknown"):
        validate_budget_options({"misspelled_limit": 1})


def test_snapshot_preserves_both_readonly_values_interfaces_and_complete_wire() -> None:
    source: JsonObject = {"example": {"result": {"constants": [None, True, 1]}}}
    snapshot = EngineSnapshot("ai_primary", "converged", "closed_under_profile", values=source)
    assert isinstance(snapshot, Mapping)
    frozen_values = snapshot["values"]
    assert isinstance(frozen_values, Mapping)
    assert snapshot.values["example"] == frozen_values["example"]
    assert list(snapshot.values()) == [snapshot[key] for key in snapshot]
    assert snapshot.guarantees.solver_status == snapshot.solver_status
    assert snapshot.guarantees.proof_status == "not_requested"
    wire = snapshot.to_dict()
    assert EngineSnapshot.from_wire(wire).to_dict() == wire
    require_object(require_object(wire["values"])["example"])["result"] = None
    assert snapshot.values["example"] == frozen_values["example"]
    assert not isinstance(frozen_values, MutableMapping)
    with pytest.raises(ValueError, match="Missing snapshot fields"):
        EngineSnapshot.from_wire(
            {"engine": "ai_primary", "solver_status": "converged", "coverage_status": "open"}
        )
    wire["values"] = None
    with pytest.raises(TypeError, match="object field"):
        EngineSnapshot.from_wire(wire)


def test_wire_preserves_scalar_tags_empty_presence_and_shared_acyclic_data() -> None:
    assert get_type_hints(freeze_snapshot)["return"] is not object
    shared: list[object] = [None, True, 1]
    assert thaw_snapshot(freeze_snapshot({"left": shared, "right": shared})) == {
        "left": [None, True, 1],
        "right": [None, True, 1],
    }
    cycle: list[object] = []
    cycle.append(cycle)
    with pytest.raises(ValueError, match="reference cycle"):
        freeze_snapshot(cycle)
    with pytest.raises(ValueError, match="reference cycle"):
        thaw_snapshot(cycle)
    value = value_snapshot(constants=(None, True, 1), items=())
    assert value["constants"] == (None, True, 1)
    assert value["tagged_constants"] == (
        {"type": "none", "value": None},
        {"type": "bool", "value": True},
        {"type": "int", "value": 1},
    )
    assert value["items"] == ()
    assert "items" not in value_snapshot()


def test_actual_detached_oracle_update_is_billed_issuer_bound_and_committed_once() -> None:
    oracle = IncrementalCandidateOracle(source_digest="source", profile="python-core-v1")
    cell = CellId("global:example:value")
    oracle.publish_cell(cell, Value.constant(1))
    original = oracle.kernel
    billed: list[int] = []
    first = oracle.prepare_reabstract({}, charge=billed.append)
    assert oracle.kernel is original
    assert first.work_precharged is True
    assert first.work == sum(billed) > 0
    first.commit()
    assert oracle.kernel is first.kernel
    second = oracle.prepare_reabstract({})
    assert second.work_precharged is False
    second.commit()
    assert oracle.kernel is second.kernel
    first.commit()
    assert oracle.kernel is second.kernel
    assert oracle.candidates(cell).values.exact_constants() == [1]
    foreign = IncrementalCandidateOracle(source_digest="source", profile="python-core-v1")
    with pytest.raises(ValueError, match="issuer"):
        foreign.commit_reabstract(first)


class _CandidateOnly:
    def __init__(self) -> None:
        self.reads = 0

    def candidates(self, cell: CellId, consumer: UnitId | None = None) -> CandidateAnswer[Value]:
        self.reads += 1
        return CandidateAnswer(Value.bottom(), "partial", 0, (), "readonly-candidates")

    def statistics(self) -> JsonObject:
        return {"coverage": "partial", "permits_exclusion": False, "reads": self.reads}


def test_candidate_only_real_host_survives_address_epoch_without_lifecycle_hooks() -> None:
    module = SourceModule(
        "example",
        "example.py",
        "try:\n    missing\nexcept NameError:\n    pass\nvalue = 1\nresult = value\n",
    )
    candidate = _CandidateOnly()
    driver = InterproceduralDriver((module,), oracle=candidate)
    before = driver.run((EntryScenario("example"),)).to_dict()
    reads = candidate.reads
    assert reads > 0
    driver.reabstract_addresses({ObjectId("unused:old"): ObjectId("unused:summary")})
    assert driver.ops.arena.epoch == 1
    assert driver.oracle is candidate
    assert driver.incremental_oracle is None
    after = driver.run((EntryScenario("example"),)).to_dict()
    assert candidate.reads > reads
    assert after["values"] == before["values"]
    assert after["coverage_status"] == before["coverage_status"] == "closed_under_profile"


def test_installer_rejects_mixed_producers_and_candidate_assignment_clears_lifecycle() -> None:
    driver = InterproceduralDriver((SourceModule("example", "example.py", "result = 1\n"),))
    producer = IncrementalCandidateOracle(
        source_digest=driver.source_digest, profile=driver.semantic_profile
    )
    unrelated = IncrementalCandidateOracle(
        source_digest=driver.source_digest, profile=driver.semantic_profile
    )
    with pytest.raises(ValueError, match="installed candidate"):
        driver.install_oracle(producer, incremental=unrelated)
    driver.install_oracle(producer, incremental=producer)
    assert driver.incremental_oracle is producer
    candidate = _CandidateOnly()
    driver.oracle = candidate
    assert driver.oracle is candidate
    assert driver.incremental_oracle is None
