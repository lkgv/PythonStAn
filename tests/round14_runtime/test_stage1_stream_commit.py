"""Independent wire examples for committed frames, exercised through real pipes.

The worker controls below do not run target source. They isolate the supervisor
contract; a finished worker still does not establish solver completion.
"""
import json
from pathlib import Path
import subprocess
import sys

import pytest

from pythonstan_inspector import project_runner
from pythonstan_inspector.project_progress import _read_progress
from scripts import analyze_project as native_cli


PROGRESS_V2 = "project_progress_jsonl_v2"
RECEIPT_V2 = "project_inspector_json_line_v2"


def _progress_frames(*, protocol=PROGRESS_V2):
    binding = {
        "run_id": "run", "request_sha256": "request",
        "implementation_sha256_before": "a" * 64,
        "configuration_sha256": "b" * 64,
    }
    if protocol is not None:
        binding["progress_protocol"] = protocol
    module = {
        "path": "app.py", "module": "app", "ownership": "first_party",
        "sha256": "c" * 64, "bytes": 4, "parsed": False, "lowered": False,
    }
    return [
        {**binding, "sequence": 1, "phase": "inventory_completed", "inventory": [module]},
        {**binding, "sequence": 2, "phase": "module_frontend_completed",
         "module_record": {**module, "parsed": True, "lowered": True}},
    ]


def _encoded(frame, ending="\n"):
    return (json.dumps(frame) + ending).encode("utf-8")


@pytest.mark.parametrize("reader", [_read_progress, native_cli._read_progress], ids=["installed", "native"])
@pytest.mark.parametrize("ending,events,lowered", [
    ("\n", 2, 1), ("\r\n", 2, 1), ("\r", 1, 0), ("", 1, 0),
])
def test_progress_json_body_without_delimiter_is_not_a_committed_frame(
        tmp_path, reader, ending, events, lowered):
    first, final = _progress_frames()
    path = tmp_path / "progress.jsonl"
    path.write_bytes(_encoded(first) + _encoded(final, ending))
    result = reader(path, "run", "request")
    assert result["events"] == events
    assert result["coverage_observed"]["lowered_modules"] == lowered
    committed = ending.endswith("\n")
    assert result["rejected_or_torn_frames"] == (0 if committed else 1)
    assert result["semantic_complete"] is False
    assert result["progress_protocol"] == PROGRESS_V2
    assert result["frame_recovery_policy"] == "continuous_committed_prefix"
    if not committed:
        assert result["first_rejection"]["reason"] == "unterminated_frame"


@pytest.mark.parametrize("reader", [_read_progress, native_cli._read_progress], ids=["installed", "native"])
@pytest.mark.parametrize("encoding", ["utf-16-be", "utf-32-be"])
def test_progress_does_not_autodetect_non_utf8_wire_encoding(tmp_path, reader, encoding):
    first, _ = _progress_frames()
    # Big-endian encodings also end their line terminator in 0x0a. A byte
    # delimiter check alone must not let json.loads auto-detect these formats.
    wire = (json.dumps(first) + "\n").encode(encoding)
    assert wire.endswith(b"\n")
    path = tmp_path / "progress.jsonl"
    path.write_bytes(wire)
    result = reader(path, "run", "request")
    assert result["events"] == 0
    assert result["available"] is False
    assert result["first_rejection"]["line"] == 1


