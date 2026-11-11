"""Application integrity and independently frozen requirements, without Flask."""
import ast
import hashlib
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2] / "benchmark/round7_apps/web"


def manifest():
    return json.loads((ROOT / "manifest.json").read_text())["cases"][0]


def test_entire_upstream_application_subtree_is_unchanged():
    metadata = json.loads((ROOT / "provenance.json").read_text())
    assert metadata["version"] == "3.1.2"
    assert metadata["sdist_sha256"] == "bf656c15c80190ed628ad08cdfd3aaa35beb087855e2f494910aa3774cc4fd87"
    assert len(metadata["files"]) == 22
    for record in metadata["files"]:
        path = ROOT / "flask_tutorial" / record["path"]
        assert path.is_file()
        assert len(path.read_bytes()) == record["bytes"]
        assert hashlib.sha256(path.read_bytes()).hexdigest() == record["sha256"]


def test_application_inventory_includes_every_first_party_module():
    case = manifest()
    project = ROOT / case["project_root"]
    discovered = sorted(str(p.relative_to(project)) for p in (project / "flaskr").rglob("*.py"))
    assert discovered == ["flaskr/__init__.py", "flaskr/auth.py", "flaskr/blog.py", "flaskr/db.py"]
    assert case["first_party_module_count"] == len(discovered)
    assert case["whole_first_party_application"] is True
    assert case["whole_dependency_closure"] is False


def test_hand_reviewed_gold_is_frozen_independently_of_analysis():
    case = manifest()
    binding = json.loads((ROOT / "gold_sha256.json").read_text())
    wire = json.dumps({key:case[key] for key in binding["fields"]},sort_keys=True,separators=(",", ":")).encode()
    assert hashlib.sha256(wire).hexdigest() == binding["sha256"]
    required = set(map(tuple, case["required_edges"]))
    negative = set(map(tuple, case["forbidden_edges"]))
    assert len(required) == 16 and len(negative) == 3
    assert not required & negative
    assert len(set(case["required_entry_points"])) == 11
    assert len(set(case["runtime_checks"])) == 17


@pytest.mark.parametrize("path", ["flaskr/__init__.py", "flaskr/auth.py", "flaskr/blog.py", "flaskr/db.py"])
def test_original_python_application_module_is_parseable(path):
    source = (ROOT / "flask_tutorial" / path).read_text()
    assert ast.parse(source).body


def test_real_runtime_dependencies_have_pinned_artifact_hashes():
    lock = (ROOT / "runtime_requirements.txt").read_text().splitlines()
    assert len(lock) == 7 and all("==" in line for line in lock)
    artifacts = json.loads((ROOT / "dependency_artifacts.json").read_text())["artifacts"]
    assert len(artifacts) == 7
    assert all(len(a["hashes"]["sha256"]) == 64 for a in artifacts)
    assert {(a["name"].lower(), a["version"]) for a in artifacts} == {
        tuple(line.lower().split("==")) for line in lock}


def test_oracle_preserves_runtime_static_separation():
    tree = ast.parse((ROOT / "oracle.py").read_text())
    calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)]
    assert not any(isinstance(c.func, ast.Attribute) and c.func.attr in {"settrace", "setprofile"} for c in calls)
    assert "required_edges" not in (ROOT / "oracle.py").read_text()
