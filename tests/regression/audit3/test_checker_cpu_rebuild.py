"""Actual checker CPU grants reject before parsing and never publish partial proofs."""

from __future__ import annotations

import ast
from threading import Thread

import pytest

from pythonstan.analysis.contracts.json_data import JsonObject
from pythonstan.analysis.contracts.resources import RunLedger, WorkReservation
from pythonstan.analysis.verification.guarded_dispatch import (
    VerificationBudget,
    VerificationResult,
    candidate_to_dict,
    check_source_candidate,
    checker,
    language,
    parse_candidate,
)
from pythonstan.analysis.verification.guarded_dispatch.obligations import WorkMeter
from pythonstan.llm.providers import OfflineDispatchProposer

SOURCE = """def identity(payload):
    return payload
def box(payload):
    return {'value': payload}
def dispatch(table,key,payload):
    return table[key](payload)
table={'read':identity,'write':box}
result=dispatch(table,'read','after')
"""


def candidate() -> JsonObject:
    proposal = OfflineDispatchProposer().propose(SOURCE)
    assert proposal.candidate is not None
    return candidate_to_dict(parse_candidate(proposal.candidate))


def ledger(work: int = 5000) -> RunLedger:
    return RunLedger(work, 0, 0, memory_reader=lambda: 0)


@pytest.mark.parametrize("raw", ["{}", {}])
def test_zero_work_never_enters_json_decode_or_mapping_copy(
    monkeypatch: pytest.MonkeyPatch, raw: object
) -> None:
    def forbidden(*args: object, **kwargs: object) -> object:
        raise AssertionError("unfunded parsing/copy")

    monkeypatch.setattr(language.json, "loads", forbidden)
    monkeypatch.setattr(language, "freeze_snapshot", forbidden)
    request = ledger()
    result = check_source_candidate(
        SOURCE, raw, resource_ledger=request, budget=VerificationBudget(max_work=0)
    )
    assert result.status == "unknown" and result.certificate is None
    assert result.checked_work == 0 and request.work.used == request.work.reserved == 0


def test_malformed_json_is_paid_even_though_source_obligations_never_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden(*args: object, **kwargs: object) -> object:
        raise AssertionError("malformed candidate reached source generation")

    monkeypatch.setattr(checker, "generate_obligations", forbidden)
    request = ledger()
    result = check_source_candidate(SOURCE, "{broken", resource_ledger=request)
    assert result.status == "rejected" and result.certificate is None
    assert result.checked_work == request.work.used > 0
    assert request.work.reserved == 0


def test_source_byte_failure_stops_before_parser_and_whole_encoded_copy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    proposal = candidate()

    def forbidden(*args: object, **kwargs: object) -> ast.Module:
        raise AssertionError("over-budget source reached parser")

    monkeypatch.setattr(ast, "parse", forbidden)
    request = ledger()
    result = check_source_candidate(
        "x" * 100000,
        proposal,
        resource_ledger=request,
        budget=VerificationBudget(max_source_bytes=3),
    )
    assert result.status == "unknown" and result.certificate is None
    assert result.checked_work == request.work.used > 0
    assert 0 < request.raw_structural["source_byte_scan"] < 100


def test_hosted_grant_accounts_once_and_caller_owns_known_unused_settlement() -> None:
    proposal = candidate()
    request = ledger()
    request.charge_work(17, "earlier_phase")
    grant = request.reserve_work(3000, category="guarded_checker_cpu")
    result = check_source_candidate(
        SOURCE, proposal, resource_ledger=request, work_reservation=grant
    )
    assert result.certified and result.checked_work > 0
    assert grant.status == "reserved" and grant.observed == result.checked_work
    assert request.work.used == 17 + result.checked_work
    assert request.work.reserved == grant.held
    grant.settle(grant.observed)
    assert request.work.reserved == 0


