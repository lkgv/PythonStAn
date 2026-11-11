"""Real source/model observations, with separate queued and stored evidence.

Only the fixed trusted finite strings are executed by CPython. The recording
boundary test monkeypatches the real port solely to inspect operation ordering;
production runs otherwise use the native port, real adapter and sealed bundle.
"""
from contextlib import redirect_stdout
import hashlib
from io import StringIO
import uuid

import pytest

from pythonstan.analysis.pointer.kcfa.config import Config
from pythonstan.world import World
from pythonstan.world.pipeline import Pipeline
from pythonstan_inspector.adapter import capture
from pythonstan_inspector.contract import CaptureRequest, QueryRequest
from pythonstan_inspector.core import query
from pythonstan_inspector.storage import load_bundle, write_bundle
from pythonstan_inspector.trace import TraceRecorder
from tests.round12_semantics.test_continuations import edges, event_values
from tests.round14_formal.test_key_context_model import _result


F1_SOURCE = """def take(**kw):
    return next(iter(kw.keys()))
result = take(x=1)
"""
CALLBACK_SOURCE = """events = []
def chosen():
    events.append('chosen')
    return 7
def poison():
    events.append('poison')
    return 99
def forward(**kwargs):
    def names():
        yield from kwargs.keys()
    return kwargs[next(names())]()
result = forward(callback=chosen)
"""
_VIEWS = ("node_points", "ctx_points", "pfg_edges", "call_edges", "kwargs_model")


def _run(path, source, *, mode="targeted", selector_line=None, before_run=None):
    path.mkdir()
    filename = path / "subject.py"
    filename.write_text(source)
    digest = hashlib.sha256(source.encode()).hexdigest()
    run_id = uuid.uuid4().hex
    World.setup()
    config = Config(context_policy="0-cfa", keyword_context_model="none",
        max_iterations=50000, work_scheduling_policy="fair", log_level="ERROR",
        enable_ai_intrinsics=False, enable_synthetic_method_contexts=False)
    with redirect_stdout(StringIO()):
        pipeline = Pipeline(config={"filename": str(filename), "project_path": str(path),
            "library_paths": [], "import_level": 0, "mock_libs": False, "time_count": False,
            "analysis": [{"name": "pta", "id": "PointerAnalysis", "prev_analysis": ["ir"],
                          "options": config.to_dict()}]})
        driver = pipeline.analysis_manager.get_analyzer("pta")
        solver = getattr(driver, "driver", driver).solver
        solver.enable_inspector_boundaries()
        if mode != "off":
            selector = ({"file": str(filename), "line": selector_line or len(source.splitlines()),
                         "column": 9, "model": "keyword_collector"}
                        if mode == "targeted" else None)
            solver.enable_inspector_diagnostics(mode, run_id, selector=selector)
        if before_run is not None:
            before_run(solver)
        pipeline.run()
    assert solver._stats["converged"] == 1 and solver._stats["pending_work"] == 0
    batch = solver.drain_inspector_events() if mode != "off" else None
    recorder = TraceRecorder() if batch is not None else None
    if recorder is not None:
        recorder.record_native_batch(batch)
        assert recorder.health["native_batch_failures"] == 0
    snapshot = capture(solver, CaptureRequest(views=_VIEWS), run_metadata={
        "run_id": run_id, "configuration": config.to_dict(), "source_sha256": digest,
        "source_validation": "pre_post_verified", "inputs": [{"path": str(filename),
            "sha256_before": digest, "sha256_after": hashlib.sha256(filename.read_bytes()).hexdigest()}]})
    bundle = load_bundle(write_bundle(snapshot, path / "bundle",
        trace=recorder.events if recorder is not None else None))
    return bundle, solver, batch


def _ref_key(reference):
    return reference["run_id"], reference["epoch"], reference["ref"]


