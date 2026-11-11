"""Passive trace protocol tests; never a replay or semantic-producer oracle."""
import gc
import weakref

import pytest

from pythonstan_inspector.contract import InspectorError, canonical_bytes
from pythonstan_inspector.trace import TraceRecorder


def step(iteration=1):
    return {"iteration": iteration, "kind": "points_to", "cursor": 0,
            "pending_work_by_kind": {"static": 2, "constraint_replay": 3,
                                     "builtin_replay": 4, "points_to": 5}}


def test_work_observation_is_owned_flat_and_not_entity_history():
    recorder = TraceRecorder()
    event = step()
    recorder.record("run_start", stage="analysis")
    recorder.observe_step(event)
    event["pending_work_by_kind"]["points_to"] = 900
    records = recorder.events
    assert records[1]["pending_points_to"] == 5
    assert records[1]["evidence_kind"] == "work_completion"
    assert records[1]["kind"] == "work_step"
    assert records[1]["work_kind"] == "points_to"
    assert [record["sequence"] for record in records] == [1, 2]
    assert len({record["stream_id"] for record in records}) == 1
    assert all(type(value) not in (dict, list) for record in records for value in record.values())
    assert recorder.health["entity_mapping"] == "unsupported"
    assert recorder.health["descriptor_state"] == "not_recorded"
    records[0]["kind"] = "changed"
    recorder.health["entity_mapping"] = "invented"
    assert recorder.events[0]["kind"] == "run_start"
    assert recorder.health["entity_mapping"] == "unsupported"


def test_unknown_primitive_record_is_not_semantic_producer():
    recorder = TraceRecorder()
    recorder.record("work_start", rule="r1")
    recorder.record("fact_added", entity=7)
    assert all(event["evidence_kind"] == "observed_only" for event in recorder.events)
    assert "not_semantic_producer" in recorder.health["history_claim"]
    assert recorder.health["entity_mapping"] == "unsupported"


def test_byte_ring_bounds_all_serialized_metadata_and_loss_summary():
    recorder = TraceRecorder(max_bytes=3000)
    for index in range(200):
        recorder.record("work_start", index=index, label="x" * 100)
        health = recorder.health
        assert health["accounted_bytes"] <= 3000
        assert len(canonical_bytes(health)) <= health["metadata_reserved_bytes"]
        assert health["retained_event_bytes"] == sum(len(canonical_bytes(event)) for event in recorder.events)
    health = recorder.health
    assert 0 < health["retained_events"] < 200
    assert health["overwritten_events"] == 200 - health["retained_events"]
    assert health["loss"]["first_sequence"] == 1
    assert health["loss"]["precision"] == "enclosing_interval_not_all_sequences_lost"
    assert health["status"] == "degraded"
    assert [event["sequence"] for event in recorder.events] == sorted(event["sequence"] for event in recorder.events)


def test_too_small_budget_rejected_and_no_capacity_records_loss():
    for invalid in (0, 1, 2047, True, float("inf")):
        with pytest.raises(InspectorError):
            TraceRecorder(max_bytes=invalid)
    recorder = TraceRecorder(max_bytes=2048)
    recorder.record("run_start")
    assert recorder.events == []
    assert recorder.health["dropped_events"] == 1
    assert recorder.health["accounted_bytes"] == 2048
    assert recorder.health["status"] == "degraded"


class Foreign:
    def __repr__(self):
        raise AssertionError("repr must never run")

    def __str__(self):
        raise AssertionError("str must never run")

    def __iter__(self):
        raise AssertionError("iteration must never run")


def test_rejected_live_objects_are_not_formatted_or_retained(monkeypatch):
    import pythonstan_inspector.trace as trace
    recorder = TraceRecorder()
    calls = []
    original = trace.canonical_bytes

    def encode(value):
        calls.append(1)
        return original(value)

    monkeypatch.setattr(trace, "canonical_bytes", encode)

    def rejected():
        live = Foreign()
        reference = weakref.ref(live)
        with pytest.raises(InspectorError):
            recorder.record("unknown", live=live)
        event = step()
        event["pending_work_by_kind"]["points_to"] = live
        recorder.observe_step(event)
        return reference

    reference = rejected()
    gc.collect()
    assert reference() is None
    assert calls == []
    assert recorder.events == []
    assert recorder.health["rejected_events"] == 2


