"""Finite semantic and paid-boundary controls for rebuilt publication."""

from __future__ import annotations

from dataclasses import replace
from types import MappingProxyType

import pytest

from pythonstan import Source, ValueQuery, analyze
from pythonstan.analysis.contracts.identity import EntryScenario, ObjectId, SourceModule
from pythonstan.analysis.contracts.resources import ResourceStop, RunLedger
from pythonstan.analysis.domains.values import Value
from pythonstan.analysis.engines.abstract.driver import InterproceduralDriver
from pythonstan.analysis.engines.abstract.publication import (
    ExportedPublication,
    _export_value,
    _ExportOwnership,
)
from pythonstan.analysis.state.store import HeapObject


def shape(heap: dict[ObjectId, HeapObject], value: Value) -> dict[str, object]:
    return dict(_export_value(value, {key: obj.kind for key, obj in heap.items()}, heap))


def test_nested_tuple_and_list_shapes() -> None:
    inner, outer = ObjectId("inner"), ObjectId("outer")
    heap = {
        inner: HeapObject(
            "tuple", {0: Value.constant(1), 1: Value.constant(1)}, metadata={"length": 2}
        ),
        outer: HeapObject("list", {0: Value.ref(inner)}, metadata={"length": 1}),
    }
    assert shape(heap, Value.ref(outer))["items"] == [{"tuple": [1, 1]}]
    assert shape(heap, Value.ref(inner))["items"] == [1, 1]


@pytest.mark.parametrize("fault", ["missing", "default", "havoc", "extra", "length"])
def test_nonexact_container_keeps_reference_without_exact_items(fault: str) -> None:
    obj = HeapObject("list", {0: Value.constant(7)}, metadata={"length": 1})
    if fault == "missing":
        obj.maybe_missing.add(0)
    elif fault == "default":
        obj.default = Value.unknown()
    elif fault == "havoc":
        obj.havoc = True
    elif fault == "extra":
        obj.fields[1] = Value.constant(8)
    else:
        obj.metadata["length"] = 2
    result = shape({ObjectId("x"): obj}, Value.ref(ObjectId("x")))
    assert result["objects"] == ["x"]
    assert "items" not in result
    assert result["shape_projection"] == {"complete": False, "omitted_fields": ["items"]}


def test_reference_union_requires_all_alternatives_to_agree() -> None:
    a, b = ObjectId("a"), ObjectId("b")
    heap = {
        a: HeapObject("list", {0: Value.constant(7)}, metadata={"length": 1}),
        b: HeapObject("list", {0: Value.constant(7)}, metadata={"length": 1}),
    }
    union = Value(refs=frozenset({a, b}))
    assert shape(heap, union)["items"] == [7]
    heap[b].fields[0] = Value.constant(8)
    assert "items" not in shape(heap, union)
    heap.pop(b)
    assert "items" not in shape(heap, union)


def test_boolean_integer_alternatives_are_not_the_same_shape() -> None:
    a, b = ObjectId("a"), ObjectId("b")
    heap = {
        a: HeapObject("list", {0: Value.constant(1)}, metadata={"length": 1}),
        b: HeapObject("list", {0: Value.constant(True)}, metadata={"length": 1}),
    }
    assert "items" not in shape(heap, Value(refs=frozenset({a, b})))


def test_mixed_scalar_and_container_has_no_exact_shape() -> None:
    ref = ObjectId("a")
    value = replace(Value.constant(3), refs=frozenset({ref}))
    heap = {ref: HeapObject("list", {0: Value.constant(7)}, metadata={"length": 1})}
    assert "items" not in shape(heap, value)


def test_cycles_keep_refs_without_recursing_indefinitely() -> None:
    ref = ObjectId("cycle")
    heap = {ref: HeapObject("list", {0: Value.ref(ref)}, metadata={"length": 1})}
    result = shape(heap, Value.ref(ref))
    assert result["objects"] == ["cycle"] and "items" not in result


def test_depth_boundary_is_explicit_shape_omission() -> None:
    heap: dict[ObjectId, HeapObject] = {}
    value = Value.constant(1)
    for index in range(55):
        ref = ObjectId(str(index))
        heap[ref] = HeapObject("list", {0: value}, metadata={"length": 1})
        value = Value.ref(ref)
    assert "items" not in shape(heap, value)


def test_denied_first_visit_never_enters_value_export(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*args: object, **kwargs: object) -> object:
        raise AssertionError("Unfunded Value export")

    def deny(amount: int) -> None:
        raise ResourceStop("test_budget")

    monkeypatch.setattr(Value, "export", forbidden)
    with pytest.raises(ResourceStop, match="test_budget"):
        _export_value(Value.constant("x"), {}, {}, visit=deny)


def test_forged_frozen_payload_receipt_is_not_an_authority() -> None:
    values = MappingProxyType({})
    forged = ExportedPublication(values, values, _ExportOwnership(values, values))
    with pytest.raises(ValueError, match="genuine"):
        forged.validate()


def test_shared_driver_pays_actual_prepare_and_export() -> None:
    ledger = RunLedger(10000, 0, 0, memory_reader=lambda: 0)
    source = SourceModule("subject", "subject.py", "result=[(1,2)]\n")
    driver = InterproceduralDriver((source,), resource_ledger=ledger)
    snapshot = driver.run().to_dict()
    assert snapshot["values"]["subject"]["result"]["items"] == [{"tuple": [1, 2]}]
    assert ledger.work.by_kind["publication_export"] > 0
    assert ledger.raw_structural["publication_export"] > 0
    assert ledger.work.used <= ledger.work.limit


