"""Format and failure tests; real engine/adapter gates live in integration tests."""
import hashlib
import json
import os

import pytest

from pythonstan_inspector.contract import InspectorError, SealedSnapshot, canonical_bytes
from pythonstan_inspector.storage import ReadLimits, load_bundle, write_bundle


def artifact(snapshot_id="s1"):
    return {
        "schema": {"major": 1, "minor": 0}, "run": {"stop_reason": "timeout"},
        "snapshot": {
            "run_id": "r1", "snapshot_id": snapshot_id,
            "boundary": {"coherent": True, "stage": "stopped"}, "scope": "all_current_state",
            "entities": [
                {"id": 1, "kind": "node", "label": "same name", "origin": {},
                 "attributes": {}, "links": {"dangling": {"ref": None,
                     "status": "missing_in_engine", "reason": "complete registry inspected"}}},
                {"id": 2, "kind": "object", "label": "same name", "origin": {},
                 "attributes": {}, "links": {"shared": {"ref": 1, "status": "resolved"}}},
            ],
            "views": {
                "node_points": {
                    "version": 1, "fidelity": "exact", "dimensions": ["owner", "object", "lane", "context"],
                    "operations": ["enumerate", "contains"],
                    "coverage": {"registry": "complete", "owners": {"1": {"fields": {"facts": "complete"}}}},
                    "rows": [{"owner": 1, "object": 2, "lane": "objects"}],
                },
                "pfg_edges": {
                    "version": 1, "fidelity": "exact", "dimensions": ["source", "target", "edge_kind"],
                    "operations": ["enumerate"],
                    "coverage": {"registry": "complete", "owners": {"1": {"fields": {"edges": "complete"}}}},
                    "rows": [{"source": 1, "target": 1, "edge_kind": "normal", "identity_copy": True}],
                },
            }, "stats": {"solve_seconds": 0.01},
        },
    }


def installed(tmp_path, data=None, trace=None):
    write_bundle(SealedSnapshot.seal(data or artifact()), tmp_path, trace=trace)
    return load_bundle(tmp_path)


def descriptor_path(path, key):
    manifest = json.loads((path / "manifest.json").read_text())
    return path / manifest[key]["file"]


def test_roundtrip_has_owned_fresh_trees_and_preserves_bad_state(tmp_path):
    data = artifact()
    bundle = installed(tmp_path, data, [{"type": "run_stopped", "sequence": 1}])
    data["snapshot"]["entities"][0]["label"] = "live mutation"
    value = bundle.data
    value["snapshot"]["entities"].clear()
    assert len(bundle.data["snapshot"]["entities"]) == 2
    assert bundle.data["snapshot"]["entities"][0]["links"]["dangling"]["status"] == "missing_in_engine"
    bundle.trace[0]["type"] = "mutated"
    bundle.manifest.clear()
    bundle.trace_health.clear()
    assert bundle.trace[0]["type"] == "run_stopped"
    assert bundle.manifest["schema"]["major"] == 1
    assert bundle.trace_health["status"] == "complete"


def test_refs_are_scoped_to_snapshot_not_local_numbers(tmp_path):
    left = installed(tmp_path / "left", artifact("left"))
    right = installed(tmp_path / "right", artifact("right"))
    assert left.data["snapshot"]["entities"][0]["id"] == right.data["snapshot"]["entities"][0]["id"]
    assert left.snapshot.ref != right.snapshot.ref


@pytest.mark.parametrize("mutation", [
    lambda x: x["schema"].update(major=2),
    lambda x: x["snapshot"]["entities"].append(dict(x["snapshot"]["entities"][0])),
    lambda x: x["snapshot"]["entities"][0].update(id=True),
    lambda x: x["snapshot"]["entities"][1]["links"]["shared"].update(ref=9),
    lambda x: x["snapshot"]["views"]["node_points"]["rows"][0].update(object=True),
    lambda x: x["snapshot"]["views"]["node_points"]["rows"][0].update(object=9),
    lambda x: x["snapshot"]["entities"][0]["attributes"].update(value=float("inf")),
    lambda x: x["snapshot"]["entities"][0]["attributes"].update(value=2**63),
])
def test_rejects_structural_invalidity_not_semantic_bad_state(tmp_path, mutation):
    data = artifact()
    mutation(data)
    with pytest.raises((InspectorError, ValueError)):
        write_bundle(SealedSnapshot.seal(data), tmp_path)
    assert not (tmp_path / "manifest.json").exists()


def test_unknown_optional_data_and_view_versions_are_retained(tmp_path):
    data = artifact()
    data["schema"]["minor"] = 9
    data["optional_future"] = {"tag": "not executable"}
    data["snapshot"]["views"]["future_domain"] = {"version": 8, "opaque": {"a": [1, 2]}}
    data["snapshot"]["views"]["ctx_points"] = {"version": 2, "new_representation": [1]}
    bundle = installed(tmp_path, data)
    assert bundle.data == data


def test_corrupt_snapshot_digest_and_unsealed_temporary_rejected(tmp_path):
    installed(tmp_path)
    descriptor_path(tmp_path, "snapshot").write_bytes(b"{")
    with pytest.raises(InspectorError, match="digest/size"):
        load_bundle(tmp_path)
    path = tmp_path / "unsealed"
    path.mkdir()
    (path / ".inspector-unsealed.tmp").write_bytes(canonical_bytes(artifact()))
    with pytest.raises(InspectorError):
        load_bundle(path)