def _assert_exact_binding(bundle, batch):
    data = bundle.data["snapshot"]
    mapping = data["trace_reference_mapping"]
    assert mapping["semantic_version"] == "native_weak_identity_to_snapshot_v1"
    assert mapping["coverage"]["registry"] == "complete"
    refs = {_ref_key(row["run_ref"]): row["local_id"] for row in mapping["rows"]}
    binding = next(event for event in batch["events"] if event["kind"] == "collector_binding")
    parameter = refs[_ref_key(binding["refs"]["collector_parameter"])]
    collector = refs[_ref_key(binding["refs"]["collector_object"])]
    assert any(row["owner"] == parameter and row["object"] == collector
               for row in data["views"]["ctx_points"]["rows"])
    assert any(row["collector"]["parameter"]["ref"] == parameter
               and collector in {item["object"] for item in row["collector"]["objects"]}
               for row in data["views"]["kwargs_model"]["rows"])
    return refs


@pytest.mark.parametrize("consumer, expected", [("keys", "x"), ("values", 1)])
def test_original_f1_has_actual_source_events_and_snapshot_binding(tmp_path, consumer, expected):
    source = F1_SOURCE.replace("kw.keys()", "kw." + consumer + "()")
    concrete = {}
    exec(source, concrete)
    assert concrete["result"] == expected
    off, _, _ = _run(tmp_path / "off", source, mode="off")
    bundle, _, batch = _run(tmp_path / "targeted", source)
    assert _result(off.data["snapshot"]) == _result(bundle.data["snapshot"]) == {expected}
    assert batch["health"]["failures"] == 0
    assert batch["health"]["dropped_events"] == batch["health"]["dropped_descriptors"] == 0
    events = batch["events"]
    assert {"model_enter", "collector_binding", "dependency_registration_returned", "publication_requested",
            "model_complete", "output_delta"} <= {event["kind"] for event in events}
    assert all(event["source"] == {"file": str(tmp_path / "targeted" / "subject.py"),
        "line": 3, "column": 9, "call_index": 0} for event in events)
    assert {event["fields"]["channel"] for event in events if event["kind"] == "dependency_registration_returned"} == {"key", "aggregate"}
    assert all(event["fields"]["stage"] == "publication_requested"
               for event in events if event["kind"] == "collector_binding")
    assert all(event["fields"]["channel"] == "native_keys"
               for event in events if event["kind"] == "publication_requested")
    _assert_exact_binding(bundle, batch)
    recorder = TraceRecorder()
    recorder.record_native_batch(batch)
    assert recorder.health["native_batch_failures"] == 0
    assert recorder.health["native_operation_frames"] == len(events)
    result = query(bundle, QueryRequest("events", {"file": str(tmp_path / "targeted" / "subject.py"),
        "line": 3, "model": "keyword_collector"}))
    assert result["answer"] == "observed_events" and result["evidence"]
    decoded = result["coverage"]["native_reference_decode"]
    assert decoded["descriptor_missing"] == decoded["snapshot_mapping_conflicts"] == 0
    assert decoded["snapshot_mapped"] > 0


def test_callback_keeps_return_heap_effect_and_source_selected_trace(tmp_path):
    concrete = {}
    exec(CALLBACK_SOURCE, concrete)
    assert concrete["result"] == 7 and concrete["events"] == ["chosen"]
    off, _, _ = _run(tmp_path / "off", CALLBACK_SOURCE, mode="off")
    bundle, _, batch = _run(tmp_path / "targeted", CALLBACK_SOURCE)
    for data in (off.data["snapshot"], bundle.data["snapshot"]):
        assert _result(data) == {7}
        assert ("forward", "chosen") in edges(data)
        assert ("forward", "poison") not in edges(data)
        assert event_values(data) == {"chosen"}
    assert batch["events"] and batch["health"]["failures"] == 0
    assert all(event["source"]["line"] == len(CALLBACK_SOURCE.splitlines()) for event in batch["events"])
    assert {event["fields"]["keyword"] for event in batch["events"]
            if event["kind"] == "publication_requested"} == {"callback"}
    _assert_exact_binding(bundle, batch)


