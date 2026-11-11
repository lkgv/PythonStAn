"""Relocation controls use a genuine sealed analyzer bundle, no new analysis.

Only the trial wrapper is a protocol fixture. It is not scored as a real
fourteen-project cohort success or a newly measured process timeout.
"""
import copy
from dataclasses import asdict
import json
from pathlib import Path
import shutil

import pytest

from benchmark.round12_projects import inspect_bundles as inspector
from benchmark.round8_corpus.registry import projects
from pythonstan_inspector.storage import load_bundle


@pytest.fixture
def real_bundle():
    original = Path(__file__).resolve().parents[2] / "docs/inspector/examples/django"
    bundle = load_bundle(original)
    return original, bundle


def _row(bundle, origin):
    return {"project_id": "django", "result": {
        "status": "timeout", "run_id": bundle.snapshot.ref.run_id,
        "native_report": {"status": "incomplete"},
        "capture_state": {"status": "sealed", "snapshot_ref": asdict(bundle.snapshot.ref),
                          "snapshot_sha256": bundle.snapshot.sha256},
        "bundle": str(origin), "last_sealed_snapshot": {"path": str(origin),
            "snapshot_ref": asdict(bundle.snapshot.ref), "snapshot_sha256": bundle.snapshot.sha256}}}


def _copy_bundle(original, bundle, directory):
    target = directory / "bundles" / "django" / bundle.snapshot.ref.run_id / "bundle"
    target.parent.mkdir(parents=True)
    shutil.copytree(original, target)
    return target


def test_genuine_bundle_relocation_ignores_obsolete_absolute_origin(tmp_path, real_bundle):
    original, bundle = real_bundle
    old = tmp_path / "old-observed"
    origin = _copy_bundle(original, bundle, old)
    row = _row(bundle, origin)
    relocated = tmp_path / "extracted-elsewhere"
    shutil.move(str(old), relocated)
    assert not origin.exists()
    result = inspector._inspect_bundle(row, relocated, "django")
    assert result["status"] == "queried"
    assert result["recorded_bundle_origin"] == str(origin)
    assert result["bundle_relative_path"] == f"bundles/django/{bundle.snapshot.ref.run_id}/bundle"
    assert Path(result["queried_bundle_path"]).is_relative_to(relocated)
    assert result["snapshot_sha256"] == bundle.snapshot.sha256
    assert result["native_process_status"] == "timeout"
    assert result["native_report_status"] == "incomplete"
    assert set(result["queries"]) == set(inspector.OPERATIONS)
    assert all(value["status"] == "returned" for value in result["queries"].values())
    assert result["queries"]["show"]["result"]["answer"] == "present_at_snapshot"


def test_missing_local_bundle_never_falls_back_to_existing_old_origin(tmp_path, real_bundle, monkeypatch):
    original, bundle = real_bundle
    old = _copy_bundle(original, bundle, tmp_path / "still-existing-old-observed")
    relocated = tmp_path / "new-observed-without-bundle"
    relocated.mkdir()
    row = _row(bundle, old)
    calls = []
    actual = inspector.load_bundle

    def observed(path):
        calls.append(Path(path))
        return actual(path)

    monkeypatch.setattr(inspector, "load_bundle", observed)
    result = inspector._inspect_bundle(row, relocated, "django")
    assert old.is_dir()
    assert result["status"] == "bundle_refused"
    assert calls == [relocated / "bundles" / "django" / bundle.snapshot.ref.run_id / "bundle"]
    assert all(value["status"] == "not_run" for value in result["queries"].values())


def test_forged_recorded_locator_cannot_redirect_local_queries(tmp_path, real_bundle, monkeypatch):
    original, bundle = real_bundle
    local = _copy_bundle(original, bundle, tmp_path / "portable-observed")
    forged = tmp_path / "unrelated-external-origin"
    row = _row(bundle, forged)
    calls = []
    actual = inspector.load_bundle

    def observed(path):
        calls.append(Path(path))
        return actual(path)

    monkeypatch.setattr(inspector, "load_bundle", observed)
    result = inspector._inspect_bundle(row, tmp_path / "portable-observed", "django")
    assert result["status"] == "queried"
    assert result["recorded_bundle_origin"] == str(forged)
    assert calls == [local]


@pytest.mark.parametrize("unsafe", [None, True, "", ".", "..", "../outside", "x/y", "x\\y", "x\n", "é", "x" * 129])
def test_unsafe_run_locator_is_refused_before_file_access(tmp_path, real_bundle, monkeypatch, unsafe):
    original, bundle = real_bundle
    row = _row(bundle, original)
    row["result"]["run_id"] = unsafe

    def forbidden(*args, **kwargs):
        raise AssertionError("unsafe run ID must not reach the filesystem")

    monkeypatch.setattr(inspector, "load_bundle", forbidden)
    result = inspector._inspect_bundle(row, tmp_path, "django")
    assert result["status"] == "bundle_refused"
    assert "unsafe OBS run_id locator" in result["reason"]


@pytest.mark.parametrize("field", ["last_digest", "last_ref", "capture_ref", "capture_digest"])
def test_local_bundle_must_still_match_exact_recorded_snapshot_identity(tmp_path, real_bundle, field):
    original, bundle = real_bundle
    local = _copy_bundle(original, bundle, tmp_path)
    row = _row(bundle, local)
    if field == "last_digest":
        row["result"]["last_sealed_snapshot"]["snapshot_sha256"] = "0" * 64
    elif field == "last_ref":
        row["result"]["last_sealed_snapshot"]["snapshot_ref"]["snapshot_id"] = "other-snapshot"
    elif field == "capture_ref":
        row["result"]["capture_state"]["snapshot_ref"]["snapshot_id"] = "other-snapshot"
    else:
        row["result"]["capture_state"]["snapshot_sha256"] = "0" * 64
    result = inspector._inspect_bundle(row, tmp_path, "django")
    assert result["status"] == "bundle_refused"
    assert all(value["status"] == "not_run" for value in result["queries"].values())


def test_local_symlink_cannot_escape_to_an_external_bundle(tmp_path, real_bundle):
    original, bundle = real_bundle
    external = _copy_bundle(original, bundle, tmp_path / "external")
    observed = tmp_path / "observed"
    parent = observed / "bundles" / "django"
    parent.mkdir(parents=True)
    (parent / bundle.snapshot.ref.run_id).symlink_to(external.parent, target_is_directory=True)
    result = inspector._inspect_bundle(_row(bundle, external), observed, "django")
    assert result["status"] == "bundle_refused"


def test_missing_local_replay_keeps_all_fourteen_projects(tmp_path):
    records = projects()
    plan = {"plan_id": "a" * 64, "identity": {"arm": "observed", "engine_sha256": "b" * 64,
        "source_identity_sha256": {record["id"]: "c" * 64 for record in records}},
        "declared_projects": 14, "declared_runtime_python_files": 8482,
        "project_order": [record["id"] for record in records]}
    (tmp_path / "plan.json").write_text(json.dumps(plan))
    result = inspector.inspect_cohort(tmp_path)
    assert result["declared_projects"] == len(result["rows"]) == 14
    assert result["project_outcomes"] == {"trial_missing": 14}
    assert result["query_outcomes"] == {"not_run": 98}