@pytest.mark.parametrize("tail,expected,count", [
    (b'{"type":"a"}\n{"type":', "truncated_tail", 1),
    (b'{"type":"a"}\nBROKEN\n{"type":"b"}\n', "corrupt_middle", 1),
    (b'{"type":"a"}\nBROKEN\n', "corrupt_tail", 1),
    (b'{"type":"a"}', "complete", 1),
])
def test_trace_prefix_distinguishes_corruption_and_torn_tail(tmp_path, tail, expected, count):
    installed(tmp_path, trace=[{"type": "original"}])
    descriptor_path(tmp_path, "trace").write_bytes(tail)
    bundle = load_bundle(tmp_path)
    assert len(bundle.trace) == count
    assert bundle.trace_health["status"] == expected
    assert bundle.trace_health["integrity"] == "mismatch"
    assert bundle.data["snapshot"]["snapshot_id"] == "s1"


def test_missing_optional_trace_downgrades_observability(tmp_path):
    installed(tmp_path, trace=[{"type": "original"}])
    descriptor_path(tmp_path, "trace").unlink()
    bundle = load_bundle(tmp_path)
    assert bundle.trace == []
    assert bundle.trace_health["status"] == "unavailable"


def test_bounded_trace_query_and_snapshot_read(tmp_path):
    installed(tmp_path, trace=[{"type": "a"}, {"type": "b"}])
    bundle = load_bundle(tmp_path, ReadLimits(max_events=1))
    assert len(bundle.trace) == 1
    assert bundle.trace_health["status"] == "reader_truncated"
    for limits in (ReadLimits(max_snapshot_bytes=10), ReadLimits(max_depth=2), ReadLimits(max_rows=1)):
        with pytest.raises(InspectorError):
            load_bundle(tmp_path, limits)


def test_paths_and_symlinks_are_not_followed(tmp_path):
    path = tmp_path / "bundle"
    installed(path)
    manifest_path = path / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    original = manifest["snapshot"]["file"]
    manifest["snapshot"]["file"] = "../outside.json"
    manifest_path.write_bytes(canonical_bytes(manifest))
    with pytest.raises(InspectorError, match="basenames"):
        load_bundle(path)
    manifest["snapshot"]["file"] = original
    manifest_path.write_bytes(canonical_bytes(manifest))
    target = path / original
    outside = tmp_path / "outside.json"
    target.rename(outside)
    target.symlink_to(outside)
    with pytest.raises(InspectorError):
        load_bundle(path)
    link = tmp_path / "linked"
    link.symlink_to(path, target_is_directory=True)
    with pytest.raises(InspectorError):
        load_bundle(link)
    with pytest.raises(InspectorError):
        write_bundle(SealedSnapshot.seal(artifact()), link / "child")


def test_failed_manifest_install_preserves_previous_commit(tmp_path, monkeypatch):
    installed(tmp_path)
    original = os.replace

    def fail_manifest(source, target, **kwargs):
        if target == "manifest.json":
            raise OSError("injected disk failure")
        return original(source, target, **kwargs)

    monkeypatch.setattr(os, "replace", fail_manifest)
    with pytest.raises(InspectorError, match="installation failed"):
        write_bundle(SealedSnapshot.seal(artifact("new")), tmp_path)
    assert load_bundle(tmp_path).snapshot.ref.snapshot_id == "s1"
    assert not list(tmp_path.glob("*.tmp"))


@pytest.mark.parametrize("encoded", [b'{"a":1,"a":2}', b'{"a":NaN}', b'{"a":1e999}'])
def test_duplicate_keys_and_nonfinite_numbers_rejected(tmp_path, encoded):
    with pytest.raises(InspectorError):
        write_bundle(SealedSnapshot(encoded), tmp_path)


def test_valid_digest_does_not_make_invalid_json_a_snapshot(tmp_path):
    installed(tmp_path)
    malformed = b'{"schema":'
    path = descriptor_path(tmp_path, "snapshot")
    path.write_bytes(malformed)
    manifest_path = tmp_path / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["snapshot"].update(bytes=len(malformed), sha256=hashlib.sha256(malformed).hexdigest())
    manifest_path.write_bytes(canonical_bytes(manifest))
    with pytest.raises(InspectorError, match="invalid snapshot JSON"):
        load_bundle(tmp_path)


def test_call_edge_view_roundtrip_and_resolved_endpoint_validation(tmp_path):
    data = artifact()
    view = {"version": 1, "semantic_version": "raw_native_storage_v1",
            "fidelity": "exact", "dimensions": ["callsite", "callee", "call_kind"],
            "operations": ["enumerate", "contains"],
            "coverage": {"registry": "complete", "owners": {"1": {"fields": {"edges": "complete"}}}},
            "rows": [{"callsite": 1, "callee": 2, "call_kind": "function"}]}
    data["snapshot"]["views"]["call_edges"] = view
    loaded = installed(tmp_path / "valid", data)
    assert loaded.data["snapshot"]["views"]["call_edges"] == view
    # Format validation does not execute or infer native call semantics.
    for field, value in (("callsite", True), ("callee", 99), ("call_kind", None)):
        original = view["rows"][0][field]
        view["rows"][0][field] = value
        with pytest.raises(InspectorError):
            write_bundle(SealedSnapshot.seal(data), tmp_path / field)
        view["rows"][0][field] = original
