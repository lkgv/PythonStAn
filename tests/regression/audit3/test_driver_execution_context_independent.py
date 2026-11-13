"""Independent checks of real regional execution and host resource ownership."""

from __future__ import annotations

import pytest

from pythonstan.analysis.composition.hybrid import configure_driver
from pythonstan.analysis.composition.plan import build_plan
from pythonstan.analysis.contracts import SourceModule
from pythonstan.analysis.domains.values import ValueOperations, ValuePolicy
from pythonstan.analysis.engines.abstract.driver import BudgetStop, InterproceduralDriver
from pythonstan.analysis.policies.region import EffectiveRegionPolicy, RegionExecutionContext
from pythonstan.analysis.state.store import Store
from pythonstan.config import resolve_config
from pythonstan.inputs import Source, snapshot_target

from .domain_oracles import require_members


def test_mixed_callsite_policies_leave_host_and_cached_helpers_unchanged() -> None:
    target = Source("def f(x):\n    return x\na=f(1)\nb=f(2)\nresult=a+b\n", module="subject")
    config = resolve_config(
        target,
        config={
            "engine": "ai_primary",
            "regions": [
                {
                    "selector": {"module": "subject", "kind": "block", "line_start": 3},
                    "abstract": {"context_depth": 0},
                },
                {
                    "selector": {"module": "subject", "kind": "block", "line_start": 4},
                    "abstract": {"context_depth": 1},
                },
            ],
        },
    )
    snapshot = snapshot_target(target, config)
    driver = InterproceduralDriver(snapshot.module_map, config.to_dict())
    original_ops = driver.ops
    original_transfer = driver.transfer
    original_abstract = dict(driver.abstract)
    configure_driver(driver, plan=build_plan(snapshot, config), config=config)
    payload = driver.run(snapshot.scenarios).to_dict()
    require_members(payload["values"]["subject"]["result"], [3])
    assert driver.ops is original_ops
    assert driver.transfer is original_transfer
    assert driver.transfer.ops is original_ops
    assert driver.transfer.attributes.ops is original_ops
    assert driver.transfer.continuations.ops is original_ops
    assert driver.abstract == original_abstract
    assert driver.context_depth == 2
    call_frames = [unit.frame for unit in driver.units.values() if not unit.is_module]
    assert {len(frame.context) for frame in call_frames} == {0, 1}


def test_module_equations_do_not_reuse_a_different_effective_policy() -> None:
    driver = InterproceduralDriver([SourceModule("subject", "subject.py", "value=1\n")])
    policy = EffectiveRegionPolicy.from_abstract({"context_depth": 0})
    scoped = RegionExecutionContext(
        policy, ValueOperations(policy.value_policy, arena=driver.ops.arena)
    )
    driver.request_module("subject", Store())
    driver.request_module("subject", Store(), execution=scoped)
    assert len(driver.units) == 2
    assert {driver.execution_for(unit.frame).context_depth for unit in driver.units.values()} == {
        0,
        2,
    }


def test_context_cannot_renew_the_global_work_quota() -> None:
    driver = InterproceduralDriver(
        [SourceModule("subject", "subject.py", "pass\n")], {"resources": {"max_work": 2}}
    )
    policy = EffectiveRegionPolicy.from_abstract({"max_stmt_visits": 100})
    scoped = RegionExecutionContext(
        policy, ValueOperations(policy.value_policy, arena=driver.ops.arena)
    )
    driver.tick(execution=driver.default_execution)
    driver.tick(execution=scoped)
    with pytest.raises(BudgetStop, match="max_work"):
        driver.tick(execution=driver.default_execution)
    assert driver.work == 2


def test_context_rejects_foreign_arena_and_inconsistent_operations() -> None:
    driver = InterproceduralDriver([SourceModule("subject", "subject.py", "pass\n")])
    policy = EffectiveRegionPolicy.from_abstract({"context_depth": 0})
    with pytest.raises(ValueError, match="different identity arena"):
        driver.transfer_for(RegionExecutionContext(policy, ValueOperations(policy.value_policy)))
    with pytest.raises(ValueError, match="resolved value policy"):
        driver.transfer_for(
            RegionExecutionContext(
                policy,
                ValueOperations(ValuePolicy(max_scalar_pairs=1), arena=driver.ops.arena),
            )
        )
