"""Independent containment and compact project-identity counterexamples."""
import pytest

from pythonstan_inspector.contract import QueryRequest
from pythonstan_inspector.core import diff_query, query
from tests.inspector.fixtures import artifact, bundle


def _target(data, local_id=17):
    data["snapshot"]["entities"].append({"id": local_id, "kind": "object", "label": "target",
        "origin": {}, "attributes": {"native_summary": False, "description_coverage": "complete"}, "links": {}})


@pytest.mark.parametrize("kind,summary", [("opaque", False), ("object", None),
                                           ("object", 0), ("object", True)])
def test_opaque_or_unproven_object_shape_blocks_only_negative_claims(kind, summary):
    data = artifact()
    _target(data)
    native_object = next(entity for entity in data["snapshot"]["entities"] if entity["id"] == 9)
    native_object["kind"] = kind
    native_object["attributes"].pop("native_summary", None)
    if summary is not None:
        native_object["attributes"]["native_summary"] = summary
    captured = bundle(data)
    positive = query(captured, QueryRequest("show", {"view": "node_points", "owner": 7,
        "object": 9, "contains": True}))
    negative = query(captured, QueryRequest("show", {"view": "node_points", "owner": 7,
        "object": 17, "contains": True}))
    assert positive["answer"] == "present_at_snapshot"
    assert negative["answer"] == "unknown"


def test_unknown_object_of_another_owner_does_not_erase_local_complete_evidence():
    data = artifact()
    _target(data)
    data["snapshot"]["entities"].append({"id": 18, "kind": "opaque", "label": "opaque",
        "origin": {}, "attributes": {"description_coverage": "not_captured"}, "links": {}})
    data["snapshot"]["views"]["node_points"]["rows"].append(
        {"owner": 8, "object": 18, "lane": "objects"})
    captured = bundle(data)
    known = query(captured, QueryRequest("show", {"view": "node_points", "owner": 7,
        "object": 17, "contains": True}))
    unknown = query(captured, QueryRequest("show", {"view": "node_points", "owner": 8,
        "object": 17, "contains": True}))
    assert known["answer"] == "absent_at_snapshot"
    assert known["concrete_reachability_proved"] is False
    assert unknown["answer"] == "unknown"


def _catalog():
    return {"identity_algorithm": "sha256_ascii_canonical_json_rows_v1",
        "inventory_sha256": "a" * 64, "consumed_sha256": "b" * 64,
        "inventory_count": 3, "consumed_count": 2}


def test_supported_project_catalog_authorizes_only_conditional_structural_diff():
    left, right = artifact(), artifact("second")
    for data in (left, right):
        data["run"]["inputs"] = _catalog()
    result = diff_query(bundle(left), bundle(right), QueryRequest("diff"))
    assert result["comparability"]["comparable"] is True
    assert result["answer"] == "structural_diff"
    assert result["concrete_reachability_proved"] is False


@pytest.mark.parametrize("field,value", [
    ("identity_algorithm", "future_catalog_v2"), ("inventory_sha256", "A" * 64),
    ("consumed_sha256", None), ("inventory_count", True), ("consumed_count", 4),
    ("consumed_count", -1),
])
def test_invalid_project_catalog_cannot_be_waived_as_an_intended_change(field, value):
    left, right = artifact(), artifact("second")
    left["run"]["inputs"] = _catalog()
    right["run"]["inputs"] = _catalog()
    right["run"]["inputs"][field] = value
    result = diff_query(bundle(left), bundle(right), QueryRequest("diff", {"intended_changes": ["inputs"]}))
    assert result["comparability"]["comparable"] is False
    assert result["status"] == "incomparable"
    assert result["comparability"]["invalid_identity_evidence"]["right"]
