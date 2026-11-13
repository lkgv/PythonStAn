"""L1 reruns share request resources and keep a usable baseline on failure."""

from __future__ import annotations

from collections.abc import Sequence

import pytest

from pythonstan import Source, ValueQuery
from pythonstan.analysis.contracts.json_data import (
    JsonObject,
    JsonValue,
    require_array,
    require_int,
    require_object,
)
from pythonstan.analysis.contracts.resources import RunLedger
from pythonstan.config import resolve_config
from pythonstan.inputs import SourceSnapshot, snapshot_target
from pythonstan.llm.strategy import PolicyProposal, RefinementController
from pythonstan.results.output import OutputSpec
from pythonstan.results.result import AnalysisResult
from pythonstan.session import AnalysisSession


class _Provider:
    def __init__(self, proposals: Sequence[PolicyProposal | JsonValue]) -> None:
        self.proposals = proposals
        self.calls = 0

    def propose(
        self, baseline: JsonObject, snapshot: SourceSnapshot, config: JsonObject
    ) -> Sequence[PolicyProposal | JsonValue]:
        self.calls += 1
        return self.proposals


def _setup() -> tuple[Source, SourceSnapshot, JsonObject, JsonObject, RunLedger]:
    target = Source("result=17\n", module="subject")
    config = resolve_config(
        target,
        {
            "engine": "ai_primary",
            "resources": {"max_work": 5000, "max_seconds": 0, "max_memory_mb": 0},
            "llm": {"enabled": True, "level1": True},
        },
    ).to_dict()
    snapshot = snapshot_target(target, config)
    baseline: JsonObject = {
        "solver_status": "converged",
        "coverage_status": "closed_under_profile",
        "source_digest": snapshot.source_digest,
        "values": {"subject": {"result": {"constants": [17], "top": False, "top_sorts": []}}},
        "stats": {"total_work": 999999},
    }
    ledger = RunLedger(5000, 0, 0, memory_reader=lambda: 0)
    ledger.charge_work(7, "synthetic_baseline")
    return target, snapshot, config, baseline, ledger


def _proposal(snapshot: SourceSnapshot, resources: JsonObject | None = None) -> PolicyProposal:
    return PolicyProposal(
        query="relations",
        source_digest=snapshot.source_digest,
        region={"module": "*", "qualname": "*", "kind": "block"},
        strategy="key_partitions",
        parameters={"enable_value_partition": True},
        resources=resources or {},
    )


def _run(
    provider: _Provider, context: tuple[Source, SourceSnapshot, JsonObject, JsonObject, RunLedger]
) -> tuple[JsonObject, JsonObject]:
    target, snapshot, config, baseline, ledger = context
    return RefinementController(provider).run(
        target=target,
        source_snapshot=snapshot,
        config=config,
        queries=[ValueQuery("subject", "result")],
        output=OutputSpec(),
        baseline=baseline,
        resource_ledger=ledger,
    )


