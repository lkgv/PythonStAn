"""Unknown object descriptions cannot establish a native-domain negative."""
import importlib.util
from pathlib import Path

import pytest

from pythonstan_inspector.contract import QueryRequest
from pythonstan_inspector.core import query

spec = importlib.util.spec_from_file_location("capture_containment_fixtures", Path(__file__).with_name("fixtures.py"))
fixtures = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixtures)


@pytest.mark.parametrize("kind,attributes", [
    ("opaque", {"description_coverage": "not_captured"}),
    ("object", {}),
    ("object", {"native_summary": True}),
])
def test_incomplete_object_description_keeps_positive_but_blocks_negative(kind, attributes):
    data = fixtures.artifact()
    obj = next(e for e in data["snapshot"]["entities"] if e["id"] == 9)
    obj.update(kind=kind, attributes=attributes)
    bundle = fixtures.bundle(data)
    present = query(bundle, QueryRequest("show", {"view": "node_points", "owner": 7, "object": 9, "contains": True}))
    missing = query(bundle, QueryRequest("show", {"view": "node_points", "owner": 7, "object": 99, "contains": True}))
    assert present["answer"] == "present_at_snapshot"
    assert missing["answer"] == "unknown"
    explanation = query(bundle, QueryRequest("explain", {"owner": 7, "object": 99}))
    assert explanation["evidence"][0]["fact_represented"] is None


def test_known_unsummarized_objects_allow_snapshot_local_negative():
    answer = query(fixtures.bundle(), QueryRequest("show", {
        "view": "node_points", "owner": 7, "object": 99, "contains": True}))
    assert answer["answer"] == "absent_at_snapshot"
