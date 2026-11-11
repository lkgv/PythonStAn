"""Whole-project native roots, isolated resources, and final safe captures."""
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from pythonstan.analysis.project import ProjectConfig, analyze_project
from pythonstan_inspector.contract import CaptureRequest, InspectorError, QueryRequest
from pythonstan_inspector.core import query
from pythonstan_inspector.project_runner import _ProjectDiagnostic, run_project
from pythonstan_inspector.storage import load_bundle


def _project(tmp_path, *, max_iterations=5000, analysis_stage="solver"):
    sources = {
        "app/__init__.py": "pass\n",
        "app/guard.py": "def guard(view):\n def wrapped(**kwargs):\n  return view(**kwargs)\n return wrapped\n",
        "app/views.py": "from .guard import guard\ndef callback():\n return 7\n@guard\ndef selected(item):\n return callback()\n",
    }
    for name, text in sources.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    return ProjectConfig(str(tmp_path), "app/__init__.py", source_roots=("app",),
        auto_framework_entries=False, entry_points=("app.views.selected",),
        max_iterations=max_iterations, analysis_stage=analysis_stage)


def _bridge(tmp_path, *, request=None, trace=False):
    incident = tmp_path / "incident"
    incident.mkdir()
    return _ProjectDiagnostic(incident, "project-test", request or CaptureRequest(), trace)


def test_isolated_whole_project_keeps_original_roots_and_export_continuation(tmp_path):
    config = _project(tmp_path)
    result = run_project(config, tmp_path / "captures", timeout_seconds=20, trace=True)
    assert result["status"] == "worker_finished", result["diagnostic"]
    assert result["capture_state"]["status"] == "sealed", result["capture_state"]
    report = result["native_report"]
    assert report["effective_config"] == json.loads(json.dumps(asdict(config)))
    assert report["coverage"]["first_party_translated"] == 3
    assert ["app.guard.guard.wrapped", "app.views.selected"] in report["call_edges"]
    assert ["app.views.selected", "app.views.callback"] in report["call_edges"]
    assert report["exported_entry_activation"][0]["status"] == "exported_binding_call_installed"
    for row in report["modules"]:
        assert row["consumed_sha256"] == hashlib.sha256((tmp_path / row["path"]).read_bytes()).hexdigest()
        assert row["source_read_matches_inventory"] is True
    bundle = load_bundle(result["bundle"])
    assert bundle.data["run"]["inputs"]["inventory_count"] == 3
    assert bundle.data["run"]["inputs"]["consumed_count"] == 3
    assert bundle.data["run"]["configuration_sha256"] == report["configuration_sha256"]
    assert bundle.data["run"]["project_report"]["sha256"] is None
    assert bundle.data["run"]["project_report"]["binding"] == "separate_incident_receipt_after_snapshot"
    assert result["worker_incident_receipt"]["artifacts"]["native_report"]["sha256"] == hashlib.sha256(
        Path(result["native_report_path"]).read_bytes()).hexdigest()
    assert bundle.data["run"]["native_project_solver_completed"] is True
    assert bundle.data["run"]["whole_program_soundness_proved"] is False
    summary = query(bundle, QueryRequest("summary"))
    assert summary["boundary"]["coherent"] is True
    assert bundle.trace
    # The minimum real path still works without the analyzer installed.
    isolated = tmp_path / "offline"
    package = isolated / "pythonstan_inspector"
    package.mkdir(parents=True)
    original = Path(__file__).resolve().parents[2] / "pythonstan_inspector"
    for name in ("__init__.py", "contract.py", "core.py", "storage.py", "identity.py"):
        shutil.copyfile(original / name, package / name)
    script = isolated / "query.py"
    script.write_text("import sys\nsys.path.insert(0," + repr(str(isolated)) + ")\n"
        "from pythonstan_inspector.storage import load_bundle\n"
        "from pythonstan_inspector.core import query\n"
        "from pythonstan_inspector.contract import QueryRequest\n"
        "b=load_bundle(" + repr(result["bundle"]) + ")\n"
        "assert query(b,QueryRequest('summary'))['boundary']['coherent'] is True\n"
        "assert not any(k=='pythonstan' or k.startswith('pythonstan.') for k in sys.modules)\n")
    finished = subprocess.run([sys.executable, "-I", "-S", str(script)], text=True,
                              capture_output=True, timeout=10)
    assert finished.returncode == 0, finished.stderr


