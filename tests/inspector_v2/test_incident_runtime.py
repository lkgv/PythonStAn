"""Controlled protocol failures, not claims about natural project incidents."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

from pythonstan_inspector.contract import CaptureRequest, InspectorError
from pythonstan_inspector.incident import (IncidentRecorder, TRACE_PROTOCOL, MAX_RECEIPT_BYTES,
    read_receipt, read_trace, write_receipt)
from pythonstan_inspector import project_process, project_runner
from pythonstan_inspector.trace import TraceRecorder


def _replace_worker(monkeypatch, code):
    original = project_process.subprocess.Popen
    def launch(command, **kwargs):
        return original([sys.executable, "-c", code], **kwargs)
    monkeypatch.setattr(project_process.subprocess, "Popen", launch)


def _config(tmp_path):
    (tmp_path / "app.py").write_text("def f():\n return 7\nx=f()\n")
    return {"project_root": str(tmp_path), "entry_file": "app.py", "max_iterations": 1000,
            "auto_framework_entries": False}


def test_binary_transport_caps_flood_and_preserves_stderr_tail():
    code = "import os,sys; sys.stdin.buffer.read(); os.write(1,b'x'*2500000); os.write(2,b'e'*200000+b'TAIL')"
    completed = project_process.run_worker_process([sys.executable, "-c", code],
        input="{}", cwd=Path.cwd(), timeout=5)
    assert completed.returncode == 0
    assert len(completed.stdout.encode()) == project_process.MAX_STDOUT_BYTES
    assert len(completed.stderr.encode()) == project_process.MAX_STDERR_BYTES
    assert completed.stderr.endswith("TAIL")
    assert completed.transport_health["stdout_overflow"] is True
    assert completed.transport_health["stderr_tail_truncated"] is True


def test_overflow_refuses_an_earlier_complete_bound_terminal_marker(tmp_path, monkeypatch):
    _replace_worker(monkeypatch, r'''
import json,os,sys
p=json.load(sys.stdin)
response={'transport_protocol':'project_inspector_json_line_v2','run_id':p['run_id'],'request_sha256':p['request_sha256']}
print('PROJECT_INSPECTOR_JSON:'+json.dumps(response),flush=True)
os.write(1,b'x'*2200000)
''')
    result = project_runner.run_project(_config(tmp_path), tmp_path / "out", timeout_seconds=5)
    assert result["status"] == "worker_error"
    assert result["incident_receipt"]["transport"]["health"]["stdout_overflow"]
    assert result["bundle"] is None


def test_timeout_preserves_small_started_receipt_without_snapshot(tmp_path, monkeypatch):
    _replace_worker(monkeypatch, r'''
import json,sys,time
from pythonstan_inspector.incident import IncidentRecorder
p=json.load(sys.stdin)
r=IncidentRecorder(p['incident'],p['run_id'],p['request_sha256'],'a'*64,p['configuration'],'counters')
r.phase({'phase':'one_long_atomic_operation_started','sequence':1})
r.checkpoint(force=True)
time.sleep(10)
''')
    began = time.monotonic()
    result = project_runner.run_project(_config(tmp_path), tmp_path / "out", timeout_seconds=0.3)
    assert time.monotonic() - began < 2.5
    assert result["status"] == "timeout"
    assert result["bundle"] is None
    receipt = result["incident_receipt"]
    assert receipt["phase"]["last_entered"] == "one_long_atomic_operation_started"
    assert receipt["phase"]["last_completed"] is None
    assert receipt["identity"]["source_verification"] == "unverified"
    assert receipt["observation"]["heartbeat_preemption"] is False
    assert read_receipt(result["incident_receipt_path"], result["run_id"], receipt["request_sha256"]) == receipt


def test_report_export_failure_retains_receipt_and_independent_snapshot(tmp_path, monkeypatch):
    _replace_worker(monkeypatch, r'''
from pythonstan_inspector import project_runner
def fail(path, report):
    raise MemoryError('controlled full native report export failure')
project_runner._stream_report=fail
project_runner._worker()
''')
    result = project_runner.run_project(_config(tmp_path), tmp_path / "out", timeout_seconds=10)
    assert result["status"] == "worker_finished", result
    assert result["native_report"] is None
    assert result["capture_state"]["status"] == "sealed", result["capture_state"]
    receipt = result["worker_incident_receipt"]
    assert receipt["artifacts"]["native_report"]["reason"] == "report_export_failed"
    assert receipt["health"]["report_export_error"]["exception"] == "MemoryError"
    assert receipt["artifacts"]["snapshot"]["status"] == "sealed"
    from pythonstan_inspector.storage import load_bundle
    bundle = load_bundle(result["bundle"])
    assert bundle.data["run"]["project_report"]["sha256"] is None
    assert bundle.data["run"]["project_report"]["binding"] == "separate_incident_receipt_after_snapshot"
    assert result["incident_receipt"]["transport"]["health"]["stdout_retained_bytes"] < MAX_RECEIPT_BYTES


def test_counter_mode_has_no_snapshot_and_large_report_has_explicit_reference(tmp_path):
    result = project_runner.run_project(_config(tmp_path), tmp_path / "out", timeout_seconds=10,
        observation_mode="counters", max_report_bytes=128)
    assert result["status"] == "worker_finished", result
    assert result["bundle"] is None
    assert result["capture_state"]["status"] == "not_requested"
    assert result["native_report"] is None
    assert result["native_report_loading"]["reason"] == "inline_byte_budget_exceeded"
    reference = result["native_report_reference"]
    assert reference["bytes"] > 128 and len(reference["sha256"]) == 64


def test_real_targeted_source_trace_is_normalized_and_independently_readable(tmp_path):
    source = tmp_path / "app.py"
    source.write_text("def take(**kw):\n return next(iter(kw.keys()))\nresult=take(x=1)\n")
    result = project_runner.run_project({"project_root": str(tmp_path), "entry_file": "app.py",
        "auto_framework_entries": False, "max_iterations": 2000}, tmp_path / "out", timeout_seconds=10,
        observation_mode="targeted", trace_selector={"file": "app.py", "line": 3, "model": "keyword_collector"})
    assert result["status"] == "worker_finished", result
    assert result["capture_state"]["status"] == "sealed", result["capture_state"]
    receipt = result["worker_incident_receipt"]
    assert receipt["observation"]["selector"]["file"] == str(source)
    assert receipt["observation"]["selector_source"]["consumed_source_binding"] == "matched_initial_selection"
    assert receipt["counters"]["cumulative"]["selected_work_items"] > 0
    assert receipt["counters"]["actual_bootstrap"]["decision_coverage"] == "all_native_seed_attempts_since_configuration"
    trace = result["runtime_trace"]
    assert trace is not None and trace["first_rejection"] is None
    operations = [row["native_event"] for row in trace["events"] if row["kind"] == "targeted_operation"]
    assert operations
    assert all(row["source"]["file"] == str(source) and row["source"]["line"] == 3 for row in operations)
    assert any(row["kind"] == "collector_binding" for row in operations)
    assert receipt["health"]["trace"]["native_batch_failures"] == 0


@pytest.mark.parametrize("selector", [
    {"file": "missing.py", "line": 3},
    {"file": "app.py", "line": 1},
    {"file": "app.py", "line": 3, "column": 0},
])
def test_targeted_selector_requires_verified_source_call(tmp_path, selector):
    (tmp_path / "app.py").write_text("def f():\n return 7\nx=f()\n")
    with pytest.raises(InspectorError):
        project_runner.run_project({"project_root": str(tmp_path)}, tmp_path / "out",
            observation_mode="targeted", trace_selector=selector)


def test_targeted_selector_outside_project_is_rejected_before_worker(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    outside = tmp_path / "outside.py"
    outside.write_text("f()\n")
    def forbidden(*args, **kwargs):
        raise AssertionError("invalid source selector launched worker")
    monkeypatch.setattr(project_runner, "run_worker_process", forbidden)
    with pytest.raises(InspectorError, match="inside the declared project_root"):
        project_runner.run_project({"project_root": str(project)}, tmp_path / "out",
            observation_mode="targeted", trace_selector={"file": str(outside), "line": 1})


@pytest.mark.parametrize("variant", ["symlink", "fifo", "oversize", "wrong_run", "invalid_utf8"])
def test_receipt_reader_fails_closed(tmp_path, variant):
    recorder = IncidentRecorder(tmp_path, "run", "request", "a" * 64, {}, "counters")
    recorder.checkpoint(force=True)
    path = tmp_path / "incident.json"
    if variant == "symlink":
        path.rename(tmp_path / "real.json")
        path.symlink_to(tmp_path / "real.json")
    elif variant == "fifo":
        path.unlink()
        os.mkfifo(path)
    elif variant == "oversize":
        with path.open("wb") as stream:
            stream.truncate(MAX_RECEIPT_BYTES + 1)
    elif variant == "wrong_run":
        recorder.data["run_id"] = "other"
        write_receipt(path, recorder.data)
    else:
        path.write_bytes(b'{"invalid":"\xff"}')
    with pytest.raises((InspectorError, OSError, ValueError, UnicodeError)):
        read_receipt(path, "run", "request")


def test_trace_batch_is_bounded_and_reader_stops_at_first_broken_frame(tmp_path):
    trace = TraceRecorder(max_bytes=8192)
    for number in range(30):
        trace.record("selected_model_operation", index=number)
    batch = trace.cold_batch(max_events=3, max_bytes=1024)
    assert 0 < len(batch["events"]) <= 3
    assert batch["encoded_event_bytes"] <= 1024
    frames = [{**row, "trace_protocol": TRACE_PROTOCOL, "run_id": "run", "request_sha256": "request"}
              for row in batch["events"]]
    path = tmp_path / "runtime_trace.jsonl"
    good = json.dumps(frames[0]).encode() + b"\n"
    later = json.dumps(frames[-1]).encode() + b"\n"
    path.write_bytes(good + b'{"torn":\n' + later)
    result = read_trace(path, "run", "request")
    assert len(result["events"]) == 1
    assert result["first_rejection"]["line"] == 2
    path.write_bytes(json.dumps(frames[0]).encode())
    assert read_trace(path, "run", "request")["events"] == []


def test_hot_ingestion_does_not_write_or_call_arbitrary_sink(tmp_path, monkeypatch):
    bridge = project_runner._ProjectDiagnostic(tmp_path, "run", CaptureRequest(), trace=True)
    def forbidden(*args, **kwargs):
        raise AssertionError("hot observer performed I/O")
    monkeypatch.setattr(Path, "open", forbidden)
    bridge.observe_step({"iteration": 1, "kind": "static", "cursor": 0,
        "pending_work_by_kind": {"static": 0, "constraint_replay": 0, "builtin_replay": 0, "points_to": 0}})
    assert bridge.recorder.events[-1]["kind"] == "work_step"


def test_typed_native_batch_keeps_run_refs_separate_from_snapshot_ids():
    recorder = TraceRecorder()
    ref = {"run_id": "run", "epoch": 1, "ref": 7}
    batch = {"schema_version": 1, "run_id": "run", "epoch": 1, "selector": {},
        "descriptors": [{"ref": ref, "kind": "Scope"}],
        "events": [{"run_id": "run", "epoch": 1, "sequence": 1,
            "kind": "collector_bound", "model": "keyword_collector",
            "source": {"file": "/app.py", "line": 3, "column": 0, "call_index": 1},
            "refs": {"collector": ref}, "fields": {"explicit_keywords": 1}}], "health": {}}
    recorder.record_native_batch(batch)
    assert [row["kind"] for row in recorder.events] == ["native_descriptor", "targeted_operation"]
    assert recorder.events[1]["native_event"]["refs"]["collector"] == ref
    assert recorder.health["entity_mapping"] == "unsupported"
    assert recorder.health["native_batch_failures"] == 0
    bad = {**batch, "events": [{**batch["events"][0], "refs": {"collector": {**ref, "run_id": "foreign"}}}]}
    recorder.record_native_batch(bad)
    assert recorder.health["native_batch_failures"] == 1
    assert recorder.health["status"] == "degraded"
    with pytest.raises(InspectorError):
        recorder.record("arbitrary_nested", data=batch)


def test_malformed_terminal_capture_state_gets_external_failure_receipt(tmp_path, monkeypatch):
    _replace_worker(monkeypatch, r'''
import json,sys
p=json.load(sys.stdin)
print('PROJECT_INSPECTOR_JSON:'+json.dumps({'run_id':p['run_id'],'request_sha256':p['request_sha256'],'capture_state':[]}),flush=True)
''')
    result = project_runner.run_project(_config(tmp_path), tmp_path / "out", timeout_seconds=5)
    assert result["status"] == "worker_error"
    assert result["incident_receipt_path"] is not None
    assert result["incident_receipt"]["identity"]["source_verification"] == "unverified"


@pytest.mark.parametrize("kwargs", [
    {"observation_mode": "unknown"},
    {"observation_mode": "off", "trace": True},
    {"observation_mode": "targeted", "trace_selector": {"file": "a.py", "line": 1, "context": 3}},
    {"observation_mode": "capture", "trace_selector": {"file": "a.py", "line": 1}},
    {"report_mode": "discard"}, {"max_report_bytes": 65 * 1024 * 1024},
])
def test_runtime_option_contract_is_closed(tmp_path, kwargs):
    with pytest.raises(InspectorError):
        project_runner.run_project({}, tmp_path / "out", **kwargs)