@pytest.mark.parametrize("value", [[], {}, object(), float("nan"), float("inf"), 2**63, "a" * 1025, "\ud800"])
def test_scalar_validation_before_serialization(value):
    recorder = TraceRecorder()
    with pytest.raises(InspectorError):
        recorder.record("unknown", value=value)
    assert recorder.events == []
    assert recorder.health["rejected_events"] == 1


def test_observer_malformed_inputs_degrade_without_raising():
    recorder = TraceRecorder()
    for event in (Foreign(), {"too": "little"}, {str(i): i for i in range(10000)},
                  {**step(), "cursor": True}, {**step(), "kind": "unknown"}):
        recorder.observe_step(event)
    assert recorder.events == []
    assert recorder.health["rejected_events"] == 5
    assert recorder.health["loss"]["first_sequence"] == 1
    assert recorder.health["loss"]["last_sequence"] == 5


def test_sink_is_deferred_and_receives_owned_records_once():
    deliveries = []

    def sink(event):
        deliveries.append(dict(event))
        event["kind"] = "mutated by sink"

    recorder = TraceRecorder(sink=sink)
    recorder.record("run_start")
    recorder.observe_step(step())
    assert deliveries == []
    recorder.flush()
    recorder.flush()
    assert len(deliveries) == 2
    assert recorder.events[0]["kind"] == "run_start"
    assert recorder.health["sink_failures"] == 0


def test_sink_exception_degrades_and_does_not_retain_exception_payload():
    reference = []

    def sink(event):
        live = Foreign()
        reference.append(weakref.ref(live))
        raise RuntimeError(live)

    recorder = TraceRecorder(sink=sink)
    recorder.record("run_start")
    recorder.flush()
    gc.collect()
    assert reference[0]() is None
    assert recorder.health["sink_failures"] == 1
    assert recorder.health["status"] == "degraded"
    assert len(recorder.events) == 1


def test_sink_interrupt_propagates_and_flush_recovers_guard():
    def sink(event):
        raise KeyboardInterrupt()

    recorder = TraceRecorder(sink=sink)
    recorder.record("run_start")
    with pytest.raises(KeyboardInterrupt):
        recorder.flush()
    assert recorder._flushing is False
    assert recorder.health["sink_failures"] == 0


def test_streams_isolate_sequences_and_do_not_reuse_after_exhaustion():
    left, right = TraceRecorder(), TraceRecorder()
    left.record("run_start")
    right.record("run_start")
    assert left.events[0]["stream_id"] != right.events[0]["stream_id"]
    left._sequence = 2**63 - 2
    left.record("run_stop")
    with pytest.raises(InspectorError, match="exhausted"):
        left.record("run_stop")
    assert left.events[-1]["sequence"] == 2**63 - 1
    assert left.health["sequence_exhausted"] is True
    assert len(canonical_bytes(left.health)) <= left.health["metadata_reserved_bytes"]


def test_overwritten_records_count_as_unavailable_to_deferred_sink():
    recorder = TraceRecorder(max_bytes=3000, sink=lambda event: None)
    for index in range(100):
        recorder.record("unknown", index=index)
    assert recorder.health["sink_skipped_events"] == recorder.health["overwritten_events"]
    recorder.flush()
    count = recorder.health["sink_skipped_events"]
    recorder.record("run_stop")
    assert recorder.health["sink_skipped_events"] == count


def test_actual_runner_events_survive_offline_storage(tmp_path):
    from pythonstan_inspector.runner import run_source
    from pythonstan_inspector.storage import load_bundle, write_bundle
    source = tmp_path / "subject.py"
    source.write_text("source = 1\ntarget = source\n")
    snapshot, events, _ = run_source(source, trace=True)
    write_bundle(snapshot, tmp_path / "bundle", trace=events)
    bundle = load_bundle(tmp_path / "bundle")
    assert bundle.trace[0]["kind"] == "run_start"
    assert bundle.trace[-1]["kind"] == "run_stop"
    assert any(event["kind"] == "work_step" for event in bundle.trace)
    assert bundle.data["run"]["trace_health"]["entity_mapping"] == "unsupported"
    assert bundle.trace_health["integrity"] == "matched"
