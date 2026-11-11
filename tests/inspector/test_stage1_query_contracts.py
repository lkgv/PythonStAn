"""Stage 1 query contracts over formal bundles, independent of engine state.

These tests distinguish readable forensic rows from common-cut claims and
exercise the public dispatcher, direct checker, and CLI parameter boundary.
"""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest

from pythonstan_inspector.checker import COPY_CLOSURE, MODULE_COPY_SPEC, check
from pythonstan_inspector.contract import InspectorError, QueryLimits, QueryRequest, SealedSnapshot
from pythonstan_inspector.core import query
from pythonstan_inspector.storage import load_bundle, write_bundle


_spec = importlib.util.spec_from_file_location(
    "stage1_inspector_fixtures", Path(__file__).with_name("fixtures.py"))
_fixtures = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_fixtures)
artifact = _fixtures.artifact


def _bundle(tmp_path, data):
    return load_bundle(write_bundle(SealedSnapshot.seal(data), tmp_path / "bundle"))


def _contains(captured, **args):
    return query(captured, QueryRequest("show", {
        "view": "node_points", "contains": True, **args}))


@pytest.mark.parametrize("object_id", [9, 99], ids=["positive", "negative"])
def test_incoherent_contains_keeps_rows_without_common_cut_claim(tmp_path, object_id):
    data = artifact()
    data["snapshot"]["boundary"]["coherent"] = False
    captured = _bundle(tmp_path, data)
    result = _contains(captured, owner=7, object=object_id)
    assert result["answer"] == "unknown"
    assert "coherent_snapshot_required" in result["diagnostics"]
    assert result["query_complete"] is True
    assert result["evidence"] == ([{"owner": 7, "object": 9, "lane": "objects"}]
                                  if object_id == 9 else [])
    raw = query(captured, QueryRequest("show", {"view": "node_points", "owner": 7}))
    assert raw["answer"] == "captured_rows" and raw["evidence"]
    entity = query(captured, QueryRequest("show", {"id": 7}))
    assert entity["answer"] == "entities" and entity["evidence"][0]["id"] == 7


@pytest.mark.parametrize("coherent", [False, True], ids=["forensic", "coherent"])
def test_summary_containment_needs_common_cut(tmp_path, coherent):
    data = artifact()
    data["snapshot"]["boundary"]["coherent"] = coherent
    points = data["snapshot"]["views"]["node_points"]
    points.update(fidelity="native_summary", containment="finite_top_v1")
    points["rows"][0]["summary"] = "top"
    captured = _bundle(tmp_path, data)
    result = _contains(captured, owner=7, object=99)
    assert result["answer"] == ("covered" if coherent else "unknown")
    if not coherent:
        assert "coherent_snapshot_required" in result["diagnostics"]
        raw = query(captured, QueryRequest("show", {"view": "node_points", "owner": 7}))
        assert raw["answer"] == "captured_rows"
        assert raw["evidence"][0]["summary"] == "top"


def test_incoherent_explain_and_checker_do_not_join_common_cut_facts(tmp_path):
    data = artifact()
    data["snapshot"]["boundary"]["coherent"] = False
    captured = _bundle(tmp_path, data)
    result = query(captured, QueryRequest("explain", {"owner": 8, "object": 9}))
    assert result["answer"] == "unknown"
    assert "coherent_snapshot_required" in result["diagnostics"]
    assert result["evidence"] == []
    checked = check(captured, QueryRequest("check"))
    assert checked["check_status"] == "indeterminate"
    assert "coherent_snapshot_required" in checked["diagnostics"]


def test_coherent_exact_partial_preserves_positive_membership_and_support(tmp_path):
    data = artifact()
    points = data["snapshot"]["views"]["node_points"]
    points["coverage"]["registry"] = "partial"
    for coverage in points["coverage"]["owners"].values():
        coverage["fields"]["facts"] = "partial"
    captured = _bundle(tmp_path, data)
    assert _contains(captured, owner=7, object=9)["answer"] == "present_at_snapshot"
    assert _contains(captured, owner=7, object=99)["answer"] == "unknown"
    result = query(captured, QueryRequest("explain", {"owner": 8, "object": 9}))
    assert result["answer"] == "reconstructed_support"
    assert result["evidence"][0]["fact_represented"] is True
    assert result["evidence"][0]["alternatives"][0]["premises"][0]["represented"] is True
    assert check(captured, QueryRequest("check"))["check_status"] == "indeterminate"


def _graph(edges):
    data = artifact()
    snap = data["snapshot"]
    owners = sorted({owner for edge in edges for owner in edge})
    template = snap["entities"][0]
    for owner in owners:
        if owner not in (7, 8):
            entity = copy.deepcopy(template)
            entity.update(id=owner, label="node:" + str(owner), links={})
            snap["entities"].append(entity)
    points = snap["views"]["node_points"]
    points["rows"] = [{"owner": owner, "object": 9, "lane": "objects"} for owner in owners]
    points["coverage"]["owners"] = {
        str(owner): {"fields": {"facts": "complete"}} for owner in owners}
    snap["views"]["pfg_edges"]["rows"] = [
        {"source": source, "target": target, "edge_kind": "normal", "identity_copy": True}
        for source, target in edges]
    return data


