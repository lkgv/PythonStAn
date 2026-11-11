import io
import json
from pathlib import Path
import subprocess
import sys

import pytest

from pythonstan import Source, File, Project, ValueQuery, OutputSpec, analyze
from pythonstan.config import resolve_config, ConfigurationError, catalog, json_schema
from pythonstan.inputs import snapshot_target, InputError


def test_source_never_reads_cwd_configuration(tmp_path, monkeypatch):
    (tmp_path / "pythonstan.yaml").write_text("unknown: bad")
    monkeypatch.chdir(tmp_path)
    assert resolve_config(Source("x=1"))["engine"] == "native"
    with pytest.raises(ConfigurationError):
        resolve_config(File(tmp_path / "file.py"))
    assert resolve_config(File(tmp_path / "file.py"), {})["engine"] == "native"


def test_config_priority_duplicate_unknown_and_conflicts(tmp_path):
    path = tmp_path / "pythonstan.json"
    path.write_text('{"engine":"ai_primary","resources":{"max_work":30}}')
    config = resolve_config(File(tmp_path / "x.py"), overrides={"resources.max_work": 50})
    assert config["resources"]["max_work"] == 50
    assert config.provenance["resources.max_work"] == "override"
    (tmp_path / "pythonstan.yaml").write_text("engine: native")
    with pytest.raises(ConfigurationError, match="Conflicting"):
        resolve_config(File(tmp_path / "x.py"))
    for text in ('{"engine":"native","engine":"hybrid"}', '{"abstract":{"mispelling":1}}'):
        path.write_text(text)
        with pytest.raises(ConfigurationError): resolve_config(config=path)
    yaml_path = tmp_path / "dupe.yaml"
    yaml_path.write_text("engine: native\nengine: hybrid")
    with pytest.raises(ConfigurationError, match="Duplicate"): resolve_config(config=yaml_path)
    for config in ({"engine": "bad"}, {"mode": "native", "engine": "native"},
                   {"resources": {"max_work": True}}, {"resources": {"max_seconds": float("inf")}},
                   {"andersen": {"heap_context_depth": 4}}):
        with pytest.raises(ConfigurationError): resolve_config(config=config)


def test_paths_are_config_relative_and_source_bytes_are_retained(tmp_path):
    config_path = tmp_path / "pythonstan.json"
    config_path.write_text('{"inputs":{"source_roots":["src"]}}')
    config = resolve_config(config=config_path)
    assert config["inputs"]["source_roots"] == [str(tmp_path / "src")]
    path = tmp_path / "encoded.py"
    path.write_bytes(b"# coding: latin-1\nvalue = 'caf\xe9'\n")
    source = snapshot_target(File(path), resolve_config(config={})).modules[0]
    assert source.encoding == "iso-8859-1" and "café" in source.source
    assert source.original_bytes_digest != source.digest


def test_project_module_namespace_and_independent_initialization_roots(tmp_path):
    (tmp_path / "a.py").write_text("x=1")
    (tmp_path / "b.py").write_text("x=2")
    snapshot = snapshot_target(Project(tmp_path), resolve_config(config={}))
    assert set(snapshot.module_map) == {"a", "b"}
    assert len(snapshot.scenarios) == 2
    assert len({s.scenario_id for s in snapshot.scenarios}) == 2
    assert snapshot.entry_contract["entry_coverage"] == "initialization_only"
    with pytest.raises(InputError): snapshot_target(Project(tmp_path, entries=("missing:main",)), resolve_config(config={}))


@pytest.mark.parametrize("engine", ["native", "ai_primary"])
def test_actual_three_inputs_results_and_sessions(tmp_path, engine, capsys):
    path = tmp_path / "example.py"; path.write_text("result=17")
    query = ValueQuery("example", "result")
    targets = [Source("result=17", module="example"), File(path), Project(tmp_path)]
    for target in targets:
        result = analyze(target, config={"engine": engine}, queries=(query, "calls"))
        answer = result.query(query)
        assert answer.status == "answered"
        assert 17 in answer.value["constants"] and not answer.value["top"]
        assert answer.approximation == ("flow_insensitive_may" if engine == "native" else "flow_sensitive_may")
        old = result.serialize()
        analyze(Source("result=42", module="example"), config={"engine": engine})
        assert result.serialize() == old
        data = result.to_dict(OutputSpec(views=("calls",), max_items=0))
        assert all(key in data for key in ("solver_status", "coverage_status", "proof_status", "obligations"))
        assert result.query("stats").status == "not_requested"
        stream = io.StringIO(); result.write(stream)
        assert json.loads(stream.getvalue())["actual_engine"] == engine
    assert capsys.readouterr().out == ""


def test_query_and_output_precedence():
    config = {"engine": "ai_primary", "queries": ["calls"], "output": {"views": ["calls"]}}
    result = analyze(Source("x=1"), config=config, queries=(), output=OutputSpec(views=("summary",)))
    assert result.query("calls").status == "not_requested"
    assert result.query("summary").status == "answered"
    assert result.proof_status == "not_requested"
    assert result.effective_config["queries"] == []
    assert result.effective_config["output"]["views"] == ["summary"]
    assert result.to_dict()["config_provenance"]["queries"] == "query_argument"


def test_cli_json_channels_and_quality_codes(tmp_path):
    command = [sys.executable, "-m", "pythonstan"]
    proc = subprocess.run(command + ["analyze", "--code", "x=1", "--format", "json",
                                     "--set", "engine=ai_primary", "--query", "values"], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    data = json.loads(proc.stdout)
    assert data["actual_engine"] == "ai_primary"
    assert data["answers"]["values"]["value"]["snippet"]["x"]["constants"] == [1]
    bad = subprocess.run(command + ["analyze", "--code", "x=1", "--set", "unknown=1"], capture_output=True, text=True)
    assert bad.returncode == 2 and not bad.stdout
    gate = subprocess.run(command + ["analyze", "--code", "x=1", "--require-verified"], capture_output=True, text=True)
    assert gate.returncode == 4
    assert json.loads(gate.stdout)["proof_status"] == "not_requested"
    stdin = subprocess.run(command + ["analyze", "--stdin", "--filename", "memory.py", "--set", "engine=ai_primary", "--query", "values"],
                           input="x=3", capture_output=True, text=True)
    assert stdin.returncode == 0 and json.loads(stdin.stdout)["answers"]["values"]["value"]["memory"]["x"]["constants"] == [3]


def test_catalog_has_canonical_strict_schema():
    options = {item["path"]: item for item in catalog()}
    assert options["andersen.context_policy"]["choices"][-1] == "2c1o"
    assert len(options["andersen.context_policy"]["choices"]) == 19
    assert options["abstract.max_const_ints"]["default"] == 4
    assert options["abstract.int_domain_max_consts"]["default"] == 10
    assert json_schema()["additionalProperties"] is False
