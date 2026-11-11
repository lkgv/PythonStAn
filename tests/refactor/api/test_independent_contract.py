"""Independent API/config/input/session/CLI contract witnesses."""

import json
from pathlib import Path
import subprocess
import sys

import pytest

from pythonstan import File, Project, Source, ValueQuery, OutputSpec, analyze
from pythonstan.config import ConfigurationError, resolve_config
from pythonstan.inputs import InputError, snapshot_target


def _answer(result, module="example", symbol="result"):
    answer = result.query(ValueQuery(module, symbol))
    assert answer.status == "answered", answer.to_dict()
    assert answer.value["top"] is False
    return answer.value


def test_canonical_duplicate_dotted_and_nested_paths_rejected():
    # Different spellings for one canonical leaf are still a conflict, not
    # dictionary-order-dependent precedence within a single configuration tier.
    for config in (
        {"abstract.max_const_strings": 10, "abstract": {"max_const_strings": 20}},
        {"abstract": {"max_const_strings": 20}, "abstract.max_const_strings": 10},
    ):
        with pytest.raises(ConfigurationError, match="[Cc]onflict|[Dd]uplicate"):
            resolve_config(config=config)
    with pytest.raises(ConfigurationError, match="[Cc]onflict|[Dd]uplicate"):
        resolve_config(config={}, overrides={"resources.max_work": 20,
                                             "resources": {"max_work": 30}})


def test_invalid_configuration_fails_before_target_parsing():
    with pytest.raises(ConfigurationError):
        analyze(Source("not valid Python !!!"), config={"abstract": {"typo": True}})


@pytest.mark.parametrize("config", [
    {"abstract": {"solver_options": {"widen_delay": "soon"}}},
    {"abstract": {"solver_options": {"max_traces_per_block": True}}},
    {"abstract": {"solver_options": {"enable_trace_partitioning": 3}}},
    {"abstract": {"solver_options": {"merge_points": "node"}}},
    {"andersen": {"ai_budget": {"invented_limit": 1}}},
])
def test_nested_registered_options_are_strict_before_execution(config):
    # Nested option objects are not an escape hatch from the public catalog's
    # type and unknown-key validation, even when a parent feature is disabled.
    with pytest.raises(ConfigurationError):
        resolve_config(config=config)


def test_yaml_cannot_construct_python_objects(tmp_path, capsys):
    path = tmp_path / "unsafe.yaml"
    path.write_text("!!python/object/apply:builtins.print ['must-not-execute']\n")
    with pytest.raises(ConfigurationError):
        resolve_config(config=path)
    assert capsys.readouterr().out == ""


def test_explicit_configuration_disables_file_discovery(tmp_path):
    (tmp_path / "pythonstan.yaml").write_text("invalid: true\n")
    target = File(tmp_path / "a.py")
    assert resolve_config(target, config={"engine": "ai_primary"})["engine"] == "ai_primary"
    assert resolve_config(target, config=resolve_config(config={"engine": "ai_primary"}))["engine"] == "ai_primary"
    with pytest.raises(ConfigurationError):
        resolve_config(target)


def test_query_reads_are_detached_and_do_not_refine(monkeypatch):
    query = ValueQuery("example", "result")
    result = analyze(Source("result = 'after'", module="example"),
                     config={"engine": "ai_primary"}, queries=(query,))
    original = result.serialize()
    first = result.query(query).value
    first["constants"].append("corrupted")
    config = result.effective_config
    config["engine"] = "native"
    from pythonstan.analysis.engines.abstract.driver import InterproceduralDriver
    def forbidden(*args, **kwargs):
        raise AssertionError("Read-only result operation launched an analysis")
    monkeypatch.setattr(InterproceduralDriver, "run", forbidden)
    assert _answer(result)["constants"] == ["after"]
    assert result.query("calls").status == "not_requested"
    assert result.serialize() == original


def test_result_does_not_require_source_file_after_session(tmp_path):
    path = tmp_path / "example.py"
    path.write_bytes(b"# coding: latin-1\nresult = 'caf\xe9'\n")
    query = ValueQuery("example", "result")
    result = analyze(File(path), config={"engine": "ai_primary"}, queries=(query,))
    path.unlink()
    assert _answer(result)["constants"] == ["café"]
    assert json.loads(result.serialize())["source_digest"]


def test_empty_queries_and_output_projection_keep_guarantees():
    result = analyze(Source("result = 1", module="example"),
                     config={"engine": "ai_primary", "queries": ["values", "effects"],
                             "output": {"views": ["effects"]}},
                     overrides={"queries": ["calls"]}, queries=(),
                     output=OutputSpec(views=("values",), max_items=0))
    assert result.query("values").status == "not_requested"
    doc = result.to_dict()
    assert doc["answers"]["values"]["status"] == "not_requested"
    assert all(key in doc for key in ("solver_status", "coverage_status", "proof_status", "obligations"))
    assert result.effective_config["queries"] == []
    assert result.effective_config["output"]["views"] == ["values"]


