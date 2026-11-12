"""Behavioral obligations of actual immutable policy and optional oracle ports."""

from __future__ import annotations

import ast
from collections.abc import Mapping

import pytest

from pythonstan.analysis.composition import native_snapshot
from pythonstan.analysis.composition.plan import RegionDecision, build_plan
from pythonstan.analysis.contracts import json_data
from pythonstan.analysis.contracts.identity import CellId, UnitId
from pythonstan.analysis.contracts.json_data import (
    FrozenJsonValue,
    JsonObject,
    JsonValue,
    require_array,
    require_object,
    validate_json_object,
)
from pythonstan.analysis.contracts.oracle import CandidateAnswer, CandidateOraclePort
from pythonstan.analysis.domains.values import Value
from pythonstan.analysis.engines.abstract.driver import InterproceduralDriver
from pythonstan.config import resolve_config
from pythonstan.frontend.source import source_node_identity
from pythonstan.inputs import Source, snapshot_target


def test_source_bound_decision_is_typed_and_projection_cannot_mutate_policy() -> None:
    code = "def f():\n    value = 1\n    return value\nresult = f()\n"
    config = resolve_config(
        config={
            "engine": "ai_primary",
            "regions": [
                {"selector": {"qualname": "example.f"}, "abstract": {"max_const_strings": 12}}
            ],
        }
    )
    snapshot = snapshot_target(Source(code, module="example"), config)
    plan = build_plan(snapshot, config)
    module = snapshot.modules[0]
    tree = ast.parse(module.source)
    function = tree.body[0]
    assert isinstance(function, ast.FunctionDef)
    point = source_node_identity(module.module, module.digest, function.body[0])
    decision = plan.decision("example", "example.f", point)
    assert isinstance(decision, RegionDecision)
    assert decision.abstract["max_const_strings"] == 12
    projection = plan.to_dict()
    exported = require_object(require_object(projection["decisions"])[point])
    require_object(exported["abstract"])["max_const_strings"] = 99
    assert plan.decision("example", "example.f", point).abstract["max_const_strings"] == 12
    assert plan.decisions[point] is decision


class _EmptyCandidates:
    """The supported minimal provider intentionally has no lifecycle hooks."""

    reads: int

    def __init__(self) -> None:
        self.reads = 0

    def candidates(self, cell: CellId, consumer: UnitId | None = None) -> CandidateAnswer[Value]:
        self.reads += 1
        return CandidateAnswer(Value.bottom(), "partial", 0, (), "empty-partial-provider")

    def statistics(self) -> JsonObject:
        return {"coverage": "partial", "permits_exclusion": False, "candidate_reads": self.reads}


def _run(provider: CandidateOraclePort[Value] | None) -> JsonObject:
    config = resolve_config(config={"engine": "ai_primary", "resources": {"max_seconds": 0}})
    snapshot = snapshot_target(
        Source(
            "try:\n    missing\nexcept NameError:\n    pass\ndef f():\n    return 5\nresult = f()\n",
            module="example",
        ),
        config,
    )
    driver = InterproceduralDriver(snapshot.module_map, config.to_dict(), oracle=provider)
    return validate_json_object(driver.run(snapshot.scenarios).to_dict())


def test_empty_candidate_only_provider_preserves_independent_ai_results() -> None:
    provider = _EmptyCandidates()
    baseline, candidate = _run(None), _run(provider)
    assert provider.reads > 0
    assert candidate["values"] == baseline["values"]
    assert candidate["calls"] == baseline["calls"]
    answer = require_object(
        require_object(require_object(candidate["values"])["example"])["result"]
    )
    assert require_array(answer["constants"]) == [5]
    assert candidate["coverage_status"] == baseline["coverage_status"] == "closed_under_profile"


def test_repeated_native_statistics_do_not_thaw_the_values_projection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot: JsonObject = {
        "source_digest": "source",
        "profile": "python-core-v1",
        "model_digest": "pythonstan-builtins-v1",
        "solver_status": "converged",
        "stats": {"native_work": 7},
        "values": {"example": {"value": {"constants": [1, 2, 3]}}},
    }
    oracle = native_snapshot.NativeSnapshotOracle(
        snapshot, source_digest="source", profile="python-core-v1"
    )
    original = json_data.thaw_json

    def scalar_metadata_only(value: FrozenJsonValue) -> JsonValue:
        assert not isinstance(value, (Mapping, tuple)), (
            "statistics traversed a container projection"
        )
        return original(value)

    monkeypatch.setattr(json_data, "thaw_json", scalar_metadata_only)
    monkeypatch.setattr(native_snapshot, "thaw_json", scalar_metadata_only)
    first = oracle.statistics()
    assert first["native_work"] == 7
    assert first["native_solver_status"] == "converged"
    assert oracle.statistics() == first
