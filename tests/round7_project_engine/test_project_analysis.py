"""Source-separated shared-arena regressions; no target execution in static API."""
from pathlib import Path

import pytest

from pythonstan.analysis.project import ProjectConfig, analyze_project


def project(tmp_path, sources, **options):
    for name, source in sources.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source)
    return ProjectConfig(str(tmp_path), options.pop("entry_file", "app/__init__.py"),
                         source_roots=options.pop("source_roots", ("app",)),
                         auto_framework_entries=False, **options)


def test_transitive_relative_imports_share_real_callback_objects(tmp_path):
    config = project(tmp_path, {
        "app/__init__.py": "from .service import run\nrun()\n",
        "app/service.py": "from .repo import callback\ndef run():\n return callback()\n",
        "app/repo.py": "def callback():\n return 42\n",
    })
    report = analyze_project(config)
    assert report["solver_completed"], report.get("analysis_error")
    assert report["coverage"]["first_party_translated"] == 3
    assert ["app.service.run", "app.repo.callback"] in report["call_edges"]
    assert sorted(report["import_edges"]) == [["app", "app.service"], ["app.service", "app.repo"]]
    assert report["semantic_complete"] is False


def test_inventory_covers_unimported_modules_without_claiming_body_reachability(tmp_path):
    config = project(tmp_path, {
        "app/__init__.py": "x=1\n",
        "app/dormant.py": "def not_an_entry():\n return callback()\ndef callback():\n return 2\n",
    })
    report = analyze_project(config)
    assert report["coverage"]["first_party_translated"] == 2
    assert ["app.dormant.not_an_entry", "app.dormant.callback"] not in report["call_edges"]
    scopes = {row["name"]: row for row in report["scope_coverage"]}
    assert not scopes["app.dormant.not_an_entry"]["constraints_scheduled"]
    assert "synthetic" in report["synthetic_method_context_policy"]


def test_exact_entries_do_not_seed_same_named_function_in_other_module(tmp_path):
    config = project(tmp_path, {
        "app/__init__.py": "pass\n",
        "app/left.py": "def entry():\n return selected()\ndef selected():\n return 1\n",
        "app/right.py": "def entry():\n return rejected()\ndef rejected():\n return 2\n",
    }, entry_points=("app.left.entry",))
    report = analyze_project(config)
    assert report["entry_points"] == ["app.left.entry"]
    assert ["app.left.entry", "app.left.selected"] in report["call_edges"]
    assert ["app.right.entry", "app.right.rejected"] not in report["call_edges"]


def test_short_entry_name_is_unresolved_not_ambiguous_fallback(tmp_path):
    config = project(tmp_path, {"app/__init__.py": "def selected():\n return 1\n"}, entry_points=("selected",))
    report = analyze_project(config)
    assert report["entry_points"] == []
    assert report["unresolved_entries"] == ["selected"]


def test_actual_export_root_preserves_cross_module_decorator_closure(tmp_path):
    config = project(tmp_path, {
        "app/__init__.py": "pass\n",
        "app/guard.py": "def guard(view):\n def wrapped(**kwargs):\n  return view(**kwargs)\n return wrapped\n",
        "app/views.py": "from .guard import guard\ndef callback():\n return 7\n@guard\ndef selected(item):\n return callback()\n",
    }, entry_points=("app.views.selected",))
    report = analyze_project(config)
    assert ["app.guard.guard.wrapped", "app.views.selected"] in report["call_edges"]
    assert ["app.views.selected", "app.views.callback"] in report["call_edges"]
    assert report["source_scope_entry_assumptions"] == []
    assert report["exported_entry_activation"][0]["status"] == "exported_binding_call_installed"


def test_known_replacement_export_does_not_activate_original_body(tmp_path):
    config = project(tmp_path, {"app/__init__.py": '''def forbidden():
 return 0
def actual():
 return 7
def replacement():
 return actual()
def replace(view):
 return replacement
@replace
def selected():
 return forbidden()
'''}, entry_points=("app.selected",))
    report = analyze_project(config)
    assert ["app.replacement", "app.actual"] in report["call_edges"]
    assert ["app.selected", "app.forbidden"] not in report["call_edges"]


