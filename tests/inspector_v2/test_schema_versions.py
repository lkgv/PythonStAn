"""Boolean and floating values cannot select integer semantic versions."""
from pythonstan_inspector.contract import QueryRequest, SealedSnapshot
from pythonstan_inspector.core import query
from pythonstan_inspector.storage import load_bundle, write_bundle
from pythonstan_inspector.trace import TraceRecorder
from tests.inspector_v2.test_collector_observation import F1_SOURCE, _run


def test_optional_mapping_boolean_version_cannot_create_entity_joins(tmp_path):
    bundle, _solver, batch = _run(tmp_path / "source", F1_SOURCE)
    recorder = TraceRecorder()
    recorder.record_native_batch(batch)
    for index, version in enumerate((True, 1.0, "1")):
        data = bundle.data
        data["snapshot"]["trace_reference_mapping"]["version"] = version
        copied = load_bundle(write_bundle(SealedSnapshot.seal(data), tmp_path / str(index),
                                         trace=recorder.events))
        result = query(copied, QueryRequest("events", {"model": "keyword_collector"}))
        assert result["evidence"]
        assert result["mapped_references"] == []
        assert result["coverage"]["native_reference_decode"]["snapshot_mapping_uninterpreted"] > 0


def test_native_batch_boolean_version_remains_rejected_observation(tmp_path):
    _bundle, _solver, batch = _run(tmp_path / "source", F1_SOURCE)
    for version in (True, 1.0, "1"):
        recorder = TraceRecorder()
        recorder.record_native_batch({**batch, "schema_version": version})
        assert recorder.health["native_batch_failures"] == 1
        assert not recorder.events
