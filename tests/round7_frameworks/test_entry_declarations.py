import hashlib
from pathlib import Path

import pytest

from pythonstan.analysis.project_frameworks import discover_framework_entries


def discover(source, **kw):
    return discover_framework_entries({"app": source}, **kw)


def names(result):
    return {entry["qualified_name"] for entry in result["entries"]}


def test_flask_constructor_alias_source_and_entry_binding():
    source = '''from flask import Blueprint as BP
bp = BP("blog", __name__)
@bp.route("/")
def index():
    return "ok"
'''
    result = discover(source, external_frameworks={"flask": "3.1.2"})
    assert names(result) == {"app.index"}
    entry = result["entries"][0]
    assert entry["binding"] == "flask.Blueprint.route"
    assert entry["source_sha256"] == hashlib.sha256(source.encode()).hexdigest()
    assert entry["framework_version"] == "3.1.2"
    assert not entry["solver_derived"] and result["solver_edges_emitted"] == 0
    assert not result["complete_external_argument_coverage"]


def test_multiple_module_reexports_fixedpoint():
    sources = {"pkg": "from .factory import bp\n",
               "pkg.factory": 'from flask import Blueprint\nbp=Blueprint("x",__name__)\n',
               "pkg.views": 'from . import bp\n@bp.get("/")\ndef index():pass\n'}
    result = discover_framework_entries(sources)
    assert result["binding_exports_stable"]
    assert names(result) == {"pkg.views.index"}


@pytest.mark.parametrize("prefix", [
    'class Fake:\n    def route(self,*a): return lambda f:f\nbp=Fake()\n',
    'from flask import Blueprint\nBlueprint = object\nbp=Blueprint()\n',
    'from flask import Blueprint\nbp=Blueprint("x",__name__)\nbp=object()\n',
])
def test_lookalike_and_rebound_receivers_do_not_gain_framework_identity(prefix):
    result = discover(prefix + '@bp.route("/")\ndef index():pass\n')
    assert not result["entries"]
    assert any("receiver_unresolved" in r["reason"] for r in result["residuals"])


@pytest.mark.parametrize("sources", [
    {"flask": "class Blueprint:pass\n", "app": 'from flask import Blueprint\nbp=Blueprint()\n@bp.route("/")\ndef index():pass\n'},
    {"flask.custom": "", "app": 'import flask\nbp=flask.Blueprint()\n@bp.route("/")\ndef index():pass\n'},
])
def test_firstparty_framework_name_shadows_external_identity(sources):
    result = discover_framework_entries(sources)
    assert not result["entries"]


def test_function_parameter_and_whole_scope_local_shadowing():
    source = '''from flask import Blueprint
bp=Blueprint("x",__name__)
def factory(bp):
    @bp.route("/")
    def parameter_shadow():pass
def other():
    @bp.route("/")
    def local_shadow():pass
    bp = object()
'''
    assert not discover(source)["entries"]


def test_factory_local_receiver_is_conditional_entry():
    source = '''import flask as web
def factory():
    app = web.Flask(__name__)
    @app.get("/")
    def index():pass
    return app
'''
    result = discover(source)
    assert names(result) == {"app.factory.index"}
    assert result["entries"][0]["conditional_declaration"] is True


def test_class_namespace_is_not_method_lexical_scope():
    source = '''class Views:
    from flask import Blueprint
    bp=Blueprint("x",__name__)
    def factory(self):
        @bp.route("/")
        def unbound():pass
'''
    assert not discover(source)["entries"]


def test_unresolved_decorator_requires_explicit_callback_identity_assumption():
    result = discover('''from flask import Flask
app=Flask(__name__)
@app.route("/")
@possibly_replace
def index():pass
''')
    assert names(result) == {"app.index"}
    assert not result["entries"][0]["callback_identity_verified"]
    assert result["entries"][0]["unknown_decorators"] == ["possibly_replace"]
    assert any(r["reason"] == "unresolved_decorator_chain_may_replace_handler" for r in result["residuals"])


def test_fastapi_router_and_lifecycle_entries():
    result = discover('''from fastapi import APIRouter as Router
from flask import Flask
router=Router()
app=Flask(__name__)
@router.post("/items")
async def create():pass
@app.teardown_appcontext
def cleanup(error):pass
''')
    assert names(result) == {"app.create", "app.cleanup"}
    assert {x["kind"] for x in result["entries"]} == {"web_handler", "lifecycle_callback"}


def test_click_group_subcommands_and_nonframework_route():
    result = discover('''import click as cli
@cli.group()
def main():pass
@main.command()
@cli.option("--name")
def hello(name):pass
''')
    assert names(result) == {"app.main", "app.hello"}
    assert all(not e["unknown_decorators"] for e in result["entries"])


def test_pluggy_marker_imported_across_project_modules():
    result = discover_framework_entries({
        "example": "from pluggy import HookimplMarker\nhookimpl=HookimplMarker('example')\n",
        "example.plugin": "from . import hookimpl\nclass Plugin:\n    @hookimpl\n    def work(self):pass\n",
    })
    assert names(result) == {"example.plugin.Plugin.work"}


