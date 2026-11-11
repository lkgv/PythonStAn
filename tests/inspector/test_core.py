"""Offline protocol tests. Real vertical-chain integration is separate."""
import copy
import importlib.util
from pathlib import Path

import pytest

from pythonstan_inspector.contract import InspectorError, QueryLimits, QueryRequest
from pythonstan_inspector.core import query, diff_query

_spec = importlib.util.spec_from_file_location("inspector_fixed_fixtures", Path(__file__).with_name("fixtures.py"))
_fixtures = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_fixtures)
artifact, bundle = _fixtures.artifact, _fixtures.bundle


def ask(data, args, **kwargs):
    return query(bundle(data), QueryRequest("show", {"view": "node_points", "contains": True, **args}, **kwargs))


def test_partial_positive_is_only_existence_and_negative_unknown():
    data = artifact()
    view = data["snapshot"]["views"]["node_points"]
    view["coverage"]["registry"] = "partial"
    view["coverage"]["owners"]["7"]["fields"]["facts"] = "partial"
    assert ask(data, {"owner": 7, "object": 9})["answer"] == "present_at_snapshot"
    assert ask(data, {"owner": 7, "object": 99})["answer"] == "unknown"
    assert ask(data, {"object": 99})["answer"] == "unknown"
    assert ask(data, {"owner": 8, "object": 99})["answer"] == "absent_at_snapshot"


def test_timeout_absence_is_snapshot_only_and_incoherent_unknown():
    data = artifact()
    result = ask(data, {"owner": 7, "object": 99})
    assert result["answer"] == "absent_at_snapshot"
    assert result["concrete_reachability_proved"] is False
    data["snapshot"]["boundary"]["coherent"] = False
    assert ask(data, {"owner": 7, "object": 99})["answer"] == "unknown"


@pytest.mark.parametrize("change", [
    lambda v: v.update(version=2),
    lambda v: v.update(operations=["enumerate"]),
    lambda v: v.update(dimensions=["owner", "object"]),
])
def test_unknown_semantics_or_lost_dimensions_never_definite(change):
    data = artifact()
    change(data["snapshot"]["views"]["node_points"])
    assert ask(data, {"owner": 7, "object": 9})["status"] == "unsupported"


def test_display_summary_not_precise_native_top_pure_operator():
    data = artifact()
    view = data["snapshot"]["views"]["node_points"]
    view["fidelity"] = "display_summary"
    assert ask(data, {"owner": 7, "object": 9})["answer"] == "unknown"
    view.update(fidelity="native_summary", containment="finite_top_v1")
    view["rows"][0]["summary"] = "top"
    assert ask(data, {"owner": 7, "object": 99})["answer"] == "covered"
    view["containment"] = "unimplemented_domain"
    assert ask(data, {"owner": 7, "object": 99})["answer"] == "unknown"


def test_extra_advertised_context_is_not_a_v1_interpreter():
    data = artifact()
    data["snapshot"]["views"]["node_points"]["dimensions"].append("context")
    assert ask(data, {"owner": 7, "object": 9, "context": 100})["status"] == "unsupported"
    assert ask(data, {"owner": 7, "invented": 1})["status"] == "unsupported"


def test_budget_scan_and_byte_limits_do_not_prove_absence():
    data = artifact()
    result = ask(data, {"object": 99}, limits=QueryLimits(max_visited=2))
    assert result["answer"] == "unknown"
    data["run"]["many_metadata"] = {str(i): "x" * 100 for i in range(1000)}
    result = query(bundle(data), QueryRequest("summary", limits=QueryLimits(max_bytes=1000)))
    assert len(__import__("json").dumps(result, separators=(",", ":")).encode()) <= 1000
    assert result["answer"] == "unknown"
    with pytest.raises(InspectorError, match="envelope"):
        query(bundle(data), QueryRequest("summary", limits=QueryLimits(max_bytes=1)))


def test_pagination_bound_to_snapshot_query_and_view():
    data = artifact()
    request = QueryRequest("show", {"view": "node_points"}, limits=QueryLimits(max_rows=1))
    first = query(bundle(data), request)
    assert len(first["evidence"]) == 1 and first["cursor"]
    next_request = QueryRequest("show", request.args, limits=request.limits, cursor=first["cursor"])
    second = query(bundle(data), next_request)
    assert second["evidence"][0]["owner"] == 8
    other = artifact(snapshot_id="s2")
    with pytest.raises(InspectorError, match="cursor"):
        query(bundle(other), next_request)
    with pytest.raises(InspectorError, match="cursor"):
        query(bundle(data), QueryRequest("show", {"view": "node_points", "owner": 7},
                                        limits=request.limits, cursor=first["cursor"]))


def test_source_selector_and_same_name_id_isolation():
    data = artifact()
    data["snapshot"]["entities"][1]["label"] = "node:x"
    result = query(bundle(data), QueryRequest("show", {"label": "node:x"}))
    assert len(result["evidence"]) == 2
    result = query(bundle(data), QueryRequest("show", {"file": "subject.py", "line": 2}))
    assert result["evidence"][0]["id"] == 8


