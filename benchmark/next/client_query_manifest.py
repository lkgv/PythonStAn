"""Validate frozen client questions without running the target or the analyzer."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path

type JsonValue = None | bool | int | float | str | list["JsonValue"] | dict[str, "JsonValue"]
type JsonObject = dict[str, JsonValue]
REPOSITORY = Path(__file__).resolve().parents[2]
MANIFEST = REPOSITORY / "docs/development/next/query_manifest.json"


class ManifestError(ValueError):
    """A frozen identity or semantic preregistration invariant is invalid."""


def json_value(value: object) -> JsonValue:
    """Narrow the single JSON boundary; no unchecked payload reaches validation."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, list):
        return [json_value(item) for item in value]
    if isinstance(value, dict):
        result: JsonObject = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise ManifestError("JSON object key must be a string")
            result[key] = json_value(item)
        return result
    raise ManifestError("Unsupported JSON value")


def object_value(value: JsonValue) -> JsonObject:
    if not isinstance(value, dict):
        raise ManifestError("Expected JSON object")
    return value


def array_value(value: JsonValue) -> list[JsonValue]:
    if not isinstance(value, list):
        raise ManifestError("Expected JSON array")
    return value


def string_value(value: JsonValue) -> str:
    if not isinstance(value, str):
        raise ManifestError("Expected JSON string")
    return value


def load_object(path: Path) -> JsonObject:
    raw: object = json.loads(path.read_text())
    return object_value(json_value(raw))


def canonical_digest(value: JsonValue) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode()).hexdigest()


def byte_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def check(condition: bool, message: str) -> None:
    if not condition:
        raise ManifestError(message)


def checked_path(root: Path, relative: JsonValue) -> Path:
    path = root / string_value(relative)
    check(path.resolve().is_relative_to(root.resolve()), "Manifest path escapes repository")
    check(path.is_file(), "Required frozen manifest file is absent")
    return path


def check_file_digest(root: Path, relative: JsonValue, expected: JsonValue) -> Path:
    path = checked_path(root, relative)
    check(byte_digest(path) == expected, f"Frozen file changed: {relative}")
    return path


def validate_case(case: JsonObject, root: Path, *, require_reviewed: bool) -> str:
    frozen = object_value(case["freeze"])
    payload = dict(case)
    payload["freeze"] = {key: value for key, value in frozen.items() if key != "manifest_digest"}
    check(canonical_digest(payload) == frozen["manifest_digest"], "Case freeze digest changed")
    check(case["acceptance_class"] == "mandatory", "Client acceptance class changed")
    check(case["track"] == "real_client", "Client question moved to another denominator")
    identity = object_value(case["identity"])
    entry = object_value(case["entry"])
    entry_payload = {key: value for key, value in entry.items() if key != "entry_contract_digest"}
    check(
        canonical_digest(entry_payload) == entry["entry_contract_digest"], "Entry contract changed"
    )
    check_file_digest(root, entry["input_bound_file"], entry["input_bound_digest"])
    model = load_object(
        check_file_digest(root, entry["model_manifest_file"], entry["model_digest"])
    )
    if "callback_source" in model:
        callback = object_value(model["callback_source"])
        check_file_digest(root, callback["path"], callback["sha256"])
    check_file_digest(root, identity["dependency_manifest_path"], identity["dependency_digest"])
    mapping = load_object(checked_path(root, identity["source_map_path"]))
    entry_file = checked_path(root, identity["source_map_path"]).parent / "entry.json"
    check(byte_digest(entry_file) == identity["harness_digest"], "Prepared entry harness changed")
    harness = load_object(entry_file)
    check(identity["project"] == case["family"], "Case and project identities differ")
    check(mapping["project"] == identity["project"], "Source mapping project differs")
    check(mapping["case_id"] == case["case_id"], "Source mapping case differs")
    fragments = array_value(mapping["mappings"])
    check(bool(fragments), "Source mappings cannot be empty")
    full = object_value(mapping["full_project"])
    check(full["unreduced_runtime_inventory"] is True, "Full project scope reduced")
    check(full["same_entry_input_model_query_as_slice"] is True, "Project and slice entries differ")
    for fragment_value in array_value(mapping["mappings"]):
        fragment = object_value(fragment_value)
        check_file_digest(root, fragment["exact_fragment_path"], fragment["exact_fragment_sha256"])
        check(
            fragment["transformation"]
            == "none; source lines preserved including indentation and comments",
            "Source transfer replaced instead of preserved",
        )
    budget = object_value(case["budget"])
    check(
        budget
        == {
            "max_work": 100000,
            "max_seconds": 30,
            "max_memory_mib": 1536,
            "outer_timeout_seconds": 40,
            "work_schema_version": "baseline-public-e8d9e0d-v1",
        },
        "Preregistered budget changed",
    )
    queries = [object_value(item) for item in array_value(case["queries"])]
    query_ids = {string_value(query["id"]) for query in queries}
    check(len(queries) == 1 and len(query_ids) == 1, "Mandatory query removed or duplicated")
    for query in queries:
        check(
            query["kind"] in {"relation", "effects", "outcomes", "identity"}, "Literal substitution"
        )
        check(query["module"] == harness["module"], "Query and entry modules differ")
        check(query["qualname"] == harness["qualname"], "Query and entry functions differ")
        check(
            query["qualname"]
            in {string_value(object_value(fragment)["qualname"]) for fragment in fragments},
            "Query function absent from preserved source",
        )
    covered: set[str] = set()
    for item in array_value(case["oracles"]):
        oracle = object_value(item)
        evidence = load_object(
            check_file_digest(root, oracle["evidence_path"], oracle["evidence_sha256"])
        )
        check(oracle["kind"] == "reviewed_closed_profile", "Complete gold source changed")
        check(oracle["complete_scope"] is True, "A partial witness cannot establish complete gold")
        check(evidence["analyzer_outputs_consulted"] is False, "Analyzer supplies its own expected")
        check(
            evidence["dynamic_execution_used_to_exclude_behavior"] is False,
            "Dynamic observations cannot exclude unobserved behavior",
        )
        check(bool(oracle["expected_must"]) and bool(oracle["expected_may"]), "Finite gold missing")
        covered.update(string_value(value) for value in array_value(oracle["query_ids"]))
        if require_reviewed:
            check(oracle["reviewer"] != "pending_independent_review", "Gold review is pending")
            check(evidence["review_status"] == "reviewed", "Gold evidence not reviewed")
    check(covered == query_ids, "Oracle and query scopes do not match")
    if require_reviewed:
        check(frozen["independent_reviewer"] != "pending_independent_review", "Case review pending")
    return string_value(case["family"])


