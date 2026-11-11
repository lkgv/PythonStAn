"""Independent checks of exported values and conditional decorator transfers."""
import hashlib

import pytest

from pythonstan.analysis.project import ProjectConfig, analyze_project
from tests.analysis.legacy_ai_support import isolated_world


def analyze(tmp_path, sources, *, entries=("app.views.selected",), limit=50000,
            frameworks=(("flask", "3.1.2"),)):
    for name, source in sources.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source)
    with isolated_world():
        return analyze_project(ProjectConfig(str(tmp_path), "app/__init__.py",
            source_roots=("app",), external_frameworks=frameworks,
            entry_points=entries, max_iterations=limit))


def test_external_export_call_follows_cross_module_captured_callback(tmp_path):
    report = analyze(tmp_path, {
        "app/__init__.py": "",
        "app/decorators.py": "def decorate(callback):\n    def wrapped(*args, **kwargs):\n        return callback(*args, **kwargs)\n    return wrapped\n",
        "app/views.py": "from .decorators import decorate\ndef chosen():\n    return 7\n@decorate\ndef selected(value, /):\n    return chosen()\n",
    })
    assert ["app.decorators.decorate.wrapped", "app.views.selected"] in report["call_edges"]
    assert ["app.views.selected", "app.views.chosen"] in report["call_edges"]
    assert report["source_scope_entry_assumptions"] == []
    assert report["exported_entry_activation"][0]["parameter_coverage"] == "unknown_witnesses_not_exhaustive"


@pytest.mark.parametrize("order", ["route_outside", "replace_outside"])
def test_identity_route_never_bypasses_known_replacement(tmp_path, order):
    decorators = "@app.route('/')\n@replace\n" if order == "route_outside" else "@replace\n@app.route('/')\n"
    source = '''from flask import Flask
app=Flask(__name__)
def forbidden():
    return 0
def chosen():
    return 7
def replacement():
    return chosen()
def replace(original):
    return replacement
''' + decorators + '''def selected():
    return forbidden()
'''
    report = analyze(tmp_path, {"app/__init__.py": "", "app/views.py": source})
    assert ["app.views.replacement", "app.views.chosen"] in report["call_edges"]
    assert ["app.views.selected", "app.views.forbidden"] not in report["call_edges"]
    assert len(report["boundary_contract_applications"]) == 1
    assert report["boundary_contract_applications"][0]["call_edges_injected"] == 0
    assert report["semantic_complete"] is False


def test_unknown_decorator_result_does_not_activate_original_export(tmp_path):
    report = analyze(tmp_path, {"app/__init__.py": "", "app/views.py": '''from flask import Flask
from unavailable import replace
app=Flask(__name__)
def forbidden():
    return 0
@replace
@app.route('/')
def selected():
    return forbidden()
'''})
    assert ["app.views.selected", "app.views.forbidden"] not in report["call_edges"]
    assert report["dependency_boundaries"]
    assert len(report["boundary_contract_applications"]) == 1


def test_second_fixpoint_consumes_remaining_iteration_budget(tmp_path):
    files = {"app/__init__.py": "", "app/views.py": "def callback():\n    return 7\ndef selected():\n    return callback()\n"}
    baseline = analyze(tmp_path, files, entries=())
    limit = baseline["statistics"]["iterations"] + 1
    report = analyze(tmp_path, files, limit=limit)
    assert report["exported_entry_activation"]
    assert report["statistics"]["iterations"] <= limit
    assert report["solver_completed"] is False
    assert report["status"] == "incomplete"


@pytest.mark.parametrize("mutation", ["wrong_hash", "different_module"])
def test_identity_contract_cannot_cross_source_binding(tmp_path, monkeypatch, mutation):
    import pythonstan.analysis.project_frameworks as discovery
    original = discovery.discover_framework_entries
    source_a = "from flask import Flask\napp=Flask(__name__)\n@app.route('/')\ndef selected():\n    return 1\n"
    source_b = '''def forbidden():
    return 0
def chosen():
    return 7
def replacement():
    return chosen()
def replace(original):
    return replacement
@replace
def selected():
    return forbidden()
'''
    def altered(*args, **kwargs):
        report = original(*args, **kwargs)
        if mutation == "wrong_hash":
            for contract in report["identity_decorator_contracts"]:
                contract["source_sha256"] = "0" * 64
        else:
            report["identity_decorator_contracts"] = [{
                "operation": "applied_decorator_preserves_input_callable_identity",
                "module": "app.other", "qualified_name": "app.views.selected",
                "source_sha256": hashlib.sha256(source_a.encode()).hexdigest(), "decorator_index": 0,
            }]
        return report
    monkeypatch.setattr(discovery, "discover_framework_entries", altered)
    report = analyze(tmp_path, {"app/__init__.py": "", "app/other.py": source_a, "app/views.py": source_b})
    assert report["boundary_contract_applications"] == []
    assert ["app.views.selected", "app.views.forbidden"] not in report["call_edges"]


def test_click_command_callback_is_typed_entry_not_identity_decorator(tmp_path):
    report = analyze(tmp_path, {"app/__init__.py": "", "app/views.py": '''import click
def chosen():
    return 7
@click.command()
@click.option('--value', default=1)
def selected(value):
    return chosen()
'''}, frameworks=(("click", "8.1.8"),))
    assert ["app.views.selected", "app.views.chosen"] in report["call_edges"]
    assert report["boundary_contract_applications"] == []
    assert report["framework_callback_entry_contracts"][0]["origin"] == "conditional_click_command_callback"
    assert report["framework_callback_entry_contracts"][0]["module_export_is_function"] is False
    assert report["semantic_complete"] is False


def test_unknown_click_callback_replacement_is_not_seeded_as_original(tmp_path):
    report = analyze(tmp_path, {"app/__init__.py": "", "app/views.py": '''import click
from unavailable import replace
def forbidden():
    return 0
@click.command()
@replace
def selected():
    return forbidden()
'''}, frameworks=(("click", "8.1.8"),))
    assert ["app.views.selected", "app.views.forbidden"] not in report["call_edges"]
    assert report["framework_callback_entry_contracts"] == []
    assert report["boundary_contract_applications"] == []
