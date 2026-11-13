"""Independent finite gamma checks for reconstructed Native heap updates."""

from __future__ import annotations

import pytest

from pythonstan.analysis.composition.region_engine import NativeRegionEngine
from pythonstan.analysis.contracts.identity import FrameId, ObjectId, SourceModule
from pythonstan.analysis.domains.ints import IntLattice, Parity
from pythonstan.analysis.domains.values import Value
from pythonstan.analysis.engines.abstract.driver import InterproceduralDriver
from pythonstan.analysis.state.flow import NormalStmtFlow
from pythonstan.analysis.state.handles import StateHandle
from pythonstan.analysis.state.store import Frame, HeapObject, Store

from .domain_oracles import concrete_result, public_answer, require_members


def int_member(value: Value, concrete: int) -> bool:
    """Interpret the documented integer component, without an analyzer order call."""
    if value.top or "int" in value.top_sorts:
        return True
    integer = value.ints
    if integer.is_bottom:
        return False
    if integer.is_top:
        return True
    if integer.consts:
        return concrete in integer.consts
    return (
        (integer.lo is None or integer.lo <= concrete)
        and (integer.hi is None or concrete <= integer.hi)
        and (
            integer.parity is Parity.TOP
            or integer.parity is Parity.EVEN
            and concrete % 2 == 0
            or integer.parity is Parity.ODD
            and concrete % 2 != 0
        )
    )


def contains_map(obj: HeapObject, concrete: dict[int, int]) -> bool:
    """Conjoin independent key presence, values, and cardinality components."""
    if obj.havoc:
        return True
    if obj.kind != "dict":
        return False
    length = obj.metadata.lookup("length")
    length_value = obj.metadata.lookup("length_value")
    if length is not None and length != len(concrete):
        return False
    if length_value is not None and not int_member(length_value, len(concrete)):
        return False
    if any(key not in concrete for key in obj.fields if key not in obj.maybe_missing):
        return False
    return all(
        int_member(obj.fields.get(key, obj.default), value) for key, value in concrete.items()
    )


def execute_write(obj: HeapObject, statement: str) -> HeapObject:
    driver = InterproceduralDriver(
        (SourceModule("subject", "subject.py", statement + "\n"),),
        {"resources": {"max_work": 10000, "max_seconds": 2, "max_memory_mb": 512}},
    )
    frame = Frame("subject", "subject", (), FrameId("root"), is_module=True)
    identity = ObjectId("dict_object")
    store = Store(cells={frame.cell("d"): Value.ref(identity)}, heap={identity: obj})
    original = NormalStmtFlow(StateHandle.capture(store))
    flows = NativeRegionEngine().execute(driver.trees["subject"].body[0], original, frame, driver)
    normal = [flow for flow in flows if flow.kind == "normal"]
    assert len(normal) == 1
    assert not any(flow.kind == "raise" for flow in flows)
    return normal[0].store.heap[identity]


def test_oracle_rejects_false_exact_length_and_false_must_presence() -> None:
    wrong_length = HeapObject(
        "dict", fields={0: Value.constant(9), 1: Value.constant(1)}, metadata={"length": 1}
    )
    assert not contains_map(wrong_length, {0: 9, 1: 1})
    wrong_must = HeapObject(
        "dict", fields={0: Value.constant(9), 1: Value.constant(1)}, metadata={}
    )
    assert not contains_map(wrong_must, {1: 1})
    assert contains_map(
        HeapObject("dict", fields={1: Value.constant(1)}, metadata={"length": 1}), {1: 1}
    )


def test_strong_new_key_insertion_updates_independent_length() -> None:
    original = HeapObject("dict", fields={1: Value.constant(1)}, metadata={"length": 1})
    result = execute_write(original, "d[0]=9")
    assert contains_map(result, {1: 1, 0: 9}), result
    assert result.metadata.lookup("length") == 2
    assert not result.havoc
    assert original.metadata.lookup("length") == 1 and 0 not in original.fields


def test_weak_new_key_keeps_untouched_alias_and_inserted_object() -> None:
    original = HeapObject(
        "dict", fields={1: Value.constant(1)}, singleton=False, metadata={"length": 1}
    )
    result = execute_write(original, "d[0]=9")
    assert contains_map(result, {1: 1}), result
    assert contains_map(result, {1: 1, 0: 9}), result
    assert not result.singleton and not result.havoc


