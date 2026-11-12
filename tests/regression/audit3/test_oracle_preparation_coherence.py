"""Detached oracle updates cannot erase a producer's newer source state."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import replace
from typing import Literal

import pytest

from pythonstan.analysis.composition.oracle import IncrementalCandidateOracle, PreparedOracleUpdate
from pythonstan.analysis.contracts.identity import (
    CellId,
    EntryScenario,
    ObjectId,
    ProgramPointId,
    SourceModule,
)
from pythonstan.analysis.domains.values import Value, ValueOperations
from pythonstan.analysis.engines.abstract.driver import InterproceduralDriver

type Mutation = Literal[
    "facts", "copy", "load", "store", "call", "subscription", "bind", "policy", "identity"
]


def _host() -> tuple[InterproceduralDriver, IncrementalCandidateOracle, CellId]:
    driver = InterproceduralDriver(
        (SourceModule("example", "example.py", "result = 0\n"),),
        options={"resources": {"max_seconds": 0}},
    )
    oracle = IncrementalCandidateOracle(
        source_digest=driver.source_digest, profile=driver.semantic_profile
    )
    driver.install_oracle(oracle, incremental=oracle)
    driver.run((EntryScenario("example"),))
    cell = CellId("external:value")
    oracle.publish_cell(cell, Value.constant(1))
    return driver, oracle, cell


@pytest.mark.parametrize(
    "mutation",
    ["facts", "copy", "load", "store", "call", "subscription", "bind", "policy", "identity"],
)
def test_unused_preparation_rejects_newer_producer_inputs(mutation: Mutation) -> None:
    driver, oracle, cell = _host()
    token = oracle.prepare_reabstract({})
    fact_version = oracle.kernel.version
    if mutation == "facts":
        oracle.publish_cell(cell, Value.constant(2))
    elif mutation == "copy":
        oracle.register_copy(CellId("late:source"), CellId("late:target"))
    elif mutation == "load":
        oracle.register_load(CellId("late:owner"), "field", CellId("late:target"))
    elif mutation == "store":
        oracle.register_store(CellId("late:owner"), "field", CellId("late:source"))
    elif mutation == "call":
        oracle.observe_call("example", "example.helper", ProgramPointId("source:call"))
    elif mutation == "subscription":
        oracle.candidates(CellId("late:read"), next(iter(driver.units)))
    elif mutation == "bind":
        oracle.bind(driver)
    elif mutation == "policy":
        oracle.kernel.ops.policy = replace(oracle.kernel.ops.policy, max_const_strings=1)
    elif mutation == "identity":
        oracle.policy_digest = "changed-policy-identity"
    if mutation != "facts":
        assert oracle.kernel.version == fact_version, "Topology-only mutation must be tested"
    with pytest.raises(ValueError, match="stale"):
        token.commit()
    expected = {1, 2} if mutation == "facts" else {1}
    assert set(oracle.candidates(cell).values.exact_constants() or ()) == expected
    if mutation == "copy":
        oracle.publish_cell(CellId("late:source"), Value.constant(3))
        assert oracle.candidates(CellId("late:target")).values.exact_constants() == [3]


def test_mutation_during_preparation_is_rejected_without_erasing_live_growth() -> None:
    _, oracle, cell = _host()
    changed = False

    def charge(amount: int) -> None:
        nonlocal changed
        if not changed:
            changed = True
            oracle.publish_cell(cell, Value.constant(2))

    with pytest.raises(ValueError, match="stale"):
        oracle.prepare_reabstract({}, charge=charge)
    assert set(oracle.candidates(cell).values.exact_constants() or ()) == {1, 2}


def test_unrelated_published_arena_epoch_cannot_authorize_a_prepared_update() -> None:
    driver, oracle, _ = _host()
    token = oracle.prepare_reabstract({ObjectId("left"): ObjectId("summary")})
    driver.ops.arena.publish(
        driver.ops.arena.prepare({ObjectId("unrelated"): ObjectId("different")})
    )
    with pytest.raises(ValueError, match="stale"):
        token.commit()


def test_actual_host_checks_stale_token_before_publishing_its_alias_epoch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    driver, oracle, cell = _host()
    original = oracle.prepare_reabstract
    original_units = driver.units
    epoch = driver.ops.arena.epoch

    def interleaved_growth(
        mapping: Mapping[ObjectId, ObjectId],
        ops: ValueOperations | None = None,
        charge: Callable[[int], None] | None = None,
    ) -> PreparedOracleUpdate:
        prepared = original(mapping, ops, charge)
        oracle.publish_cell(cell, Value.constant(2))
        return prepared

    monkeypatch.setattr(oracle, "prepare_reabstract", interleaved_growth)
    with pytest.raises(ValueError, match="stale"):
        driver.reabstract_addresses({ObjectId("left"): ObjectId("summary")})
    assert driver.ops.arena.epoch == epoch
    assert driver.units is original_units
    assert set(oracle.candidates(cell).values.exact_constants() or ()) == {1, 2}


@pytest.mark.parametrize("publish_target", [False, True])
def test_unchanged_source_and_exact_prepared_quotient_are_the_two_valid_commits(
    publish_target: bool,
) -> None:
    driver, oracle, cell = _host()
    mapping = {ObjectId("left"): ObjectId("summary")}
    prepared = oracle.prepare_reabstract(mapping)
    prepared.validate()
    if publish_target:
        driver.ops.arena.publish(driver.ops.arena.prepare(mapping))
    prepared.commit()
    assert oracle.kernel is prepared.kernel
    assert oracle.candidates(cell).values.exact_constants() == [1]
    prepared.commit()
    with pytest.raises(ValueError, match="already committed"):
        prepared.validate()


def test_late_callback_mutation_aborts_before_another_clone_visit() -> None:
    _, oracle, cell = _host()
    callbacks = 0
    added = CellId("external:late")

    def charge(amount: int) -> None:
        nonlocal callbacks
        callbacks += 1
        if callbacks == 4:
            oracle.publish_cell(added, Value.constant(2))

    with pytest.raises(ValueError, match="stale"):
        oracle.prepare_reabstract({}, charge=charge)
    assert callbacks == 4
    assert oracle.candidates(cell).values.exact_constants() == [1]
    assert oracle.candidates(added).values.exact_constants() == [2]