def test_project_observation_preserves_native_logical_results(tmp_path):
    config = _project(tmp_path)
    baseline = analyze_project(config)
    diagnostic = _bridge(tmp_path)
    observed = analyze_project(config, diagnostic=diagnostic)
    assert observed["call_edges"] == baseline["call_edges"]
    assert observed["statistics"] == baseline["statistics"]
    assert observed["resolved_entry_points"] == baseline["resolved_entry_points"]
    assert observed["source_scope_entry_assumptions"] == baseline["source_scope_entry_assumptions"]
    assert observed["coverage"] == baseline["coverage"]
    assert observed["effective_config"] == baseline["effective_config"]
    assert diagnostic.capture_state["status"] == "sealed"
    assert all("consumed_sha256" not in row for row in baseline["modules"])


def test_iteration_limit_remains_an_incomplete_but_coherent_capture(tmp_path):
    config = _project(tmp_path, max_iterations=1)
    diagnostic = _bridge(tmp_path)
    report = analyze_project(config, diagnostic=diagnostic)
    assert report["solver_completed"] is False
    assert report["effective_config"]["max_iterations"] == 1
    assert diagnostic.capture_state["status"] == "sealed"
    bundle = load_bundle(diagnostic.bundle_path)
    assert bundle.data["run"]["stop_reason"] == "iteration_limit"
    assert bundle.data["snapshot"]["boundary"]["coherent"] is True


@pytest.mark.parametrize("stage", ["inventory", "frontend"])
def test_no_solver_stage_has_no_fabricated_snapshot(tmp_path, stage):
    diagnostic = _bridge(tmp_path)
    report = analyze_project(_project(tmp_path, analysis_stage=stage), diagnostic=diagnostic)
    assert report["solver_completed"] is False
    assert diagnostic.capture_state["status"] == "unsupported"
    assert diagnostic.report_path.is_file()
    assert not diagnostic.bundle_path.exists()


def test_aborted_report_cannot_certify_a_previously_stopped_solver(tmp_path, monkeypatch):
    from pythonstan.analysis.project import analysis as native
    config = _project(tmp_path)
    diagnostic = _bridge(tmp_path)

    def failed_projection(state):
        raise RuntimeError("test report projection aborted outside solver work")

    monkeypatch.setattr(native, "_scope_context_counts", failed_projection)
    report = analyze_project(config, diagnostic=diagnostic)
    assert report["status"] == "analysis_error"
    assert diagnostic.capture_state["status"] == "refused"
    assert not diagnostic.bundle_path.exists()


def test_consumed_hash_detects_transient_source_change_hidden_by_pre_post(tmp_path, monkeypatch):
    config = _project(tmp_path)
    target = tmp_path / "app" / "__init__.py"
    original_bytes = target.read_bytes()
    changed_bytes = b"value = 99\n"
    original_read = Path.read_bytes

    def transient_read(path):
        if path == target:
            target.write_bytes(changed_bytes)
            try:
                return original_read(path)
            finally:
                target.write_bytes(original_bytes)
        return original_read(path)

    diagnostic = _bridge(tmp_path)
    monkeypatch.setattr(Path, "read_bytes", transient_read)
    report = analyze_project(config, diagnostic=diagnostic)
    row = next(row for row in report["modules"] if row["path"] == "app/__init__.py")
    assert row["sha256"] == hashlib.sha256(original_bytes).hexdigest()
    assert row["consumed_sha256"] == hashlib.sha256(changed_bytes).hexdigest()
    assert row["source_read_matches_inventory"] is False
    assert report["source_snapshot_stable"] is False
    assert report["status"] == "unstable_evidence"
    assert diagnostic.capture_state["status"] == "refused"


def test_diagnostic_hook_failures_do_not_change_native_report(tmp_path):
    config = _project(tmp_path)
    baseline = analyze_project(config)

    class BrokenDiagnostic:
        def solver_ready(self, solver):
            raise OSError("test setup sink failure")

        def project_finished(self, solver, report):
            raise OSError("test final sink failure")

    observed = analyze_project(config, diagnostic=BrokenDiagnostic())
    assert observed["status"] == baseline["status"]
    assert observed["statistics"] == baseline["statistics"]
    assert observed["call_edges"] == baseline["call_edges"]
    assert observed["inspector"]["status"] == "failed"
    assert {row["hook"] for row in observed["inspector"]["errors"]} == {"solver_ready", "project_finished"}


def test_timeout_never_reuses_an_old_output_bundle(tmp_path):
    config = _project(tmp_path)
    output = tmp_path / "captures"
    stale = output / "bundle"
    stale.mkdir(parents=True)
    (stale / "manifest.json").write_text('{"old":true}')
    result = run_project(config, output, timeout_seconds=0.001)
    assert result["status"] == "timeout"
    assert result["bundle"] is None
    assert result["last_sealed_snapshot"] is None
    assert result["capture_state"]["status"] == "unavailable"
    assert Path(result["incident"]).parent == output
    assert Path(result["incident"]) != output


