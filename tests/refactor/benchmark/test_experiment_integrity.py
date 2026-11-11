"""Negative controls for evidence identity, denominator and source-pin integrity."""
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tarfile

import pytest


REPO = Path(__file__).resolve().parents[3]


def load_module(name):
    path = REPO / "benchmark/refactor" / (name + ".py")
    spec = importlib.util.spec_from_file_location("refactor_" + name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def fixture_run(root):
    identity = {"git_commit": "frozen", "code_sha256": "bytes"}
    manifest = {"stage": "official", "executable": identity, "selected_cases": ["one"],
                "selected_modes": ["A", "B"], "input_identities": {"one": {"digest": "source"}}}
    (root / "run_manifest.json").write_text(json.dumps(manifest))
    for mode, status in (("A", "valid_result"), ("B", "timeout")):
        folder = root / "one" / mode
        folder.mkdir(parents=True)
        row = {"case_id": "one", "mode": mode, "process_status": status,
               "capture_status": "complete", "executable": identity,
               "executable_before": identity, "executable_after": identity,
               "matched_version_valid": True, "input_digest": "source",
               "observed_input_digest": "source", "input_identity_matches": True,
               "result": {"stats": {}}}
        (folder / "record.json").write_text(json.dumps(row))


def test_all_failures_retained_and_missing_work_not_zero(tmp_path):
    fixture_run(tmp_path)
    summary = load_module("aggregate").aggregate(tmp_path)
    assert summary["denominator_complete"]
    assert summary["modes"]["B"]["process_status"] == {"timeout": 1}
    assert all(row["native_work"] is None for row in summary["records"])
    assert len(summary["records"]) == 2


def test_aggregator_rechecks_identities_instead_of_trusting_marker(tmp_path):
    fixture_run(tmp_path)
    path = tmp_path / "one/A/record.json"
    row = json.loads(path.read_text())
    row["executable_after"] = {"git_commit": "other", "code_sha256": "changed"}
    row["observed_input_digest"] = "other_source"
    # A stale or buggy supervisor's Boolean cannot certify these identities.
    assert row["matched_version_valid"] is True
    path.write_text(json.dumps(row))
    summary = load_module("aggregate").aggregate(tmp_path)
    assert not summary["same_version_valid"]
    assert not summary["same_input_valid"]


def test_duplicate_rows_do_not_inflate_the_completed_denominator(tmp_path):
    fixture_run(tmp_path)
    extra = tmp_path / "duplicate/A"
    extra.mkdir(parents=True)
    (extra / "record.json").write_text((tmp_path / "one/A/record.json").read_text())
    summary = load_module("aggregate").aggregate(tmp_path)
    assert summary["record_count"] == 3 and summary["unique_rows"] == 2
    assert not summary["denominator_complete"]


def test_changed_cache_cannot_keep_the_original_archive_identity(tmp_path):
    runner = load_module("runner")
    archives = tmp_path / "archives"
    archives.mkdir()
    archive = archives / "sample.tar.gz"
    source = b"value = 'after'\n"
    with tarfile.open(archive, "w:gz") as packed:
        info = tarfile.TarInfo("sample-1/pkg/__init__.py")
        info.size = len(source)
        packed.addfile(info, io.BytesIO(source))
    case = {"id": "sample", "kind": "corpus", "category": "test", "archive_filename": archive.name,
            "archive_sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
            "project_root": "sample/sample-1", "runtime_source_roots": ["pkg"],
            "import_roots": ["."], "exclusions": [], "expected_inventory_count": 1,
            "reference_fact": None}
    cache = tmp_path / "cache"
    runner.prepare_case(case, cache, archives)
    changed = cache / "sample/sample-1/pkg/__init__.py"
    changed.write_text("value = 'changed-cache'\n")
    with pytest.raises(ValueError, match="differs from pinned archive"):
        runner.prepare_case(case, cache, archives)
    assert changed.read_text() == "value = 'changed-cache'\n", "Do not overwrite a changed cached source to fake identity"


def test_distribution_sibling_exclusions_keep_all_runtime_package_files(tmp_path):
    from pythonstan import Project
    from pythonstan.config import resolve_config
    from pythonstan.inputs import snapshot_target

    runner = load_module("runner")
    package_files = {"pkg/__init__.py", "pkg/tasks.py", "pkg/tests/test_bundled.py", "pkg/build/model.py"}
    for name in package_files | {"tasks.py", "tests/test_distribution.py"}:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("value = 'after'\n")
    target = Project(tmp_path, source_roots=(str(tmp_path),),
                     exclusions=runner.root_exclusion_patterns(["tasks.py", "tests"]))
    shared = json.loads((REPO / "benchmark/refactor/manifest.json").read_text())["shared_algorithm_configuration"]
    config = resolve_config(target, shared)
    snapshot = snapshot_target(target, config)
    actual = {str(Path(item["filename"]).relative_to(tmp_path)) for item in snapshot.inventory}
    assert actual == package_files