@pytest.mark.parametrize("singleton", [True, False])
def test_existing_must_key_overwrite_does_not_invent_length(singleton: bool) -> None:
    original = HeapObject(
        "dict", fields={1: Value.constant(1)}, singleton=singleton, metadata={"length": 1}
    )
    result = execute_write(original, "d[1]=9")
    assert contains_map(result, {1: 9}), result
    if not singleton:
        assert contains_map(result, {1: 1}), result
    assert result.metadata.lookup("length") == 1 and not result.havoc


def test_maybe_present_key_weak_write_covers_both_cardinalities() -> None:
    original = HeapObject(
        "dict",
        fields={0: Value.constant(8), 1: Value.constant(1)},
        maybe_missing={0},
        singleton=False,
        metadata={"length_value": Value(ints=IntLattice.from_consts(frozenset({1, 2})))},
    )
    result = execute_write(original, "d[0]=9")
    assert contains_map(result, {1: 1}), result
    assert contains_map(result, {1: 1, 0: 9}), result
    assert not result.havoc


def test_unknown_default_does_not_prove_absent_before_insert() -> None:
    original = HeapObject(
        "dict",
        fields={1: Value.constant(1)},
        default=Value.constant(8),
        singleton=False,
        metadata={"length_value": Value(ints=IntLattice.from_consts(frozenset({1, 2})))},
    )
    result = execute_write(original, "d[0]=9")
    assert contains_map(result, {1: 1}), result
    assert contains_map(result, {1: 1, 0: 9}), result
    assert not result.havoc


def test_bool_integer_duplicate_is_an_overwrite_not_new_cardinality() -> None:
    result = execute_write(
        HeapObject("dict", fields={1: Value.constant(1)}, metadata={"length": 1}), "d[True]=9"
    )
    assert contains_map(result, {1: 9})
    assert result.metadata.lookup("length") == 1


def test_literal_failure_does_not_publish_partial_candidate_to_old_name() -> None:
    source = "d={'keep':1}\ntry:\n    d={0:2,1:missing}\nexcept NameError:\n    result=d['keep']\n"
    assert concrete_result(source) == 1
    require_members(public_answer(source, native_line=3), [1])


def test_repeated_one_entry_literals_retain_old_alias_without_union_length() -> None:
    driver = InterproceduralDriver(
        (SourceModule("subject", "subject.py", "d={key:1}\n"),),
        {"resources": {"max_work": 10000, "max_seconds": 2, "max_memory_mb": 512}},
    )
    frame = Frame("subject", "subject", (), FrameId("root"), is_module=True)
    engine = NativeRegionEngine()
    original = Store(cells={frame.cell("key"): Value.constant(0)})
    first_flows = engine.execute(
        driver.trees["subject"].body[0],
        NormalStmtFlow(StateHandle.capture(original)),
        frame,
        driver,
    )
    first = next(flow.store for flow in first_flows if flow.kind == "normal")
    second_input = first.copy()
    second_input.cells[frame.cell("key")] = Value.constant(1)
    second_flows = engine.execute(
        driver.trees["subject"].body[0],
        NormalStmtFlow(StateHandle.capture(second_input)),
        frame,
        driver,
    )
    second = next(flow.store for flow in second_flows if flow.kind == "normal")
    references = second.cells[frame.cell("d")].refs
    assert len(references) == 1
    joined = second.heap[next(iter(references))]
    assert contains_map(joined, {0: 1})
    assert contains_map(joined, {1: 1})
    assert not contains_map(joined, {0: 1, 1: 1})
    assert not joined.singleton and not joined.havoc
    first_ref = next(iter(first.cells[frame.cell("d")].refs))
    assert first.heap[first_ref].fields.keys() == {0}


def test_one_abstract_literal_key_still_has_one_entry_in_each_concrete_map() -> None:
    driver = InterproceduralDriver(
        (SourceModule("subject", "subject.py", "d={key:7}\n"),),
        {"resources": {"max_work": 10000, "max_seconds": 2, "max_memory_mb": 512}},
    )
    frame = Frame("subject", "subject", (), FrameId("root"), is_module=True)
    store = Store(cells={frame.cell("key"): Value(ints=IntLattice.from_consts(frozenset({0, 1})))})
    flows = NativeRegionEngine().execute(
        driver.trees["subject"].body[0], NormalStmtFlow(StateHandle.capture(store)), frame, driver
    )
    result = next(flow.store for flow in flows if flow.kind == "normal")
    obj = result.heap[next(iter(result.cells[frame.cell("d")].refs))]
    assert contains_map(obj, {0: 7}) and contains_map(obj, {1: 7})
    assert not contains_map(obj, {0: 7, 1: 7})
    assert not obj.havoc
