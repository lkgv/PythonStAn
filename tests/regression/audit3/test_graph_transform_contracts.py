"""Independent finite graph, ownership and quotient controls for TYPE/STATE."""

from __future__ import annotations

import ast
import inspect
import struct
import types
import typing
from dataclasses import dataclass, fields

import pytest

from pythonstan.analysis.composition.state_adapter import FullStateAdapter
from pythonstan.analysis.contracts.identity import CellId, IdentityEpoch, ObjectId, StateRevision
from pythonstan.analysis.domains.reabstract import IdentitySubstitution
from pythonstan.analysis.domains.values import Value, ValueOperations
from pythonstan.analysis.state.bindings import BindingRef
from pythonstan.analysis.state.boundary import BoundaryObject, HostStateBoundary
from pythonstan.analysis.state.flow import (
    BreakFlow,
    ContinueFlow,
    ExceptionValue,
    FlowTail,
    NormalExprFlow,
    NormalStmtFlow,
    RaiseFlow,
    ReturnFlow,
    SuspendFlow,
)
from pythonstan.analysis.state.guards import MembershipGuard
from pythonstan.analysis.state.handles import ObjectHandle, StateHandle
from pythonstan.analysis.state.metadata import (
    ContainerMeta,
    GeneratorMeta,
    Metadata,
    StateHandleField,
)
from pythonstan.analysis.state.store import Frame, HeapObject, Store
from pythonstan.analysis.state.transform import (
    PayloadTransformer,
    detached_payload,
    payload_signature,
    semantic_payload_equal,
    walk_graph,
)


def root_graph() -> Store:
    root = Store(cells={CellId("x"): Value.constant("before")})
    root.heap = {
        ObjectId(name): HeapObject(
            "generator", {"payload": Value.constant(name)}, metadata={"entry": root}
        )
        for name in ("A", "B", "C")
    }
    return root


@pytest.mark.parametrize("reverse", [False, True])
def test_three_way_cyclic_quotient_canonicalizes_standalone_owner_aliases(reverse: bool) -> None:
    root = root_graph()
    names = ("C", "B", "A") if reverse else ("A", "B", "C")
    graph = {name: root.heap[ObjectId(name)] for name in names}
    graph["root"] = root
    mapped = PayloadTransformer(
        {ObjectId(name): ObjectId("S") for name in names}, ValueOperations()
    ).visit(graph)
    current = mapped["root"]
    canonical = current.heap[ObjectId("S")]
    assert all(mapped[name] is canonical for name in names)
    assert canonical.metadata["entry"] is current
    assert set(canonical.fields["payload"].exact_constants()) == {"A", "B", "C"}
    assert not canonical.singleton
    current.write_cell(CellId("x"), Value.constant("after"))
    assert canonical.metadata["entry"].cells[CellId("x")].exact_constants() == ["after"]
    assert root.cells[CellId("x")].exact_constants() == ["before"]


@pytest.mark.parametrize("mutable", [False, True])
def test_reference_roles_precede_alias_first_tuple_and_list_copy(mutable: bool) -> None:
    objects = [ObjectId("A"), ObjectId("B")] if mutable else (ObjectId("A"), ObjectId("B"))
    pair = ("A", CellId("A"))
    bindings = [pair] if mutable else (pair,)
    metadata = Metadata.from_component_graph(
        "class", {"mro": objects, "closure": bindings, "label": "A"}
    )
    graph = {"objects": objects, "pair": pair, "bindings": bindings, "metadata": metadata}
    aliases = IdentitySubstitution(
        {ObjectId("A"): ObjectId("S"), ObjectId("B"): ObjectId("S")}, {CellId("A"): CellId("C")}
    )
    mapped = PayloadTransformer(aliases, ValueOperations()).visit(graph)
    result = mapped["metadata"]
    assert mapped["objects"] is result["mro"]
    assert mapped["bindings"] is result["closure"]
    assert mapped["pair"] is result["closure"][0]
    assert tuple(result["mro"]) == ("S", "S")
    assert result["closure"][0] == ("A", "C")
    assert result["label"] == "A"