def test_actual_rerun_uses_same_ledger_and_ignores_forged_baseline_cost(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = _setup()
    original = AnalysisSession.execute
    observed: list[RunLedger] = []

    def execute(session: AnalysisSession) -> AnalysisResult:
        observed.append(session.resource_ledger)
        return original(session)

    monkeypatch.setattr(AnalysisSession, "execute", execute)
    _, feedback = _run(_Provider([_proposal(context[1])]), context)
    ledger = context[-1]
    assert observed == [ledger]
    assert feedback["baseline_work"] == 7
    assert feedback["total_work"] == ledger.work.used < ledger.work.limit
    assert feedback["reanalysis_work"] == ledger.work.used - 7


def test_failed_validation_has_consumed_fees_without_starting_reanalysis(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = _setup()

    def execute(session: AnalysisSession) -> AnalysisResult:
        raise AssertionError("A rejected proposal must not start analysis")

    monkeypatch.setattr(AnalysisSession, "execute", execute)
    selected, feedback = _run(_Provider([{"verified": True}]), context)
    assert selected is context[3]
    assert context[-1].work.used > 7
    assert require_object(require_array(feedback["proposals"])[0])["status"] == "rejected"
    assert context[-1].work.by_kind["l1_validation_attempt"] == 1


def test_child_one_tick_scope_is_not_a_fresh_global_budget() -> None:
    context = _setup()
    selected, feedback = _run(_Provider([_proposal(context[1], {"max_work": 1})]), context)
    assert selected is context[3]
    row = require_object(require_array(feedback["proposals"])[0])
    assert row["status"] == "retained_baseline"
    scopes = require_array(context[-1].snapshot()["work_scopes"])
    child = next(
        require_object(scope) for scope in scopes if require_object(scope)["name"] == "l1_rerun"
    )
    assert child["limit"] == child["consumed"] == 1
    assert context[-1].work.used == feedback["total_work"] <= 5000


def test_failure_after_measured_work_does_not_charge_an_invented_full_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = _setup()
    original = AnalysisSession.execute

    def execute(session: AnalysisSession) -> AnalysisResult:
        result = original(session)
        result.raw_snapshot()
        raise ValueError("synthetic failure after actual measured publication")

    monkeypatch.setattr(AnalysisSession, "execute", execute)
    selected, feedback = _run(_Provider([_proposal(context[1])]), context)
    assert selected is context[3]
    assert 7 < require_int(feedback["total_work"]) == context[-1].work.used < 5000
    assert require_object(require_array(feedback["proposals"])[0])["error_kind"] == "ValueError"


def test_resource_degraded_after_snapshot_cannot_replace_a_closed_baseline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = _setup()

    def execute(session: AnalysisSession) -> AnalysisResult:
        session.resource_ledger.charge_work(3, "synthetic_core")
        return AnalysisResult(
            {
                "solver_status": "converged",
                "coverage_status": "closed_under_profile",
                "actual_engine": "ai_primary",
                "values": {"subject": {"result": {"constants": [99]}}},
            },
            session.queries,
            session.output,
        )

    def raw(result: AnalysisResult) -> JsonObject:
        assert result.solver_status == "converged"
        return {
            "solver_status": "budget_exhausted",
            "coverage_status": "open",
            "values": {},
            "stats": {"total_work": 0},
        }

    monkeypatch.setattr(AnalysisSession, "execute", execute)
    monkeypatch.setattr(AnalysisResult, "raw_snapshot", raw)
    selected, feedback = _run(_Provider([_proposal(context[1])]), context)
    assert selected is context[3]
    row = require_object(require_array(feedback["proposals"])[0])
    assert row["status"] == "retained_baseline"
    assert row["solver_status"] == "budget_exhausted"
    assert "policy_digest" in row


def test_exhausted_outer_scope_does_not_even_call_the_proposer() -> None:
    context = _setup()
    provider = _Provider([_proposal(context[1])])
    with context[-1].work_scope(0, "exhausted_owner"):
        selected, feedback = _run(provider, context)
    assert selected is context[3] and provider.calls == 0
    assert feedback["attempted"] == 0
    assert context[-1].work.used == 7


def test_lower_memory_cap_is_sampled_before_child_analysis(monkeypatch: pytest.MonkeyPatch) -> None:
    target, snapshot, config, baseline, _ = _setup()
    require_object(config["resources"])["max_memory_mb"] = 3
    ledger = RunLedger(5000, 0, 3, memory_reader=lambda: 2 * 1024 * 1024)
    ledger.charge_work(7, "synthetic_baseline")

    def execute(session: AnalysisSession) -> AnalysisResult:
        raise AssertionError("Child starts above its memory limit")

    monkeypatch.setattr(AnalysisSession, "execute", execute)
    selected, feedback = _run(
        _Provider([_proposal(snapshot, {"max_memory_mb": 1})]),
        (target, snapshot, config, baseline, ledger),
    )
    row = require_object(require_array(feedback["proposals"])[0])
    assert selected is baseline
    assert row["reason"] == "stage_max_memory_mb"
    assert ledger.stop_reason is None


def test_proposer_cannot_mutate_shared_baseline_or_policy_configuration() -> None:
    context = _setup()

    class MutatingProvider(_Provider):
        def propose(
            self, baseline: JsonObject, snapshot: SourceSnapshot, config: JsonObject
        ) -> Sequence[PolicyProposal | JsonValue]:
            baseline["values"] = {}
            require_object(config["resources"])["max_work"] = 999999
            return [{"verified": True}]

    selected, _ = _run(MutatingProvider([]), context)
    assert selected is context[3]
    assert require_object(context[3]["values"])["subject"]
    assert require_object(context[2]["resources"])["max_work"] == 5000


def test_global_deadline_is_not_refreshed_after_proposal_returns() -> None:
    target, snapshot, config, baseline, _ = _setup()
    clock = [0.0]
    ledger = RunLedger(5000, 1, 0, clock=lambda: clock[0], memory_reader=lambda: 0)
    ledger.charge_work(7, "synthetic_baseline")

    class SlowProvider(_Provider):
        def propose(
            self, baseline: JsonObject, snapshot: SourceSnapshot, config: JsonObject
        ) -> Sequence[PolicyProposal | JsonValue]:
            clock[0] = 2.0
            return self.proposals

    selected, feedback = _run(
        SlowProvider([_proposal(snapshot)]), (target, snapshot, config, baseline, ledger)
    )
    assert selected is baseline
    assert ledger.deadline == 1.0 and ledger.stop_reason == "max_seconds"
    assert feedback["attempted"] == 0
