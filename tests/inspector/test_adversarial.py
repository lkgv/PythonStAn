"""Independent adversarial gates, including real-source adapter observations.

Protocol-only fixtures below are deliberately distinct from real engine tests.
They falsify overclaims; passing them is not a proof of Python soundness.
"""
from contextlib import redirect_stdout
from io import StringIO
import json

import pytest

from pythonstan_inspector.adapter import capture
from pythonstan_inspector.contract import (
    CaptureRequest, InspectorError, QueryLimits, QueryRequest, SealedSnapshot,
    canonical_bytes,
)
from pythonstan_inspector.core import query
from pythonstan_inspector.storage import load_bundle, write_bundle


def _source_solver(tmp_path, source, max_iterations=10000):
    """Use the original source frontend/solver, not a DTO-derived oracle."""
    from pythonstan.analysis.pointer.kcfa.config import Config
    from pythonstan.world import World
    from pythonstan.world.pipeline import Pipeline
    path = tmp_path / "adversarial.py"
    path.write_text(source)
    World.setup()
    with redirect_stdout(StringIO()):
        pipeline = Pipeline(config={"filename": str(path), "project_path": str(tmp_path),
            "library_paths": [], "import_level": 0, "mock_libs": False, "time_count": False,
            "analysis": [{"name": "pta", "id": "PointerAnalysis", "prev_analysis": ["ir"],
                "options": Config(max_iterations=max_iterations, log_level="ERROR",
                    enable_ai_intrinsics=False, enable_synthetic_method_contexts=False,
                    work_scheduling_policy="fair").to_dict()}]})
        driver = pipeline.analysis_manager.get_analyzer("pta")
        actual = getattr(driver, "driver", driver)
        actual.solver.enable_inspector_boundaries()
        pipeline.run()
    return actual.solver


def _fixture():
    return {"schema": {"major": 1, "minor": 0}, "run": {"stop_reason": "timeout"},
        "snapshot": {"run_id": "r", "snapshot_id": "s", "scope": "all_current_state",
            "boundary": {"coherent": True, "stage": "stopped"},
            "entities": [{"id": 7, "kind": "node", "label": "same", "origin": {},
                          "attributes": {}, "links": {}},
                         {"id": 8, "kind": "object", "label": "same", "origin": {},
                          "attributes": {"native_summary": False}, "links": {}}],
            "views": {"node_points": {"version": 1, "fidelity": "exact",
                "dimensions": ["owner", "object", "lane"], "operations": ["enumerate", "contains"],
                "coverage": {"registry": "complete", "owners": {"7": {"fields": {"facts": "complete"}}}},
                "rows": [{"owner": 7, "object": 8, "lane": "objects"}]}}}}


def _bundle(tmp_path, data):
    write_bundle(SealedSnapshot.seal(data), tmp_path)
    return load_bundle(tmp_path)


def _contains(bundle, **parameters):
    return query(bundle, QueryRequest("show", {"view": "node_points", "contains": True, **parameters}))


@pytest.mark.parametrize("mutation", [
    lambda v: v.update(version=2),
    lambda v: v.update(semantic_version="unknown_future_native_semantics"),
    lambda v: v.update(fidelity="display_summary"),
    lambda v: v.update(operations=["enumerate"]),
])
def test_protocol_unknown_semantics_cannot_decide_membership(tmp_path, mutation):
    data = _fixture()
    mutation(data["snapshot"]["views"]["node_points"])
    bundle = _bundle(tmp_path, data)
    assert _contains(bundle, owner=7, object=8)["answer"] not in (
        "present_at_snapshot", "absent_at_snapshot")
    assert _contains(bundle, owner=7, object=7)["answer"] != "absent_at_snapshot"


def test_protocol_partial_exact_positive_is_only_existence(tmp_path):
    data = _fixture()
    view = data["snapshot"]["views"]["node_points"]
    view["coverage"] = {"registry": "partial", "owners": {"7": {"fields": {"facts": "partial"}}}}
    bundle = _bundle(tmp_path, data)
    yes = _contains(bundle, owner=7, object=8)
    assert yes["answer"] == "present_at_snapshot"
    assert yes["concrete_reachability_proved"] is False
    assert _contains(bundle, owner=7, object=7)["answer"] == "unknown"


def test_protocol_ownerless_partial_field_cannot_support_absence(tmp_path):
    data = _fixture()
    data["snapshot"]["views"]["node_points"]["coverage"]["owners"]["7"]["fields"]["facts"] = "partial"
    assert _contains(_bundle(tmp_path, data), object=7)["answer"] == "unknown"