def test_native_project_off_does_not_import_optional_inspector(tmp_path):
    config = _project(tmp_path)
    script = tmp_path / "off.py"
    script.write_text("import sys,importlib.abc\n"
        "sys.path.insert(0," + repr(str(Path(__file__).resolve().parents[2])) + ")\n"
        "class Block(importlib.abc.MetaPathFinder):\n"
        " def find_spec(self,fullname,path=None,target=None):\n"
        "  if fullname.startswith('pythonstan_inspector'): raise AssertionError(fullname)\n"
        "sys.meta_path.insert(0,Block())\n"
        "from pythonstan.analysis.project import analyze_project\n"
        "result=analyze_project(" + repr(asdict(config)) + ")\n"
        "assert result['solver_completed'] is True\n"
        "assert all('consumed_sha256' not in r for r in result['modules'])\n"
        "assert not any(k.startswith('pythonstan_inspector') for k in sys.modules)\n")
    finished = subprocess.run([sys.executable, str(script)], text=True, capture_output=True,
                              cwd=Path(__file__).resolve().parents[2], timeout=15)
    assert finished.returncode == 0, finished.stderr


@pytest.mark.parametrize("invalid", [None, True, 1, "", "fair", [], {}])
def test_project_points_policy_rejects_unknown_values(invalid):
    with pytest.raises(ValueError, match="points_work_policy"):
        ProjectConfig("/unused", "app.py", points_work_policy=invalid)


@pytest.mark.parametrize("policy", ["fifo", "consumer_fair"])
def test_project_points_policy_is_preserved_in_configuration_identity(policy):
    config = ProjectConfig("/unused", "app.py", points_work_policy=policy)
    assert asdict(config)["points_work_policy"] == policy
    assert ProjectConfig.from_dict(asdict(config)).points_work_policy == policy


def test_native_report_binding_mismatch_does_not_invent_a_bound_report(tmp_path):
    from pythonstan_inspector.project_runner import _read_bound_artifacts
    config = _project(tmp_path)
    diagnostic = _bridge(tmp_path)
    analyze_project(config, diagnostic=diagnostic)
    report = json.loads(diagnostic.report_path.read_bytes())
    report["status"] = "forged_status"
    diagnostic.report_path.write_text(json.dumps(report))
    report, actual_sha, sealed, errors = _read_bound_artifacts(diagnostic.incident, diagnostic.run_id)
    assert report is None
    assert actual_sha is not None
    assert sealed is not None  # The sealed state remains independently readable.
    assert "native_report_content_does_not_match_sealed_binding" in errors


def test_metadata_budget_refusal_preserves_complete_native_configuration(tmp_path):
    config = _project(tmp_path)
    diagnostic = _bridge(tmp_path, request=CaptureRequest(max_bytes=4096))
    report = analyze_project(config, diagnostic=diagnostic)
    assert report["solver_completed"] is True
    assert report["effective_config"] == asdict(config)
    assert diagnostic.capture_state["status"] == "refused"
    assert "metadata" in diagnostic.capture_state["reason"]
    preserved = json.loads(diagnostic.report_path.read_bytes())
    assert preserved["effective_config"] == json.loads(json.dumps(asdict(config)))
    assert len(preserved["modules"]) == 3
    assert not diagnostic.bundle_path.exists()


def test_project_diagnostic_does_not_swallow_interruption(tmp_path):
    class InterruptingDiagnostic:
        def solver_ready(self, solver):
            raise KeyboardInterrupt("test explicit interruption")

        def project_finished(self, solver, report):
            raise AssertionError("not reached")

    with pytest.raises(KeyboardInterrupt, match="explicit interruption"):
        analyze_project(_project(tmp_path), diagnostic=InterruptingDiagnostic())


def test_timeout_recovers_only_a_separately_sealed_bound_snapshot(tmp_path, monkeypatch):
    """Protocol fault after real engine/adapter sealing, not a fake JSON fact."""
    config = _project(tmp_path)

    def timeout_after_sealing(command, *, input, cwd, timeout):
        payload = json.loads(input)
        diagnostic = _ProjectDiagnostic(payload["incident"], payload["run_id"], CaptureRequest(),
            request_sha256=payload["request_sha256"], configuration=payload["configuration"])
        report = analyze_project(ProjectConfig.from_dict(payload["configuration"]), diagnostic=diagnostic)
        assert report["solver_completed"] is True
        assert diagnostic.capture_state["status"] == "sealed"
        from pythonstan_inspector.project_process import WorkerTimeout
        raise WorkerTimeout({"child_reaped": True, "child_returncode": -9})

    monkeypatch.setattr("pythonstan_inspector.project_runner.run_worker_process", timeout_after_sealing)
    result = run_project(config, tmp_path / "captures", timeout_seconds=20)
    assert result["status"] == "timeout"
    assert result["capture_state"]["status"] == "sealed"
    assert result["capture_state"]["recovered_after_worker_termination"] is True
    assert result["last_sealed_snapshot"]["snapshot_ref"]["run_id"] == result["run_id"]
    assert result["native_report"]["solver_completed"] is True
    assert result["resource_limits"]["address_space_limit_enforced"] is None


