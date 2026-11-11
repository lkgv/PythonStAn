"""String-import module identities and report projection preserve their scope."""
from dataclasses import dataclass
import json
import subprocess
import sys
from types import SimpleNamespace

import pytest

from pythonstan.analysis.project import ProjectConfig, analyze_project
from pythonstan.analysis.project.analysis import _scope_context_counts
from pythonstan.analysis.pointer.kcfa.state import PointerAnalysisState
from scripts.analyze_project import _read_progress


def package(tmp_path, module_name):
    folder = tmp_path / "pkg" / "migrations"
    folder.mkdir(parents=True)
    (tmp_path / "pkg" / "__init__.py").write_text("")
    (folder / "__init__.py").write_text("")
    (folder / (module_name + ".py")).write_text("def apply():\n    return 7\n")
    return {"project_root": str(tmp_path), "entry_file": "pkg/__init__.py", "source_roots": ["pkg"],
            "entry_points": ["pkg.migrations." + module_name + ".apply"], "analysis_stage": "frontend"}


@pytest.mark.parametrize("name", ["0001_initial", "0002_auto_20160226_1747", "plugin-with-hyphen", "配置"])
def test_source_module_identity_matches_real_string_import(tmp_path, name):
    settings = package(tmp_path, name)
    # A self-contained, harmless language witness, not execution of a corpus project.
    witness = "import sys,importlib;sys.path.insert(0,sys.argv[1]);print(importlib.import_module(sys.argv[2]).apply())"
    run = subprocess.run([sys.executable, "-I", "-c", witness, str(tmp_path), "pkg.migrations." + name],
                         capture_output=True, text=True, timeout=10, check=True)
    assert run.stdout.strip() == "7"
    report = analyze_project(settings)
    assert report["status"] == "frontend_completed"
    assert report["coverage"]["discovered_python_files"] == 3
    assert report["coverage"]["lowered_modules"] == 3
    assert report["solver_completed"] is False
    row = next(r for r in report["inventory"] if r["path"].endswith(name + ".py"))
    assert row["module"] == "pkg.migrations." + name
    assert row["lowered"] is True
    assert row["module_name_syntax"] == ("identifier" if name.isidentifier() else "string_import_only")


def test_dot_inside_filename_does_not_alias_nested_module(tmp_path):
    settings = package(tmp_path, "0001.initial")
    settings["analysis_stage"] = "inventory"
    report = analyze_project(settings)
    rows = [r for r in report["inventory"] if r["path"].endswith("0001.initial.py")]
    assert len(rows) == 1 and rows[0]["status"] == "invalid_module_name"
    assert rows[0]["parsed"] is False
    assert report["solver_completed"] is False


def test_numeric_module_qualname_can_be_an_explicit_entry(tmp_path):
    settings = package(tmp_path, "0001_initial")
    settings.update(analysis_stage="solver", max_iterations=10000, auto_framework_entries=False)
    report = analyze_project(settings)
    assert report["frontend_errors"] == []
    assert report["unresolved_entries"] == []
    assert report["first_party_translation_complete"] is True
    assert "pkg.migrations.0001_initial.apply" in report["resolved_entry_points"]
    # Entry selection does not establish dynamic migration-loader semantics.
    assert report["whole_program_soundness_proved"] is False


@dataclass(frozen=True)
class ContextScope:
    stmt: object
    context: int


@pytest.mark.parametrize("duplicate_internal", [False, True])
def test_batched_projection_agrees_with_existing_context_query(duplicate_internal):
    first, second, absent = object(), object(), object()
    a, b, c = ContextScope(first, 1), ContextScope(first, 2), ContextScope(second, 1)
    internal = {"a": a, "c": c}
    if duplicate_internal:
        internal["same_scope_for_second_object"] = a
    graph_nodes = [a, b, b, c]
    state = SimpleNamespace(_internal_scope=internal, call_graph=SimpleNamespace(get_nodes=lambda: graph_nodes))
    counts = _scope_context_counts(state)
    for ir in (first, second, absent):
        assert counts[ir] == len(PointerAnalysisState.get_scopes_for_ir(state, ir))
    # Reporting cannot mutate the source scope registries.
    assert list(state.call_graph.get_nodes()) == graph_nodes
    assert state._internal_scope is internal


def test_statistics_checkpoint_is_retained_without_certifying_a_timeout(tmp_path):
    path = tmp_path / "progress.jsonl"
    base = {"run_id": "run", "request_sha256": "request", "implementation_sha256_before": "a" * 64,
            "configuration_sha256": "b" * 64}
    frames = [dict(base, sequence=1, phase="report_semantics_started", statistics={"converged": 1, "iterations": 23}),
              dict(base, sequence=2, phase="report_scope_coverage_started"),
              dict(base, sequence=3, phase="wrong", request_sha256="other", statistics={"converged": 0})]
    path.write_text("".join(json.dumps(f) + "\n" for f in frames))
    report = _read_progress(path, "run", "request")
    assert report["last_observed_statistics"] == {"converged": 1, "iterations": 23}
    assert report["last_observed_statistics_phase"] == "report_semantics_started"
    assert report["last_phase"] == "report_scope_coverage_started"
    assert report["call_projection_completed"] is False
    assert report["semantic_complete"] is False
    assert report["source_snapshot_stability_after_termination"] == "unverified"
