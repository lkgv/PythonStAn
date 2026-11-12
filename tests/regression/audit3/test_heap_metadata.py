"""Typed metadata schema and real graph-transport ownership controls."""

from __future__ import annotations

import ast

import pytest

from pythonstan.analysis.contracts.identity import CellId, ObjectId, ProgramPointId
from pythonstan.analysis.domains.values import Value
from pythonstan.analysis.state.effects import CellWrite
from pythonstan.analysis.state.metadata import (
    BindingsField,
    ClassMeta,
    ComponentPayload,
    ContainerMeta,
    DictionaryIteratorMeta,
    GeneratorMeta,
    LiteralGraphField,
    Metadata,
    NamespaceMeta,
    ReferenceField,
    ReferencesField,
    SummaryMeta,
    validate_component,
)
from pythonstan.analysis.state.store import HeapObject, Store


def test_container_record_is_the_actual_mutated_storage() -> None:
    metadata = Metadata("dict", {"length": 0})
    assert isinstance(metadata.record, ContainerMeta)
    assert metadata.record.length == 0
    metadata["length"] = 2
    assert metadata.record.length == 2
    assert metadata.lookup("length", 99) == 2
    del metadata["length"]
    assert metadata.record.length is None
    assert "length" not in metadata
    assert metadata.lookup("length", 99) == 99


def test_failed_metadata_write_does_not_publish_a_partial_record() -> None:
    metadata = Metadata("dict", {"length": 2})
    before = metadata.record
    with pytest.raises(TypeError, match="integer"):
        metadata["length"] = "invalid"
    assert metadata.record is before and metadata["length"] == 2


def test_normal_producers_cannot_silently_add_an_unknown_semantic_field() -> None:
    with pytest.raises(KeyError, match="explicit component"):
        Metadata("dict", {"lenght": 2})
    metadata = Metadata.from_record("dict", ContainerMeta(length=2))
    assert isinstance(metadata.record, ContainerMeta)
    assert metadata["length"] == 2
    with pytest.raises(KeyError, match="explicit component"):
        metadata["lenght"] = 3
    metadata.attach_graph("capture", [Value.constant(2)])
    assert "capture" in metadata


def test_actual_l2_allocation_provenance_is_a_normal_container_field() -> None:
    point = ProgramPointId("subject:callsite")
    metadata = Metadata("dict", {"length": 1, "fresh_source_call": point})
    assert isinstance(metadata.record, ContainerMeta)
    assert metadata.record.fresh_source_call == point
    assert metadata["fresh_source_call"] == point
    assert "fresh_source_call" not in metadata.components.entries


def test_partial_generator_keeps_original_entry_and_shared_list() -> None:
    entry = Store()
    shared: list[ComponentPayload] = []
    metadata = Metadata.from_component_graph("generator", {"entry": entry, "shared": shared})
    assert isinstance(metadata.record, GeneratorMeta)
    assert metadata.record.entry is entry
    assert metadata["entry"] is entry and metadata["shared"] is shared
    assert isinstance(metadata.components.entries["shared"], LiteralGraphField)
    shared.append(metadata)
    assert validate_component(shared) is shared


def test_metadata_roles_distinguish_references_from_literal_labels() -> None:
    metadata = Metadata.from_component_graph(
        "namespace",
        {
            "owner": "A",
            "mro": ("A", "B"),
            "closure": (("x", "A"),),
            "label": "A",
        },
    )
    assert isinstance(metadata.record, NamespaceMeta)
    entries = metadata.transformation_entries()
    assert isinstance(entries["owner"], ReferenceField)
    assert isinstance(entries["mro"], ReferencesField)
    assert isinstance(entries["closure"], BindingsField)
    assert isinstance(entries["label"], LiteralGraphField)
    assert metadata.namespace_owner() == ObjectId("A")
    assert metadata["label"] == "A"


def test_metadata_mapping_presence_and_order_match_the_admitted_source() -> None:
    source: dict[str, object] = {"entry": Store(), "index": 0, "stage": "created"}
    metadata = Metadata.from_legacy("generator", source)
    assert list(metadata) == list(source)
    assert dict(metadata) == source
    assert Metadata.from_legacy("generator", metadata) is metadata
    metadata["resume_count"] = 1
    assert list(metadata)[-1] == "resume_count"


