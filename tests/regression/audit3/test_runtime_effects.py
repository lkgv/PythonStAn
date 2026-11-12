"""A real call/effect/exit/query path for the TYPE-01 migration slice."""

from __future__ import annotations

import pytest

from pythonstan.analysis.contracts import SourceModule
from pythonstan.analysis.contracts.identity import CellId, ObjectId
from pythonstan.analysis.domains.values import Value, ValueOperations
from pythonstan.analysis.engines.abstract.driver import InterproceduralDriver
from pythonstan.analysis.engines.abstract.state import Flow, HeapObject, Store
from pythonstan.analysis.state.effects import CellWrite, Havoc, HeapDelete, HeapWrite


@pytest.mark.parametrize("exit_kind", ["return", "raise"])
@pytest.mark.parametrize("location", ["cell", "heap"])
def test_actual_call_uses_typed_effects_on_both_exit_kinds(exit_kind: str, location: str) -> None:
    setup = "marker=0\n" if location == "cell" else "box=[0]\n"
    write = "global marker\n    marker=7" if location == "cell" else "box[0]=7"
    read = "marker" if location == "cell" else "box[0]"
    exit_statement = "return 3" if exit_kind == "return" else "raise ValueError()"
    source = (
        setup
        + "def mutate():\n    "
        + write
        + "\n    "
        + exit_statement
        + "\ntry:\n    mutate()\nexcept ValueError:\n    pass\nresult="
        + read
        + "\n"
    )
    driver = InterproceduralDriver(
        (SourceModule("subject", "subject.py", source),),
        {"resources": {"max_work": 10000, "max_seconds": 8, "max_memory_mb": 1536}},
    )
    payload = driver.run().to_dict()
    assert payload["values"]["subject"]["result"]["constants"] == [7]
    assert payload["coverage_status"] == "closed_under_profile"
    callees = [unit for unit in driver.units.values() if unit.frame.qualname == "subject.mutate"]
    assert callees and all(unit.outcomes for unit in callees)
    expected_type = CellWrite if location == "cell" else HeapWrite
    assert any(
        isinstance(effect, expected_type)
        for unit in callees
        for out in unit.outcomes
        for effect in out.store.writes
    )
    assert all(
        not isinstance(effect, tuple)
        for unit in driver.units.values()
        for out in unit.outcomes
        for effect in out.store.writes
    )
    assert any(out.kind == exit_kind for unit in callees for out in unit.outcomes)


def test_joined_delete_does_not_kill_the_other_branch_write() -> None:
    """A footprint materializes the poststate; it is not a command replay log."""
    owner = ObjectId("box")
    ops = ValueOperations()
    caller = Store(heap={owner: HeapObject("dict", {"key": Value.constant("before")})})
    written = caller.copy()
    written.write_slot(owner, "key", Value.constant("after"), ops)
    deleted = caller.copy()
    deleted.heap[owner].fields.pop("key")
    deleted.heap[owner].maybe_missing.add("key")
    deleted.writes.add(HeapDelete(owner, "key"))
    merged = written.join(deleted, ops)
    applied = caller.apply(Flow(merged), ops)
    assert applied.heap[owner].fields["key"].exact_constants() == ["after"]
    assert "key" in applied.heap[owner].maybe_missing
    assert caller.heap[owner].fields["key"].exact_constants() == ["before"]


def test_unknown_effect_preserves_private_caller_state_and_havocs_shared_cells() -> None:
    """Exercise real unknown publication and application for both exit kinds."""
    driver = InterproceduralDriver((SourceModule("subject", "subject.py", ""),))
    private = CellId("local:caller:x")
    global_cell = CellId("global:subject:shared")
    captured = CellId("local:owner:captured")
    function = ObjectId("function:capturer")
    caller = Store(
        cells={
            private: Value.constant("current"),
            global_cell: Value.constant(1),
            captured: Value.constant(2),
        },
        heap={function: HeapObject("function", metadata={"closure": (("captured", captured),)})},
    )
    summary_entry = caller.copy()
    summary_entry.cells[private] = driver.ops.join(Value.constant("stale"), caller.cells[private])
    outcomes = driver.transfer.unknown(summary_entry, None, "review_external")
    assert {outcome.kind for outcome in outcomes} == {"normal", "raise"}
    for outcome in outcomes:
        havoc = next(effect for effect in outcome.store.writes if isinstance(effect, Havoc))
        assert havoc.cells == frozenset({global_cell, captured})
        applied = caller.apply(outcome, driver.ops)
        assert applied.cells[private].exact_constants() == ["current"]
        assert applied.cells[global_cell].top and applied.cells[global_cell].unbound
        assert applied.cells[captured].top and applied.cells[captured].unbound