def test_handle_cloning_rebinds_cycle_owner_and_keeps_checkpoint_versions() -> None:
    root = Store(identity_epoch=2, state_revision=StateRevision(7))
    flow = SuspendFlow(
        StateHandle.capture(root), Value.constant(1), FlowTail(resume_target={"root": root})
    )
    root.heap[ObjectId("G")] = HeapObject("generator", metadata={"entry": root, "pending": flow})
    copied = detached_payload(flow)
    assert copied is copied.store.heap[ObjectId("G")].metadata["pending"]
    assert copied.state.root is copied.store and copied.state.is_current()
    assert copied.state.epoch == 2 and copied.state.revision == 7
    assert copied.resume_target["root"] is copied.store
    changed = PayloadTransformer(epoch=5).visit(flow)
    assert changed.state.epoch == changed.store.identity_epoch == 5
    assert changed.state.revision == 7


def test_physical_versions_do_not_change_semantic_order_or_signature() -> None:
    left, right = (
        Store(identity_epoch=2, state_revision=StateRevision(3)),
        Store(identity_epoch=9, state_revision=StateRevision(13)),
    )
    assert semantic_payload_equal(left, right)
    assert semantic_payload_equal(StateHandle.capture(left), StateHandle.capture(right))
    assert payload_signature(StateHandle.capture(left)) == payload_signature(
        StateHandle.capture(right)
    )
    first, second = (
        ObjectHandle(ObjectId("A"), IdentityEpoch(1)),
        ObjectHandle(ObjectId("A"), IdentityEpoch(9)),
    )
    assert semantic_payload_equal(first, second)
    assert payload_signature(first) == payload_signature(second)


def test_binding_and_object_handle_use_separate_namespaces() -> None:
    aliases = IdentitySubstitution({ObjectId("A"): ObjectId("O")}, {CellId("A"): CellId("C")})
    binding, handle = PayloadTransformer(aliases, ValueOperations(), epoch=4).visit(
        (BindingRef("A", CellId("A"), "local"), ObjectHandle(ObjectId("A"), IdentityEpoch(1)))
    )
    assert binding == BindingRef("A", CellId("C"), "local")
    assert handle == ObjectHandle(ObjectId("O"), IdentityEpoch(4))


def test_failed_preparation_charges_work_without_publishing_source_mutations() -> None:
    root = root_graph()
    before = payload_signature(root)
    charges: list[int] = []

    def stop(amount: int) -> None:
        charges.append(amount)
        if len(charges) == 12:
            raise RuntimeError("budget sentinel")

    transform = PayloadTransformer({ObjectId("A"): ObjectId("S")}, ValueOperations(), charge=stop)
    with pytest.raises(RuntimeError, match="budget sentinel"):
        transform.visit(root)
    assert sum(charges) == transform.work == transform.preparation_work == 12
    assert payload_signature(root) == before
    assert root.heap[ObjectId("A")].metadata["entry"] is root


def test_decoder_rejects_unknown_dataclasses_deep_items_and_container_subclasses() -> None:
    @dataclass
    class Unknown:
        value: str

    class UnknownList(list[str]):
        pass

    for value in (Unknown("hidden"), {"nested": [object()]}, UnknownList(["A"])):
        with pytest.raises(TypeError):
            detached_payload(value)
    with pytest.raises(TypeError, match="dictionary key"):
        detached_payload({object(): Value.constant(1)})


def test_all_seven_completions_preserve_type_tail_and_owned_boundary_graph() -> None:
    root = root_graph()
    state, tail = StateHandle.capture(root), FlowTail(resume_target={"root": root})
    flows = (
        NormalExprFlow(state, Value.constant(None), tail),
        NormalStmtFlow(state, tail),
        ReturnFlow(state, Value.constant(3), tail),
        RaiseFlow(state, ExceptionValue(Value.constant("error"), "ValueError"), tail),
        BreakFlow(state, tail),
        ContinueFlow(state, tail),
        SuspendFlow(state, Value.constant("yielded"), tail),
    )
    adapter = FullStateAdapter()
    for flow in flows:
        boundary = adapter.encode(flow)
        assert isinstance(boundary.heap[ObjectId("A")], BoundaryObject)
        assert type(boundary._store_root.heap[ObjectId("A")]) is HeapObject
        restored = adapter.decode(boundary)
        assert type(restored) is type(flow)
        assert restored.resume_target["root"] is restored.store
        assert restored.store.heap[ObjectId("A")].metadata["entry"] is restored.store
        assert restored.exception == flow.exception
        assert adapter.check_roundtrip(flow, ValueOperations())