@pytest.mark.parametrize("edges,target,revisited", [
    ([(7, 8), (8, 17)], 17, []),
    ([(7, 8), (7, 17), (8, 18), (17, 18)], 18, [7]),
    ([(7, 7)], 7, [7]),
    ([(7, 8), (8, 7)], 8, [8]),
], ids=["chain", "diamond", "self_loop", "two_node_cycle"])
def test_explain_deduplicates_without_inventing_cycle_evidence(tmp_path, edges, target, revisited):
    captured = _bundle(tmp_path, _graph(edges))
    result = query(captured, QueryRequest("explain", {"owner": target, "object": 9}))
    assert result["answer"] == "reconstructed_support"
    assert result["query_complete"] is True
    assert not any(row["kind"] == "cycle_reference" for row in result["evidence"])
    assert [row["owner"] for row in result["evidence"]
            if row["kind"] == "revisited_reference"] == revisited
    assert len(result["evidence"]) <= len({n for edge in edges for n in edge}) + len(revisited)


def test_explain_depth_limit_stays_explicit(tmp_path):
    captured = _bundle(tmp_path, _graph([(7, 8), (8, 17)]))
    result = query(captured, QueryRequest("explain", {"owner": 17, "object": 9},
                                        limits=QueryLimits(max_depth=1)))
    assert result["answer"] == "unknown"
    assert result["query_complete"] is False
    assert "depth_limit" in result["diagnostics"]
    assert all(row["depth"] <= 1 for row in result["evidence"])


@pytest.mark.parametrize("entrypoint", [query, check], ids=["dispatcher", "direct"])
@pytest.mark.parametrize("extra", [
    {"owner": 999}, {"scope": "selected-owner"},
    {"unsupported_scope": "selected-owner"}, {"cheker": COPY_CLOSURE},
    {"source_text": "x = 1\ny = x\n"},
], ids=["owner", "scope", "unknown_scope", "typo", "wrong_checker_argument"])
def test_check_rejects_unimplemented_selectors_without_weaker_pass(tmp_path, entrypoint, extra):
    captured = _bundle(tmp_path, artifact())
    request = QueryRequest("check", {"checker": COPY_CLOSURE, **extra}, claim=COPY_CLOSURE)
    result = entrypoint(captured, request)
    assert result["answer"] == result["check_status"] == "unsupported"
    assert "unsupported_checker_parameters" in result["diagnostics"]
    assert result["requested_claim"] == result["claim"] == COPY_CLOSURE
    assert result["parameters"] == request.args
    assert result["evidence"] == []


def _source_artifact():
    source = "x = 1\ny = x\n"
    data = artifact()
    checksum = hashlib.sha256(source.encode("utf-8")).hexdigest()
    data["run"]["inputs"][0].update(sha256_before=checksum, sha256_after=checksum)
    data["run"]["source_validation"] = "pre_post_verified"
    next(entity for entity in data["snapshot"]["entities"]
         if entity["id"] == 16)["attributes"]["engine_class"] = "IRModule"
    return data, source


@pytest.mark.parametrize("entrypoint", [query, check], ids=["dispatcher", "direct"])
def test_named_checkers_keep_their_legitimate_arguments_and_claims(tmp_path, entrypoint):
    data, source = _source_artifact()
    captured = _bundle(tmp_path, data)
    assert entrypoint(captured, QueryRequest("check"))["check_status"] == "passed"
    args = {"checker": MODULE_COPY_SPEC, "source_text": source}
    legitimate = entrypoint(captured, QueryRequest("check", args, claim=MODULE_COPY_SPEC))
    assert legitimate["check_status"] == "passed"
    rejected = entrypoint(captured, QueryRequest("check", {**args, "scope": "selected-owner"},
                                                claim=MODULE_COPY_SPEC))
    assert rejected["check_status"] == "unsupported"
    assert "unsupported_checker_parameters" in rejected["diagnostics"]
    assert rejected["requested_claim"] == MODULE_COPY_SPEC
    mismatch = entrypoint(captured, QueryRequest("check", args, claim=COPY_CLOSURE))
    assert mismatch["check_status"] == "unsupported"
    assert mismatch["requested_claim"] == COPY_CLOSURE


@pytest.mark.parametrize("entrypoint", [query, check], ids=["dispatcher", "direct"])
def test_checker_parameter_types_have_explicit_failures(tmp_path, entrypoint):
    captured = _bundle(tmp_path, artifact())
    with pytest.raises(InspectorError, match="dictionary args"):
        entrypoint(captured, QueryRequest("check", []))
    with pytest.raises(InspectorError, match="checker name must be a string"):
        entrypoint(captured, QueryRequest("check", {"checker": 42}))
    result = entrypoint(captured, QueryRequest("check", {
        "checker": MODULE_COPY_SPEC, "source_text": 42}, claim=MODULE_COPY_SPEC))
    assert result["check_status"] == "unsupported"
    assert "source_text_required_for_named_fixture_spec" in result["diagnostics"]


def test_cli_uses_the_same_checker_scope_contract(tmp_path):
    _bundle(tmp_path, artifact())
    args = {"checker": COPY_CLOSURE, "scope": "selected-owner"}
    result = subprocess.run([sys.executable, "-m", "pythonstan_inspector", "check",
        str(tmp_path / "bundle"), "--args", json.dumps(args), "--claim", COPY_CLOSURE],
        cwd=Path(__file__).resolve().parents[2], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    response = json.loads(result.stdout)
    assert response["check_status"] == "unsupported"
    assert "unsupported_checker_parameters" in response["diagnostics"]
    assert response["parameters"] == args
