"""Application provenance and independent runtime obligations, not static gold generation."""
import ast
import configparser
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile

import pytest

ROOT = Path(__file__).resolve().parents[2]
BENCH = ROOT / "benchmark/round7_apps/nonweb"


def manifest():
    return json.loads((BENCH / "manifest.json").read_text())


def run_oracle(path=BENCH):
    proc = subprocess.run([sys.executable, str(path / "oracle.py")],
                          text=True, capture_output=True, timeout=15)
    lines = [line.split(":", 1)[1] for line in proc.stdout.splitlines()
             if line.startswith("APP_RUNTIME_JSON:")]
    assert len(lines) == 1, proc.stdout + proc.stderr
    return proc.returncode, json.loads(lines[0])


def test_inventory_is_exact_and_explicitly_classified():
    data = manifest()
    assert data["unique_application_count"] == 1
    assert len(data["cases"]) == 2
    assert len({c["project_identity"] for c in data["cases"]}) == 1
    case = data["cases"][0]
    assert case["modules"] == {"first_party_runtime": 5, "dependency_runtime": 6, "packaging_excluded": 2}
    project = BENCH / case["project_root"]
    for row in case["source_inventory"]:
        assert hashlib.sha256((project / row["path"]).read_bytes()).hexdigest() == row["sha256"]
    actual = {str(p.relative_to(project)) for p in project.rglob("*.py")
              if "__pycache__" not in p.parts}
    declared = {row["path"] for row in case["source_inventory"] if row["path"].endswith(".py")}
    assert actual == declared
    assert case["source_inventory"] == data["cases"][1]["source_inventory"]


def test_application_is_complete_upstream_tree_not_extracted_functions():
    with tarfile.open(BENCH / "archives/pluggy-1.6.0.tar.gz") as archive:
        prefix = "pluggy-1.6.0/docs/examples/"
        selected = [m for m in archive.getmembers() if m.isfile()
                    and m.name.startswith((prefix + "eggsample/", prefix + "eggsample-spam/"))]
        assert len(selected) == 7
        for member in selected:
            relative = member.name.removeprefix(prefix)
            assert (BENCH / "eggsample/upstream" / relative).read_bytes() == archive.extractfile(member).read()
        assert (BENCH / "eggsample/LICENSE_pluggy_1.6.0.txt").read_bytes() == archive.extractfile("pluggy-1.6.0/LICENSE").read()


def test_complete_compatible_framework_source_is_preserved():
    with tarfile.open(BENCH / "archives/pluggy-0.13.1.tar.gz") as archive:
        source = [m for m in archive.getmembers() if m.isfile()
                  and m.name.startswith("pluggy-0.13.1/src/pluggy/") and m.name.endswith(".py")]
        assert len(source) == 6
        for member in source:
            relative = member.name.removeprefix("pluggy-0.13.1/")
            target = BENCH / "eggsample/dependencies/pluggy-0.13.1" / relative
            assert target.read_bytes() == archive.extractfile(member).read()


def test_archive_digests_bind_acquisition_records():
    data = json.loads((BENCH / "acquisition.json").read_text())
    for row in data["records"]:
        if "archive_sha256" in row:
            data = (BENCH / "archives" / ("pluggy-" + row["version"] + ".tar.gz")).read_bytes()
            assert hashlib.sha256(data).hexdigest() == row["archive_sha256"]
            assert row["metadata_url"] == "https://pypi.org/pypi/pluggy/" + row["version"] + "/json"


@pytest.mark.parametrize("folder,dist", [("eggsample", "eggsample-0.0.0.dist-info"),
                                         ("eggsample-spam", "eggsample_spam-0.0.0.dist-info")])
def test_installer_metadata_uses_original_setup_entrypoints(folder, dist):
    tree = ast.parse((BENCH / "eggsample/upstream" / folder / "setup.py").read_text())
    setup = next(n.value for n in tree.body if isinstance(n, ast.Expr) and isinstance(n.value, ast.Call))
    declarations = ast.literal_eval(next(k.value for k in setup.keywords if k.arg == "entry_points"))
    parser = configparser.ConfigParser()
    parser.read(BENCH / "eggsample/runtime_metadata" / dist / "entry_points.txt")
    for group, lines in declarations.items():
        for entry in lines:
            name, value = map(str.strip, entry.split("=", 1))
            assert parser[group][name] == value


def test_full_application_runtime_reaches_real_metadata_plugin_and_effects():
    code, result = run_oracle()
    assert code == 0 and result["status"] == "completed", result
    assert len(result["checks"]) == 7 and all(c["passed"] for c in result["checks"])
    assert result["versions"]["pluggy_source"] == "0.13.1"
    assert result["gold_generated_from_trace"] is False
    assert result["runtime_observations_are_static_evidence"] is False
    assert result["source_modified"] is False
    counts = result["results"]["observed_function_counts"]
    assert counts["eggsample.host.main"] == 1
    assert counts["eggsample_spam.eggsample_prep_condiments"] == 1
    assert not any(name.startswith("eggsample.hookspecs.") for name in counts)


@pytest.mark.parametrize("before,after,failed_check", [
    ('condiments["spam sauce"] = 42', 'condiments["spam sauce"] = 41', 'shared_condiment_mutations'),
    ('"lovely spam", "wonderous spam"', '"wrong spam", "wonderous spam"', 'ingredient_multiset'),
    ('return "Now this is what I call a condiments tray!"', 'return "wrong comment"', 'plugin_comment_displayed'),
])
def test_runtime_oracle_rejects_independent_plugin_mutations(tmp_path, before, after, failed_check):
    copy = tmp_path / "benchmark"
    shutil.copytree(BENCH, copy, ignore=shutil.ignore_patterns("__pycache__"))
    plugin = copy / "eggsample/upstream/eggsample-spam/eggsample_spam.py"
    source = plugin.read_text()
    assert before in source
    plugin.write_text(source.replace(before, after))
    code, result = run_oracle(copy)
    assert code == 2 and result["status"] == "oracle_mismatch", result
    assert next(c for c in result["checks"] if c["id"] == failed_check)["passed"] is False


def test_frozen_graph_requirements_have_unique_denominators():
    for case in manifest()["cases"]:
        assert len(case["required_edges"]) == len(set(map(tuple, case["required_edges"]))) == 9
        assert len(case["forbidden_edges"]) == len(set(map(tuple, case["forbidden_edges"]))) == 2
        assert set(map(tuple, case["required_edges"])).isdisjoint(map(tuple, case["forbidden_edges"]))
        assert case["entry_points"] == case["required_entry_points"] == ["eggsample.host.main"]
