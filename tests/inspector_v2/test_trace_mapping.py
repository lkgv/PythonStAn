"""Actual source observations and strict native-ref/snapshot association.

Arena resets, equal clones, expired weak refs, and budget limits below are
controlled native/protocol safety experiments, not natural project failures.
"""
from dataclasses import replace
import gc

from pythonstan.analysis.pointer.kcfa import points_to_set
from pythonstan_inspector.adapter import _Projection, _trace_reference_mapping, capture
from pythonstan_inspector.contract import CaptureRequest, QueryRequest
from pythonstan_inspector.core import query
from pythonstan_inspector.storage import load_bundle, write_bundle
from pythonstan_inspector.trace import TraceRecorder
from tests.inspector_v2.test_collector_observation import F1_SOURCE, _VIEWS, _run


def _key(ref):
    return ref["run_id"], ref["epoch"], ref["ref"]


def _with_trace(tmp_path, snapshot, batch):
    recorder = TraceRecorder()
    recorder.record_native_batch(batch)
    assert recorder.health["native_batch_failures"] == 0
    assert recorder.health["dropped_events"] == 0
    return load_bundle(write_bundle(snapshot, tmp_path, trace=recorder.events))


def _all_referenced(solver, request=None):
    projection = _Projection(request or CaptureRequest())
    for run_ref, weak, _epoch in solver._diagnostics.reference_bindings():
        obj = weak()
        if obj is not None:
            assert solver.resolve_inspector_event_ref(run_ref) is obj
            projection.entity(obj)
    projection.finish_entities()
    return projection


def test_actual_targeted_source_roundtrip_resolves_only_explicit_snapshot_map(tmp_path):
    captured, solver, batch = _run(tmp_path / "source", F1_SOURCE)
    assert batch["events"] and batch["descriptors"]
    assert all(set(descriptor) == {"ref", "kind"} for descriptor in batch["descriptors"])
    traced = _with_trace(tmp_path / "traced", captured.snapshot, batch)
    selector = {"model": "keyword_collector", "file": str(tmp_path / "source" / "subject.py"),
                "line": 3, "column": 9}
    result = query(traced, QueryRequest("events", selector))
    decode = result["coverage"]["native_reference_decode"]
    assert result["evidence"] and decode["descriptor_missing"] == 0
    assert decode["snapshot_mapped"] > 0
    assert result["coverage"]["entity_mapping"] == "explicit_snapshot_map_only"
    mapping = {_key(row["run_ref"]): row["local_id"]
               for row in traced.data["snapshot"]["trace_reference_mapping"]["rows"]}
    binding = next(event for event in batch["events"] if event["kind"] == "collector_binding")
    parameter = mapping[_key(binding["refs"]["collector_parameter"])]
    dictionary = mapping[_key(binding["refs"]["collector_object"])]
    assert any(row["owner"] == parameter and row["object"] == dictionary
               for row in traced.data["snapshot"]["views"]["ctx_points"]["rows"])
    assert all(row["entity_ref"]["snapshot"] == result["snapshot_ref"]
               for row in result["mapped_references"])
    # Same native objects in a second capture still receive a distinct scoped
    # destination; neither event refs nor coincident local IDs erase that cut.
    other = capture(solver, CaptureRequest(views=_VIEWS), run_metadata={"run_id": solver._diagnostics.run_id})
    other_result = query(_with_trace(tmp_path / "other-traced", other, batch), QueryRequest("events", selector))
    assert other_result["snapshot_ref"] != result["snapshot_ref"]
    assert other_result["coverage"]["native_reference_decode"]["snapshot_mapped"] > 0
    assert all(row["entity_ref"]["snapshot"] == other_result["snapshot_ref"]
               for row in other_result["mapped_references"])


def test_mapping_does_not_export_uncaptured_objects_or_use_native_equality(tmp_path):
    _captured, solver, batch = _run(tmp_path / "source", F1_SOURCE)
    binding = next(event for event in batch["events"] if event["kind"] == "collector_binding")
    wanted = binding["refs"]["collector_parameter"]
    original = solver.resolve_inspector_event_ref(wanted)
    clone = replace(original)
    assert clone == original and clone is not original
    projection = _Projection(CaptureRequest())
    clone_id = projection.entity(clone)
    projection.finish_entities()
    before = len(projection.entities)
    mapping = _trace_reference_mapping(solver, projection, solver._diagnostics.run_id, points_to_set._object_table_generation)
    assert len(projection.entities) == before
    assert not any(_key(row["run_ref"]) == _key(wanted) for row in mapping["rows"])
    assert clone_id in {entity["id"] for entity in projection.entities}
    empty = _Projection(CaptureRequest())
    missing = _trace_reference_mapping(solver, empty, solver._diagnostics.run_id, points_to_set._object_table_generation)
    assert missing["rows"] == [] and empty.entities == []
    assert missing["coverage"]["reason_counts"]["not_captured_as_same_native_object"] > 0