def test_protocol_context_is_not_invented_from_declared_extra_dimension(tmp_path):
    data = _fixture()
    # A future/incorrect producer claiming a dimension does not install a new
    # offline interpreter. v1 has no materialized context column in facts.
    data["snapshot"]["views"]["node_points"]["dimensions"].append("context")
    bundle = _bundle(tmp_path, data)
    answer = _contains(bundle, owner=7, object=8, context=8)
    assert answer["answer"] not in ("present_at_snapshot", "absent_at_snapshot")


def test_protocol_unsupported_selector_cannot_turn_into_absence(tmp_path):
    answer = _contains(_bundle(tmp_path, _fixture()), wholly_unknown_selector=8)
    assert answer["status"] == "unsupported"
    assert answer["answer"] == "unknown"


def test_protocol_timeout_and_query_truncation_remain_separate(tmp_path):
    bundle = _bundle(tmp_path, _fixture())
    assert _contains(bundle, owner=7, object=7)["answer"] == "absent_at_snapshot"
    data = _fixture()
    data["snapshot"]["views"]["node_points"]["rows"] *= 20
    bundle = _bundle(tmp_path / "many", data)
    limited = query(bundle, QueryRequest("show", {"view": "node_points", "contains": True,
        "owner": 7, "object": 7}, limits=QueryLimits(max_visited=2)))
    assert limited["answer"] == "unknown"
    assert limited.get("query_complete") is False


def test_protocol_cursor_is_scoped_even_when_local_ids_repeat(tmp_path):
    data = _fixture()
    data["snapshot"]["views"]["node_points"]["rows"] *= 3
    left = _bundle(tmp_path / "left", data)
    request = QueryRequest("show", {"view": "node_points"}, limits=QueryLimits(max_rows=1))
    answer = query(left, request)
    assert answer["cursor"]
    data["snapshot"]["snapshot_id"] = "different"
    right = _bundle(tmp_path / "right", data)
    with pytest.raises(InspectorError, match="cursor"):
        query(right, QueryRequest("show", request.args, limits=request.limits, cursor=answer["cursor"]))
    with pytest.raises(InspectorError, match="cursor"):
        query(left, QueryRequest("show", {"view": "node_points", "owner": 7},
                                 limits=request.limits, cursor=answer["cursor"]))


def test_protocol_full_response_bytes_are_bounded_or_refused(tmp_path):
    data = _fixture()
    data["run"]["large_but_valid_optional_note"] = "x" * 100000
    bundle = _bundle(tmp_path, data)
    try:
        answer = query(bundle, QueryRequest("summary", limits=QueryLimits(max_bytes=4096)))
    except InspectorError:
        return
    assert len(canonical_bytes(answer)) <= 4096


def test_real_source_same_named_definitions_do_not_merge(tmp_path):
    solver = _source_solver(tmp_path, "def repeat():\n return 1\nfirst = repeat\n"
        "def repeat():\n return 2\nsecond = repeat\nfirst()\nsecond()\n")
    data = capture(solver).data["snapshot"]
    definitions = [entity for entity in data["entities"] if entity["kind"] == "object"
                   and entity["attributes"].get("engine_class") == "FunctionObject"
                   and entity["origin"].get("name") == "repeat"]
    assert len(definitions) == 2
    assert definitions[0]["id"] != definitions[1]["id"]
    assert {entity["origin"]["line"] for entity in definitions} == {1, 4}


def test_real_source_large_python_integer_can_be_saved_without_misdescribing_value(tmp_path):
    solver = _source_solver(tmp_path, "large = 1208925819614629174706176\n")
    snapshot = capture(solver)
    write_bundle(snapshot, tmp_path / "bundle")
    bundle = load_bundle(tmp_path / "bundle")
    constants = [e for e in bundle.data["snapshot"]["entities"]
                 if e["attributes"].get("engine_class") == "ConstantObject"]
    assert constants
    large = [e for e in constants if e["attributes"].get("value_coverage") == "opaque"]
    assert large
    assert all(e["attributes"].get("value") is None for e in large)


def test_real_source_actual_stop_reason_overrides_metadata_claim(tmp_path):
    solver = _source_solver(tmp_path, "a = 1\nb = 2\nc = a\nd = b\n", max_iterations=1)
    assert solver._stats["iteration_limit_reached"] == 1
    snapshot = capture(solver, run_metadata={"stop_reason": "completed"})
    assert snapshot.data["run"]["stop_reason"] == "iteration_limit"


def test_real_adapter_entity_budget_preserves_explicit_boundary_links(tmp_path):
    solver = _source_solver(tmp_path, "source = 1\ntarget = source\n")
    snapshot = capture(solver, CaptureRequest(max_entities=1, max_rows=1))
    bundle = _bundle(tmp_path / "bundle", snapshot.data)
    data = bundle.data["snapshot"]
    assert len(data["entities"]) == 1
    assert data["omissions"]
    assert any(link["status"] == "omitted_by_capture" for entity in data["entities"]
               for link in entity["links"].values())
    assert any(view["coverage"]["registry"] == "partial" for view in data["views"].values())


