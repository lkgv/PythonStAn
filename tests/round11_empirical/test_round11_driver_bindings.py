import json
from pathlib import Path
import subprocess

import pytest

from benchmark.round11_protocol import run_pair


def record():
    return {"id": "demo", "package": "demo", "version": "1", "archive_sha256": "a" * 64,
        "python_files": 1, "source_files": [{"path": "demo.py", "sha256": "b" * 64}],
        "project_root": "demo", "entry_file": "demo.py", "source_roots": ["demo.py"],
        "import_roots": ["."], "entry_points": ["demo.entry"]}


def repository(tmp_path):
    repo = tmp_path / "repository"
    (repo / "pythonstan").mkdir(parents=True)
    (repo / "pythonstan/__init__.py").write_text("")
    (repo / "scripts").mkdir()
    (repo / "scripts/analyze_project.py").write_text("# isolated fake engine\n")
    return repo


@pytest.mark.parametrize("failure", ["source", "spawn", "timeout"])
def test_driver_failure_keeps_declared_identity_and_unmeasured_report(tmp_path, monkeypatch, failure):
    repo = repository(tmp_path)
    cache, output = tmp_path / "cache", tmp_path / "output"
    output.mkdir()
    called = []
    def source(*args):
        if failure == "source":
            raise ValueError("changed source")
        return {"source_integrity_verified": True, "python_files": 1,
                "project_root": str((cache / "demo").resolve())}
    def spawn(*args, **kwargs):
        called.append(args[0])
        if failure == "timeout":
            raise subprocess.TimeoutExpired("isolated engine", 85)
        raise OSError("unavailable interpreter")
    monkeypatch.setattr(run_pair, "verify_project", source)
    monkeypatch.setattr(run_pair.subprocess, "run", spawn)
    result = run_pair.run_trial(repo, Path("/venv/bin/python"), cache, "candidate", "broad",
                                record(), "solver", 1, output, run_pair.core_fingerprint(repo))
    raw = json.loads((output / result["path"]).read_text())
    assert raw["project_id"] == "demo" and raw["repeat"] == 1
    assert raw["source_identity"]["python_files"] == 1
    assert raw["report"]["status"] == ("driver_timeout" if failure == "timeout" else "driver_error")
    assert raw["report"]["solver_completed"] is False
    assert raw["report"]["call_edges"] == []
    assert raw["engine_exit_code"] is None
    assert bool(called) == (failure != "source")


def test_selected_venv_interpreter_symlink_is_not_replaced_by_system_target(tmp_path, monkeypatch):
    repo = repository(tmp_path)
    system = tmp_path / "system-python"
    system.write_text("# executable identity\n")
    venv = tmp_path / "venv/bin/python"
    venv.parent.mkdir(parents=True)
    venv.symlink_to(system)
    observed = []
    monkeypatch.setattr(run_pair, "_git", lambda repo, *args: "revision" if args[0] == "rev-parse" else "")
    monkeypatch.setattr(run_pair, "trial_specs", lambda kind: [(record(), "solver", 1)])
    def run_trial(repository, python, cache, phase, kind, record, stage, repeat, output, core, scheduling_policy=None):
        observed.append(python)
        return {"project_id": "demo", "stage": stage, "repeat": repeat,
                "engine_core_matches_phase": True, "source_after_verified": True}
    monkeypatch.setattr(run_pair, "run_trial", run_trial)
    run_pair.run(repo, venv, tmp_path / "cache", "candidate", "broad", tmp_path / "result")
    assert observed == [venv.absolute()]
    assert observed[0] != system.resolve()
