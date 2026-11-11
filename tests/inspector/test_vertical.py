"""The mandatory source -> engine -> adapter -> independent reader slice."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

from pythonstan_inspector.contract import QueryRequest
from pythonstan_inspector.core import query


def test_real_source_vertical_and_isolated_reader(tmp_path):
    from pythonstan_inspector.runner import run_source
    from pythonstan_inspector.storage import load_bundle, write_bundle
    source = tmp_path / "subject.py"
    source.write_text("source = 1\ntarget = source\n")
    snapshot, _, _ = run_source(source)
    path = write_bundle(snapshot, tmp_path / "bundle")
    bundle = load_bundle(path)
    summary = query(bundle, QueryRequest("summary"))
    assert summary["boundary"]["coherent"] is True
    entities = bundle.data["snapshot"]["entities"]
    assert any("target" in e.get("label", "") for e in entities)
    rows = bundle.data["snapshot"]["views"]["node_points"]["rows"]
    row = next(row for row in rows if any(e["id"] == row["owner"] and
               "target" in e.get("label", "") for e in entities))
    assert next(e for e in entities if e["id"] == row["object"])["attributes"]["value"] == 1
    answer = query(bundle, QueryRequest("show", {"view": "node_points", "contains": True, **row}))
    assert answer["answer"] == "present_at_snapshot"
    assert answer["concrete_reachability_proved"] is False
    # Copy only offline files; -I -S excludes site, CWD and repository imports.
    isolated = tmp_path / "isolated"
    package = isolated / "pythonstan_inspector"
    package.mkdir(parents=True)
    original = Path(__file__).resolve().parents[2] / "pythonstan_inspector"
    for name in ("__init__.py", "contract.py", "core.py", "identity.py", "storage.py"):
        shutil.copyfile(original / name, package / name)
    script = isolated / "read.py"
    script.write_text("import sys,json\nsys.path.insert(0," + repr(str(isolated)) + ")\n"
        "from pythonstan_inspector.storage import load_bundle\n"
        "from pythonstan_inspector.core import query\n"
        "from pythonstan_inspector.contract import QueryRequest\n"
        "b=load_bundle(" + repr(str(path)) + ")\n"
        "r=query(b,QueryRequest('show'," + repr({"view": "node_points", "contains": True, **row}) + "))\n"
        "assert r['answer']=='present_at_snapshot'\n"
        "assert not any(k=='pythonstan' or k.startswith('pythonstan.') for k in sys.modules)\n"
        "print(json.dumps(r))\n")
    result = subprocess.run([sys.executable, "-I", "-S", str(script)],
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["answer"] == "present_at_snapshot"


def test_engine_off_without_inspector(tmp_path):
    root = Path(__file__).resolve().parents[2]
    script = tmp_path / "off.py"
    script.write_text("import sys,importlib.abc\nsys.path.insert(0," + repr(str(root)) + ")\n"
        "class Block(importlib.abc.MetaPathFinder):\n"
        " def find_spec(self, fullname, path=None, target=None):\n"
        "  if fullname.startswith('pythonstan_inspector'): raise AssertionError(fullname)\n"
        "sys.meta_path.insert(0,Block())\n"
        "from benchmark.inspector_v1.measure import finite_arena\n"
        "solver,state=finite_arena(8)\nsolver.solve_to_fixpoint()\n"
        "assert solver._stats['converged']==1\n"
        "assert not any(k.startswith('pythonstan_inspector') for k in sys.modules)\n")
    result = subprocess.run([sys.executable, str(script)], capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr


def test_stable_bad_engine_can_be_sealed(tmp_path, monkeypatch):
    """A test-only missing late replay is not rejected by capture validation."""
    from pythonstan_inspector import adapter
    from pythonstan_inspector.runner import run_source
    from pythonstan_inspector.storage import load_bundle, write_bundle
    from pythonstan.analysis.pointer.kcfa.pointer_flow_graph import NormalNode, PointerFlowEdge, PointerFlowKind
    handle = {}
    original = adapter.capture
    def retain(solver, request=None, run_metadata=None):
        handle["solver"] = solver
        return original(solver, request, run_metadata)
    monkeypatch.setattr(adapter, "capture", retain)
    source = tmp_path / "subject.py"
    source.write_text("source = 1\ntarget = 2\n")
    run_source(source)
    solver = handle["solver"]
    nodes = {node.var.content.name: node for node in solver.state._env
             if isinstance(node, NormalNode) and hasattr(node.var.content, "name")}
    # Deliberately skip state._add_points_flow_edge's current-source replay.
    # This is an actual registered production graph, not hand-edited JSON.
    solver.state._pointer_flow_graph.add_edge(
        PointerFlowEdge(nodes["source"], nodes["target"], PointerFlowKind.NORMAL))
    snapshot = original(solver)
    path = write_bundle(snapshot, tmp_path / "bad_bundle")
    data = load_bundle(path).data["snapshot"]
    points = {(r["owner"], r["object"], r["lane"]) for r in data["views"]["node_points"]["rows"]}
    violations = [(edge, obj, lane) for edge in data["views"]["pfg_edges"]["rows"]
        if edge["identity_copy"] for owner, obj, lane in points if owner == edge["source"]
        and (edge["target"], obj, lane) not in points]
    assert violations
    before = snapshot.encoded
    solver.state._env.clear()
    solver._stats["pending_work_by_kind"] = {"nested": ["changed"]}
    solver.state._pointer_flow_graph.edges.clear()
    assert snapshot.encoded == before
    assert load_bundle(path).snapshot.encoded == before