@pytest.mark.parametrize("mode", ["counters", "targeted"])
def test_unselected_mode_never_constructs_native_descriptors(tmp_path, monkeypatch, mode):
    from pythonstan.analysis.pointer.kcfa import diagnostics

    def forbidden(*args, **kwargs):
        raise AssertionError("unselected source reached native event construction")

    monkeypatch.setattr(diagnostics, "_source", forbidden)
    monkeypatch.setattr(diagnostics.NativeDiagnostics, "_reference", forbidden)
    bundle, _, batch = _run(tmp_path / mode, F1_SOURCE, mode=mode, selector_line=4)
    assert _result(bundle.data["snapshot"]) == {"x"}
    assert batch["events"] == batch["descriptors"] == []
    assert batch["health"]["reference_count"] == batch["health"]["failures"] == 0


def test_expanded_registration_is_a_constraint_and_stored_deltas_are_separate(tmp_path):
    source = """def take(*, consumed, **kw):
    return next(iter(kw.keys()))
result = take(**{'consumed': 'poison', 'x': 1})
"""
    bundle, _, batch = _run(tmp_path / "expanded", source)
    assert _result(bundle.data["snapshot"]) == {"x"}
    registrations = [event for event in batch["events"] if event["kind"] == "dependency_registration_returned"]
    assert registrations and all(event["fields"] == {"channel": "expanded_mapping_copy",
        "registration_kind": "mapping_constraint"} for event in registrations)
    assert any(event["kind"] == "output_delta" and event["fields"]["owner_role"] == "collector_field"
               for event in batch["events"])
    _assert_exact_binding(bundle, batch)


@pytest.mark.parametrize("expanded", [False, True])
def test_event_sites_follow_registration_and_precede_actual_queued_store(tmp_path, monkeypatch, expanded):
    from pythonstan.analysis.pointer.kcfa.constraints import MappingCopyConstraint
    from pythonstan.analysis.pointer.kcfa.diagnostics import NativeDiagnostics
    from pythonstan.analysis.pointer.kcfa.pointer_flow_graph import NormalNode
    original = NativeDiagnostics.record
    state = {}
    observed = set()

    def record(port, kind, scope, call, model, *, refs=None, fields=None):
        solver = state["solver"]

        def pending(owner, obj):
            return any(isinstance(node, NormalNode) and node.var is owner and obj in box.val
                       for _, node, box in solver.state._worklist.items_list)

        if kind == "collector_binding":
            if refs["collector_object"] not in solver.state.get_points_to(refs["collector_parameter"]):
                assert pending(refs["collector_parameter"], refs["collector_object"])
                observed.add("binding_requested_before_store")
        elif kind == "dependency_registration_returned":
            if fields["registration_kind"] == "mapping_constraint":
                assert any(isinstance(constraint, MappingCopyConstraint)
                    and constraint.source is refs["source_mapping"]
                    and constraint.target_object is refs["collector_object"]
                    for _, constraint in solver.state._constraints._constraints)
            else:
                assert any(isinstance(edge.source, NormalNode) and isinstance(edge.target, NormalNode)
                    and edge.source.var is refs["source"] and edge.target.var is refs["target"]
                    for edge in solver.state._pointer_flow_graph.edges)
            observed.add("registered_before_event")
        elif kind == "publication_requested":
            if refs["object"] not in solver.state.get_points_to(refs["target"]):
                assert pending(refs["target"], refs["object"])
                observed.add("key_requested_before_store")
        return original(port, kind, scope, call, model, refs=refs, fields=fields)

    monkeypatch.setattr(NativeDiagnostics, "record", record)
    source = F1_SOURCE.replace("take(x=1)", "take(**{'x': 1})") if expanded else F1_SOURCE
    bundle, _, batch = _run(tmp_path / "ordering", source, before_run=lambda solver: state.update(solver=solver))
    assert _result(bundle.data["snapshot"]) == {"x"}
    assert {"binding_requested_before_store", "registered_before_event"} <= observed
    if not expanded:
        assert "key_requested_before_store" in observed
    assert batch["health"]["failures"] == 0