def test_project_cycles_keep_both_initialization_scenarios(tmp_path):
    (tmp_path / "a.py").write_text("stage = 'a-start'\nimport b\nstage = 'a-done'\n")
    (tmp_path / "b.py").write_text("import a\nseen = a.stage\n")
    query = ValueQuery("b", "seen")
    result = analyze(Project(tmp_path), config={"engine": "ai_primary"}, queries=(query,))
    value = _answer(result, "b", "seen")
    assert set(value["constants"]) == {"a-start", "a-done"}, value
    entry = result.to_dict()["entry_contract"]
    assert entry["entry_coverage"] == "initialization_only"
    assert len(entry["scenarios"]) == 2
    assert len({scenario["id"] for scenario in entry["scenarios"]}) == 2


def test_duplicate_module_identities_fail_before_analysis(tmp_path):
    for name in ("one", "two"):
        root = tmp_path / name
        root.mkdir()
        (root / "same.py").write_text("x = 1\n")
    with pytest.raises(InputError, match="Duplicate module identity"):
        snapshot_target(Project(tmp_path, source_roots=("one", "two")), resolve_config(config={}))


def test_no_entry_and_syntax_errors_are_explicit(tmp_path):
    with pytest.raises(InputError, match="no_entry"):
        analyze(Project(tmp_path), config={"engine": "ai_primary"})
    with pytest.raises(InputError):
        analyze(Source("def incomplete("), config={"engine": "ai_primary"})


def test_function_definition_does_not_activate_its_body():
    source = "value='before'\ndef f():\n    global value\n    value='after'\nresult=value\n"
    query = ValueQuery("example", "result")
    result = analyze(Source(source, module="example"), config={"engine": "ai_primary"}, queries=(query,))
    assert _answer(result)["constants"] == ["before"]


@pytest.mark.parametrize("signature", ["x", "*, x"])
def test_explicit_entry_binds_required_external_parameters(tmp_path, signature):
    (tmp_path / "app.py").write_text("def main(" + signature + "):\n    return 'after'\n")
    query = ValueQuery("app", "main", "function_exit")
    result = analyze(Project(tmp_path, entries=("app:main",)),
                     config={"engine": "ai_primary"}, queries=(query,))
    answer = result.query(query)
    assert answer.status == "answered", answer.to_dict()
    assert answer.value["constants"] == ["after"] and not answer.value["top"], answer.to_dict()
    entry = result.to_dict()["entry_contract"]
    assert entry["entry_coverage"] == "declared_entries"
    assert entry["scenarios"][0]["coverage"] == "declared_abstract_inputs"


def test_cli_conflicting_targets_and_duplicate_sets_are_input_errors():
    command = [sys.executable, "-m", "pythonstan", "analyze"]
    for args in (("--code", "x=1", "--file", "never-open.py"),
                 ("--code", "x=1", "--set", "engine=native", "--set", "engine=ai_primary")):
        completed = subprocess.run(command + list(args), text=True, capture_output=True, timeout=12)
        assert completed.returncode == 2
        assert completed.stdout == ""
        assert completed.stderr


@pytest.mark.parametrize("error", [TypeError("injected solver failure"), ValueError("injected solver failure"), RuntimeError("injected solver failure")])
def test_cli_internal_failures_do_not_become_input_errors(monkeypatch, capsys, error):
    from pythonstan import cli
    def broken_analyzer(*args, **kwargs):
        raise error
    monkeypatch.setattr(cli, "analyze", broken_analyzer)
    code = cli.main(["analyze", "--code", "x=1", "--set", "engine=ai_primary"])
    captured = capsys.readouterr()
    assert code == 3, "A valid input followed by an internal solver failure must exit3"
    assert captured.out == ""
    assert "internal failure" in captured.err


@pytest.mark.parametrize("options", [
    ["--query", "unregistered_query"],
    ["--query", "value:example:result:invalid_point"],
    ["--view", "unregistered_view"],
])
def test_cli_invalid_registered_query_and_view_are_input_errors(options):
    completed = subprocess.run(
        [sys.executable, "-m", "pythonstan", "analyze", "--code", "result=1",
         "--set", "engine=ai_primary", *options], text=True, capture_output=True, timeout=12)
    assert completed.returncode == 2, completed.stderr
    assert completed.stdout == "" and completed.stderr


def test_cli_api_value_and_effective_configuration_agree(tmp_path):
    query = ValueQuery("example", "result")
    config = tmp_path / "options.json"
    config.write_text(json.dumps({"engine": "ai_primary", "resources": {"max_work": 20000}}))
    source = "result = 'after'\n"
    api = analyze(Source(source, filename="example.py", module="example"), config=config,
                  overrides={"abstract.max_const_strings": 20}, queries=(query, "values"))
    completed = subprocess.run(
        [sys.executable, "-m", "pythonstan", "analyze", "--stdin", "--filename", "example.py",
         "--config", str(config), "--set", "abstract.max_const_strings=20", "--query", "values"],
        input=source, text=True, capture_output=True, timeout=12,
    )
    assert completed.returncode == 0, completed.stderr
    cli = json.loads(completed.stdout)
    assert cli["actual_engine"] == api.actual_engine == "ai_primary"
    assert cli["solver_status"] == api.solver_status
    assert cli["coverage_status"] == api.coverage_status
    assert cli["answers"]["values"]["value"]["example"]["result"] == _answer(api)
    # CLI adds its presentation/query selection but all algorithm leaves match.
    for section in ("abstract", "andersen", "resources", "semantic_profile"):
        assert cli["effective_config"][section] == api.effective_config[section]