def test_real_adapter_native_missing_reference_is_not_silently_complete(tmp_path):
    from pythonstan.analysis.pointer.kcfa import points_to_set
    solver = _source_solver(tmp_path, "source = 1\n")
    # A controlled stable bad engine: the decoder registry itself has lost an
    # object while facts retain its bit. This is not a mutated JSON example.
    points_to_set._global_table._id_to_obj.clear()
    snapshot = capture(solver)
    write_bundle(snapshot, tmp_path / "bundle")
    data = load_bundle(tmp_path / "bundle").data["snapshot"]
    assert any(row["reason"] == "missing_in_engine_object_table" for row in data["omissions"])
    assert all(view["coverage"]["registry"] == "partial" for name, view in data["views"].items()
               if name in ("node_points", "ctx_points"))


def test_real_source_none_returning_container_operation_keeps_heap_effect(tmp_path):
    solver = _source_solver(tmp_path, "items = []\nreturned = items.append(42)\n")
    snapshot = capture(solver)
    bundle = _bundle(tmp_path / "bundle", snapshot.data)
    data = bundle.data["snapshot"]
    entities = {row["id"]: row for row in data["entities"]}
    fields = {key for key, value in entities.items()
              if value["kind"] == "ctx" and value["attributes"].get("content_kind") == "field"}
    assert any(value["kind"] == "ctx" and value["label"] == "returned"
               for value in entities.values())
    effect = next(row for row in data["views"]["ctx_points"]["rows"]
                  if row["owner"] in fields and entities[row["object"]]["attributes"].get("value") == 42)
    answer = query(bundle, QueryRequest("show", {"view": "ctx_points", "contains": True, **effect}))
    assert answer["answer"] == "present_at_snapshot"
    # This gate asserts separate heap evidence, not engine precision for None.
    assert entities[effect["owner"]]["attributes"]["field_kind"] == "elem"


def test_real_interruption_propagates_and_does_not_publish_a_safe_boundary(monkeypatch):
    from benchmark.inspector_v1.measure import finite_arena
    solver, _ = finite_arena(2)
    solver.enable_inspector_boundaries()

    def interrupted(*args):
        raise KeyboardInterrupt("controlled interruption")

    monkeypatch.setattr(solver, "_apply_static", interrupted)
    with pytest.raises(KeyboardInterrupt):
        next(solver)
    with pytest.raises(InspectorError, match="unsafe_boundary"):
        capture(solver)


def test_real_bad_negative_bitmask_is_captured_as_partial_not_replayed(tmp_path):
    from pythonstan.analysis.pointer.kcfa.points_to_set import PointsToSet
    from pythonstan.analysis.pointer.kcfa.pointer_flow_graph import PointerFlowNode
    solver = _source_solver(tmp_path, "source = 1\n")
    owner = next(key for key in solver.state._env if isinstance(key, PointerFlowNode))
    solver.state._env[owner] = PointsToSet(-1, 0, 0)
    before = solver._iteration
    snapshot = capture(solver)
    write_bundle(snapshot, tmp_path / "bundle")
    assert solver._iteration == before
    view = snapshot.data["snapshot"]["views"]["node_points"]
    assert view["coverage"]["registry"] == "partial"
    assert any(row["reason"] == "invalid_engine_bitmask" for row in snapshot.data["snapshot"]["omissions"])


def test_protocol_diff_singleton_same_name_different_contexts_are_not_forced_matched(tmp_path):
    from pythonstan_inspector.core import diff_query
    left = _fixture()
    left["run"].update(implementation_sha256="same", inputs=[], configuration={},
        entry_points=[], dependency_policy="no_imports", models="same", cache="same")
    snap = left["snapshot"]
    snap["entities"][0].update(kind="ctx", origin={"file": "x.py", "qualname": "f"},
        attributes={"engine_class": "Ctx", "name": "x"},
        links={"context": {"ref": 9, "status": "resolved"}})
    snap["entities"][1]["origin"] = {"file": "x.py", "line": 1}
    snap["entities"].extend([
        {"id": 9, "kind": "context", "label": "CallStringContext", "origin": {},
         "attributes": {"engine_class": "CallStringContext", "k": 1},
         "links": {"call_sites:0": {"ref": 10, "status": "resolved"}}},
        {"id": 10, "kind": "origin", "label": "CallSite", "origin": {"file": "x.py", "line": 10},
         "attributes": {"engine_class": "CallSite"}, "links": {}}])
    right = json.loads(json.dumps(left))
    right["snapshot"]["snapshot_id"] = "right"
    right["snapshot"]["entities"][-1]["origin"]["line"] = 20
    right["snapshot"]["views"]["node_points"]["rows"] = []
    answer = diff_query(_bundle(tmp_path / "left", left), _bundle(tmp_path / "right", right),
                        QueryRequest("diff", {"view": "node_points"}))
    assert answer["comparability"]["comparable"] is True
    assert {"left": 7, "right": 7} not in answer["alignment"]["matched"]
    assert not answer["evidence"]


