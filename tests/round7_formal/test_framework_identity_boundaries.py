"""Independent checks of lexical identity evidence, never solver call edges."""
import pytest

from pythonstan.analysis.project_frameworks import discover_framework_entries


@pytest.mark.parametrize("source", [
    '''from flask import Flask
app=Flask(__name__)
def fake(path):
    return lambda function: function
app.route=fake
@app.route("/")
def view():
    return 1
''',
    '''import flask
def fake(path):
    return lambda function: function
flask.Flask=fake
app=flask.Flask(__name__)
@app.route("/")
def view():
    return 1
''',
    '''from flask import Flask
app=Flask(__name__)
del app.route
@app.route("/")
def view():
    return 1
''',
    '''from flask import Flask
app=Flask(__name__)
alias=app
alias.route=other
@app.route("/")
def view():
    return 1
''',
])
def test_known_attribute_mutation_cannot_retain_verified_framework_identity(source):
    result = discover_framework_entries({"application": source}, external_frameworks={"flask": "3.1.2"})
    assert not result["entries"]
    assert result["residuals"]
    assert result["solver_edges_emitted"] == 0


def test_project_named_flask_is_not_assumed_to_be_external_framework():
    result = discover_framework_entries({
        "flask": "class Flask:\n    pass\n",
        "application": "from flask import Flask\napp=Flask()\n@app.route('/')\ndef view():\n    return 1\n",
    }, external_frameworks={"flask": "3.1.2"})
    assert result["entries"] == []
    assert result["residuals"]


def test_recognized_declaration_remains_an_external_entry_assumption():
    result = discover_framework_entries({"application": "from flask import Flask\napp=Flask(__name__)\n@app.route('/')\ndef view():\n    return 1\n"}, external_frameworks={"flask": "3.1.2"})
    assert len(result["entries"]) == 1
    entry = result["entries"][0]
    assert entry["solver_derived"] is False
    assert entry["origin"] == "conditional_framework_entry"
    assert entry["parameter_coverage"] == "unmodeled_unknown_external_arguments"
    assert result["solver_edges_emitted"] == 0
    assert result["complete_registration_coverage"] is False
    assert result["full_python_soundness_proved"] is False