def test_trace_never_resurrects_state_or_links_same_numeric_entity():
    data = artifact()
    events = [{"kind": "work_start", "sequence": 7, "owner": 7, "object": 99}]
    captured = bundle(data, trace=events)
    assert query(captured, QueryRequest("events", {"kind": "work_start"}))["evidence"] == events
    assert query(captured, QueryRequest("events", {"owner": 7}))["status"] == "unsupported"
    assert query(captured, QueryRequest("show", {"view": "node_points", "contains": True,
        "owner": 7, "object": 99}))["answer"] == "absent_at_snapshot"


def test_reconstructed_support_is_or_rules_and_and_premises_not_history():
    result = query(bundle(), QueryRequest("explain", {"owner": 8, "object": 9}))
    assert result["answer"] == "reconstructed_support"
    assert result["generated_history"] == "unsupported"
    first = result["evidence"][0]
    assert first["operator"] == "OR"
    assert first["alternatives"][0]["premises"][0]["operator"] == "AND"
    assert result["global_why_not"] == "unsupported"


def test_diff_comparability_alignment_partial_deletions_and_id_reuse():
    left, right = artifact(), artifact("s2", "run2")
    right["snapshot"]["views"]["node_points"]["rows"].pop()
    request = QueryRequest("diff")
    result = diff_query(bundle(left), bundle(right), request)
    assert result["comparability"]["comparable"]
    # A v1 display descriptor never certified sufficient native identity.
    assert result["evidence"] == []
    assert result["alignment"]["matched"] == []
    assert result["comparison_coverage"]["left"]["unmatched_reference_rows"] == 2
    right["snapshot"]["views"]["node_points"]["coverage"]["registry"] = "partial"
    result = diff_query(bundle(left), bundle(right), request)
    assert result["evidence"] == []
    assert result["comparison_coverage"]["right_absence_coverage_complete"] is False
    right["run"]["configuration"]["context_policy"] = "2-cfa"
    assert diff_query(bundle(left), bundle(right), request)["answer"] == "incomparable"
    request = QueryRequest("diff", {"intended_changes": ["configuration"]})
    assert diff_query(bundle(left), bundle(right), request)["comparability"]["comparable"]


def test_ambiguous_origin_never_force_matches():
    left, right = artifact(), artifact("s2")
    duplicate = copy.deepcopy(left["snapshot"]["entities"][0])
    duplicate["id"] = 10
    left["snapshot"]["entities"].append(duplicate)
    result = diff_query(bundle(left), bundle(right), QueryRequest("diff"))
    # These are legacy descriptions. Their identity is unsupported before
    # candidate uniqueness is considered; new native controls cover ambiguity.
    assert 7 in result["alignment"]["unmatched_left"]
    assert 10 in result["alignment"]["unmatched_left"]
    assert not any(row["left"] in (7, 10) for row in result["alignment"]["matched"])


def test_actual_recorded_profile_without_double_count_claim():
    captured = bundle(trace=[{"kind": "work_step", "work_kind": "points_to", "sequence": 1}])
    result = query(captured, QueryRequest("profile"))
    assert result["observed_work_counts"] == {"points_to": 1}
    assert result["statistics"]["capture_seconds"] == .001
    assert result["downstream_attribution"] == "unsupported"


def test_unknown_optional_view_encoding_is_opaque():
    data = artifact()
    data["snapshot"]["views"]["future_domain"] = {"version": 2, "coverage": "new_encoding", "rows": 3}
    data["snapshot"]["views"]["ctx_points"] = {"version": 2, "coverage": "new_encoding", "rows": 3}
    result = query(bundle(data), QueryRequest("summary"))
    assert result["views"]["future_domain"]["known_semantics"] is False
    assert result["views"]["ctx_points"]["coverage"]["registry"] == "unsupported"
    assert query(bundle(data), QueryRequest("show", {"view": "ctx_points"}))["status"] == "unsupported"


def test_optional_unknown_event_descriptor_is_retained_without_crash():
    captured = bundle(trace=[{"kind": "future_event", "sequence": "future-id"}])
    result = query(captured, QueryRequest("events"))
    assert len(result["evidence"]) == 1
    assert query(captured, QueryRequest("events", {"sequence_min": 0}))["evidence"] == []


def test_native_summary_object_blocks_unsupported_negative_and_false_premise():
    data = artifact()
    data["snapshot"]["entities"][2]["attributes"]["native_summary"] = True
    assert ask(data, {"owner": 7, "object": 99})["answer"] == "unknown"
    result = query(bundle(data), QueryRequest("explain", {"owner": 8, "object": 99}))
    assert result["evidence"][0]["fact_represented"] is None
    assert result["evidence"][0]["alternatives"][0]["premises"][0]["represented"] is None


def test_admission_refuses_before_decode_with_too_little_work():
    class TooLarge:
        snapshot = type("Snapshot", (), {"encoded": b"x" * 20000})()
        @property
        def data(self):
            raise AssertionError("must not decode after admission refusal")
    with pytest.raises(InspectorError, match="admission"):
        query(TooLarge(), QueryRequest("summary", limits=QueryLimits(max_visited=1)))


