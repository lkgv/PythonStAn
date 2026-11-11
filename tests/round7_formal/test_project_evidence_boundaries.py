"""Independent application coverage/entry claims, distinct from solver checks."""
import pytest

from pythonstan.analysis.project import ProjectConfig, analyze_project
from tests.analysis.legacy_ai_support import isolated_world


def project(tmp_path, files, **kwargs):
    for name, source in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source)
    with isolated_world():
        return analyze_project(ProjectConfig(str(tmp_path), "entry.py", auto_framework_entries=False, **kwargs))


def test_discovered_source_function_is_not_reported_as_reached_body(tmp_path):
    result = project(tmp_path, {"entry.py": "value=1\ndef dormant(callback):\n    return callback()\n"})
    assert result["coverage"]["discovered_python_files"] == 1
    dormant = next(row for row in result["scope_coverage"] if row["name"] == "entry.dormant")
    assert not dormant["translated"]
    assert result["semantic_complete"] is False
    assert result["whole_program_soundness_proved"] is False


def test_source_coverage_retains_invalid_unimported_module_denominator(tmp_path):
    result = project(tmp_path, {"entry.py": "result=1\n", "broken.py": "def invalid(:\n"})
    assert result["coverage"]["discovered_python_files"] == 2
    assert result["coverage"]["parsed_modules"] == 1
    assert result["coverage"]["unprocessed_files"] == 1
    assert result["frontend_errors"]
    assert result["first_party_translation_complete"] is False


def test_external_import_remains_boundary_after_module_fixpoint(tmp_path):
    result = project(tmp_path, {"entry.py": "import unavailable_library\nresult=1\n"})
    assert result["solver_completed"] is True
    assert any(row["kind"] == "unresolved_import" for row in result["dependency_boundaries"])
    assert result["semantic_complete"] is False
    assert result["analysis_complete"] is False
    assert result["target_source_executed"] is False


def test_budget_excluded_file_is_unmeasured_in_full_inventory(tmp_path):
    result = project(tmp_path, {"entry.py": "result=1\n", "other.py": "result=2\n"}, max_modules=1)
    assert result["coverage"]["discovered_python_files"] == 2
    assert result["coverage"]["lowered_modules"] == 1
    assert result["coverage"]["unprocessed_files"] == 1
    assert any(row["status"] == "source_budget_exhausted" for row in result["modules"])
    assert not result["first_party_translation_complete"]


def test_explicit_unknown_entry_is_not_silently_counted_as_resolved(tmp_path):
    result = project(tmp_path, {"entry.py": "def actual():\n    return 3\n"}, entry_points=("entry.missing",))
    assert result["unresolved_entries"] == ["entry.missing"]
    assert result["entry_points"] == []
    assert not result["entry_roots"]


def test_package_prefix_comes_from_import_root_not_inventory_subtree(tmp_path):
    files = {"entry.py": "from package.service import run\nresult=run()\n",
             "package/__init__.py": "",
             "package/service.py": "def run():\n    return 5\n"}
    result = project(tmp_path, files, source_roots=("entry.py", "package"))
    assert {row["module"] for row in result["modules"]} == {"entry", "package", "package.service"}
    assert ["entry", "package.service.run"] in [list(edge) for edge in result["call_edges"]]


def test_duplicate_module_file_and_package_are_not_silently_merged(tmp_path):
    result = project(tmp_path, {"entry.py": "result=1\n", "conflict.py": "x=2\n", "conflict/__init__.py": "x=3\n"})
    ambiguous = [row for row in result["modules"] if row["status"] == "ambiguous_module_identity"]
    assert len(ambiguous) == 2
    assert result["coverage"]["unprocessed_files"] == 2
    assert not result["first_party_translation_complete"]


def test_included_dependency_does_not_inflate_first_party_module_count(tmp_path):
    result = project(tmp_path, {"entry.py": "from dependency import run\nresult=run()\n",
                                "dependency.py": "def run():\n    return 7\n"},
                     source_roots=("entry.py",), dependency_source_roots=("dependency.py",))
    assert result["coverage"]["first_party_discovered"] == 1
    assert result["coverage"]["dependency_source_discovered"] == 1
    assert result["coverage"]["dependency_source_translated"] == 1
    assert ["entry", "dependency.run"] in [list(edge) for edge in result["call_edges"]]


def test_source_snapshot_changes_make_evidence_unstable(tmp_path, monkeypatch):
    import pythonstan.analysis.project.analysis as implementation
    original = implementation._inventory
    def changed(config, root):
        result = original(config, root)
        (root / "entry.py").write_text("changed=11\n")
        return result
    monkeypatch.setattr(implementation, "_inventory", changed)
    result = project(tmp_path, {"entry.py": "initial=9\n"})
    assert result["source_snapshot_stable"] is False
    assert result["status"] == "unstable_evidence"
    assert result["whole_program_soundness_proved"] is False


def test_new_file_after_inventory_is_not_a_stable_full_source_snapshot(tmp_path, monkeypatch):
    import pythonstan.analysis.project.analysis as implementation
    original = implementation._inventory
    def changed(config, root):
        result = original(config, root)
        (root / "late.py").write_text("added=11\n")
        return result
    monkeypatch.setattr(implementation, "_inventory", changed)
    result = project(tmp_path, {"entry.py": "initial=9\n"})
    assert result["source_snapshot_stable"] is False
    assert result["status"] == "unstable_evidence"


def test_source_symlink_escape_is_not_counted_as_project_source(tmp_path):
    outside = tmp_path.parent / (tmp_path.name + "_outside.py")
    outside.write_text("secret=13\n")
    (tmp_path / "entry.py").write_text("result=1\n")
    (tmp_path / "escaped.py").symlink_to(outside)
    with pytest.raises(ValueError, match="symlink escapes"):
        with isolated_world():
            analyze_project(ProjectConfig(str(tmp_path), "entry.py", auto_framework_entries=False))
