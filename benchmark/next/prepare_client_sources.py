"""Prepare exact pinned client sources; never import, install, build or run targets."""

from __future__ import annotations

import argparse
import json
import tarfile
from pathlib import Path, PurePosixPath

from benchmark.next.client_query_manifest import (
    MANIFEST,
    REPOSITORY,
    JsonObject,
    ManifestError,
    array_value,
    byte_digest,
    canonical_digest,
    check,
    checked_path,
    load_object,
    object_value,
    string_value,
    validate_manifest,
)


def prepare_project(name: str, project: JsonObject, archives: Path) -> JsonObject:
    archive = archives / string_value(project["archive_filename"])
    check(archive.is_file(), f"Pinned archive absent: {name}")
    check(byte_digest(archive) == project["archive_sha256"], f"Archive pin mismatch: {name}")
    inventory = load_object(checked_path(REPOSITORY, project["inventory_path"]))
    destination = REPOSITORY / string_value(project["project_root"])
    if not destination.exists():
        destination.parent.mkdir(parents=True, exist_ok=True)
        with tarfile.open(archive) as packed:
            for member in packed.getmembers():
                member_path = PurePosixPath(member.name)
                check(
                    not member_path.is_absolute() and ".." not in member_path.parts,
                    "Unsafe archive path",
                )
            packed.extractall(destination.parent, filter="data")
    actual: JsonObject = {}
    for root in array_value(inventory["runtime_roots"]):
        source_root = destination / string_value(root)
        for path in sorted(source_root.rglob("*.py")):
            actual[str(path.relative_to(destination))] = byte_digest(path)
    check(actual == inventory["files"], f"Cached source inventory mismatch: {name}")
    check(canonical_digest(actual) == project["source_digest"], f"Source identity mismatch: {name}")
    return {"project": name, "source_count": len(actual), "source_digest": project["source_digest"]}


def verify_fragments(manifest: JsonObject) -> None:
    """Verify each frozen fragment against its unmodified pinned project source."""
    for value in array_value(manifest["cases"]):
        case = object_value(value)
        identity = object_value(case["identity"])
        mapping = load_object(checked_path(REPOSITORY, identity["source_map_path"]))
        source_root = REPOSITORY / string_value(identity["target_path"])
        for fragment_value in array_value(mapping["mappings"]):
            fragment = object_value(fragment_value)
            source_file = source_root / string_value(fragment["original_path"])
            check(
                byte_digest(source_file) == fragment["original_file_sha256"],
                "Original fragment source changed",
            )
            start, end = fragment["start_line"], fragment["end_line"]
            if not isinstance(start, int) or not isinstance(end, int):
                raise ManifestError("Invalid source line coordinates")
            extracted = "".join(source_file.read_text().splitlines(keepends=True)[start - 1 : end])
            exact_fragment = checked_path(REPOSITORY, fragment["exact_fragment_path"])
            check(extracted == exact_fragment.read_text(), "Frozen fragment is not exact source")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archives", type=Path, required=True)
    args = parser.parse_args()
    try:
        validate_manifest()
        manifest = load_object(MANIFEST)
        projects = object_value(manifest["projects"])
        rows = [
            prepare_project(name, object_value(project), args.archives)
            for name, project in projects.items()
        ]
        verify_fragments(manifest)
    except (ManifestError, OSError, tarfile.TarError) as error:
        print(json.dumps({"status": "source_preparation_failed", "reason": str(error)}))
        return 1
    print(json.dumps({"status": "verified_pinned_sources", "projects": rows}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