def test_real_source_produces_all_seven_useful_offline_paths(tmp_path):
    from pythonstan_inspector.runner import run_source
    from pythonstan_inspector.storage import load_bundle, write_bundle
    source = tmp_path / "program.py"
    source.write_text("def identity(x):\n    return x\nresult = identity(1)\n")
    snapshot, trace, _ = run_source(source, trace=True)
    captured = load_bundle(write_bundle(snapshot, tmp_path / "bundle", trace=trace))
    data = captured.data["snapshot"]
    rows = data["views"]["node_points"]["rows"]
    target = next(row for row in rows if any(entity["id"] == row["owner"] and
        entity.get("attributes", {}).get("name") == "result" for entity in data["entities"]))
    assert query(captured, QueryRequest("summary"))["answer"] == "snapshot_summary"
    assert query(captured, QueryRequest("show", {"view": "node_points", "contains": True, **target}))["answer"] == "present_at_snapshot"
    assert query(captured, QueryRequest("events", {"kind": "work_step"}))["evidence"]
    assert query(captured, QueryRequest("explain", {"owner": target["owner"], "object": target["object"], "lane": target["lane"]}))["evidence"]
    assert query(captured, QueryRequest("profile"))["observed_work_counts"]
    result = query(captured, QueryRequest("check"))
    assert result["answer"] == "passed", result
    result = diff_query(captured, captured, QueryRequest("diff"))
    assert result["answer"] == "structural_diff"
    assert result["comparability"]["comparable"] and result["alignment"]["matched"]
    assert result["evidence"] == []
    call_rows = data["views"]["call_edges"]["rows"]
    assert call_rows
    answer = query(captured, QueryRequest("show", {"view": "call_edges", "contains": True, **call_rows[0]}))
    assert answer["answer"] == "present_at_snapshot"


@pytest.mark.parametrize("mutation", [
    lambda run: run.update(source_validation="changed_during_analysis"),
    lambda run: run.update(implementation_stable=False),
    lambda run: run["inputs"][0].update(sha256_after="different-consumed-source"),
])
def test_diff_content_instability_is_not_waived_by_matching_metadata(mutation):
    left, right = artifact(), artifact("s2")
    mutation(left["run"])
    mutation(right["run"])
    result = diff_query(bundle(left), bundle(right), QueryRequest("diff", {"intended_changes": ["inputs"]}))
    assert result["answer"] == "incomparable"
    assert result["comparability"]["invalid_identity_evidence"]["left"]


def test_old_cursor_rejected_before_unsupported_new_selector():
    captured = bundle()
    first = query(captured, QueryRequest("show", {"view": "node_points"}, limits=QueryLimits(max_rows=1)))
    with pytest.raises(InspectorError, match="cursor"):
        query(captured, QueryRequest("show", {"view": "unimplemented_view"}, cursor=first["cursor"]))


@pytest.mark.parametrize("coverage", ["partial", "not_captured", "opaque"])
def test_truncated_origin_prefix_is_not_a_complete_alignment_key(coverage):
    left, right = artifact(), artifact("s2")
    for data in (left, right):
        # No duplicate attrs marker: origin coverage independently governs its
        # own evidence, even when both clipped display prefixes are identical.
        entity = data["snapshot"]["entities"][0]
        entity["origin"].update(file="same-prefix" * 40, description_coverage=coverage)
    right["snapshot"]["views"]["node_points"]["rows"] = [
        row for row in right["snapshot"]["views"]["node_points"]["rows"] if row["owner"] != 7]
    result = diff_query(bundle(left), bundle(right), QueryRequest("diff"))
    assert result["comparability"]["comparable"] is True
    assert {"left": 7, "right": 7} not in result["alignment"]["matched"]
    assert 7 in result["alignment"]["unmatched_left"]
    assert not any(change.get("left_row", {}).get("owner") == 7 for change in result["evidence"])


@pytest.mark.parametrize("entity_id", [7, 13])
def test_legacy_missing_description_fidelity_cannot_force_cross_run_match(entity_id):
    left, right = artifact(), artifact("s2")
    for data in (left, right):
        entity = next(entity for entity in data["snapshot"]["entities"] if entity["id"] == entity_id)
        entity["attributes"].pop("description_coverage")
    # A legacy exact tuple is still a valid local structural witness. Only
    # the unsupported cross-run description-based alignment is degraded.
    assert ask(left, {"owner": 7, "object": 9})["answer"] == "present_at_snapshot"
    right["snapshot"]["views"]["node_points"]["rows"] = [
        row for row in right["snapshot"]["views"]["node_points"]["rows"] if row["owner"] != 7]
    result = diff_query(bundle(left), bundle(right), QueryRequest("diff"))
    assert result["comparability"]["comparable"] is True
    assert {"left": 7, "right": 7} not in result["alignment"]["matched"]
    assert 7 in result["alignment"]["unmatched_left"]
    assert not any(change.get("left_row", {}).get("owner") == 7 for change in result["evidence"])
