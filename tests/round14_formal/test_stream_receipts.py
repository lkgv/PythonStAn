"""Finite independent native-supervisor receipts, using actual child pipes.

These children emit trusted controlled bytes; they perform no source analysis.
Expected framing is frozen in stream_obligations.json before predictions.
"""
import json
import subprocess
import sys

import pytest

from scripts import analyze_project


REPORT = {
    "status": "incomplete",
    "solver_completed": False,
    "semantic_complete": False,
    "whole_program_soundness_proved": False,
    "statistics": {"iterations": 7, "pending_work": 3},
}


@pytest.mark.parametrize("mode", ["complete", "missing-newline", "torn", "nonzero"])
def test_native_supervisor_requires_complete_successful_transport_frame(tmp_path, monkeypatch, mode):
    payload = json.dumps(REPORT)
    stdout = "PROJECT_JSON:" + payload + "\n"
    if mode == "missing-newline":
        stdout = stdout[:-1]
    elif mode == "torn":
        stdout = "PROJECT_JSON:" + payload[:-1]
    code = "import sys; sys.stdout.write(" + repr(stdout) + "); sys.stdout.flush(); sys.exit(" + (
        "9" if mode == "nonzero" else "0") + ")"
    popen = subprocess.Popen

    def controlled_child(_command, **kwargs):
        return popen([sys.executable, "-c", code], **kwargs)

    monkeypatch.setattr(analyze_project.subprocess, "Popen", controlled_child)
    result = analyze_project.run_isolated(
        {"project_root": str(tmp_path), "entry_file": "unused.py"}, timeout=2, memory_mb=128)
    if mode == "complete":
        assert result["status"] == "incomplete"
        assert result["solver_completed"] is False
        assert result["statistics"] == REPORT["statistics"]
    else:
        assert result["status"] == "worker_error"
        assert result["solver_completed"] is False
        assert result["semantic_complete"] is False


def test_native_supervisor_does_not_promote_complete_bytes_before_process_exit(tmp_path, monkeypatch):
    stdout = "PROJECT_JSON:" + json.dumps(REPORT) + "\n"
    code = "import sys,time; sys.stdout.write(" + repr(stdout) + "); sys.stdout.flush(); time.sleep(3)"
    popen = subprocess.Popen

    def controlled_child(_command, **kwargs):
        return popen([sys.executable, "-c", code], **kwargs)

    monkeypatch.setattr(analyze_project.subprocess, "Popen", controlled_child)
    result = analyze_project.run_isolated(
        {"project_root": str(tmp_path), "entry_file": "unused.py"}, timeout=0.2, memory_mb=128)
    assert result["status"] == "timeout"
    assert result["solver_completed"] is False
    assert result["semantic_complete"] is False
