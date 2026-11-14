"""The staged public route consumes the real Native table and rejects before AI."""

from __future__ import annotations

import pytest

from pythonstan import Source, ValueQuery, analyze
from pythonstan.analysis.contracts.json_data import JsonObject, require_object
from pythonstan.analysis.contracts.resources import RunLedger
from pythonstan.compat import legacy_runner
from pythonstan.config.model import EffectiveConfig
from pythonstan.inputs import SourceSnapshot


@pytest.mark.parametrize("policy,expected", [("0-cfa", [5, 7]), ("2-cfa", [7])])
def test_staged_route_uses_actual_targeted_native_prepass(
    monkeypatch: pytest.MonkeyPatch, policy: str, expected: list[int]
) -> None:
    actual: list[JsonObject] = []
    run = legacy_runner.run_legacy

    def capture(
        snapshot: SourceSnapshot, config: EffectiveConfig | JsonObject, *, ledger: RunLedger | None
    ) -> JsonObject:
        result = run(snapshot, config, ledger=ledger)
        actual.append(result)
        return result

    monkeypatch.setattr(legacy_runner, "run_legacy", capture)
    raw = analyze(
        Source(
            "def identity(x):\n    return x\na=identity(5)\nresult=identity(7)\n", module="subject"
        ),
        config={
            "engine": "native_then_ai",
            "andersen": {
                "context_policy": "2-cfa",
                "method_context_policies": [
                    {"module": "subject", "qualname": "subject.identity", "policy": policy}
                ],
            },
            "resources": {"max_work": 10000, "max_seconds": 2, "max_memory_mb": 512},
        },
        queries=(ValueQuery("subject", "result"), "stats"),
    ).raw_snapshot()
    assert len(actual) == 1 and actual[0]["solver_status"] == "converged"
    prepass = actual[0]
    value = require_object(require_object(require_object(prepass["values"])["subject"])["result"])
    assert value["constants"] == expected and not value["top"], prepass
    native_stats = require_object(require_object(raw["stats"])["native_prepass"])
    decisions = require_object(require_object(native_stats["method_context_policy"])["decisions"])
    assert decisions["subject:subject.identity:" + policy] >= 2
    final = require_object(require_object(require_object(raw["values"])["subject"])["result"])
    assert 7 in final["constants"], raw
    assert prepass["coverage_status"] == "open"


def test_staged_unmatched_rule_rejects_before_either_engine_publishes_source_facts() -> None:
    raw = analyze(
        Source("def identity(x):\n    return x\nresult=identity(17)\n", module="subject"),
        config={
            "engine": "native_then_ai",
            "andersen": {
                "method_context_policies": [
                    {"module": "subject", "qualname": "subject.missing", "policy": "0-cfa"}
                ]
            },
            "resources": {"max_work": 10000, "max_seconds": 2, "max_memory_mb": 512},
        },
        queries=(ValueQuery("subject", "result"), "calls", "stats"),
    ).raw_snapshot()
    assert raw["solver_status"] == "failed", raw
    assert raw["coverage_status"] == "open"
    assert raw["method_policy_validation"] == "rejected"
    assert require_object(raw["resource_ledger"])["stop_reason"] is None
    assert not raw["values"] and not raw["calls"], raw
    assert "Unmatched source method policy" in str(raw)
