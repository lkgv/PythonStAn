"""Native identity controls and formal, sealed cross-snapshot mutations."""
import ast
import copy
import importlib.util
from pathlib import Path

import pytest

from pythonstan_inspector.adapter import _Projection
from pythonstan_inspector.contract import CaptureRequest, InspectorError, QueryLimits, QueryRequest, SealedSnapshot
from pythonstan_inspector.core import Budget, _alignment_key, diff_query, query, validate_parameters
from pythonstan_inspector.storage import load_bundle, write_bundle


def _fixture():
    path = Path(__file__).parents[1] / "inspector" / "fixtures.py"
    spec = importlib.util.spec_from_file_location("identity_v2_legacy_fixture", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _project(obj):
    projection = _Projection(CaptureRequest())
    local_id = projection.entity(obj)
    projection.finish_entities()
    registry = {entity["id"]: entity for entity in projection.entities}
    key = _alignment_key(registry[local_id], registry, Budget(QueryLimits(max_depth=32)))
    return local_id, registry, key


def _native():
    from pythonstan.analysis.pointer.kcfa.context import CallStringContext, Scope
    from pythonstan.analysis.pointer.kcfa.object import AllocKind, AllocSite, ModuleObject
    from pythonstan.ir.ir_statements import IRModule, IRCall
    context = CallStringContext((), 0)
    module = IRModule("subject", ast.parse("f()\n"), filename="subject.py")
    module_object = ModuleObject(context, AllocSite(module, AllocKind.MODULE), module)
    scope = Scope(module, module_object, context, None, None)
    statement = IRCall(ast.parse("f()\n").body[0].value)
    return context, scope, statement


def _sealed(tmp_path, data, name):
    return load_bundle(write_bundle(SealedSnapshot.seal(data), tmp_path / name))


def _source_snapshot(tmp_path):
    from pythonstan_inspector.runner import run_source
    source = tmp_path / "subject.py"
    source.write_text("def identity(x):\n    return x\nresult = identity(1)\n")
    snapshot, _, _ = run_source(source)
    return snapshot.data


def test_native_callsite_index_is_required_by_containing_ctx():
    from pythonstan.analysis.pointer.kcfa.context import CallSite, Ctx
    context, scope, statement = _native()
    left = Ctx(context, scope, CallSite(statement, "subject", 0))
    right = Ctx(context, scope, CallSite(statement, "subject", 1))
    assert left != right
    left_key, right_key = _project(left)[2], _project(right)[2]
    assert left_key is not None and right_key is not None
    assert left_key != right_key
    assert left_key == _project(Ctx(context, scope, CallSite(statement, "subject", 0)))[2]


@pytest.mark.parametrize("field", ["instance", "class", "scope_object"])
def test_bound_method_and_scope_preserve_native_identity_fields(field):
    from pythonstan.analysis.pointer.kcfa.context import Scope
    from pythonstan.analysis.pointer.kcfa.object import AllocKind, AllocSite, ClassObject, InstanceObject, MethodObject
    from pythonstan.ir.ir_statements import IRClass, IRFunc, IRCall
    context, module, _ = _native()
    cls_ast = ast.parse("class Example:\n    def im(self):\n        return 1\n").body[0]
    cls_ir = IRClass("subject.Example", cls_ast)
    other_ir = IRClass("subject.Other", ast.parse("\nclass Other:\n    pass\n").body[0])
    cls0 = ClassObject(context, AllocSite(cls_ir, AllocKind.CLASS), module, cls_ir)
    cls1 = ClassObject(context, AllocSite(other_ir, AllocKind.CLASS), module, other_ir)
    func = IRFunc("subject.Example.im", cls_ast.body[0], is_method=True)
    site0 = IRCall(ast.parse("Example()\n").body[0].value)
    site1 = IRCall(ast.parse("\nExample()\n").body[0].value)
    inst0 = InstanceObject(context, AllocSite(site0, AllocKind.INSTANCE), cls0)
    inst1 = InstanceObject(context, AllocSite(site1, AllocKind.INSTANCE), cls0)
    first = MethodObject(context, AllocSite(func, AllocKind.METHOD), module, func, cls0, inst0)
    second = MethodObject(context, first.alloc_site, module, func,
                          cls1 if field == "class" else cls0,
                          inst1 if field != "class" else inst0)
    if field == "scope_object":
        first = Scope(func, first, context, module, module)
        second = Scope(func, second, context, module, module)
    assert first != second
    left_key, right_key = _project(first)[2], _project(second)[2]
    assert left_key is not None and right_key is not None
    assert left_key != right_key


@pytest.mark.parametrize("change", ["receiver", "receiver_var"])
def test_builtin_bound_receiver_and_source_variable_are_explicit(change):
    from pythonstan.analysis.pointer.kcfa.object import AllocKind, AllocSite, ConstantObject, BuiltinMethodObject
    from pythonstan.analysis.pointer.kcfa.variable import Variable
    context, _, statement = _native()
    receiver0 = ConstantObject(context, AllocSite(statement, AllocKind.CONSTANT), 0)
    receiver1 = ConstantObject(context, receiver0.alloc_site, 1)
    site = AllocSite(statement, AllocKind.BUILTIN)
    left = BuiltinMethodObject(context, site, "append", receiver0, Variable("left"))
    right = BuiltinMethodObject(context, site, "append",
        receiver1 if change == "receiver" else receiver0,
        Variable("right") if change == "receiver_var" else Variable("left"))
    assert left != right
    assert _project(left)[2] is not None
    assert _project(left)[2] != _project(right)[2]
    local_id, registry, _ = _project(left)
    registry[local_id]["links"].pop("receiver")
    assert _alignment_key(registry[local_id], registry, Budget(QueryLimits(max_depth=32))) is None


def test_protocol_partition_and_nested_parameter_tuple_shape_are_required():
    from pythonstan.analysis.pointer.kcfa.context import CallSite, ParamContext
    from pythonstan.analysis.pointer.kcfa.object import AllocKind, AllocSite
    from pythonstan.analysis.pointer.kcfa.protocol_context import KeywordPartition, ProtocolKeyContext
    context, _, statement = _native()
    operation = CallSite(statement, "subject", 0)
    receiver = AllocSite(statement, AllocKind.DICT)
    left = ProtocolKeyContext(context, KeywordPartition(operation, receiver, "exact", "x"))
    right = ProtocolKeyContext(context, KeywordPartition(operation, receiver, "exact", "y"))
    assert _project(left)[2] is not None
    assert _project(left)[2] != _project(right)[2]
    one = ParamContext(((),), 2)
    two = ParamContext(((), ()), 2)
    assert _project(one)[2] is not None
    assert _project(one)[2] != _project(two)[2]


def test_actual_adapter_callsite_index_mutation_cannot_hide_in_empty_diff(tmp_path):
    left = _source_snapshot(tmp_path)
    right = copy.deepcopy(left)
    right["snapshot"].update(snapshot_id="independent-right", run_id="independent-run")
    right["run"]["run_id"] = "independent-run"
    entities = {entity["id"]: entity for entity in right["snapshot"]["entities"]}
    callsite = next(entity for entity in entities.values()
                    if entity["kind"] == "ctx" and entity["attributes"].get("content_kind") == "callsite")
    content = entities[callsite["links"]["content"]["ref"]]
    before = _alignment_key(callsite, entities, Budget(QueryLimits(max_depth=32)))
    assert before is not None
    content["attributes"]["index"] += 1
    after = _alignment_key(callsite, entities, Budget(QueryLimits(max_depth=32)))
    assert after is not None and after != before
    result = diff_query(_sealed(tmp_path, left, "left"), _sealed(tmp_path, right, "right"),
        QueryRequest("diff", {"view": "call_edges"}, limits=QueryLimits(max_depth=32)))
    assert result["comparability"]["comparable"]
    assert {"left": callsite["id"], "right": callsite["id"]} not in result["alignment"]["matched"]
    assert result["comparison_coverage"]["left"]["unmatched_reference_rows"] >= 1
    assert result["empty_diff_meaning"] == "no_difference_observed_in_compared_aligned_rows"


def test_actual_supported_alignment_detects_missing_row_and_reports_mixed_scope(tmp_path):
    left = _source_snapshot(tmp_path)
    right = copy.deepcopy(left)
    right["snapshot"]["snapshot_id"] = "right"
    registry = {entity["id"]: entity for entity in left["snapshot"]["entities"]}
    rows = right["snapshot"]["views"]["node_points"]["rows"]
    chosen = next(row for row in rows if registry[row["owner"]]["attributes"].get("name") == "result")
    rows.remove(chosen)
    result = diff_query(_sealed(tmp_path, left, "left"), _sealed(tmp_path, right, "right"),
        QueryRequest("diff", limits=QueryLimits(max_depth=32)))
    assert any(change.get("left_row") == chosen and change["classification"] == "absent_in_right_snapshot"
               for change in result["evidence"])
    scope = result["comparison_coverage"]
    assert scope["left"]["compared_rows"] > 0
    assert scope["left"]["unmatched_reference_rows"] > 0  # opaque translator temporaries
    assert scope["left"]["compared_owner_ids"]
    assert scope["left"]["scanned_rows"] == len(left["snapshot"]["views"]["node_points"]["rows"])


@pytest.mark.parametrize("degrade", ["legacy", "unfamiliar", "clipped", "missing", "cycle"])
def test_display_completeness_cannot_substitute_for_supported_identity(degrade):
    from pythonstan.analysis.pointer.kcfa.context import CallSite, Ctx
    context, scope, statement = _native()
    local_id, registry, key = _project(Ctx(context, scope, CallSite(statement, "subject", 0)))
    assert key is not None
    entity = registry[local_id]
    if degrade == "legacy":
        entity["attributes"].pop("identity_model")
    elif degrade == "unfamiliar":
        entity["attributes"]["identity_model"] = "future-model"
    elif degrade == "clipped":
        entity["attributes"]["description_coverage"] = "partial"
    elif degrade == "missing":
        entity["links"].pop("content")
    else:
        entity["links"]["content"]["ref"] = local_id
    assert _alignment_key(entity, registry, Budget(QueryLimits(max_depth=32))) is None


def test_legacy_raw_membership_survives_all_unmatched_diff(tmp_path):
    fixture = _fixture()
    left, right = fixture.artifact(), fixture.artifact("right")
    captured = _sealed(tmp_path, left, "left")
    assert query(captured, QueryRequest("show", {"view": "node_points", "contains": True,
        "owner": 7, "object": 9}))["answer"] == "present_at_snapshot"
    result = diff_query(captured, _sealed(tmp_path, right, "right"), QueryRequest("diff"))
    assert result["evidence"] == [] and result["alignment"]["matched"] == []
    assert result["comparison_coverage"]["left"]["aligned_rows"] == 0
    assert result["comparison_coverage"]["left"]["unmatched_reference_rows"] == 2


def test_budget_limited_comparison_counts_describe_only_the_scanned_scope():
    fixture = _fixture()
    captured = fixture.bundle()
    result = diff_query(captured, captured, QueryRequest("diff", limits=QueryLimits(max_visited=20)))
    assert result["query_complete"] is False
    assert result["answer"] == "unknown"
    for side in ("left", "right"):
        coverage = result["comparison_coverage"][side]
        assert coverage["count_scope"] == "scanned_captured_rows"
        assert coverage["scanned_rows"] + coverage["unscanned_rows"] == coverage["captured_rows"]
        assert coverage["compared_rows"] <= coverage["aligned_rows"] <= coverage["scanned_rows"]
        assert coverage["comparison_scan_complete"] is False


def test_actual_source_diamond_reports_shared_expansion_without_cycle(tmp_path):
    from pythonstan_inspector.runner import run_source
    source = tmp_path / "diamond.py"
    source.write_text("a = 1\nb = a\nc = a\nd = b\nd = c\n")
    snapshot, _, _ = run_source(source)
    captured = _sealed(tmp_path, snapshot.data, "diamond")
    entities = {entity["id"]: entity for entity in captured.data["snapshot"]["entities"]}
    target = next(row for row in captured.data["snapshot"]["views"]["node_points"]["rows"]
                  if entities[row["owner"]]["attributes"].get("name") == "d")
    result = query(captured, QueryRequest("explain", {"owner": target["owner"], "object": target["object"]}))
    assert result["query_complete"] is True
    assert any(row["kind"] == "revisited_reference" for row in result["evidence"])
    assert not any(row["kind"] == "cycle_reference" for row in result["evidence"])


def test_nonunique_supported_descriptions_stay_ambiguous(tmp_path):
    left = _source_snapshot(tmp_path)
    right = copy.deepcopy(left)
    right["snapshot"]["snapshot_id"] = "right"
    duplicate = copy.deepcopy(next(entity for entity in left["snapshot"]["entities"]
                                  if entity["kind"] == "ctx" and entity["attributes"].get("name") == "result"))
    original_id = duplicate["id"]
    duplicate["id"] = max(entity["id"] for entity in left["snapshot"]["entities"]) + 1
    left["snapshot"]["entities"].append(duplicate)
    result = diff_query(_sealed(tmp_path, left, "left"), _sealed(tmp_path, right, "right"),
        QueryRequest("diff", limits=QueryLimits(max_depth=32)))
    assert any(set(group["left"]) == {original_id, duplicate["id"]} for group in result["alignment"]["ambiguous"])
    assert not any(pair["left"] in (original_id, duplicate["id"]) for pair in result["alignment"]["matched"])
    owner = next(entity for entity in left["snapshot"]["entities"]
                 if entity["kind"] == "node" and entity["links"].get("variable", {}).get("ref") == original_id)
    assert not any(pair["left"] == owner["id"] for pair in result["alignment"]["matched"])


@pytest.mark.parametrize("kind,args", [
    ("summary", {"scope": "selected"}), ("profile", {"owner": 1}),
    ("show", {"contains": True}), ("show", {"view": "node_points", "file": "subject.py"}),
    ("events", {"owner": 1}), ("explain", {"owner": 1, "object": 2, "scope": "selected"}),
    ("diff", {"owner": 1}),
])
def test_all_query_kinds_reject_unimplemented_selection_without_substitution(kind, args):
    assert validate_parameters(QueryRequest(kind, args)) is not None


@pytest.mark.parametrize("kind,args", [
    ("show", {"view": "node_points", "owner": True}),
    ("show", {"view": "node_points", "contains": 1}),
    ("events", {"sequence_max": 2**63}), ("events", {"sequence_min": 3, "sequence_max": 2}),
    ("explain", {"owner": 1, "object": -1}),
    ("diff", {"intended_changes": ["inputs", "inputs"]}),
])
def test_parameter_types_ranges_and_conflicts_are_explicit(kind, args):
    with pytest.raises(InspectorError):
        validate_parameters(QueryRequest(kind, args))


def test_unknown_requested_claim_is_preserved_and_refused():
    fixture = _fixture()
    request = QueryRequest("show", {"view": "node_points"}, claim="all_python_soundness")
    result = query(fixture.bundle(), request)
    assert result["status"] == "unsupported"
    assert result["requested_claim"] == request.claim
    assert result["evidence"] == []


def test_named_checker_uses_same_closed_validator():
    request = QueryRequest("check", {"checker": "named", "owner": 999})
    assert validate_parameters(request, checker_parameters={"checker": str}) == "unsupported_checker_parameters"


_COLLECTOR_SOURCE = "def take(**kw):\n    return next(iter(kw.keys()))\nresult = take(x=1)\n"


def _model_snapshot(tmp_path):
    from pythonstan_inspector.runner import run_source
    source = tmp_path / "collector.py"
    source.write_text(_COLLECTOR_SOURCE)
    snapshot, _, _ = run_source(source, request=CaptureRequest(
        views=("node_points", "ctx_points", "pfg_edges", "call_edges", "kwargs_model")))
    return snapshot.data


def test_model_view_has_closed_typed_raw_enumeration_and_keeps_partial_rows(tmp_path):
    data = _model_snapshot(tmp_path)
    row = data["snapshot"]["views"]["kwargs_model"]["rows"][0]
    captured = _sealed(tmp_path, data, "model")
    request = QueryRequest("show", {"view": "kwargs_model", "invocation": row["invocation"]})
    result = query(captured, request)
    assert result["answer"] == "captured_rows" and result["evidence"] == [row]
    assert result["selection_scope"]["filters"] == {"invocation": row["invocation"]}
    assert query(captured, QueryRequest("show", {"view": "kwargs_model", "contains": True}))["status"] == "unsupported"
    assert query(captured, QueryRequest("show", {"view": "kwargs_model", "owner": row["owner"]}))["status"] == "unsupported"
    with pytest.raises(InspectorError):
        query(captured, QueryRequest("show", {"view": "kwargs_model", "invocation": True}))
    data["snapshot"]["views"]["kwargs_model"]["coverage"]["registry"] = "partial"
    partial = query(_sealed(tmp_path, data, "partial"), QueryRequest("show", {"view": "kwargs_model"}))
    assert partial["answer"] == "captured_rows" and partial["evidence"]
    data["snapshot"]["views"]["kwargs_model"]["rows"][0]["invocation"] = "1"
    malformed = query(_sealed(tmp_path, data, "malformed"), QueryRequest("show", {"view": "kwargs_model"}))
    assert malformed["status"] == "unsupported"
    assert "kwargs_model_row_encoding_unsupported" in malformed["diagnostics"]


def test_public_model_explanation_connects_admission_inputs_channels_and_consumer(tmp_path):
    data = _model_snapshot(tmp_path)
    request = QueryRequest("explain", {"model": "keyword_collector", "source_text": _COLLECTOR_SOURCE})
    result = query(_sealed(tmp_path, data, "repaired"), request)
    assert result["answer"] == "model_frontier"
    assert result["logic"] == {"candidates": "OR", "premises_within_candidate": "AND"}
    assert result["candidates"] and result["candidates"][0]["consumers"]
    assert result["candidates"][0]["frontier"] == "required_channels_represented"
    row = data["snapshot"]["views"]["kwargs_model"]["rows"][0]
    aggregate = next(channel for channel in row["collector"]["objects"][0]["channels"]
                     if channel["kind"] == "aggregate")["owner"]["ref"]
    points = data["snapshot"]["views"]["ctx_points"]
    points["rows"] = [fact for fact in points["rows"] if fact["owner"] != aggregate]
    broken = query(_sealed(tmp_path, data, "controlled-channel-mutation"), request)
    assert broken["candidates"][0]["frontier"] == "required_channel_missing"
    assert "no unique root cause" in broken["causal_claim"]
    assert validate_parameters(QueryRequest("explain", {**request.args, "owner": 999})) is not None


def _native_trace():
    ref = {"run_id": "run1", "epoch": 1, "ref": 7}
    descriptor = {"sequence": 1, "kind": "native_descriptor", "native_descriptor": {"ref": ref, "kind": "NormalNode"}}
    event = {"sequence": 2, "kind": "targeted_operation", "native_event": {
        "run_id": "run1", "epoch": 1, "sequence": 1, "kind": "collector_channel",
        "model": "keyword_collector", "source": {"file": "subject.py", "line": 3, "column": 9, "call_index": 0},
        "refs": {"input": ref}, "fields": {"channel": "aggregate"}}}
    return ref, [descriptor, event, {"sequence": 3, "kind": "work_step", "work_kind": "points_to"}]


def test_source_events_use_actual_wrapper_fields_and_explicit_snapshot_mapping_only():
    fixture = _fixture()
    ref, trace = _native_trace()
    request = QueryRequest("events", {"file": "subject.py", "line": 3, "column": 9, "model": "keyword_collector"})
    result = query(fixture.bundle(trace=trace), request)
    assert len(result["evidence"]) == 1 and result["evidence"][0]["kind"] == "targeted_operation"
    decode = result["coverage"]["native_reference_decode"]
    assert decode["descriptor_missing"] == 0
    assert decode["snapshot_mapping_uninterpreted"] == 1
    assert result["mapped_references"] == []  # run ref 7 never aliases local id 7
    data = fixture.artifact()
    data["snapshot"]["trace_reference_mapping"] = {
        "version": 1, "semantic_version": "native_weak_identity_to_snapshot_v1",
        "scope": "already_projected_exact_objects", "rows": [{"run_ref": ref, "local_id": 7}],
        "coverage": {"registry": "complete"}}
    mapped = query(fixture.bundle(data, trace=trace), request)
    assert mapped["coverage"]["native_reference_decode"]["snapshot_mapped"] == 1
    assert mapped["mapped_references"][0]["entity_ref"] == {
        "snapshot": {"run_id": "run1", "snapshot_id": "s1"}, "local_id": 7}
    assert query(fixture.bundle(data, trace=trace), QueryRequest("events", {**request.args, "kind": "work_step"}))["evidence"] == []


def test_event_descriptor_loss_mapping_miss_and_epoch_mismatch_are_separate():
    fixture = _fixture()
    ref, trace = _native_trace()
    data = fixture.artifact()
    data["snapshot"]["trace_reference_mapping"] = {
        "version": 1, "semantic_version": "native_weak_identity_to_snapshot_v1",
        "scope": "already_projected_exact_objects", "rows": [], "coverage": {"registry": "partial"}}
    result = query(fixture.bundle(data, trace=trace[1:]), QueryRequest("events", {"model": "keyword_collector"}))
    decode = result["coverage"]["native_reference_decode"]
    assert decode["descriptor_missing"] == decode["snapshot_mapping_missing"] == 1
    assert decode["snapshot_mapping_uninterpreted"] == 0
    trace[1]["native_event"]["epoch"] = 2
    mismatch = query(fixture.bundle(data, trace=trace), QueryRequest("events", {"model": "keyword_collector"}))
    assert mismatch["coverage"]["native_reference_decode"]["malformed_references"] == 1
    assert mismatch["mapped_references"] == []
    with pytest.raises(InspectorError):
        query(fixture.bundle(trace=trace), QueryRequest("events", {"line": True}))