def test_shared_denied_snapshot_has_bounded_typed_controller() -> None:
    ledger = RunLedger(0, 0, 0, memory_reader=lambda: 0)
    driver = InterproceduralDriver(
        (SourceModule("subject", "subject.py", "result=17\n"),), resource_ledger=ledger
    )
    snapshot = driver.run().to_dict()
    assert snapshot["solver_status"] == "budget_exhausted"
    assert snapshot["coverage_status"] == "open" and snapshot["values"] == {}
    assert snapshot["diagnostics"] == [{"code": "publication_unavailable", "facts_omitted": True}]
    assert ledger.work.used == 0


def test_legacy_direct_driver_retains_core_budget_scope() -> None:
    sources = (
        SourceModule("slow", "slow.py", "x=0\nwhile x<1000:\n x=x+1\n"),
        SourceModule("wanted", "wanted.py", "result='after'\n"),
    )
    driver = InterproceduralDriver(
        sources,
        {
            "resources": {"max_work": 10},
            "queries": [
                {"kind": "value", "module": "wanted", "symbol": "result", "point": "module_exit"}
            ],
        },
    )
    snapshot = driver.run((EntryScenario("slow"), EntryScenario("wanted"))).to_dict()
    assert snapshot["values"]["wanted"]["result"]["constants"] == ["after"]
    assert snapshot["stats"]["work_scope"] == "legacy_driver_core"
    assert driver.resource_ledger.work.by_kind["publication_export"] == 0


def test_empty_known_module_query_keeps_scope_and_unavailable_symbol() -> None:
    result = analyze(
        Source("pass\n", module="subject"),
        config={"engine": "ai_primary"},
        queries=[ValueQuery("subject", "missing")],
    )
    assert result.solver_status == "converged"
    assert result.coverage_status == "closed_under_profile"
    assert result.query(ValueQuery("subject", "missing")).status == "unavailable"


def test_tuple_list_alternatives_do_not_claim_a_common_exact_shape() -> None:
    a, b = ObjectId("a"), ObjectId("b")
    heap = {
        a: HeapObject("list", {0: Value.constant(7)}, metadata={"length": 1}),
        b: HeapObject("tuple", {0: Value.constant(7)}, metadata={"length": 1}),
    }
    assert "items" not in shape(heap, Value(refs=frozenset({a, b})))


def test_nonsingleton_objects_need_shape_proof_not_identity_assumptions() -> None:
    ref = ObjectId("summary")
    heap = {
        ref: HeapObject("list", {0: Value.constant(7)}, singleton=False, metadata={"length": 1})
    }
    assert shape(heap, Value.ref(ref))["items"] == [7]
    heap[ref].maybe_missing.add(0)
    assert "items" not in shape(heap, Value.ref(ref))


def test_symbolic_length_cannot_be_ignored_in_exact_view() -> None:
    ref = ObjectId("sequence")
    obj = HeapObject(
        "list", {0: Value.constant(7)}, metadata={"length": 1, "length_value": Value.unknown()}
    )
    assert "items" not in shape({ref: obj}, Value.ref(ref))
    obj.metadata["length_value"] = Value.constant(2)
    assert "items" not in shape({ref: obj}, Value.ref(ref))
    obj.metadata["length_value"] = Value.constant(1)
    assert shape({ref: obj}, Value.ref(ref))["items"] == [7]


def test_issued_export_receipt_cannot_be_replayed_against_another_prepared_origin() -> None:
    from pythonstan.analysis.contracts.identity import CellId
    from pythonstan.analysis.domains.values import ValueOperations
    from pythonstan.analysis.engines.abstract.publication import (
        PreparedPublication,
        export_publication_values,
        finalize_publication,
        prepare_publication,
    )
    from pythonstan.analysis.state.flow import NormalStmtFlow
    from pythonstan.analysis.state.handles import StateHandle
    from pythonstan.analysis.state.store import Store
    from tests.regression.audit3.test_snapshot_publication import _runtime, _source, _unit

    def prepared(number: int) -> PreparedPublication:
        store = Store(cells={CellId("global:subject:x"): Value.constant(number)})
        unit = _unit("subject", (NormalStmtFlow(StateHandle.capture(store)),), is_module=True)
        return prepare_publication(_source((unit,)), ValueOperations())

    first = prepared(1)
    second = prepared(2)
    exported = export_publication_values(first)
    with pytest.raises(ValueError, match="origin"):
        finalize_publication(second, _runtime(), published_statistics={}, exported=exported)


def test_shared_publication_work_uses_export_phase_inside_core() -> None:
    ledger = RunLedger(10000, 0, 0, memory_reader=lambda: 0)
    driver = InterproceduralDriver(
        (SourceModule("subject", "subject.py", "result=17\n"),), resource_ledger=ledger
    )
    with ledger.phase("core"):
        snapshot = driver.run()
    assert snapshot.solver_status == "converged"
    assert ledger.phase_attempts["export"] == 1
    assert ledger.raw_structural_by_phase["export"]["publication_export"] > 0
    assert ledger.raw_structural_by_phase.get("core", {}).get("publication_export", 0) == 0