def test_actual_known_mapping_without_usable_descriptor_emits_no_entity_join(tmp_path):
    captured, _solver, batch = _run(tmp_path / "source", F1_SOURCE)
    recorder = TraceRecorder()
    recorder.record_native_batch(batch)
    assert captured.data["snapshot"]["trace_reference_mapping"]["rows"]
    operations = [event for event in recorder.events if event["kind"] == "targeted_operation"]
    stripped = load_bundle(write_bundle(captured.snapshot, tmp_path / "descriptor-loss", trace=operations))
    result = query(stripped, QueryRequest("events", {"model": "keyword_collector"}))
    decode = result["coverage"]["native_reference_decode"]
    assert decode["descriptor_missing"] > 0
    assert decode["mapping_present_but_descriptor_unavailable"] > 0
    assert decode["snapshot_mapped"] == 0 and result["mapped_references"] == []
    malformed = recorder.events
    for event in malformed:
        if event["kind"] == "native_descriptor":
            event["native_descriptor"]["kind"] = "UnfamiliarOpaqueNative"
    opaque = load_bundle(write_bundle(captured.snapshot, tmp_path / "opaque-descriptor", trace=malformed))
    unknown = query(opaque, QueryRequest("events", {"model": "keyword_collector"}))
    assert unknown["mapped_references"] == []
    assert unknown["coverage"]["native_reference_decode"]["descriptor_missing"] > 0


def test_actual_arena_change_never_reinterprets_old_run_refs(tmp_path):
    _captured, solver, batch = _run(tmp_path / "source", F1_SOURCE)
    projection = _all_referenced(solver)
    current = points_to_set._object_table_generation
    before = _trace_reference_mapping(solver, projection, solver._diagnostics.run_id, current)
    assert before["rows"]
    points_to_set.reset_object_table()
    assert points_to_set._object_table_generation != current
    assert all(solver.resolve_inspector_event_ref(ref) is None
               for event in batch["events"] for ref in event["refs"].values())
    after = _trace_reference_mapping(solver, projection, solver._diagnostics.run_id, points_to_set._object_table_generation)
    assert after["rows"] == []
    assert after["coverage"]["reason_counts"]["run_or_arena_mismatch"] == after["coverage"]["examined"]


def test_weak_expiry_and_mapping_budget_loss_are_explicit(tmp_path):
    _captured, solver, _batch = _run(tmp_path / "source", F1_SOURCE)
    from pythonstan.analysis.pointer.kcfa.variable import Variable
    ephemeral = Variable("only_weakly_observed")
    run_ref = solver._diagnostics._reference(ephemeral)
    assert run_ref is not None and solver.resolve_inspector_event_ref(run_ref) is ephemeral
    del ephemeral
    gc.collect()
    assert solver.resolve_inspector_event_ref(run_ref) is None
    projection = _all_referenced(solver, CaptureRequest(max_rows=1))
    mapping = _trace_reference_mapping(solver, projection, solver._diagnostics.run_id, points_to_set._object_table_generation)
    coverage = mapping["coverage"]
    assert len(mapping["rows"]) == coverage["mapped"] == 1
    assert coverage["registry"] == "partial"
    assert coverage["reason_counts"]["weak_reference_expired"] == 1
    assert coverage["reason_counts"]["mapping_row_budget"] > 0
    assert coverage["mapped"] + coverage["unmapped"] == coverage["examined"]
    exhausted = _Projection(CaptureRequest(deadline_seconds=1e-12))
    partial = _trace_reference_mapping(solver, exhausted, solver._diagnostics.run_id, points_to_set._object_table_generation)
    assert partial["rows"] == [] and partial["coverage"]["registry"] == "partial"
    assert partial["coverage"]["unexamined"] == partial["coverage"]["registered_refs"] > 0