def validate_manifest(path: Path = MANIFEST, *, require_reviewed: bool = False) -> JsonObject:
    root = REPOSITORY
    manifest = load_object(path)
    expected_digest = manifest["freeze_digest"]
    payload = {key: value for key, value in manifest.items() if key != "freeze_digest"}
    check(canonical_digest(payload) == expected_digest, "Overall freeze digest changed")
    cases = [object_value(item) for item in array_value(manifest["cases"])]
    check(len(cases) == 6, "Mandatory client denominator must remain six")
    check(len({string_value(case["case_id"]) for case in cases}) == 6, "Duplicate client case")
    source_maps = object_value(manifest["source_map_sha256"])
    for case in cases:
        identity = object_value(case["identity"])
        check_file_digest(
            root,
            identity["source_map_path"],
            source_maps[string_value(case["case_id"])],
        )
    families = Counter(
        validate_case(case, root, require_reviewed=require_reviewed) for case in cases
    )
    check(families == {"invoke": 2, "django": 2, "fastapi": 2}, "Required real projects changed")
    projects = object_value(manifest["projects"])
    for name, value in projects.items():
        project = object_value(value)
        inventory = load_object(
            check_file_digest(root, project["inventory_path"], project["inventory_digest"])
        )
        check(
            canonical_digest(inventory["files"]) == project["source_digest"],
            "Source inventory identity changed",
        )
        check(inventory["project"] == name, "Project inventory mislabeled")
        for case in cases:
            identity = object_value(case["identity"])
            if identity["project"] == name:
                check(identity["source_digest"] == project["source_digest"], "Case source differs")
                check(
                    identity["inventory_digest"] == project["inventory_digest"],
                    "Case inventory differs",
                )
                check(identity["target_path"] == project["project_root"], "Project target changed")
    protocol = load_object(
        check_file_digest(root, manifest["protocol_path"], manifest["protocol_sha256"])
    )
    check(object_value(protocol["D_guard"])["required_pta_fact_count"] == 0, "D no-PTA guard lost")
    check(
        object_value(protocol["D_guard"])["forbid_incremental_oracle"] is True,
        "D oracle guard lost",
    )
    check(
        manifest["required_full_project_query_ids"] == ["invoke_command_names:q1"],
        "Full project obligation removed",
    )
    compatibility = load_object(checked_path(root, manifest["compatibility_manifest_path"]))
    check_file_digest(root, compatibility["source_manifest"], compatibility["source_sha256"])
    check(len(array_value(compatibility["real_case_ids"])) == 14, "Legacy real denominator changed")
    check(
        len(array_value(compatibility["control_case_ids"])) == 4,
        "Legacy control denominator changed",
    )
    check(compatibility["expected_rows"] == 90, "Legacy A-E denominator changed")
    return {
        "status": "validated_preregistration",
        "case_count": 6,
        "query_count": 6,
        "independently_reviewed": require_reviewed,
        "freeze_digest": expected_digest,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--require-reviewed", action="store_true")
    args = parser.parse_args()
    try:
        summary = validate_manifest(require_reviewed=args.require_reviewed)
    except (ManifestError, KeyError, OSError, json.JSONDecodeError) as error:
        print(json.dumps({"status": "invalid_preregistration", "reason": str(error)}))
        return 1
    print(json.dumps(summary, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
