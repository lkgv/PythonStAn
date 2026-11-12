"""Actual finite provider capabilities, explicit preparation and epoch ownership."""

from __future__ import annotations

import gc
import weakref

import pytest

from pythonstan.analysis.composition.guarded_dispatch import (
    GuardedDispatchProvider,
    PreparationState,
    RevokedSummaryEpoch,
    SummaryApplicationContext,
    UnchangedSummaryEpoch,
)
from pythonstan.analysis.contracts import EntryScenario, SemanticIdentity, SourceModule
from pythonstan.analysis.contracts.identity import IdentityEpoch, ObjectId
from pythonstan.analysis.contracts.json_data import JsonObject, require_int
from pythonstan.analysis.engines.abstract.driver import InterproceduralDriver
from pythonstan.llm.candidates import CandidateProposal
from pythonstan.llm.providers import make_summary_provider

SOURCE = """def ident(payload):
    return payload
def box(payload):
    return {'value':payload}
def dispatch(table,key,payload):
    return table[key](payload)
table={'read':ident,'write':box}
a=dispatch(table,'read','after')
b=dispatch(table,'write','before')
"""


def options() -> JsonObject:
    return {
        "engine": "ai_primary",
        "resources": {"max_work": 100000, "max_seconds": 0, "max_memory_mb": 0},
        "llm": {
            "enabled": True,
            "level2": True,
            "provider": "offline",
            "max_proposals": 2,
            "checker_budget": 10000,
        },
    }


class CountingProposer:
    def __init__(self, *, fail: bool = False) -> None:
        self.calls = 0
        self.fail = fail
        self.reenter: GuardedDispatchProvider | None = None

    def propose(
        self, source: str, *, identity: SemanticIdentity | None = None, max_work: int | None = None
    ) -> CandidateProposal:
        self.calls += 1
        if self.reenter is not None:
            self.reenter.prepare()
        if self.fail:
            raise ValueError("proposal sentinel")
        return CandidateProposal("unknown", None, "counting", 3, 0.0)


def real_driver() -> tuple[InterproceduralDriver, GuardedDispatchProvider]:
    module = SourceModule("example", "example.py", SOURCE)
    config = options()
    provider = make_summary_provider({"example": module}, config)
    assert provider is not None
    driver = InterproceduralDriver((module,), options=config)
    driver.summary_provider = provider
    driver.run((EntryScenario("example"),))
    return driver, provider


def test_constructor_is_pure_and_successful_prepare_is_idempotent() -> None:
    proposer = CountingProposer()
    module = SourceModule("example", "example.py", "")
    provider = GuardedDispatchProvider({"example": module}, options(), proposer)
    assert proposer.calls == 0 and provider.preparation_state is PreparationState.NEW
    assert not provider.programs and not provider.proof_records
    provider.prepare()
    before = provider.statistics
    assert proposer.calls == 1 and provider.preparation_state is PreparationState.PREPARED
    provider.prepare()
    assert proposer.calls == 1 and provider.statistics == before
    assert all(record["status"] == "unknown" for record in provider.proof_records)


@pytest.mark.parametrize("reentrant", [False, True])
def test_failed_or_running_preparation_cannot_be_silently_reused(reentrant: bool) -> None:
    proposer = CountingProposer(fail=not reentrant)
    module = SourceModule("example", "example.py", "")
    provider = GuardedDispatchProvider({"example": module}, options(), proposer)
    if reentrant:
        proposer.reenter = provider
    with pytest.raises((ValueError, RuntimeError), match="sentinel|running"):
        provider.prepare()
    assert provider.preparation_state is PreparationState.FAILED
    with pytest.raises(RuntimeError, match="failed"):
        provider.prepare()
    assert proposer.calls == 1 and not provider.programs


def test_context_views_are_readonly_live_and_budget_reads_follow_real_charges() -> None:
    driver, provider = real_driver()
    context = driver._summary_context()
    assert isinstance(context, SummaryApplicationContext)
    assert context.remaining_work() == max(0, driver.max_work - driver.work)
    previous = context.remaining_work()
    context.charge(2, "llm_state_invalidation_work")
    assert context.remaining_work() == previous - 2
    info = next(iter(driver.functions.values()))
    with pytest.raises(TypeError):
        context.functions[ObjectId("forbidden")] = info
    driver.functions[ObjectId("live-alias")] = info
    assert context.functions[ObjectId("live-alias")] is info
    assert all(record["status"] == "verified" for record in provider.proof_records)


def test_setup_identity_is_a_retained_marker_without_retaining_the_driver() -> None:
    driver, provider = real_driver()
    first = driver._summary_context()
    again = driver._summary_context()
    assert first.host_token is again.host_token
    before = driver.work
    provider.charge_setup(first)
    provider.charge_setup(again)
    assert driver.work == before
    token = first.host_token
    reference = weakref.ref(driver)
    del first, again, driver
    gc.collect()
    assert reference() is None
    assert token in provider._charged_drivers
    module = SourceModule("example", "example.py", SOURCE)
    another = InterproceduralDriver((module,), options=options())
    another.summary_provider = provider
    context = another._summary_context()
    assert context.host_token is not token
    expected = sum(
        require_int(provider.statistics[name])
        for name in ("proposal_work", "checker_work", "bridge_setup_work")
    )
    provider.charge_setup(context)
    assert another.work == expected and len(provider._charged_drivers) == 2


def test_epoch_preparation_is_detached_budgeted_and_committed_by_record() -> None:
    driver, provider = real_driver()
    original_programs = provider.programs
    original_records = provider.proof_records
    original_stats = provider.statistics

    def stop(amount: int) -> None:
        raise RuntimeError("budget sentinel")

    with pytest.raises(RuntimeError, match="budget sentinel"):
        provider.prepare_state_epoch(IdentityEpoch(1), charge=stop)
    assert provider.programs is original_programs
    assert provider.proof_records == original_records and provider.statistics == original_stats
    charges: list[int] = []
    prepared = provider.prepare_state_epoch(IdentityEpoch(1), charge=charges.append)
    assert isinstance(prepared, RevokedSummaryEpoch)
    assert sum(charges) == prepared.work and prepared.work_precharged
    assert provider.programs is original_programs and provider.proof_records == original_records
    provider.commit_state_epoch(prepared)
    assert not provider.programs
    assert all(record["status"] == "unknown" for record in provider.proof_records)
    assert isinstance(provider.prepare_state_epoch(IdentityEpoch(1)), UnchangedSummaryEpoch)
    provider.prepare()
    assert not provider.programs, "Repeated prepare must not resurrect retired authority"
