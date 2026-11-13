"""Native allocation/cardinality recovery, with finite concrete membership."""

from __future__ import annotations

import ast

import pytest

from pythonstan.analysis.composition.region_engine import NativeRegionEngine
from pythonstan.analysis.contracts.identity import FrameId, ObjectId, SourceModule
from pythonstan.analysis.domains.values import Value, ValueOperations
from pythonstan.analysis.engines.abstract.driver import InterproceduralDriver
from pythonstan.analysis.state.flow import Flow, NormalStmtFlow
from pythonstan.analysis.state.handles import StateHandle
from pythonstan.analysis.state.store import Frame, HeapObject, Store


def harness(source: str) -> tuple[InterproceduralDriver, Frame, NativeRegionEngine]:
    driver = InterproceduralDriver((SourceModule("subject", "subject.py", source),))
    frame = Frame("subject", "subject", (), FrameId("root"), is_module=True)
    return driver, frame, NativeRegionEngine()


def step(
    driver: InterproceduralDriver,
    frame: Frame,
    engine: NativeRegionEngine,
    node: ast.stmt,
    store: Store,
) -> Store:
    flows: list[Flow] = engine.execute(
        node, NormalStmtFlow(StateHandle.capture(store)), frame, driver
    )
    normal = [flow for flow in flows if flow.kind == "normal"]
    assert len(normal) == 1
    return normal[0].store


def obj_for(store: Store, frame: Frame, name: str) -> HeapObject:
    refs = store.cells[frame.cell(name)].refs
    assert len(refs) == 1
    return store.heap[next(iter(refs))]


def length_covers(obj: HeapObject, expected: int) -> bool:
    if obj.havoc:
        return True
    value = obj.metadata.lookup("length_value")
    if value is not None:
        integer = value.ints
        return (
            integer.is_top
            or expected in integer.consts
            or not integer.consts
            and not integer.is_bottom
            and (integer.lo is None or integer.lo <= expected)
            and (integer.hi is None or expected <= integer.hi)
        )
    return obj.metadata.lookup("length") == expected


def test_repeated_varying_key_literal_covers_both_maps_at_length_one() -> None:
    driver, frame, engine = harness("d={key:'present'}\n")
    node = driver.trees["subject"].body[0]
    first = step(driver, frame, engine, node, Store(cells={frame.cell("key"): Value.constant(0)}))
    first.cells[frame.cell("key")] = Value.constant(1)
    second = step(driver, frame, engine, node, first)
    obj = obj_for(second, frame, "d")
    assert {0, 1} <= obj.fields.keys()
    assert {0, 1} <= obj.maybe_missing
    assert length_covers(obj, 1) and not obj.singleton
    assert first.heap[next(iter(first.cells[frame.cell("d")].refs))].fields.keys() == {0}


def test_abstract_literal_key_does_not_turn_one_entry_into_length_two() -> None:
    driver, frame, engine = harness("d={key:7}\n")
    key = ValueOperations().join(Value.constant(0), Value.constant(1))
    store = step(
        driver,
        frame,
        engine,
        driver.trees["subject"].body[0],
        Store(cells={frame.cell("key"): key}),
    )
    obj = obj_for(store, frame, "d")
    assert length_covers(obj, 1)
    assert {0, 1} <= obj.maybe_missing


def test_singleton_dict_insertion_updates_independent_cardinality() -> None:
    driver, frame, engine = harness("d={1:1}\nd[0]=9\n")
    store = Store()
    for node in driver.trees["subject"].body:
        store = step(driver, frame, engine, node, store)
    obj = obj_for(store, frame, "d")
    assert obj.fields[0].exact_constants() == [9]
    assert length_covers(obj, 2)
    assert not length_covers(obj, 1)


def test_weak_summary_insertion_keeps_no_insert_and_insert_cardinality() -> None:
    driver, frame, engine = harness("d[0]=9\n")
    identifier = ObjectId("summary")
    store = Store(
        cells={frame.cell("d"): Value.ref(identifier)},
        heap={
            identifier: HeapObject(
                "dict", {1: Value.constant(1)}, singleton=False, metadata={"length": 1}
            )
        },
    )
    updated = step(driver, frame, engine, driver.trees["subject"].body[0], store)
    obj = updated.heap[identifier]
    assert length_covers(obj, 1) and length_covers(obj, 2)
    assert 0 in obj.maybe_missing


@pytest.mark.parametrize(
    "source,expected",
    [("d={1:1}\nd[True]=9\n", 1), ("d={}\nd[0]=9\n", 1), ("d={False:1,0:2}\n", 1)],
)
def test_definite_key_presence_and_bool_int_normalization(source: str, expected: int) -> None:
    driver, frame, engine = harness(source)
    store = Store()
    for node in driver.trees["subject"].body:
        store = step(driver, frame, engine, node, store)
    assert length_covers(obj_for(store, frame, "d"), expected)