@pytest.mark.parametrize("reader", [_read_progress, native_cli._read_progress], ids=["installed", "native"])
@pytest.mark.parametrize("fault", [
    "malformed", "invalid_utf8", "binding", "sequence", "unknown_version", "mixed_version",
])
def test_v2_does_not_resume_after_invalid_frame(tmp_path, reader, fault):
    first, final = _progress_frames()
    bad = dict(final)
    if fault == "malformed":
        corrupt = b'{"truncated":\n'
    elif fault == "invalid_utf8":
        corrupt = _encoded(final).replace(b"module_frontend_completed", b"invalid_\xff_phase")
    else:
        if fault == "binding":
            bad["run_id"] = "foreign"
        elif fault == "sequence":
            bad["sequence"] = 9
        elif fault == "unknown_version":
            bad["progress_protocol"] = "future_unknown_protocol"
        else:
            bad.pop("progress_protocol")
        corrupt = _encoded(bad)
    path = tmp_path / "progress.jsonl"
    path.write_bytes(_encoded(first) + corrupt + _encoded(final))
    result = reader(path, "run", "request")
    assert result["events"] == 1
    assert result["last_phase"] == "inventory_completed"
    assert result["coverage_observed"]["lowered_modules"] == 0
    assert result["first_rejection"]["line"] == 2
    assert result["frame_recovery_policy"] == "continuous_committed_prefix"


@pytest.mark.parametrize("reader", [_read_progress, native_cli._read_progress], ids=["installed", "native"])
@pytest.mark.parametrize("prefix", [b'{"invalid":\n', "unknown", "legacy_label"])
def test_v2_cannot_be_selected_after_unidentified_bad_prefix(tmp_path, reader, prefix):
    first, _ = _progress_frames()
    unknown = {**first, "progress_protocol": (
        "legacy_unversioned_progress_jsonl" if prefix == "legacy_label" else "future_unknown_protocol")}
    path = tmp_path / "progress.jsonl"
    path.write_bytes((prefix if isinstance(prefix, bytes) else _encoded(unknown)) + _encoded(first))
    result = reader(path, "run", "request")
    assert result["events"] == 0
    assert result["available"] is False
    assert result["first_rejection"]["line"] == 1


@pytest.mark.parametrize("reader", [_read_progress, native_cli._read_progress], ids=["installed", "native"])
def test_legacy_recovery_remains_explicitly_weaker(tmp_path, reader):
    first, final = _progress_frames(protocol=None)
    path = tmp_path / "progress.jsonl"
    path.write_bytes(_encoded(first) + _encoded({**final, "run_id": "foreign"}) + _encoded(final))
    result = reader(path, "run", "request")
    assert result["events"] == 2
    assert result["rejected_or_torn_frames"] == 1
    assert result["coverage_observed"]["lowered_modules"] == 1
    assert result["progress_protocol"] == "legacy_unversioned_progress_jsonl"
    assert result["frame_recovery_policy"] == "legacy_bound_frame_recovery"
    assert result["semantic_complete"] is False


