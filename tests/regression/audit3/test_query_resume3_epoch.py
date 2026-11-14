"""Actual host/observation alias transaction with admitted real Unit roots."""

from __future__ import annotations

import pytest

from pythonstan.analysis.contracts import SourceModule
from pythonstan.analysis.contracts.identity import ObjectId
from pythonstan.analysis.contracts.resources import ResourceStop, RunLedger
from pythonstan.analysis.engines.abstract.driver import InterproceduralDriver
from pythonstan.analysis.state.flow import ReturnFlow


def solved() -> InterproceduralDriver:
    source = SourceModule("m", "m.py", 'def f():\n    return {"x":17}\nx=f()\n')
    driver = InterproceduralDriver(
        {"m": source},
        options={"queries": [{"kind": "returns"}]},
        resource_ledger=RunLedger(10000, 0, 0, memory_reader=lambda: 0),
    )
    driver.run()
    return driver


def dictionary_identity(driver: InterproceduralDriver) -> ObjectId:
    return next(
        reference
        for unit in driver.units.values()
        if not unit.is_module
        for flow in unit.outcomes
        if isinstance(flow, ReturnFlow)
        for reference in flow.value.refs
    )


def test_actual_host_epoch_and_observations_publish_together_then_rerun() -> None:
    driver = solved()
    owner = driver.procedure_bridge
    assert owner is not None
    old_units = driver.units
    source = dictionary_identity(driver)
    target = ObjectId("quotient:dict")
    driver.reabstract_addresses({source: target})
    assert driver.units is not old_units
    assert driver.ops.arena.epoch == owner.store.epoch == 1
    records = owner.store.current("m", "m.f")
    assert records and not any(record.covering for record in records)
    assert records[0].payload.outcomes[0].value.refs == frozenset({target})
    driver._solve()
    current = owner.store.current("m", "m.f")
    assert current[0].covering and current[0].head.version.generation == 2


def test_failed_actual_host_preparation_leaves_both_published_epochs() -> None:
    driver = solved()
    owner = driver.procedure_bridge
    assert owner is not None
    old_units, old_revision = driver.units, owner.store.revision
    driver.max_work = driver.work + 50
    with pytest.raises(ResourceStop):
        driver.reabstract_addresses({dictionary_identity(driver): ObjectId("quotient:dict")})
    assert driver.units is old_units
    assert driver.ops.arena.epoch == owner.store.epoch == 0
    assert owner.store.revision == old_revision
    assert driver.ops.arena.aliases == {}


def test_public_address_quotient_during_later_caller_visit() -> None:
    from pythonstan import Source, ValueQuery, analyze
    from pythonstan.analysis.contracts.json_data import require_object

    source = (
        'def f():\n    return 1\nx=f()\nif p:\n    a={"x":1}\n'
        'else:\n    a={"y":2}\nresult=a\nresult2=0\n'
    )
    query = ValueQuery("m", "result")
    result = analyze(
        Source(source, module="m"),
        config={
            "engine": "ai_primary",
            "abstract": {"max_addr_set_size": 1, "solver_options": {"max_traces_per_block": 1}},
            "inputs": {"bindings": {"m.p": {"type": "bool"}}},
            "resources": {"max_work": 10000, "max_seconds": 2, "max_memory_mb": 512},
        },
        queries=[query, "returns", "outcomes"],
    )
    assert result.solver_status == "converged" and result.coverage_status == "closed_under_profile"
    answer = result.query(query)
    assert answer.status == "answered" and not require_object(answer.value)["top"]
    function = result.query(ValueQuery("m", "f", "function_exit"))
    assert function.status == "answered" and function.coverage_status == "closed_under_profile"
    assert require_object(function.value)["constants"] == [1]