@pytest.mark.parametrize("failure", ["foreign", "purpose", "unknown", "closed_scope"])
def test_bad_authority_cannot_start_candidate_parsing_or_spend_another_grant(
    monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    request = ledger()
    owner = ledger() if failure == "foreign" else request
    if failure == "closed_scope":
        with owner.work_scope(1000, "expired_scope"):
            grant = owner.reserve_work(500, category="guarded_checker_cpu")
    else:
        grant = owner.reserve_work(
            500, category="other_purpose" if failure == "purpose" else "guarded_checker_cpu"
        )
    if failure == "unknown":
        grant.unknown("external_timeout")

    def forbidden(*args: object, **kwargs: object) -> object:
        raise AssertionError("bad grant reached candidate parser")

    monkeypatch.setattr(checker, "parse_candidate", forbidden)
    result = check_source_candidate(SOURCE, {}, resource_ledger=request, work_reservation=grant)
    assert result.status == "unknown" and result.certificate is None
    assert result.checked_work == request.work.used == owner.work.used == 0
    assert grant.held == 500


def test_lower_local_checker_cap_is_not_bypassed_by_a_larger_hosted_grant() -> None:
    request = ledger()
    grant = request.reserve_work(3000, category="guarded_checker_cpu")
    result = check_source_candidate(
        SOURCE,
        candidate(),
        resource_ledger=request,
        work_reservation=grant,
        budget=VerificationBudget(max_work=1),
    )
    assert result.status == "unknown" and result.certificate is None
    assert result.checked_work == request.work.used == grant.observed == 1
    assert grant.held == 2999


def test_late_deadline_after_seal_preparation_still_publishes_no_certificate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    proposal = candidate()
    clock = [0.0]
    request = RunLedger(5000, 1, 0, clock=lambda: clock[0], memory_reader=lambda: 0)
    original = checker._certificate_stamp

    def delayed_stamp(
        summary: language.GuardedSummary, facts: checker.ProgramFacts, digest: str
    ) -> str:
        seal = original(summary, facts, digest)
        clock[0] = 2.0
        return seal

    monkeypatch.setattr(checker, "_certificate_stamp", delayed_stamp)
    result = check_source_candidate(SOURCE, proposal, resource_ledger=request)
    assert result.status == "unknown" and result.certificate is None
    assert request.stop_reason == "max_seconds"
    assert result.checked_work == request.work.used > 0 and request.work.reserved == 0


def test_registered_readonly_worker_can_use_its_live_original_quota_scope() -> None:
    proposal = candidate()
    request = ledger()
    results: list[VerificationResult] = []
    with request.work_scope(4000, "readonly_checker"):
        grant = request.reserve_work(3000, category="guarded_checker_cpu")

        def check() -> None:
            results.append(
                check_source_candidate(
                    SOURCE, proposal, resource_ledger=request, work_reservation=grant
                )
            )

        worker = Thread(target=check)
        worker.start()
        worker.join(timeout=10)
        assert not worker.is_alive()
        assert len(results) == 1 and results[0].certified
        assert request.work.used == grant.observed == results[0].checked_work
        assert grant.status == "reserved"
        grant.settle(grant.observed)


def test_host_unknown_latch_during_check_returns_unknown_without_late_refund(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    proposal = candidate()
    request = ledger()
    grant = request.reserve_work(3000, category="guarded_checker_cpu")
    original = checker.generate_obligations
    held: list[int] = []

    def invalidate_after_source(
        source: object,
        *,
        policy_digest: str = "default-policy",
        state_version: str = "0",
        budget: VerificationBudget | None = None,
        meter: WorkMeter | None = None,
        resource_ledger: RunLedger | None = None,
        work_reservation: WorkReservation | None = None,
    ) -> checker.ProgramFacts:
        facts = original(
            source,
            policy_digest=policy_digest,
            state_version=state_version,
            budget=budget,
            meter=meter,
            resource_ledger=resource_ledger,
            work_reservation=work_reservation,
        )
        grant.unknown("host_cancelled")
        held.append(grant.held)
        return facts

    monkeypatch.setattr(checker, "generate_obligations", invalidate_after_source)
    result = check_source_candidate(
        SOURCE, proposal, resource_ledger=request, work_reservation=grant
    )
    assert result.status == "unknown" and result.certificate is None
    assert result.checked_work == request.work.used == grant.observed > 0
    assert grant.status == "unknown" and grant.held == held[0] > 0
