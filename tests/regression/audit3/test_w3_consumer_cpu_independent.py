"""NEW finite consumer checks, independently fixed before first execution."""

from __future__ import annotations

import ast
import hashlib
from contextlib import nullcontext
from typing import Never

import pytest

from pythonstan import Source, ValueQuery
from pythonstan.analysis.composition.guarded_dispatch import GuardedDispatchProvider
from pythonstan.analysis.contracts import SourceModule
from pythonstan.analysis.contracts.json_data import JsonObject, require_array, require_object
from pythonstan.analysis.contracts.resources import ResourceStop, RunLedger
from pythonstan.analysis.verification.guarded_dispatch.checker import check_source_candidate
from pythonstan.analysis.verification.guarded_dispatch.obligations import VerificationBudget
from pythonstan.config import resolve_config
from pythonstan.inputs import SourceSnapshot, snapshot_target
from pythonstan.llm.providers import OfflineDispatchProposer, RecordedDispatchProposer
from pythonstan.llm.strategy import PolicyProposal, RefinementController
from pythonstan.request import resolve_request
from pythonstan.results.output import OutputSpec
from pythonstan.results.result import AnalysisResult
from pythonstan.session import AnalysisSession


def _ledger(limit: int = 2000) -> RunLedger:
    return RunLedger(limit, 0, 0, memory_reader=lambda: 0)