def test_generator_metadata_stores_one_checkpoint_and_legacy_reads_do_not_recapture() -> None:
    root = Store(identity_epoch=3, state_revision=StateRevision(5))
    checkpoint = StateHandle.capture(root)
    metadata = Metadata.from_record("generator", GeneratorMeta(entry=checkpoint))
    assert metadata.entry_handle() is checkpoint
    assert metadata.record.entry is checkpoint
    assert metadata["entry"] is root and metadata.lookup("entry") is root
    assert isinstance(metadata.transformation_entries()["entry"], StateHandleField)
    legacy = Metadata("generator", {"entry": root})
    captured = legacy.entry_handle()
    root.write_cell(CellId("later"), Value.constant(1))
    assert legacy.lookup("entry") is root and legacy.entry_handle() is captured
    assert not captured.is_current() and captured.revision == 5


@pytest.mark.parametrize(
    "literal", [1j, complex(-0.0, 1.0), complex(1.0, -0.0), complex(float("inf"), float("nan"))]
)
def test_admitted_complex_component_survives_store_copy_and_charged_preparation(
    literal: complex,
) -> None:
    metadata = Metadata.from_component_graph("object", {"capture": literal})
    root = Store(heap={ObjectId("O"): HeapObject("object", metadata=metadata)})
    copied = root.copy()
    transformed = PayloadTransformer({ObjectId("unused"): ObjectId("S")}, ValueOperations()).visit(
        root
    )
    expected = struct.pack("!dd", literal.real, literal.imag)
    assert payload_signature(literal) == ("complex", expected)
    for current in (copied, transformed):
        actual = current.heap[ObjectId("O")].metadata["capture"]
        assert struct.pack("!dd", actual.real, actual.imag) == expected
        assert semantic_payload_equal(root, current)
        assert payload_signature(root) == payload_signature(current)
    assert not semantic_payload_equal(complex(0.0, 1.0), complex(-0.0, 1.0))


def test_unregistered_record_subclasses_are_rejected_at_metadata_admission() -> None:
    @dataclass
    class ExtraStore(Store):
        extra: str = "hidden"

    @dataclass(frozen=True)
    class ExtraFrame(Frame):
        extra: str = "hidden"

    @dataclass(frozen=True)
    class ExtraFlow(NormalStmtFlow):
        extra: str = "hidden"

    @dataclass(frozen=True)
    class ExtraContainer(ContainerMeta):
        extra: str = "hidden"

    state = ExtraStore()
    frame = ExtraFrame("subject", "f", (), "frame")
    flow = ExtraFlow(StateHandle.capture(Store()))
    for value in (state, frame, flow):
        with pytest.raises(TypeError, match="Unregistered"):
            Metadata.from_component_graph("object", {"capture": value})
        with pytest.raises(TypeError):
            detached_payload(value)
    with pytest.raises(TypeError, match="registered Store"):
        Metadata("generator", {"entry": state})
    with pytest.raises(TypeError, match="registered Frame"):
        Metadata("generator", {"frame": frame})
    with pytest.raises(TypeError, match="metadata record"):
        Metadata.from_record("dict", ExtraContainer(length=1))


def test_explicit_boundary_subtype_is_registered_and_guard_alias_invalidation_is_shared() -> None:
    boundary = BoundaryObject("object", {}, Value.bottom(), set(), True, frozenset(), {}, False)
    captured = Metadata.from_component_graph("object", {"capture": boundary})
    assert type(detached_payload(captured)["capture"]) is BoundaryObject
    root = Store(guards={"condition": MembershipGuard(ObjectId("A"), "x", True)})
    mapped = PayloadTransformer({ObjectId("A"): ObjectId("S")}, ValueOperations()).visit(
        {"guards_first": root.guards, "root": root}
    )
    assert mapped["guards_first"] is mapped["root"].guards and not mapped["guards_first"]