def test_conditional_flask_and_wraps_contracts_keep_actual_wrappers(tmp_path):
    sources = {
        "app/__init__.py": "pass\n",
        "app/guard.py": "import functools\ndef guard(view):\n @functools.wraps(view)\n def wrapped(**kwargs):\n  return view(**kwargs)\n return wrapped\n",
        "app/views.py": "from flask import Blueprint\nfrom .guard import guard\nbp=Blueprint('views',__name__)\ndef callback():\n return 7\n@bp.route('/view')\n@guard\ndef selected(item):\n return callback()\n",
    }
    for name, source in sources.items():
        path = tmp_path / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_text(source)
    report = analyze_project(ProjectConfig(str(tmp_path), "app/__init__.py", source_roots=("app",), external_frameworks=(("flask", "3.1.2"),)))
    assert ["app.guard.guard.wrapped", "app.views.selected"] in report["call_edges"]
    assert ["app.views.selected", "app.views.callback"] in report["call_edges"]
    assert len(report["boundary_contract_applications"]) == 2
    assert all(item["call_edges_injected"] == 0 for item in report["boundary_contract_applications"])


def test_click_command_uses_callback_contract_not_function_identity(tmp_path):
    path = tmp_path / "app/__init__.py"; path.parent.mkdir()
    path.write_text("import click\ndef actual():\n return 7\n@click.command('selected')\ndef selected():\n return actual()\n")
    report = analyze_project(ProjectConfig(str(tmp_path), "app/__init__.py", source_roots=("app",), external_frameworks=(("click", "8.1.8"),)))
    assert ["app.selected", "app.actual"] in report["call_edges"]
    assert report["boundary_contract_applications"] == []
    assert report["exported_entry_activation"] == []
    assert report["framework_callback_entry_contracts"][0]["module_export_is_function"] is False
    assert report["framework_callback_entry_contracts"][0]["origin"] == "conditional_click_command_callback"


def test_unknown_decorator_prevents_click_original_callback_assumption(tmp_path):
    path = tmp_path / "app/__init__.py"; path.parent.mkdir()
    path.write_text("import click\nfrom missing import replace\ndef forbidden():\n return 0\n@click.command('selected')\n@replace\ndef selected():\n return forbidden()\n")
    report = analyze_project(ProjectConfig(str(tmp_path), "app/__init__.py", source_roots=("app",), external_frameworks=(("click", "8.1.8"),)))
    assert ["app.selected", "app.forbidden"] not in report["call_edges"]
    assert report["framework_callback_entry_contracts"] == []


def test_parse_error_keeps_whole_source_denominator(tmp_path):
    config = project(tmp_path, {"app/__init__.py": "pass\n", "app/bad.py": "return 1\n"})
    report = analyze_project(config)
    assert report["coverage"]["discovered_python_files"] == 2
    assert report["coverage"]["first_party_translated"] == 1
    assert not report["first_party_translation_complete"]
    assert report["frontend_errors"][0]["module"] == "app.bad"


def test_module_budget_keeps_unmeasured_files(tmp_path):
    config = project(tmp_path, {"app/__init__.py": "pass\n", "app/extra.py": "pass\n"}, max_modules=1)
    report = analyze_project(config)
    assert report["coverage"]["discovered_python_files"] == 2
    assert report["coverage"]["unprocessed_files"] == 1
    assert any(row["status"] == "source_budget_exhausted" for row in report["inventory"])


def test_external_import_is_explicit_boundary_and_not_imported_by_host(tmp_path):
    marker = tmp_path / "executed.txt"
    config = project(tmp_path, {"app/__init__.py": "import absent_dependency\n", "thirdparty/absent_dependency.py": f"open({str(marker)!r}, 'w').write('executed')\n"}, dependency_paths=(str(tmp_path / "thirdparty"),))
    report = analyze_project(config)
    assert not marker.exists()
    assert report["coverage"]["discovered_python_files"] == 1
    assert report["dependency_boundaries"][0]["kind"] == "outside_source_inventory"
    assert report["runtime_observations_used"] is False


def test_source_inventory_never_executes_target(tmp_path):
    marker = tmp_path / "executed.txt"
    config = project(tmp_path, {"app/__init__.py": f"open({str(marker)!r}, 'w').write('executed')\n"})
    report = analyze_project(config)
    assert not marker.exists()
    assert report["target_source_executed"] is False
    assert report["coverage"]["parsed_modules"] == 1