@pytest.mark.parametrize("kind", ["offline", "recorded"])
@pytest.mark.parametrize("authority", ["zero", "foreign", "expired_scope"])
def test_hosted_proposer_denies_before_input_visit_and_keeps_caller_grant(
    kind: str, authority: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    entered = []

    def forbidden_parse(*args: object, **kwargs: object) -> None:
        entered.append("parse")
        raise AssertionError("unadmitted source parser")

    class Candidate(dict):
        def items(self) -> Never:
            entered.append("candidate")
            raise AssertionError("unadmitted candidate visit")

    source = "answer=23\n"
    run = _ledger()
    owner = _ledger() if authority == "foreign" else run
    context = owner.work_scope(2000, "origin") if authority == "expired_scope" else nullcontext()
    with context:
        grant = owner.reserve_work(0 if authority == "zero" else 1, category="review_hosted")
    proposer = (
        OfflineDispatchProposer(resource_ledger=run)
        if kind == "offline"
        else RecordedDispatchProposer(
            {hashlib.sha256(source.encode()).hexdigest(): Candidate()}, resource_ledger=run
        )
    )
    monkeypatch.setattr(ast, "parse", forbidden_parse)
    result = proposer.propose(source, work_reservation=grant)
    assert result.candidate is None and result.status in {"budget_exhausted", "unavailable"}
    assert result.work == run.work.used == owner.work.used == 0
    assert not entered and grant.status == "reserved"
    assert grant.held == (0 if authority == "zero" else 1)
    assert run.stop_reason is None


@pytest.mark.parametrize("kind", ["offline", "recorded"])
def test_rejected_proposals_keep_actual_fees_and_host_settlement(kind: str) -> None:
    run = _ledger()
    grant = run.reserve_work(2000, category="review_hosted")
    source = "def malformed(" if kind == "offline" else "answer=23\n"
    proposer = (
        OfflineDispatchProposer(resource_ledger=run)
        if kind == "offline"
        else RecordedDispatchProposer(
            {hashlib.sha256(source.encode()).hexdigest(): {"invalid": {1, 2}}},
            resource_ledger=run,
        )
    )
    result = proposer.propose(source, work_reservation=grant)
    assert result.candidate is None and result.status == "unavailable"
    assert 0 < result.work == grant.observed == run.work.used < 2000
    assert grant.status == "reserved" and grant.held + grant.observed == 2000
    grant.settle(grant.observed)
    assert run.work.reserved == 0 and run.work.remaining == 2000 - result.work


@pytest.mark.parametrize("cap", [0, 1, 30, 70])
def test_checker_caps_precede_atomic_json_decode_and_count_exact_fuel(
    cap: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    import pythonstan.analysis.verification.guarded_dispatch.language as language

    entered = []

    def forbidden_decode(*args: object, **kwargs: object) -> None:
        entered.append(True)
        raise AssertionError("atomic JSON decoder entered before admission")

    monkeypatch.setattr(language.json, "loads", forbidden_decode)
    run = _ledger()
    grant = run.reserve_work(2000, category="guarded_checker_cpu")
    result = check_source_candidate(
        "answer=23\n",
        '{"padding":"' + "x" * 8000 + '"}',
        budget=VerificationBudget(max_work=cap),
        resource_ledger=run,
        work_reservation=grant,
    )
    assert result.status == "unknown" and not result.certified and not entered
    assert result.checked_work == run.work.used == grant.observed == cap
    assert grant.status == "reserved" and grant.held == 2000 - cap
    assert run.stop_reason is None


@pytest.mark.parametrize("authority", ["foreign", "expired_scope"])
def test_checker_rejects_unusable_grant_without_any_candidate_decode(
    authority: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    import pythonstan.analysis.verification.guarded_dispatch.language as language

    entered = []
    monkeypatch.setattr(language.json, "loads", lambda *args, **kwargs: entered.append(True))
    run = _ledger()
    owner = _ledger() if authority == "foreign" else run
    context = owner.work_scope(2000, "origin") if authority == "expired_scope" else nullcontext()
    with context:
        grant = owner.reserve_work(1, category="guarded_checker_cpu")
    result = check_source_candidate(
        "answer=23\n", "{}", resource_ledger=run, work_reservation=grant
    )
    assert result.status == "unknown" and result.certificate is None and not entered
    assert result.checked_work == run.work.used == owner.work.used == 0
    assert grant.status == "reserved" and grant.held == 1


def test_nested_memory_admission_counts_live_child_and_keeps_outer_request_usable() -> None:
    mb = 1024 * 1024
    run = RunLedger(2000, 0, 3, memory_reader=lambda: mb)
    run.record_child_memory(mb // 2)
    entered = []
    with run.work_scope(2000, "outer", max_memory_mb=2):
        with (
            pytest.raises(ResourceStop, match="stage_max_memory_mb"),
            run.work_scope(2000, "inner", max_memory_mb=1),
        ):
            entered.append(True)
        assert run.remaining_memory_bytes == mb // 2
        run.charge_work(1, "outer_after_local_memory_denial")
    assert not entered and run.stop_reason is None
    run.release_child_memory()
    assert run.remaining_memory_bytes == 2 * mb and run.work.used == 1


def test_child_deadline_does_not_restart_absolute_global_second() -> None:
    clock = [0.0]
    run = RunLedger(2000, 1, 0, clock=lambda: clock[0], memory_reader=lambda: 0)
    clock[0] = 0.75
    with run.work_scope(2000, "late_child", max_seconds=1):
        assert run.remaining_seconds == 0.25
        clock[0] = 1.0
        with pytest.raises(ResourceStop, match="^max_seconds$"):
            run.charge_work(1, "after_absolute_expiry")
    assert run.stop_reason == "max_seconds" and run.work.used == 0


def test_shared_recorded_setup_is_paid_once_across_prepare_and_multiple_hosts() -> None:
    source = "answer=23\n"
    run = _ledger()
    proposer = RecordedDispatchProposer(
        {hashlib.sha256(source.encode()).hexdigest(): {}}, resource_ledger=run
    )
    provider = GuardedDispatchProvider(
        {"subject": SourceModule("subject", "subject.py", source)},
        {"llm": {"max_proposals": 1, "checker_budget": 2000}, "resources": {"max_work": 2000}},
        proposer,
        resource_ledger=run,
    )
    provider.prepare()
    paid = run.work.used
    stats = provider.statistics
    assert paid > 1 and stats["total_work"] == stats["consumed_driver_work"] == paid
    assert stats["proposal_work"] == 1 and stats["bridge_setup_work"] > 0
    provider.prepare()

    class Host:
        host_token = object()

        def charge(self, amount: int, category: str) -> None:
            raise AssertionError("shared setup charged again")

    provider.charge_setup(Host())
    provider.charge_setup(Host())
    assert run.work.used == paid and run.work.reserved == 0


type L1Context = tuple[Source, SourceSnapshot, JsonObject, JsonObject, PolicyProposal]


def _l1_setup() -> L1Context:
    target = Source("answer=23\n", module="subject")
    config = resolve_config(
        target,
        {
            "engine": "ai_primary",
            "resources": {"max_work": 2000, "max_seconds": 1, "max_memory_mb": 0},
            "llm": {"enabled": True, "level1": True},
        },
    ).to_dict()
    snapshot = snapshot_target(target, config)
    baseline = {
        "solver_status": "converged",
        "coverage_status": "closed_under_profile",
        "values": {},
        "stats": {"total_work": 999999},
    }
    proposal = PolicyProposal(
        query="relations",
        source_digest=snapshot.source_digest,
        region={"module": "*", "qualname": "*", "kind": "block"},
        strategy="key_partitions",
        parameters={"enable_value_partition": True},
        resources={},
    )
    return target, snapshot, config, baseline, proposal


def _l1_run(
    context: L1Context, proposal: PolicyProposal, run: RunLedger
) -> tuple[JsonObject, JsonObject]:
    class Provider:
        def propose(
            self, baseline: JsonObject, snapshot: SourceSnapshot, config: JsonObject
        ) -> list[PolicyProposal]:
            return [proposal]

    target, snapshot, config, baseline, _ = context
    return RefinementController(Provider()).run(
        target=target,
        source_snapshot=snapshot,
        config=config,
        queries=[ValueQuery("subject", "answer")],
        output=OutputSpec(),
        baseline=baseline,
        resource_ledger=run,
    )


def test_l1_rerun_reuses_absolute_ledger_and_retains_baseline_on_late_expiry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = _l1_setup()
    clock = [0.0]
    run = RunLedger(2000, 1, 0, clock=lambda: clock[0], memory_reader=lambda: 0)
    run.charge_work(7, "review_baseline")
    clock[0] = 0.75
    original = AnalysisSession.execute
    observed = []

    def late_execute(session: AnalysisSession) -> AnalysisResult:
        observed.append((session.resource_ledger, session.resource_ledger.deadline))
        assert session.resource_ledger.remaining_seconds == 0.25
        clock[0] = 1.0
        session.resource_ledger.charge_work(1, "review_after_deadline")
        return original(session)

    monkeypatch.setattr(AnalysisSession, "execute", late_execute)
    selected, feedback = _l1_run(context, context[-1], run)
    assert observed == [(run, 1.0)] and selected is context[3]
    assert feedback["baseline_work"] == 7
    assert 7 < feedback["total_work"] == run.work.used < 2000
    assert feedback["reanalysis_work"] == run.work.used - 7
    assert run.stop_reason == "max_seconds"


def test_l1_one_tick_child_preserves_original_2000_cap_and_never_starts_core(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = _l1_setup()
    original = context[-1]
    proposal = PolicyProposal(
        query=original.query,
        source_digest=original.source_digest,
        region=original.region,
        strategy=original.strategy,
        parameters=original.parameters,
        resources={"max_work": 1},
    )
    run = _ledger()
    run.charge_work(7, "review_baseline")
    entered = []
    monkeypatch.setattr(AnalysisSession, "execute", lambda session: entered.append(True))
    selected, feedback = _l1_run(context, proposal, run)
    assert not entered and selected is context[3]
    child = next(
        require_object(item)
        for item in require_array(run.snapshot()["work_scopes"])
        if require_object(item)["name"] == "l1_rerun"
    )
    assert child["limit"] == child["consumed"] == 1
    assert run.work.limit == 2000 and feedback["total_work"] == run.work.used < 2000
    assert run.stop_reason is None


def test_session_one_tick_cannot_materialize_large_config_before_copy_admission(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import pythonstan.config.model as model

    request = resolve_request(
        Source("answer=23\n", module="subject"),
        config={
            "engine": "ai_primary",
            "resources": {"max_work": 2000, "max_seconds": 0, "max_memory_mb": 0},
            "llm": {"proposals": [{"padding": list(range(2000))}]},
        },
    )
    run = _ledger()
    original = model.thaw_json_object
    completed_copies = []

    def observe_copy(value: object) -> JsonObject:
        data = original(value)
        if "llm" in data:
            completed_copies.append((run.work.used, run.remaining_work))
        return data

    monkeypatch.setattr(model, "thaw_json_object", observe_copy)
    session = AnalysisSession(request, resource_ledger=run)
    with (
        run.work_scope(1, "one_tick_setup"),
        pytest.raises(ResourceStop, match="stage_max_work"),
    ):
        session.prepare()
    assert not completed_copies, completed_copies
    assert run.work.used == 1 and run.work.limit == 2000 and run.stop_reason is None