def test_completed_matching_replay_requests_publication_without_a_new_delta(tmp_path):
    from pythonstan.analysis.pointer.kcfa.constraints import CallConstraint
    from pythonstan.analysis.pointer.kcfa.processor.normal_call import NormalCallProcessor

    _, solver, initial = _run(tmp_path / "replay", F1_SOURCE)
    assert any(event["kind"] == "output_delta" for event in initial["events"])
    edge = next(edge for edge in solver.state._call_graph.edges
                if edge.callee.stmt.name == "take")
    scope, call = next((scope, constraint) for scope, constraint in solver.state._constraints._constraints
        if isinstance(constraint, CallConstraint) and scope == edge.callsite.scope
        and constraint.call_site == edge.callsite.content)
    before = {(owner, points.objs_mask, points.classmethods_mask, points.instancemethods_mask)
              for owner, points in solver.state._env.items()}
    # Controlled replay of an actual admitted operation, using its unchanged
    # production objects. This is not a second concrete source invocation.
    NormalCallProcessor()._match_parameters(solver, edge.callee.obj, call, scope,
        edge.callee, scope.context, edge.callee.context)
    solver.solve_to_fixpoint()
    replay = solver.drain_inspector_events()
    assert any(event["kind"] == "publication_requested" for event in replay["events"])
    assert all(event["kind"] != "output_delta" for event in replay["events"])
    after = {(owner, points.objs_mask, points.classmethods_mask, points.instancemethods_mask)
             for owner, points in solver.state._env.items()}
    assert after == before
    assert replay["health"]["failures"] == 0


def test_flow_api_return_event_does_not_claim_a_faulted_relation_exists(tmp_path, monkeypatch):
    from pythonstan.analysis.pointer.kcfa.heap_model import FieldKind
    from pythonstan.analysis.pointer.kcfa.object import DictObject
    from pythonstan.analysis.pointer.kcfa.pointer_flow_graph import NormalNode
    from pythonstan.analysis.pointer.kcfa.state import PointerAnalysisState
    from pythonstan.analysis.pointer.kcfa.variable import FieldAccess
    original = PointerAnalysisState._add_var_points_flow

    def omit_aggregate(state, source, target):
        content = target.content
        if (isinstance(content, FieldAccess) and isinstance(content.obj, DictObject)
                and content.field.kind == FieldKind.ELEMENT):
            return None
        return original(state, source, target)

    monkeypatch.setattr(PointerAnalysisState, "_add_var_points_flow", omit_aggregate)
    bundle, solver, batch = _run(tmp_path / "omitted", F1_SOURCE.replace("kw.keys()", "kw.values()"))
    assert _result(bundle.data["snapshot"]) == set()
    registration = next(event for event in batch["events"]
        if event["kind"] == "dependency_registration_returned" and event["fields"]["channel"] == "aggregate")
    assert registration["fields"]["registration_kind"] == "copy_flow"
    source = solver.resolve_inspector_event_ref(registration["refs"]["source"])
    assert source is not None
    collector = solver.resolve_inspector_event_ref(registration["refs"]["collector_object"])
    assert collector is not None
    data = bundle.data["snapshot"]
    row = next(row for row in data["views"]["kwargs_model"]["rows"] if row["signature"]["kwarg"] == "kw")
    aggregate = next(channel for channel in row["collector"]["objects"][0]["channels"]
                     if channel["kind"] == "aggregate")
    assert aggregate["owner"]["status"] == "resolved"
    assert not any(point["owner"] == aggregate["owner"]["ref"] for point in data["views"]["ctx_points"]["rows"])
    # The ephemeral unregistered target Ctx may have expired. Its weak ref must
    # not keep it alive or falsely map it to a later equal native owner.
    assert not any(isinstance(edge.source, NormalNode) and isinstance(edge.target, NormalNode)
        and edge.source.var == source and isinstance(edge.target.var.content, FieldAccess)
        and edge.target.var.content.obj == collector
        and edge.target.var.content.field.kind == FieldKind.ELEMENT
        for edge in solver.state._pointer_flow_graph.edges)
    assert not any(event["kind"] == "output_delta" and event["refs"]["owner"] == registration["refs"]["target"]
                   for event in batch["events"])