def test_imperative_registration_requires_known_receiver_and_source_callback():
    result = discover('''from flask import Flask
app=Flask(__name__)
def index():pass
app.add_url_rule("/",view_func=index)
''')
    assert names(result) == {"app.index"}
    assert result["entries"][0]["origin"] == "conditional_imperative_framework_entry"
    assert result["entries"][0]["registration_line"] == 4


def test_imperative_registration_unknown_receiver_stays_residual():
    result = discover('''def init_app(app):
    def callback():pass
    app.before_request(callback)
''')
    assert not result["entries"]
    assert any(r["reason"] == "imperative_registration_requires_framework_transfer" for r in result["residuals"])


def test_imperative_registration_does_not_escape_parameter_scope_in_branch():
    result = discover('''from flask import Flask
app=Flask(__name__)
def callback():pass
if unknown:
    def local(app):
        app.before_request(callback)
''')
    assert not result["entries"]


def test_disabled_external_framework_identity_cannot_create_entry():
    result = discover('from flask import Flask\napp=Flask(__name__)\n@app.get("/")\ndef index():pass\n', external_frameworks={})
    assert not result["entries"]


def test_wildcard_import_can_replace_previously_known_constructor():
    result = discover('''from flask import Flask
from unknown import *
app=Flask(__name__)
@app.route("/")
def index():pass
''')
    assert not result["entries"]
    assert any(r["reason"] == "wildcard_import_binding_unresolved" for r in result["residuals"])


def test_identity_contracts_bind_exact_application_index_and_source():
    source = '''from flask import Flask
from functools import wraps
app=Flask(__name__)
@app.route("/")
@unknown_wrapper
def index():pass
def decorate(view):
    @wraps(view)
    def wrapper():return view()
    return wrapper
'''
    result = discover(source)
    contracts = result["identity_decorator_contracts"]
    assert [(c["qualified_name"], c["decorator_index"], c["binding"]) for c in contracts] == [
        ("app.index", 0, "flask.Flask.route"),
        ("app.decorate.wrapper", 0, "functools.wraps")]
    assert all(c["source_sha256"] == hashlib.sha256(source.encode()).hexdigest() for c in contracts)
    assert all(c["operation"] == "applied_decorator_preserves_input_callable_identity" for c in contracts)
    assert all("input" in c["allowed_use"] for c in contracts)


def test_click_command_is_not_an_identity_decorator():
    result = discover("import click\n@click.command()\ndef main():pass\n")
    assert names(result) == {"app.main"}
    assert result["identity_decorator_contracts"] == []


def test_firstparty_functools_shadow_cannot_create_identity_contract():
    result = discover_framework_entries({"functools": "def wraps(f):return f\n",
        "app": "from functools import wraps\n@wraps(other)\ndef wrapper():pass\n"})
    assert result["identity_decorator_contracts"] == []


def test_external_module_mutation_invalidates_later_direct_import():
    result = discover('''import flask
flask.Flask = unknown
from flask import Flask
app=Flask(__name__)
@app.route("/")
def index():pass
''')
    assert not result["entries"]


def test_crossmodule_receiver_alias_mutation_prevents_trusting_routes():
    result = discover_framework_entries({
        "pkg": 'from flask import Flask\napp=Flask(__name__)\n',
        "pkg.a": 'from . import app\n@app.route("/")\ndef index():pass\n',
        "pkg.z": 'from . import app\ndef mutate():\n    app.route=unknown\n',
    })
    assert not result["entries"]


def test_unknown_branch_bindings_are_not_mistaken_for_exact_constructor():
    result = discover('''from flask import Flask
if unknown:
    app=Flask(__name__)
else:
    app=object()
@app.route("/")
def index():pass
''')
    assert not result["entries"]


def test_syntax_errors_keep_module_denominator_and_no_coverage_claim():
    result = discover_framework_entries({"good": "x=1\n", "broken": "def missing("})
    assert result["source_modules"] == 2 and result["parsed_modules"] == 1
    assert result["residuals"][0]["module"] == "broken"
    assert not result["complete_registration_coverage"]


def test_source_path_is_read_without_executing_target(tmp_path):
    source = tmp_path / "app.py"
    source.write_text('raise AssertionError("never execute")\nfrom flask import Flask\napp=Flask(__name__)\n@app.get("/")\ndef index():pass\n')
    assert names(discover_framework_entries({"app": source})) == {"app.index"}


def test_leaf_package_init_retains_its_relative_import_package(tmp_path):
    package = tmp_path / "__init__.py"
    package.write_text('from ..registry import bp\n@bp.route("/")\ndef index():pass\n')
    result = discover_framework_entries({"pkg.registry": 'from flask import Blueprint\nbp=Blueprint("x",__name__)\n',
                                        "pkg.leaf": package})
    assert names(result) == {"pkg.leaf.index"}


@pytest.mark.parametrize("kwargs", [{"external_frameworks": {"unknown": "1"}}, {"external_frameworks": {"flask": 3}}])
def test_configuration_validation(kwargs):
    with pytest.raises(ValueError):
        discover("", **kwargs)
