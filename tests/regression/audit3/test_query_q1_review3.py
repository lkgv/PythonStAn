"""Independent actual-producer and query-scope integration controls."""

from __future__ import annotations

import ast
from collections.abc import Sequence
from dataclasses import replace

import pytest

from pythonstan import Source, ValueQuery, analyze
from pythonstan.analysis.composition.guarded_dispatch import GuardedDispatchProvider
from pythonstan.analysis.composition.summary_context import SummaryApplicationContext
from pythonstan.analysis.contracts import SourceModule
from pythonstan.analysis.contracts.identity import ObjectId
from pythonstan.analysis.contracts.json_data import JsonObject, require_array, require_object
from pythonstan.analysis.contracts.resources import RunLedger
from pythonstan.analysis.domains.values import Value
from pythonstan.analysis.engines.abstract.driver import InterproceduralDriver
from pythonstan.analysis.observations.procedures import (
    PreparedObservationEpoch,
    ProcedureObservationStore,
)
from pythonstan.analysis.state.flow import Flow, ReturnFlow
from pythonstan.analysis.state.store import Frame, Store


def options() -> JsonObject:
    return {
        "engine": "ai_primary",
        "resources": {"max_work": 10000, "max_seconds": 2, "max_memory_mb": 512},
    }


def exact(answer: object, expected: list[object]) -> None:
    assert isinstance(answer, dict)
    assert answer.get("constants") == expected and not answer.get("top")


def test_actual_context0_single_and_bulk_keep_the_same_return_values() -> None:
    config = options()
    config["abstract"] = {"context_depth": 0}
    query = ValueQuery("m", "identity", "function_exit")
    result = analyze(
        Source("def identity(x):\n    return x\na=identity(17)\nb=identity(19)\n", module="m"),
        config=config,
        queries=[query, "returns", "outcomes"],
    )
    single = result.query(query)
    assert single.status == "answered" and single.coverage_status == "closed_under_profile"
    exact(single.value, [17, 19])
    bulk = require_object(result.query("returns").value)
    assert bulk["m.identity"] == single.value
    assert require_object(result.raw_snapshot()["stats"])["units"] == 2


def test_actual_function_mixed_return_and_raise_is_materialized_complete() -> None:
    query = ValueQuery("m", "f", "function_exit")
    config = options()
    config["inputs"] = {"bindings": {"m.p": {"type": "bool"}}}
    result = analyze(
        Source(
            "def f(x):\n    if x:\n        raise ValueError()\n    return 17\nresult=f(p)\n",
            module="m",
        ),
        config=config,
        queries=[query, "returns", "outcomes"],
    )
    answer = result.query(query)
    assert answer.status == "answered" and answer.coverage_status == "closed_under_profile"
    data = require_object(answer.value)
    assert data["normal_exit"] and data["exceptions"] == ["ValueError"]
    exact(data, [17])
    rows = require_array(require_object(result.query("outcomes").value)["m.f"])
    assert {require_object(row)["kind"] for row in rows} == {"return", "raise"}


def test_unrequested_function_view_has_no_closed_query_coverage() -> None:
    result = analyze(
        Source("def f():\n    return 17\nresult=f()\n", module="m"),
        config=options(),
        queries=[ValueQuery("m", "result")],
    )
    answer = result.query(ValueQuery("m", "f", "function_exit"))
    assert answer.status == "not_requested" and answer.coverage_status == "open"


def test_public_later_visit_quotient_keeps_actual_function_exit() -> None:
    config = options()
    config["abstract"] = {"max_addr_set_size": 1, "solver_options": {"max_traces_per_block": 1}}
    config["inputs"] = {"bindings": {"m.flag": {"type": "bool"}}}
    query = ValueQuery("m", "first", "function_exit")
    result = analyze(
        Source(
            'def first():\n    return 23\na=first()\nif flag:\n    payload={"alpha":1}\n'
            'else:\n    payload={"beta":2}\nobserved=0\nresult=payload\n',
            module="m",
        ),
        config=config,
        queries=[query, "returns", "outcomes", ValueQuery("m", "result")],
    )
    assert result.solver_status == "converged"
    answer = result.query(query)
    assert answer.status == "answered" and answer.coverage_status == "closed_under_profile"
    exact(answer.value, [23])
    assert require_object(result.raw_snapshot()["stats"])["address_reabstract_epochs"] >= 1


def solved() -> InterproceduralDriver:
    driver = InterproceduralDriver(
        {"m": SourceModule("m", "m.py", 'def f():\n    return {"value":17}\nx=f()\n')},
        options={"queries": [{"kind": "returns"}, {"kind": "outcomes"}]},
        resource_ledger=RunLedger(10000, 0, 0, memory_reader=lambda: 0),
    )
    driver.run()
    return driver


def returned_object(driver: InterproceduralDriver) -> ObjectId:
    return next(
        ref
        for unit in driver.units.values()
        if not unit.is_module
        for flow in unit.outcomes
        if isinstance(flow, ReturnFlow)
        for ref in flow.value.refs
    )


