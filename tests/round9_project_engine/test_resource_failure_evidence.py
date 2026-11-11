"""Resource failures retain scope and cause instead of becoming success."""
import hashlib
import json
from pathlib import Path

from pythonstan.analysis.project import analyze_project
from pythonstan.analysis.project import analysis as engine
from pythonstan.world.analysis_manager import AnalysisManager
from scripts.analyze_project import _read_progress


def source(tmp_path):
    payload = b"def entry():\n    return 7\n"
    (tmp_path / "app.py").write_bytes(payload)
    return {"project_root": str(tmp_path), "entry_file": "app.py", "entry_points": ["app.entry"]}, payload


def test_inventory_and_final_hashing_do_not_materialize_whole_files(tmp_path, monkeypatch):
    settings, payload = source(tmp_path)
    settings["analysis_stage"] = "inventory"
    def forbidden(_path):
        raise AssertionError("Whole-file materialization is not a hashing prerequisite")
    monkeypatch.setattr(Path, "read_bytes", forbidden)
    report = analyze_project(settings)
    assert report["status"] == "inventory_completed"
    assert report["inventory"][0]["sha256"] == hashlib.sha256(payload).hexdigest()
    assert report["source_snapshot_stable"] and report["implementation_stable"]
    assert report["solver_completed"] is False


def test_closure_allocation_failure_retains_the_primary_phase(tmp_path, monkeypatch):
    settings, _ = source(tmp_path)
    # This injection exercises the explicit legacy/debug retention path.
    # Module-local closure has its own failure-phase regression in Round10.
    settings["frontend_lifetime_policy"] = "retain"
    original = AnalysisManager.analysis
    def fail_closure(self, phase, *args, **kwargs):
        if phase == "closure":
            raise MemoryError("synthetic closure allocation failure")
        return original(self, phase, *args, **kwargs)
    monkeypatch.setattr(AnalysisManager, "analysis", fail_closure)
    events = []
    report = analyze_project(settings, progress=events.append)
    assert report["status"] == "resource_exhausted"
    assert report["analysis_failure_phase"] == "closure_started"
    assert "MemoryError" in report["analysis_error"]
    assert report["coverage"]["lowered_modules"] == 1
    assert report["coverage"]["translated_modules"] == 0
    assert report["call_edges"] == [] and report["solver_completed"] is False
    assert report["source_snapshot_stable"] and report["implementation_stable"]
    assert any(e["phase"] == "analysis_resource_exhausted" for e in events)


def test_fatal_frontend_allocation_does_not_analyze_a_partial_arena(tmp_path, monkeypatch):
    settings, _ = source(tmp_path)
    (tmp_path / "later.py").write_text("def unused(): return 8\n")
    original = AnalysisManager.analysis
    def fail_cfg(self, phase, *args, **kwargs):
        if phase == "cfg":
            raise MemoryError("synthetic CFG allocation failure")
        return original(self, phase, *args, **kwargs)
    monkeypatch.setattr(AnalysisManager, "analysis", fail_cfg)
    events = []
    report = analyze_project(settings, progress=events.append)
    assert report["status"] == "resource_exhausted"
    assert report["coverage"]["discovered_python_files"] == 2
    assert report["coverage"]["lowered_modules"] == 0
    assert report["coverage"]["unprocessed_files"] == 2
    assert report["solver_completed"] is False
    assert not any(e["phase"] == "closure_started" for e in events)
    assert report["inventory"][1]["parsed"] is False


def test_failed_final_verification_cannot_preserve_a_success_status(tmp_path, monkeypatch):
    settings, _ = source(tmp_path)
    settings["analysis_stage"] = "inventory"
    def fail_digest(_path):
        raise MemoryError("synthetic final hash allocation failure")
    monkeypatch.setattr(engine, "_file_digest", fail_digest)
    report = analyze_project(settings)
    assert report["source_snapshot_stable"] is False
    assert report["status"] == "unstable_evidence"
    assert report["solver_completed"] is False


def test_primary_error_survives_later_checkpoint_and_torn_output(tmp_path):
    base = {"run_id": "run", "request_sha256": "request", "implementation_sha256_before": "a" * 64,
            "configuration_sha256": "b" * 64}
    frames = [dict(base, sequence=1, phase="analysis_resource_exhausted", analysis_error="MemoryError: closure",
                   failure_phase="closure_started"), dict(base, sequence=2, phase="final_source_verification_started")]
    path = tmp_path / "events.jsonl"
    path.write_text("".join(json.dumps(e) + "\n" for e in frames) + '{"torn":')
    progress = _read_progress(path, "run", "request")
    assert progress["last_observed_analysis_error"] == {"diagnostic": "MemoryError: closure", "phase": "closure_started"}
    assert progress["last_phase"] == "final_source_verification_started"
    assert progress["call_projection_completed"] is False
    assert progress["semantic_complete"] is False