def test_dict_supervisor_imports_no_native_analysis_in_parent(tmp_path):
    config = asdict(_project(tmp_path))
    script = tmp_path / "supervisor.py"
    script.write_text("import sys,importlib.abc\n"
        "sys.path.insert(0," + repr(str(Path(__file__).resolve().parents[2])) + ")\n"
        "class Block(importlib.abc.MetaPathFinder):\n"
        " def find_spec(self,fullname,path=None,target=None):\n"
        "  if fullname=='pythonstan' or fullname.startswith('pythonstan.'): raise AssertionError(fullname)\n"
        "sys.meta_path.insert(0,Block())\n"
        "from pythonstan_inspector.project_runner import run_project\n"
        "result=run_project(" + repr(config) + "," + repr(str(tmp_path / "captures")) + ",timeout_seconds=20)\n"
        "assert result['status']=='worker_finished',result\n"
        "assert result['capture_state']['status']=='sealed',result\n"
        "assert not any(k=='pythonstan' or k.startswith('pythonstan.') for k in sys.modules)\n")
    finished = subprocess.run([sys.executable, str(script)], text=True, capture_output=True, timeout=25)
    assert finished.returncode == 0, finished.stderr


def test_invalid_configuration_is_a_typed_child_outcome(tmp_path):
    config = asdict(_project(tmp_path))
    config["unknown_project_option"] = True
    result = run_project(config, tmp_path / "captures", timeout_seconds=20)
    assert result["status"] == "worker_error"
    assert result["native_report"]["status"] == "analysis_error"
    assert result["capture_state"]["status"] == "unsupported"
    assert result["bundle"] is None


def test_oversized_bootstrap_is_refused_without_spawning(tmp_path, monkeypatch):
    config = {"project_root": str(tmp_path), "entry_file": "x" * (1024 * 1024 + 1)}

    def forbidden(*args, **kwargs):
        raise AssertionError("oversized bootstrap must not spawn")

    monkeypatch.setattr(subprocess, "Popen", forbidden)
    with pytest.raises(InspectorError, match="bootstrap payload exceeds 1 MiB"):
        run_project(config, tmp_path / "captures")


def test_failure_after_sealing_is_a_worker_error_with_an_earlier_snapshot(tmp_path, monkeypatch):
    config = _project(tmp_path)
    injected_script = """from pythonstan.analysis import project
original = project.analyze_project
def fail_after_seal(config, *, progress=None, diagnostic=None):
    def checkpoint(event):
        if event['phase'] == 'report_ready':
            raise OSError('test post-seal progress failure')
        if progress is not None:
            progress(event)
    return original(config, progress=checkpoint, diagnostic=diagnostic)
project.analyze_project = fail_after_seal
from pythonstan_inspector.project_runner import _worker
_worker()
"""
    original_popen = subprocess.Popen

    def injected_worker(command, **kwargs):
        return original_popen([sys.executable, "-c", injected_script], **kwargs)

    monkeypatch.setattr(subprocess, "Popen", injected_worker)
    result = run_project(config, tmp_path / "captures", timeout_seconds=20)
    assert result["status"] == "worker_error"
    assert result["worker_analysis_error"]["exception"] == "OSError"
    assert result["capture_state"]["status"] == "sealed"
    assert result["last_sealed_snapshot"] is not None
    assert result["native_report_origin"] == "sealed_callback_before_worker_error"


def test_adapter_code_drift_refuses_capture_without_changing_native_result(tmp_path, monkeypatch):
    from pythonstan_inspector import project_runner
    identities = iter(("a" * 64, "b" * 64))
    monkeypatch.setattr(project_runner, "_adapter_digest", lambda: next(identities))
    config = _project(tmp_path)
    diagnostic = _bridge(tmp_path)
    report = analyze_project(config, diagnostic=diagnostic)
    assert report["status"] == "completed_with_residuals"
    assert report["solver_completed"] is True
    assert report["implementation_stable"] is True
    assert diagnostic.capture_state == {"status": "refused", "reason": "tool_changed_during_analysis"}
    assert diagnostic.report_path.is_file()
    assert json.loads(diagnostic.report_path.read_bytes())["solver_completed"] is True
    assert not diagnostic.bundle_path.exists()