TRUSTED_RECEIPT_WORKER = r'''
import json, pathlib, sys
payload = json.load(sys.stdin)
frame = {"progress_protocol": "project_progress_jsonl_v2",
         "run_id": payload["run_id"], "request_sha256": payload["request_sha256"],
         "implementation_sha256_before": "a" * 64, "configuration_sha256": "b" * 64,
         "sequence": 1, "phase": "inventory_started"}
pathlib.Path(payload["incident"], "progress.jsonl").write_text(json.dumps(frame) + "\n")
response = {"transport_protocol": "project_inspector_json_line_v2",
            "run_id": payload["run_id"], "request_sha256": payload["request_sha256"],
            "capture_state": {"status": "unsupported", "reason": "controlled wire producer"},
            "memory_limit_enforced": False,
            "native_report_fallback": {"status": "incomplete", "solver_completed": False}}
record = "PROJECT_INSPECTOR_JSON:" + json.dumps(response)
if MODE == "complete":
    output = record + "\n"
elif MODE == "no_newline":
    output = record
elif MODE == "torn_after_valid":
    output = record + "\nPROJECT_INSPECTOR_JSON:{"
elif MODE == "malformed_after_valid":
    output = record + "\nPROJECT_INSPECTOR_JSON:{\n"
elif MODE == "unbound_after_valid":
    response["run_id"] = "foreign"
    output = record + "\nPROJECT_INSPECTOR_JSON:" + json.dumps(response) + "\n"
elif MODE == "unknown_protocol":
    response["transport_protocol"] = "future_unknown_protocol"
    output = "PROJECT_INSPECTOR_JSON:" + json.dumps(response) + "\n"
elif MODE == "legacy_label":
    response["transport_protocol"] = "legacy_unversioned_inspector_receipt"
    output = "PROJECT_INSPECTOR_JSON:" + json.dumps(response) + "\n"
elif MODE == "legacy":
    response.pop("transport_protocol")
    output = "PROJECT_INSPECTOR_JSON:" + json.dumps(response) + "\n"
elif MODE == "bare_cr":
    output = record + "\r"
elif MODE == "crlf":
    output = record + "\r\n"
elif MODE == "valid_utf8":
    response["diagnostic_text"] = "caf\u00e9\u2028next"
    output = "PROJECT_INSPECTOR_JSON:" + json.dumps(response, ensure_ascii=False) + "\n"
else:
    output = record + "\n"
raw = output.encode("utf-8")
if MODE == "invalid_utf8_receipt":
    raw = raw.replace(b"incomplete", b"incom\xffplete")
elif MODE == "invalid_utf8_noise":
    raw = b"unrelated diagnostic \xff\n" + raw
    sys.stderr.buffer.write(b"stderr diagnostic \xff\n")
    sys.stderr.buffer.flush()
sys.stdout.buffer.write(raw)
sys.stdout.buffer.flush()
sys.exit(7 if MODE == "nonzero_exit" else 0)
'''


@pytest.mark.parametrize("mode", [
    "complete", "no_newline", "torn_after_valid", "malformed_after_valid",
    "unbound_after_valid", "unknown_protocol", "legacy", "nonzero_exit",
    "bare_cr", "crlf", "valid_utf8", "invalid_utf8_receipt", "invalid_utf8_noise", "legacy_label",
])
def test_installed_supervisor_requires_last_committed_bound_receipt(tmp_path, monkeypatch, mode):
    original = project_runner.run_worker_process
    code = "MODE=" + repr(mode) + "\n" + TRUSTED_RECEIPT_WORKER

    def controlled(command, *, input, cwd, timeout):
        return original([sys.executable, "-I", "-S", "-c", code],
                        input=input, cwd=cwd, timeout=timeout)

    monkeypatch.setattr(project_runner, "run_worker_process", controlled)
    result = project_runner.run_project(
        {"project_root": "/trusted-control", "entry_file": "unused.py"},
        tmp_path / "capture", timeout_seconds=3)
    accepted = mode in ("complete", "legacy", "crlf", "valid_utf8", "invalid_utf8_noise")
    expected = "worker_finished" if accepted else "worker_error"
    assert result["status"] == expected
    assert result["partial_progress"]["events"] == 1
    assert result["partial_progress"]["last_phase"] == "inventory_started"
    assert result["partial_progress"]["semantic_complete"] is False
    if accepted or mode == "nonzero_exit":
        assert result["native_report"]["solver_completed"] is False
    else:
        assert result["native_report"] is None
        assert result["capture_state"]["status"] == "unavailable"


@pytest.mark.parametrize("ending,invalid_utf8,expected", [
    (b"\n", False, "incomplete"), (b"\r\n", False, "incomplete"),
    (b"\r", False, "worker_error"), (b"", False, "worker_error"),
    (b"\n", True, "worker_error"),
])
def test_native_supervisor_keeps_real_pipe_delimiters(tmp_path, monkeypatch, ending, invalid_utf8, expected):
    body = json.dumps({"status": "incomplete", "solver_completed": False}).encode("ascii")
    if invalid_utf8:
        body = body.replace(b"incomplete", b"incom\xffplete")
    output = b"PROJECT_JSON:" + body + ending
    code = "import sys; sys.stdout.buffer.write(" + repr(output) + "); sys.stdout.buffer.flush()"
    original = subprocess.Popen

    def controlled(command, **kwargs):
        return original([sys.executable, "-I", "-S", "-c", code], **kwargs)

    monkeypatch.setattr(native_cli.subprocess, "Popen", controlled)
    result = native_cli.run_isolated(
        {"project_root": str(tmp_path), "entry_file": "unused.py"}, timeout=3)
    assert result["status"] == expected
    assert result["solver_completed"] is False