def test_protocol_explain_partial_source_does_not_invent_a_false_premise(tmp_path):
    data = _fixture()
    snap = data["snapshot"]
    snap["entities"][0]["attributes"] = {"engine_class": "NormalNode", "node_kind": "normal"}
    snap["entities"].append({"id": 9, "kind": "node", "label": "source", "origin": {},
        "attributes": {"engine_class": "NormalNode", "node_kind": "normal"}, "links": {}})
    snap["views"]["node_points"]["coverage"] = {"registry": "partial", "owners": {
        "7": {"fields": {"facts": "complete"}}, "9": {"fields": {"facts": "partial"}}}}
    snap["views"]["pfg_edges"] = {"version": 1, "fidelity": "exact",
        "dimensions": ["source", "target", "edge_kind"], "operations": ["enumerate"],
        "coverage": {"registry": "complete", "owners": {}},
        "rows": [{"source": 9, "target": 7, "edge_kind": "normal", "identity_copy": True}]}
    answer = query(_bundle(tmp_path, data), QueryRequest("explain", {
        "owner": 7, "object": 8, "candidate_source": 9}))
    premise = answer["evidence"][0]["alternatives"][0]["premises"][0]
    if "represented" in premise:
        assert premise["represented"] is None
    else:
        assert premise.get("membership") == "unknown"
    assert answer["generated_history"] == "unsupported"


def test_protocol_native_summary_in_exact_storage_blocks_concrete_negative(tmp_path):
    data = _fixture()
    snap = data["snapshot"]
    snap["entities"][1]["attributes"]["native_summary"] = True
    snap["entities"].append({"id": 10, "kind": "object", "label": "concrete target", "origin": {},
        "attributes": {"native_summary": False}, "links": {}})
    snap["views"]["pfg_edges"] = {"version": 1, "fidelity": "exact",
        "dimensions": ["source", "target", "edge_kind"], "operations": ["enumerate"],
        "coverage": {"registry": "complete", "owners": {}}, "rows": []}
    bundle = _bundle(tmp_path, data)
    assert _contains(bundle, owner=7, object=8)["answer"] == "present_at_snapshot"
    assert _contains(bundle, owner=7, object=10)["answer"] in ("unknown", "covered")
    explained = query(bundle, QueryRequest("explain", {"owner": 7, "object": 10}))
    assert explained["evidence"][0]["fact_represented"] is None


def test_protocol_optional_future_trace_record_does_not_crash_reader_query(tmp_path):
    write_bundle(SealedSnapshot.seal(_fixture()), tmp_path, trace=[{
        "kind": "future_event", "sequence": "future-id", "future_payload": {"version": 2}}])
    answer = query(load_bundle(tmp_path), QueryRequest("events"))
    assert answer["answer"] != "absent_at_snapshot"
    assert answer["query"] == "events"


def test_real_solver_diagnostic_encoding_failure_degrades_observation_only(monkeypatch):
    from benchmark.inspector_v1.measure import finite_arena
    from pythonstan_inspector import trace
    solver, _ = finite_arena(2)
    recorder = trace.TraceRecorder()

    def broken_encoder(_event):
        raise ValueError("controlled diagnostic encoding failure")

    monkeypatch.setattr(trace, "canonical_bytes", broken_encoder)
    solver.set_work_step_observer(recorder.observe_step)
    solver.solve_to_fixpoint()
    assert solver._stats["converged"] == 1
    assert recorder.health["status"] == "degraded"


def test_protocol_direct_checker_respects_full_response_limit(tmp_path):
    from pythonstan_inspector.checker import check
    data = _fixture()
    snap = data["snapshot"]
    snap["entities"][0]["attributes"] = {"engine_class": "NormalNode", "node_kind": "normal"}
    snap["entities"][1]["attributes"]["native_summary"] = False
    snap["views"]["node_points"]["coverage"]["optional_note"] = "x" * 100000
    snap["views"]["pfg_edges"] = {"version": 1, "fidelity": "exact",
        "dimensions": ["source", "target", "edge_kind"], "operations": ["enumerate"],
        "coverage": {"registry": "complete", "owners": {}},
        "rows": [{"source": 7, "target": 7, "edge_kind": "normal", "identity_copy": True}]}
    try:
        answer = check(_bundle(tmp_path, data), QueryRequest("check", limits=QueryLimits(max_bytes=4096)))
    except InspectorError:
        return
    assert len(canonical_bytes(answer)) <= 4096