def test_host_boundary_and_finite_walk_preserve_typed_fields_charge_and_aliases() -> None:
    root = root_graph()
    boundary = HostStateBoundary({}, {}, (root, StateHandle.capture(root)), [("event", "point")])
    copied = detached_payload(boundary)
    assert copied.extras[1].read() is copied.extras[0]
    stores: list[Store] = []
    values: list[Value] = []
    charges: list[int] = []
    work = walk_graph(copied, on_store=stores.append, on_value=values.append, charge=charges.append)
    assert stores == [copied.extras[0]]
    assert values and work == sum(charges)
    assert copied.units == {} and copied.functions == {} and copied.events == boundary.events


def test_projection_covers_every_actual_record_field() -> None:
    import pythonstan.analysis.state.transform as transform

    node = ast.parse(inspect.getsource(transform._fields)).body[0]
    assert isinstance(node, ast.FunctionDef)
    declared = {}
    for branch in node.body:
        if not isinstance(branch, ast.If) or not isinstance(branch.test, ast.Call):
            continue
        name = branch.test.args[1]
        returned = branch.body[0]
        assert isinstance(name, ast.Name) and isinstance(returned, ast.Return)
        assert isinstance(returned.value, ast.Tuple)
        declared[name.id] = {pair.elts[0].value for pair in returned.value.elts}

    def records(alias: object) -> set[type[object]]:
        if isinstance(alias, typing.TypeAliasType):
            return records(alias.__value__)
        if typing.get_origin(alias) is types.UnionType:
            return set().union(*(records(item) for item in typing.get_args(alias)))
        assert isinstance(alias, type)
        return {alias}

    for record in records(transform.GraphRecord):
        expected = (
            set(MembershipGuard._fields)
            if record is MembershipGuard
            else {field.name for field in fields(record)}
        )
        assert declared[record.__name__] == expected, record.__name__


def test_summary_dependency_checkpoint_is_copied_without_object_namespace_aliases() -> None:
    from pythonstan.analysis.contracts.identity import PolicyId, UnitId
    from pythonstan.analysis.state.dependencies import SummaryDependency
    from pythonstan.analysis.state.store import Unit

    dependency = SummaryDependency(
        UnitId("producer"), StateRevision(7), "scope", PolicyId("policy"), IdentityEpoch(3)
    )
    unit = Unit(UnitId("U"), Frame("subject", "f", (), "frame"), (), Store())
    unit.dependencies[UnitId("producer")] = dependency
    boundary = HostStateBoundary({UnitId("U"): unit}, {}, (), [])
    mapped = PayloadTransformer(
        {ObjectId("producer"): ObjectId("S")}, ValueOperations(), epoch=8
    ).visit(boundary)
    current = mapped.units[UnitId("U")]
    assert current.dependencies[UnitId("producer")] == dependency
    assert current.dependencies[UnitId("producer")] is not dependency
    assert current.entry.identity_epoch == 8
    assert current.key == UnitId("U")


def test_normal_typed_metadata_record_validates_its_primitive_fields() -> None:
    class ExtraInt(int):
        pass

    with pytest.raises(TypeError, match="integer"):
        Metadata.from_record("dict", ContainerMeta(length=ExtraInt(1)))


@pytest.mark.parametrize(
    "source,cached",
    [
        (Store(), Value.constant("wrong-shape")),
        (Store(), None),
        (Metadata("object"), Store()),
        ((Value.constant(1),), [Value.constant(1)]),
        ({"value": Value.constant(1)}, [Value.constant(1)]),
    ],
)
def test_supplied_memo_rejects_incompatible_node_shapes(source: object, cached: object) -> None:
    with pytest.raises(TypeError, match="Incoherent payload memo"):
        detached_payload(source, {id(source): cached})


def test_coherent_record_anchor_and_unfinished_tuple_cycles_remain_admitted() -> None:
    source, target = Store(), Store()
    copied_handle = detached_payload(StateHandle.capture(source), {id(source): target})
    assert copied_handle.read() is target
    shared = []
    original = (shared,)
    shared.append(original)
    copied = detached_payload(original)
    assert copied is not original and copied[0] is not shared
    assert copied[0][0] is copied