@pytest.mark.parametrize("raw_tail", [b'PROJECT_INSPECTOR_JSON:{"incomplete":', b'PROJECT_INSPECTOR_JSON:{"bad":"\xff'])
def test_timeout_with_partial_stdout_keeps_committed_progress(tmp_path, monkeypatch, raw_tail):
    code = r'''
import json, pathlib, sys, time
payload = json.load(sys.stdin)
frame = {"progress_protocol": "project_progress_jsonl_v2",
         "run_id": payload["run_id"], "request_sha256": payload["request_sha256"],
         "implementation_sha256_before": "a" * 64, "configuration_sha256": "b" * 64,
         "sequence": 1, "phase": "solver_fixpoint_started"}
pathlib.Path(payload["incident"], "progress.jsonl").write_text(json.dumps(frame) + "\n")
sys.stdout.buffer.write(RAW_TAIL)
sys.stdout.buffer.flush()
time.sleep(3)
'''.replace("RAW_TAIL", repr(raw_tail))
    original = project_runner.run_worker_process

    def controlled(command, *, input, cwd, timeout):
        return original([sys.executable, "-I", "-S", "-c", code],
                        input=input, cwd=cwd, timeout=timeout)

    monkeypatch.setattr(project_runner, "run_worker_process", controlled)
    result = project_runner.run_project(
        {"project_root": "/trusted-control", "entry_file": "unused.py"},
        tmp_path / "capture", timeout_seconds=0.25)
    assert result["status"] == "timeout"
    assert result["partial_progress"]["events"] == 1
    assert result["partial_progress"]["last_phase"] == "solver_fixpoint_started"
    assert result["capture_state"]["status"] == "unavailable"
    assert result["native_report"] is None
    assert result["resource_limits"]["worker_termination"]["child_reaped"] is True


def test_ordinary_atomic_json_report_does_not_need_line_delimiter(tmp_path):
    incident = tmp_path / "incident"
    incident.mkdir()
    report = {"status": "incomplete", "solver_completed": False}
    path = incident / "native_project_report.json"
    path.write_bytes(json.dumps(report).encode("ascii"))
    actual, digest, sealed, errors = project_runner._read_bound_artifacts(incident, "run")
    assert actual == report
    assert digest and sealed is None and errors == []


@pytest.mark.parametrize("runner", ["installed", "native"])
def test_actual_workers_declare_v2_progress_at_the_write_boundary(tmp_path, runner):
    project = tmp_path / "project"
    project.mkdir()
    (project / "app.py").write_text("x = 1\n")
    config = {"project_root": str(project), "entry_file": "app.py", "analysis_stage": "inventory"}
    if runner == "installed":
        result = project_runner.run_project(config, tmp_path / "capture", timeout_seconds=10)
        assert result["status"] == "worker_finished", result
        progress = result["partial_progress"]
        raw = Path(result["incident"], "progress.jsonl").read_text().splitlines()
        assert raw and all(json.loads(line)["progress_protocol"] == PROGRESS_V2 for line in raw)
        assert result["resource_limits"]["worker_response_protocol"] == RECEIPT_V2
    else:
        result = native_cli.run_isolated(config, timeout=10)
        assert result["status"] == "inventory_completed", result
        progress = result["execution_progress"]
    assert progress["progress_protocol"] == PROGRESS_V2
    assert progress["frame_recovery_policy"] == "continuous_committed_prefix"
    assert progress["first_rejection"] is None