def test_mutable_semantic_sequences_remain_shared_after_validation() -> None:
    sequence = [Value.constant(1)]
    objects = [ObjectId("A")]
    iterator = Metadata("iterator", {"sequence": sequence, "index": 0})
    klass = Metadata("class", {"mro": objects})
    assert iterator["sequence"] is sequence
    assert isinstance(klass.record, ClassMeta)
    assert klass.record.mro is objects
    sequence.append(Value.constant(2))
    assert len(iterator["sequence"]) == 2


def test_dictionary_iterator_records_have_typed_iteration_fields() -> None:
    metadata = Metadata(
        "iterator",
        {
            "container": Value.ref(ObjectId("table")),
            "dictionary_iteration": "items",
            "dictionary_keys": ("value",),
            "index": 0,
            "dictionary_exhausted": False,
        },
    )
    assert isinstance(metadata.record, DictionaryIteratorMeta)
    assert metadata["dictionary_iteration"] == "items"
    assert metadata.lookup("dictionary_exhausted", True) is False


@pytest.mark.parametrize(
    "field,value",
    [
        ("stage", "invented-stage"),
        ("mro", (1,)),
        ("closure", (("x", 1),)),
        ("body", (ast.Constant(1),)),
        ("sequence", ("unvalidated",)),
    ],
)
def test_known_semantic_fields_cannot_escape_into_literal_attachments(
    field: str, value: object
) -> None:
    with pytest.raises((TypeError, ValueError)):
        Metadata("generator", {field: value})


def test_component_payload_rejects_an_unregistered_object() -> None:
    with pytest.raises(TypeError, match="Unregistered"):
        Metadata.from_component_graph("object", {"capture": [object()]})


def test_component_payload_accepts_actual_typed_effect_graphs() -> None:
    effects = {CellWrite(CellId("captured"))}
    metadata = Metadata.from_component_graph("object", {"captured_writes": effects})
    assert metadata["captured_writes"] is effects


def test_summary_reclassification_keeps_typed_facts_and_mutable_payload_aliases() -> None:
    shared: list[ComponentPayload] = ["retained"]
    original = Metadata.from_component_graph(
        "namespace", {"owner": "A", "label": "A", "shared": shared}
    )
    result = Metadata.from_legacy("summary", original)
    assert result is not original and result.kind == "summary"
    assert isinstance(result.record, SummaryMeta)
    assert result.namespace_owner() == ObjectId("A")
    assert result["label"] == "A" and result["shared"] is shared
    assert isinstance(result.transformation_entries()["owner"], ReferenceField)


def test_actual_summary_join_preserves_a_metadata_self_cycle() -> None:
    from pythonstan.analysis.domains.values import ValueOperations

    shared: list[ComponentPayload] = []
    original = Metadata.from_component_graph("namespace", {"owner": "A", "shared": shared})
    shared.append(original)
    left = HeapObject("namespace", metadata=original)
    result = left.join(HeapObject("dict"), ValueOperations())
    assert result.kind == "summary" and result.havoc
    assert result.metadata["shared"][0] is result.metadata
    assert original.kind == "namespace" and shared[0] is original


def test_actual_store_copy_preserves_typed_metadata_and_embedded_root_alias() -> None:
    root = Store()
    metadata = Metadata("generator", {"entry": root})
    root.heap[ObjectId("generator")] = HeapObject("generator", metadata=metadata)
    copied = root.copy()
    actual = copied.heap[ObjectId("generator")].metadata
    assert isinstance(actual, Metadata)
    assert actual["entry"] is copied and copied is not root


def test_actual_transform_preserves_an_alias_reached_before_metadata_owner() -> None:
    from pythonstan.analysis.domains.values import ValueOperations
    from pythonstan.analysis.state.transform import PayloadTransformer

    metadata = Metadata.from_component_graph("namespace", {"owner": "A", "label": "A"})
    original = {"alias_first": metadata, "owner_second": HeapObject("namespace", metadata=metadata)}
    transformed = PayloadTransformer({"A": "S"}, ValueOperations()).visit(original)
    assert transformed["alias_first"] is transformed["owner_second"].metadata
    assert transformed["alias_first"]["owner"] == "S"
    assert transformed["alias_first"]["label"] == "A"
