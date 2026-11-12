"""Actual project callbacks preserve solver facts and observation ownership."""

from pathlib import Path

import pytest

from pythonstan.analysis.contracts.json_data import JsonObject, require_object
from pythonstan.analysis.pointer.kcfa.config import Config
from pythonstan.analysis.pointer.kcfa.module_analysis import ModuleAnalyzer
from pythonstan.analysis.pointer.kcfa.solver import PointerSolver
from pythonstan.analysis.project.analysis import ProjectConfig, analyze_project
from pythonstan.analysis.project.report_contracts import ProjectReport
from pythonstan.world.pipeline import Pipeline


class RecordingDiagnostic:
    solver: PointerSolver | None
    report: ProjectReport | None

    def __init__(self) -> None:
        self.solver = None
        self.report = None

    def solver_ready(self, solver: PointerSolver) -> None:
        self.solver = solver

    def project_finished(self, solver: PointerSolver | None, report: ProjectReport) -> None:
        assert solver is self.solver
        self.report = report


def test_progress_payload_is_owned_while_diagnostic_observes_actual_solver(tmp_path: Path) -> None:
    (tmp_path / "entry.py").write_text("def f():\n    return 1\nresult = f()\n")
    diagnostic = RecordingDiagnostic()
    events: list[JsonObject] = []

    def progress(event: JsonObject) -> None:
        events.append(event)
        if event["phase"] == "report_semantics_started":
            require_object(event["statistics"])["converged"] = False

    report = analyze_project(
        ProjectConfig(str(tmp_path), "entry.py", auto_framework_entries=False),
        progress=progress,
        diagnostic=diagnostic,
    )

    assert diagnostic.solver is not None
    assert diagnostic.report is report
    assert report["solver_completed"] is True
    assert report["statistics"]["converged"] == 1
    assert report["call_edges"] == [["entry", "entry.f"]]
    assert events[-1]["phase"] == "report_ready"


def test_closed_module_preserves_optional_legacy_module_graph(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    entry = tmp_path / "entry.py"
    entry.write_text("answer = 1\n")
    pipeline = Pipeline(
        config={
            "filename": str(entry),
            "project_path": str(tmp_path),
            "mock_libs": False,
            "analysis": [],
        }
    )
    world = pipeline.get_world()
    module = world.get_entry_module()
    assert module is not None
    monkeypatch.setattr(world.scope_manager, "module_graph", None)

    summary = ModuleAnalyzer(Config()).analyze_module(module)

    assert sorted(summary.exports) == ["answer"]