def test_stale_active_borrow_is_not_renewed_as_epoch_coverage() -> None:
    driver = solved()
    owner = driver.procedure_bridge
    assert owner is not None
    producer = next(unit for unit in driver.units.values() if not unit.is_module)
    previous_generation = producer.visits
    owner.begin(producer)
    producer.visits += 1  # Actual Driver._solve ordering before transfer.
    driver.current = producer.key
    target = ObjectId("review3:quotient")
    driver.reabstract_addresses({returned_object(driver): target})
    record = owner.store.current("m", "m.f")[0]
    assert driver.ops.arena.epoch == owner.store.epoch == 1
    assert record.head.materialization == "invalidated" and not record.covering
    assert record.head.version.generation == previous_generation
    assert record.payload.outcomes[0].value.refs == frozenset({target})


def test_fact_validation_failure_keeps_actual_host_and_fact_epochs_atomic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    driver = solved()
    owner = driver.procedure_bridge
    assert owner is not None
    units, functions, revision = driver.units, driver.functions, owner.store.revision
    old_facts = owner.store.current("m", "m.f")
    work_before = driver.resource_ledger.work.used

    def reject(self: ProcedureObservationStore, plan: PreparedObservationEpoch) -> None:
        raise ValueError("review3 rejected prepared fact epoch")

    monkeypatch.setattr(ProcedureObservationStore, "_validate_epoch", reject)
    with pytest.raises(ValueError, match="review3 rejected"):
        driver.reabstract_addresses({returned_object(driver): ObjectId("review3:rejected")})
    assert driver.units is units and driver.functions is functions
    assert driver.ops.arena.epoch == owner.store.epoch == 0
    assert driver.ops.arena.aliases == {} and owner.store.revision == revision
    assert owner.store.current("m", "m.f")[0].head == old_facts[0].head
    assert owner.store.current("m", "m.f")[0].covering
    assert driver.resource_ledger.work.used > work_before


def test_known_empty_returns_scope_has_complete_source_inventory() -> None:
    result = analyze(Source("result=17\n", module="m"), config=options(), queries=["returns"])
    answer = result.query("returns")
    assert answer.status == "answered" and answer.coverage_status == "closed_under_profile"
    assert answer.value == {}
    unknown = result.query(ValueQuery("m", "not_a_function", "function_exit"))
    assert unknown.status == "unavailable" and unknown.coverage_status == "open"


def test_lazy_only_returns_and_actual_module_outcomes_are_distinct() -> None:
    query = ValueQuery("m", "g", "function_exit")
    result = analyze(
        Source("def g():\n    yield 17\nx=g()\n", module="m"),
        config=options(),
        queries=[query, "returns", "outcomes"],
    )
    assert result.query(query).status == "unavailable"
    returns = result.query("returns")
    assert returns.status == "unavailable" and returns.coverage_status == "open"
    outcomes = result.query("outcomes")
    assert outcomes.status == "answered" and outcomes.coverage_status == "open"
    actual = require_array(require_object(outcomes.value)["m"])
    assert {require_object(row)["kind"] for row in actual} == {"normal"}


def test_open_bulk_keeps_actual_normal_and_raise_prefix_answered() -> None:
    result = analyze(
        Source("result=list(b'ab')\n", module="m"),
        config=options(),
        queries=[ValueQuery("m", "result"), "returns", "outcomes"],
    )
    answer = result.query("outcomes")
    assert answer.status == "answered" and answer.coverage_status == "open"
    rows = require_array(require_object(answer.value)["m"])
    assert {require_object(row)["kind"] for row in rows} >= {"normal", "raise"}


def test_accepted_summary_with_missing_fact_port_does_not_close_empty_returns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = GuardedDispatchProvider.try_apply

    def legacy(
        self: GuardedDispatchProvider,
        context: SummaryApplicationContext,
        identifier: ObjectId,
        args: Sequence[Value],
        keywords: Sequence[tuple[str, Value]],
        store: Store,
        frame: Frame,
        site: ast.AST | None,
    ) -> list[Flow] | None:
        return original(
            self,
            replace(context, record_procedure_exits=None),
            identifier,
            args,
            keywords,
            store,
            frame,
            site,
        )

    monkeypatch.setattr(GuardedDispatchProvider, "try_apply", legacy)
    config = options()
    config["llm"] = {"enabled": True, "level2": True, "provider": "offline"}
    result = analyze(
        Source(
            "def identity(payload):\n    return payload\n"
            "def dispatch(table,key,payload):\n    return table[key](payload)\n"
            "table={'read':identity}\nresult=dispatch(table,'read','original')\n",
            module="m",
        ),
        config=config,
        queries=["returns", "outcomes"],
    )
    answer = result.query("returns")
    assert answer.status == "unavailable" and answer.coverage_status == "open"
