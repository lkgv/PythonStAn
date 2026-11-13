"""Actual producer work is admitted before parser and candidate copies."""

from __future__ import annotations

import ast
import hashlib

import pytest

from pythonstan.analysis.contracts import SourceModule
from pythonstan.analysis.contracts.json_data import require_int
from pythonstan.analysis.contracts.resources import RunLedger
from pythonstan.llm.providers import (
    OfflineDispatchProposer,
    RecordedDispatchProposer,
    make_summary_provider,
)


def ledger(limit: int) -> RunLedger:
    return RunLedger(limit, 0, 0, memory_reader=lambda: 0)


def test_zero_standalone_fuel_never_enters_the_source_parser(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entered = []
    monkeypatch.setattr(ast, "parse", lambda *args, **kwargs: entered.append(True))
    run = ledger(0)
    proposal = OfflineDispatchProposer(resource_ledger=run).propose("x = 1")
    assert proposal.status == "budget_exhausted" and not entered
    assert proposal.work == run.work.used == run.work.reserved == 0


def test_rejected_source_has_paid_dispatch_and_text_work() -> None:
    run = ledger(100)
    proposal = OfflineDispatchProposer(resource_ledger=run).propose("def invalid(")
    assert proposal.status == "unavailable" and proposal.candidate is None
    assert proposal.work == run.work.used > 0 and run.work.reserved == 0


def test_hosted_grant_cannot_move_into_a_zero_fuel_sibling_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entered = []
    monkeypatch.setattr(ast, "parse", lambda *args, **kwargs: entered.append(True))
    run = ledger(100)
    grant = run.reserve_work(50, category="hosted_proposer")
    with run.work_scope(0, "sibling"):
        proposal = OfflineDispatchProposer(resource_ledger=run).propose(
            "x = 1", work_reservation=grant
        )
    assert proposal.status == "budget_exhausted" and not entered
    assert run.work.used == 0 and grant.held == 50 and grant.status == "reserved"


def test_zero_recorded_grant_does_not_visit_candidate_object() -> None:
    class Opaque:
        def __deepcopy__(self, memo: object) -> None:
            raise AssertionError("candidate visited without admission")

    source = "x = 1"
    run = ledger(0)
    proposer = RecordedDispatchProposer(
        {hashlib.sha256(source.encode()).hexdigest(): Opaque()}, resource_ledger=run
    )
    proposal = proposer.propose(source)
    assert proposal.status == "budget_exhausted" and run.work.used == 0


def test_recorded_wide_keys_cannot_copy_past_a_small_registered_grant() -> None:
    source = "x = 1"
    run = ledger(100)
    proposer = RecordedDispatchProposer(
        {hashlib.sha256(source.encode()).hexdigest(): {"k" * 100000: [1, 2, 3]}},
        resource_ledger=run,
    )
    grant = run.reserve_work(3, category="hosted_recording")
    proposal = proposer.propose(source, work_reservation=grant)
    assert proposal.status == "budget_exhausted" and proposal.candidate is None
    assert proposal.work == grant.observed == run.work.used == 3
    assert grant.status == "reserved" and grant.held == 0


def test_recorded_dispatch_and_paid_copy_metrics_reconcile_with_actual_global_fuel() -> None:
    source = (
        "def ident(payload):\n    return payload\n"
        "def dispatch(table,key,payload):\n    return table[key](payload)\n"
        "table={'read':ident}\na=dispatch(table,'read','value')\n"
    )
    candidate = OfflineDispatchProposer().propose(source).candidate
    assert candidate is not None
    run = ledger(10000)
    provider = make_summary_provider(
        {"example": SourceModule("example", "example.py", source)},
        {
            "resources": {"max_work": 10000},
            "llm": {
                "enabled": True,
                "level2": True,
                "provider": "recorded",
                "proposals": [
                    {
                        "level": 2,
                        "source_digest": hashlib.sha256(source.encode()).hexdigest(),
                        "candidate": candidate,
                    }
                ],
            },
        },
        resource_ledger=run,
    )
    assert provider is not None
    stats = provider.statistics
    assert stats["proposal_work"] == 1
    assert (
        require_int(stats["bridge_setup_work"]) >= run.work.by_kind["guarded_proposer_cpu"] - 1 > 0
    )
    actual_setup = sum(
        run.work.by_kind.get(kind, 0)
        for kind in (
            "guarded_proposer_cpu",
            "guarded_candidate_parse",
            "guarded_checker_cpu",
            "guarded_bridge_setup",
        )
    )
    assert stats["total_work"] == stats["charged_to_driver_work"] == actual_setup
    assert (
        run.work.used
        == actual_setup
        + run.work.by_kind["provider_configuration_dispatch"]
        + run.work.by_kind["provider_configuration_copy"]
    )
