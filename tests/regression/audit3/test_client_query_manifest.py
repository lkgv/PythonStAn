"""Preregistration invariants; these checks do not certify analyzer answers."""

from __future__ import annotations

import json
import shutil
from copy import deepcopy
from pathlib import Path

import pytest

from benchmark.next import client_query_manifest as client_manifest
from benchmark.next.client_query_manifest import (
    MANIFEST,
    JsonObject,
    ManifestError,
    array_value,
    canonical_digest,
    load_object,
    object_value,
    validate_manifest,
)


def save_mutant(tmp_path: Path, manifest: JsonObject) -> Path:
    payload = {key: value for key, value in manifest.items() if key != "freeze_digest"}
    manifest["freeze_digest"] = canonical_digest(payload)
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest))
    return path


def rehash_case(case: JsonObject) -> None:
    frozen = object_value(case["freeze"])
    payload = deepcopy(case)
    payload["freeze"] = {key: value for key, value in frozen.items() if key != "manifest_digest"}
    frozen["manifest_digest"] = canonical_digest(payload)


def test_frozen_client_denominator_and_independent_source_identity() -> None:
    result = validate_manifest()
    assert result["status"] == "validated_preregistration"
    assert result["case_count"] == 6
    assert result["query_count"] == 6


def test_dropping_a_failed_client_case_is_rejected(tmp_path: Path) -> None:
    manifest = load_object(MANIFEST)
    manifest["cases"] = array_value(manifest["cases"])[:-1]
    with pytest.raises(ManifestError, match="denominator"):
        validate_manifest(save_mutant(tmp_path, manifest))


def test_reclassifying_mandatory_case_is_rejected(tmp_path: Path) -> None:
    manifest = load_object(MANIFEST)
    case = object_value(array_value(manifest["cases"])[0])
    case["acceptance_class"] = "stress"
    rehash_case(case)
    with pytest.raises(ManifestError, match="acceptance class"):
        validate_manifest(save_mutant(tmp_path, manifest))


def test_literal_substitution_is_rejected_even_with_rehashed_document(tmp_path: Path) -> None:
    manifest = load_object(MANIFEST)
    case = object_value(array_value(manifest["cases"])[0])
    object_value(array_value(case["queries"])[0])["kind"] = "value"
    rehash_case(case)
    with pytest.raises(ManifestError, match="Literal substitution"):
        validate_manifest(save_mutant(tmp_path, manifest))


def test_dynamic_lower_bound_cannot_replace_complete_gold(tmp_path: Path) -> None:
    manifest = load_object(MANIFEST)
    case = object_value(array_value(manifest["cases"])[0])
    object_value(array_value(case["oracles"])[0])["kind"] = "dynamic_witness_lower_bound"
    rehash_case(case)
    with pytest.raises(ManifestError, match="Complete gold source changed"):
        validate_manifest(save_mutant(tmp_path, manifest))


def test_increasing_budget_after_failure_is_rejected(tmp_path: Path) -> None:
    manifest = load_object(MANIFEST)
    case = object_value(array_value(manifest["cases"])[0])
    object_value(case["budget"])["max_work"] = 200000
    rehash_case(case)
    with pytest.raises(ManifestError, match="budget changed"):
        validate_manifest(save_mutant(tmp_path, manifest))


def test_pending_or_missing_review_is_not_reported_as_independently_approved(
    tmp_path: Path,
) -> None:
    manifest = load_object(MANIFEST)
    case = object_value(array_value(manifest["cases"])[0])
    object_value(array_value(case["oracles"])[0])["reviewer"] = "pending_independent_review"
    rehash_case(case)
    with pytest.raises(ManifestError, match="Gold review is pending"):
        validate_manifest(save_mutant(tmp_path, manifest), require_reviewed=True)


def test_removing_a_source_map_fails_its_frozen_digest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = client_manifest.REPOSITORY
    target = tmp_path / "docs/development/next/client_queries"
    shutil.copytree(root / "docs/development/next/client_queries", target)
    path = target / "cases/invoke_command_names/source_map.json"
    mapping = load_object(path)
    mapping["mappings"] = []
    path.write_text(json.dumps(mapping))
    manifest = load_object(MANIFEST)
    manifest_path = save_mutant(tmp_path, manifest)
    monkeypatch.setattr(client_manifest, "REPOSITORY", tmp_path)
    with pytest.raises(ManifestError, match="Frozen file changed"):
        validate_manifest(manifest_path)