def test_dependency_source_is_analyzed_without_counting_as_first_party(tmp_path):
    config = project(tmp_path, {"app/__init__.py": "from dependency import callback\ncallback()\n", "vendor/dependency.py": "def callback():\n return 1\n"}, dependency_source_roots=("vendor",), import_roots=(".", "vendor"))
    report = analyze_project(config)
    assert report["coverage"]["first_party_discovered"] == 1
    assert report["coverage"]["dependency_source_discovered"] == 1
    assert report["coverage"]["dependency_source_translated"] == 1
    assert ["app", "dependency.callback"] in report["call_edges"]


def test_exact_packaging_exclusion_does_not_drop_runtime_source(tmp_path):
    config = project(tmp_path, {"app/__init__.py": "pass\n", "app/setup.py": "pass\n", "app/service.py": "pass\n"}, exclude_files=("app/setup.py",))
    report = analyze_project(config)
    assert report["coverage"]["discovered_python_files"] == 2
    assert report["excluded_files"] == ["app/setup.py"]


def test_source_root_and_import_root_are_distinct(tmp_path):
    config = project(tmp_path, {"src/package/__init__.py": "from .worker import selected\nselected()\n", "src/package/worker.py": "def selected():\n return 1\n"}, source_roots=("src/package",), import_roots=("src",), entry_file="src/package/__init__.py")
    report = analyze_project(config)
    assert {row["module"] for row in report["inventory"]} == {"package", "package.worker"}
    assert ["package", "package.worker.selected"] in report["call_edges"]


def test_duplicate_module_identity_is_retained_as_ambiguity(tmp_path):
    config = project(tmp_path, {"app/__init__.py": "pass\n", "app.py": "pass\n"}, source_roots=(".",))
    report = analyze_project(config)
    assert len(report["inventory"]) == 2
    assert all(row["status"] == "ambiguous_module_identity" for row in report["inventory"])
    assert report["coverage"]["unprocessed_files"] == 2


def test_iteration_exhaustion_is_not_solver_completion(tmp_path):
    config = project(tmp_path, {"app/__init__.py": "def selected():\n return 1\nselected()\n"}, max_iterations=1)
    report = analyze_project(config)
    assert report["solver_completed"] is False


@pytest.mark.parametrize("legacy", [False, True])
def test_project_method_roots_are_explicit_and_real_calls_remain(tmp_path, legacy):
    config = project(tmp_path, {
        "app/__init__.py": "from .worker import Worker\ndef start():\n return Worker().run()\n",
        "app/worker.py": "def reached():\n return 1\ndef dormant_callback():\n return 2\nclass Worker:\n def run(self):\n  return reached()\n def unused(self):\n  return dormant_callback()\n",
    }, entry_points=("app.start",), synthetic_method_contexts=legacy)
    report = analyze_project(config)
    assert report["solver_completed"], report.get("analysis_error")
    assert ["app.start", "app.worker.Worker.run"] in report["call_edges"]
    assert ["app.worker.Worker.run", "app.worker.reached"] in report["call_edges"]
    assert report["synthetic_method_contexts_enabled"] is legacy
    scopes = {row["name"]: row for row in report["scope_coverage"]}
    if not legacy:
        assert ["app.worker.Worker.unused", "app.worker.dormant_callback"] not in report["call_edges"]
        assert scopes["app.worker.Worker.unused"]["call_graph_contexts"] == 0
        assert not scopes["app.worker.Worker.unused"]["translated"]
    else:
        assert "explicit" in report["synthetic_method_context_policy"]


@pytest.mark.parametrize("options", [{"max_modules": True}, {"max_source_bytes": 0}, {"max_iterations": -1}, {"auto_framework_entries": 1}, {"synthetic_method_contexts": 1}, {"entry_points": "entry"}, {"unknown": 1}])
def test_invalid_config_rejected(options):
    with pytest.raises((ValueError, TypeError)):
        ProjectConfig.from_dict({"project_root": ".", "entry_file": "main.py", **options})


def test_path_escape_rejected(tmp_path):
    config = project(tmp_path, {"app/__init__.py": "pass\n"}, exclude_files=("../outside.py",))
    with pytest.raises(ValueError, match="escapes root"):
        analyze_project(config)
