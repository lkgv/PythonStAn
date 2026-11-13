"""Non-author witnesses for scoped driver operations and cache attachments."""

from __future__ import annotations

from pythonstan.analysis.contracts import SourceModule
from pythonstan.analysis.contracts.identity import CellId, ObjectId
from pythonstan.analysis.domains.values import Value, ValueOperations
from pythonstan.analysis.engines.abstract.driver import InterproceduralDriver
from pythonstan.analysis.policies.region import EffectiveRegionPolicy, RegionExecutionContext
from pythonstan.analysis.state.store import HeapObject, Store


def _contexts(driver: InterproceduralDriver) -> RegionExecutionContext:
    policy = EffectiveRegionPolicy.from_abstract(
        {**driver.default_execution.abstract, "int_domain_max_consts": 4}
    )
    return RegionExecutionContext(
        policy, ValueOperations(policy.value_policy, arena=driver.ops.arena)
    )


def test_scoped_module_input_uses_its_float_enumeration_policy() -> None:
    driver = InterproceduralDriver(
        [SourceModule("subject", "subject.py", "result=x\n")],
        {
            "abstract": {"int_domain_max_consts": 1},
            "inputs": {"bindings": {"subject.x": {"constants": [1.25, 2.5]}}},
        },
    )
    scoped = _contexts(driver)
    driver.request_module("subject", Store(), execution=scoped)
    entry = next(iter(driver.units.values())).entry
    assert entry.cells[CellId("global:subject:x")].floats == frozenset({1.25, 2.5})


def test_scoped_module_normalizes_heap_collisions_with_its_value_policy() -> None:
    driver = InterproceduralDriver(
        [SourceModule("subject", "subject.py", "pass\n")],
        {"abstract": {"int_domain_max_consts": 1}},
    )
    scoped = _contexts(driver)
    root = Store(
        heap={
            ObjectId("A"): HeapObject("instance", {"value": Value.constant(1.25)}),
            ObjectId("B"): HeapObject("instance", {"value": Value.constant(2.5)}),
        }
    )
    driver.ops.arena.publish(
        driver.ops.arena.prepare({ObjectId("A"): ObjectId("S"), ObjectId("B"): ObjectId("S")})
    )
    driver.request_module("subject", root, execution=scoped)
    entry = next(iter(driver.units.values())).entry
    assert entry.heap[ObjectId("S")].fields["value"].floats == frozenset({1.25, 2.5})
    assert root.identity_epoch == 0 and set(root.heap) == {ObjectId("A"), ObjectId("B")}
