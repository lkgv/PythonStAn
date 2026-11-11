"""Audit structure must retain overloads and actual lexical ownership."""
import ast
import hashlib
import json
from pathlib import Path

import pytest

from tests.round10_applications.source_entry_audit import definitions, nodes_with_owner
from tests.round10_applications import source_entry_audit
from tests.round10_applications.test_framework_closure_reductions import REDUCTIONS


def test_reduction_provenance_binds_the_exact_reduced_sources():
    path = Path(__file__).resolve().parents[2] / "docs/certified_analysis/evidence/round10_applications/reduction_provenance.json"
    cases = json.loads(path.read_text())["cases"]
    assert {case["project_id"] for case in cases} == REDUCTIONS.keys()
    for case in cases:
        assert case["reduction_source_sha256"] == hashlib.sha256(REDUCTIONS[case["project_id"]].encode()).hexdigest()


def test_overloads_are_multiple_declarations_and_decorators_are_only_syntax():
    source = '''class Agent:
    @overload
    def run(self, value: int): ...
    @overload
    def run(self, value: str): ...
    def run(self, value): return value
'''
    nodes = definitions(source, "agent")["agent.Agent.run"]
    assert len(nodes) == 3
    assert [n["decorator_syntax"] for n in nodes] == [["overload"], ["overload"], []]
    assert all(n["definition_kind"] == "instance_method_syntax" for n in nodes)


def test_control_flow_nested_async_definition_has_lexical_function_owner():
    source = '''def add_routes(app, handler):
    if app:
        @app.post('/invoke')
        async def invoke(request):
            return await handler.invoke(request)
'''
    definition = definitions(source, "server")["server.add_routes.invoke"][0]
    assert definition["async"] and definition["definition_kind"] == "nested_function"
    calls = [(owner, ast.unparse(node)) for owner, node in nodes_with_owner(ast.parse(source), "server")
             if isinstance(node, ast.Call)]
    assert ("server.add_routes.invoke", "handler.invoke(request)") in calls
    assert ("server.add_routes", "handler.invoke(request)") not in calls


def test_changed_raw_bytes_are_rejected_before_any_source_coverage_claim(tmp_path, monkeypatch):
    monkeypatch.setattr(source_entry_audit, "projects", lambda: [
        {"id": "demo", "project_root": ".", "import_roots": ["."], "source_files": []}])
    (tmp_path / "summary.json").write_text(json.dumps({"rows": [
        {"project_id": "demo", "stage": "solver", "repeat": 1,
         "path": "demo_solver_r1.json", "sha256": "0" * 64}]}))
    (tmp_path / "demo_solver_r1.json").write_text(json.dumps(
        {"project_id": "demo", "stage": "solver", "repeat": 1, "report": {}}))
    output = tmp_path / "audit.json"
    with pytest.raises(ValueError, match="summary binding"):
        source_entry_audit.audit(tmp_path, tmp_path, output)
    assert not output.exists()


def test_duplicate_trial_rows_are_rejected_before_source_reads(tmp_path, monkeypatch):
    monkeypatch.setattr(source_entry_audit, "projects", lambda: [{"id": "demo"}])
    row = {"project_id": "demo", "stage": "solver", "repeat": 1}
    (tmp_path / "summary.json").write_text(json.dumps({"rows": [row, row]}))
    output = tmp_path / "audit.json"
    with pytest.raises(ValueError, match="exactly one"):
        source_entry_audit.audit(tmp_path, tmp_path, output)
    assert not output.exists()
