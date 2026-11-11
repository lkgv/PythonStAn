"""Phase coverage is retained independently from completed solver evidence."""
import json
from pathlib import Path
import subprocess

import pytest

from pythonstan.analysis.project import ProjectConfig, analyze_project
from scripts import analyze_project as cli


def project(tmp_path):
    (tmp_path / "app.py").write_text("from helpers import callback\ndef main(): return callback()\n")
    (tmp_path / "helpers.py").write_text("def callback(): return 7\n")
    return {"project_root": str(tmp_path), "entry_file": "app.py", "entry_points": ["app.main"]}


@pytest.mark.parametrize("stage", ["inventory", "frontend"])
def test_observational_stage_never_claims_solver_completion(tmp_path, stage):
    settings = project(tmp_path)
    settings["analysis_stage"] = stage
    report = analyze_project(settings)
    assert report["status"] == stage + "_completed"
    assert report["coverage"]["discovered_python_files"] == 2
    assert report["coverage"]["parsed_modules"] == (2 if stage == "frontend" else 0)
    assert report["call_edges"] == []
    assert report["solver_completed"] is False
    assert report["semantic_complete"] is False
    assert report["analysis_executed"] is False


def test_progress_snapshots_do_not_mutate_and_cover_real_frontend(tmp_path):
    events = []
    settings = project(tmp_path)
    report = analyze_project(settings, progress=events.append)
    inventory = next(event for event in events if event["phase"] == "inventory_completed")
    assert all(row["parsed"] is False for row in inventory["inventory"])
    assert [event["sequence"] for event in events] == list(range(1, len(events) + 1))
    assert {event["configuration_sha256"] for event in events} == {report["configuration_sha256"]}
    assert {event["implementation_sha256_before"] for event in events} == {report["implementation_sha256_before"]}
    started = next(event for event in events if event["phase"] == "solver_fixpoint_started")
    assert all(row["lowered"] and row["translated"] for row in started["module_updates"])
    assert ["app.main", "helpers.callback"] in report["call_edges"]


def test_frontend_error_and_budget_keep_entire_inventory(tmp_path):
    settings = project(tmp_path)
    (tmp_path / "broken.py").write_text("def invalid(:\n")
    settings.update(analysis_stage="frontend", max_modules=1)
    report = analyze_project(settings)
    assert report["status"] == "partial_frontend"
    assert report["coverage"]["discovered_python_files"] == 3
    assert report["coverage"]["unprocessed_files"] == 3
    assert report["coverage"]["lowered_modules"] == 1
    assert report["solver_completed"] is False


def event(sequence, phase, **details):
    return {"schema_version": 1, "sequence": sequence, "phase": phase,
            "run_id": "run", "request_sha256": "request",
            "implementation_sha256_before": "a" * 64,
            "configuration_sha256": "b" * 64, **details}


def test_torn_progress_keeps_known_denominator_and_rejects_stale_frame(tmp_path):
    path = tmp_path / "events.jsonl"
    inventory = [{"path": "app.py", "module": "app", "ownership": "first_party", "parsed": False},
                 {"path": "broken.py", "module": "broken", "ownership": "first_party", "parsed": False}]
    frames = [event(1, "inventory_started"), event(2, "inventory_completed", inventory=inventory),
              event(3, "module_frontend_completed", module_record={**inventory[0], "parsed": True, "lowered": True}),
              {**event(4, "wrong"), "run_id": "stale"}]
    path.write_text("".join(json.dumps(frame) + "\n" for frame in frames) + '{"torn":')
    report = cli._read_progress(path, "run", "request")
    assert report["events"] == 3
    assert report["rejected_or_torn_frames"] == 2
    assert report["coverage_observed"]["discovered_python_files"] == 2
    assert report["coverage_observed"]["lowered_modules"] == 1
    assert report["last_phase"] == "module_frontend_completed"
    assert report["call_projection_completed"] is False
    assert report["source_snapshot_stability_after_termination"] == "unverified"


def test_timeout_supervisor_returns_checkpoint_without_partial_call_claim(tmp_path, monkeypatch):
    settings = project(tmp_path)
    class Terminated(BaseException):
        pass
    class TerminatedProcess:
        def __init__(self, command, **kwargs):
            self.args = command
            self.pid = 1  # Signals are intercepted below; no OS process is owned.
            self.returncode = None
        def communicate(self, *, input=None, timeout=None):
            if input is None:
                self.returncode = -9
                return "", ""
            payload = json.loads(input)
            def save(record):
                record.update(run_id=payload["run_id"], request_sha256=payload["request_sha256"])
                with open(payload["progress_path"], "a") as stream:
                    stream.write(json.dumps(record) + "\n")
                if record["phase"] == "module_frontend_completed":
                    raise Terminated()
            try:
                analyze_project(payload["configuration"], progress=save)
            except Terminated:
                raise subprocess.TimeoutExpired(self.args, 2)
            raise AssertionError("witness must interrupt frontend")
        def kill(self):
            self.returncode = -9
        def poll(self):
            return self.returncode
    monkeypatch.setattr(cli.subprocess, "Popen", TerminatedProcess)
    monkeypatch.setattr(cli.os, "killpg", lambda *args: None)
    report = cli.run_isolated(settings, timeout=2)
    assert report["status"] == "timeout"
    assert report["partial_progress"]["inventory_complete"] is True
    assert report["partial_progress"]["coverage_observed"]["discovered_python_files"] == 2
    assert report["partial_progress"]["coverage_observed"]["lowered_modules"] == 1
    assert report["call_edges"] == [] and report["solver_completed"] is False
    assert report["obligations_retained"] is True


def test_real_isolated_frontend_does_not_run_target(tmp_path):
    marker = tmp_path / "target-ran"
    settings = project(tmp_path)
    (tmp_path / "app.py").write_text(f"open({str(marker)!r}, 'w').write('bad')\n")
    settings["analysis_stage"] = "frontend"
    report = cli.run_isolated(settings, timeout=20)
    assert report["status"] == "frontend_completed"
    assert report["execution_progress"]["events"] > 2
    assert report["execution_progress"]["coverage_observed"]["lowered_modules"] == 2
    assert not marker.exists()


@pytest.mark.parametrize("stage", ["inventory", "frontend"])
def test_cli_success_for_requested_observational_stage(tmp_path, stage):
    settings = project(tmp_path)
    settings["analysis_stage"] = stage
    config = tmp_path / "configuration.json"
    output = tmp_path / "report.json"
    config.write_text(json.dumps(settings))
    completed = subprocess.run([cli.sys.executable, str(Path(cli.__file__).resolve()),
                                "--config", str(config), "--output", str(output)],
                               capture_output=True, text=True, timeout=20)
    assert completed.returncode == 0, completed.stderr + completed.stdout
    report = json.loads(output.read_text())
    assert report["solver_completed"] is False
    assert report["semantic_complete"] is False


@pytest.mark.parametrize("stage", [None, True, "parse", "", 1])
def test_stage_validation_is_strict(tmp_path, stage):
    settings = project(tmp_path)
    settings["analysis_stage"] = stage
    with pytest.raises(ValueError, match="analysis_stage"):
        ProjectConfig.from_dict(settings)
