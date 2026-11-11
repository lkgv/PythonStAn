"""Calibrate the actual engine boundary and owned projections independently."""
from contextlib import redirect_stdout
from io import StringIO

import pytest

from pythonstan_inspector.adapter import capture
from pythonstan_inspector.contract import CaptureRequest, InspectorError


def _solver(tmp_path, source="source = 1\ntarget = 2\n", *, run=True):
    from pythonstan.analysis.pointer.kcfa.config import Config
    from pythonstan.world import World
    from pythonstan.world.pipeline import Pipeline
    filename = tmp_path / "subject.py"
    filename.write_text(source)
    World.setup()
    with redirect_stdout(StringIO()):
        pipeline = Pipeline(config={"filename": str(filename), "project_path": str(tmp_path),
            "library_paths": [], "import_level": 0, "mock_libs": False, "time_count": False,
            "analysis": [{"name": "pta", "id": "PointerAnalysis", "prev_analysis": ["ir"],
                "options": Config(max_iterations=10000, log_level="ERROR", enable_ai_intrinsics=False,
                                  enable_synthetic_method_contexts=False).to_dict()}]})
        driver = pipeline.analysis_manager.get_analyzer("pta")
        driver = getattr(driver, "driver", driver)
        driver.solver.enable_inspector_boundaries()
        if run:
            pipeline.run()
    return driver.solver


def _native_fingerprint(solver):
    from pythonstan.analysis.pointer.kcfa import points_to_set
    from pythonstan.analysis.pointer.kcfa.context import context_pool
    state = solver.state
    return (
        tuple((owner, points.objs_mask, points.classmethods_mask, points.instancemethods_mask)
              for owner, points in state._env.items()),
        tuple((key, tuple(registers.items())) for key, registers in state._heap.heap.items()),
        tuple(state._internal_scope.items()),
        frozenset(state._pointer_flow_graph.edges), frozenset(state._pointer_flow_graph.nodes),
        frozenset(state._call_graph.edges), frozenset(state._call_graph.reachable_scopes),
        tuple(state._constraints.iter_scoped()), tuple(state._static_constraints),
        tuple(solver._constraint_replays), tuple(solver._builtin_replays),
        tuple((scope, node, box.val) for scope, node, box in state._worklist.items_list),
        points_to_set._object_table_generation,
        len(points_to_set._global_table._id_to_obj), len(context_pool), solver._iteration,
    )


def _variables(solver):
    from pythonstan.analysis.pointer.kcfa.variable import Variable
    return {ctx.content.name: ctx for registers in solver.state._heap.heap.values()
            for ctx in registers.values() if isinstance(ctx.content, Variable)}


def test_real_adapter_exact_raw_facts_and_no_lazy_engine_calls(tmp_path, monkeypatch):
    from pythonstan.analysis.pointer.kcfa import points_to_set
    from pythonstan.analysis.pointer.kcfa.pointer_flow_graph import PointerFlowNode, NormalNode
    from pythonstan.analysis.pointer.kcfa.context import Ctx
    solver = _solver(tmp_path, "source = 1\ntarget = source\n")
    before = _native_fingerprint(solver)

    def forbidden(*args, **kwargs):
        raise AssertionError("capture called a lazy resolver or formatter")

    for name in ("get_variable", "get_field", "_get_variable_direct"):
        monkeypatch.setattr(solver.state, name, forbidden)
    monkeypatch.setattr(solver, "query", forbidden)
    monkeypatch.setattr(NormalNode, "__repr__", forbidden)
    snapshot = capture(solver, run_metadata={"run_id": "calibrated"})
    assert _native_fingerprint(solver) == before
    data = snapshot.data["snapshot"]
    entities = {entity["id"]: entity for entity in data["entities"]}
    native_owners = {"node_points": PointerFlowNode, "ctx_points": Ctx}
    for name, owner_type in native_owners.items():
        # Compare all three native bit lanes, not an exporter-derived total.
        expected = sum(points.objs_mask.bit_count() + points.classmethods_mask.bit_count() +
                       points.instancemethods_mask.bit_count()
                       for owner, points in solver.state._env.items() if isinstance(owner, owner_type))
        view = data["views"][name]
        assert view["coverage"]["registry"] == "complete"
        assert len(view["rows"]) == expected
        assert set(view["dimensions"]) == {"owner", "object", "lane"}
        for row in view["rows"]:
            assert entities[row["owner"]]["kind"] in ("node", "ctx")
            assert entities[row["object"]]["kind"] == "object"
    assert any(entity["origin"].get("file") == str(tmp_path / "subject.py")
               for entity in entities.values())
    assert points_to_set._global_table is not None


def test_owned_bytes_survive_live_nested_mutation(tmp_path):
    solver = _solver(tmp_path)
    snapshot = capture(solver)
    original = snapshot.encoded
    decoded = snapshot.data
    # IR definitions are shared mutable objects below otherwise-frozen scopes.
    next(iter(solver.state._internal_scope.values())).stmt.qualname = "changed_after_capture"
    for registers in solver.state._heap.heap.values():
        registers.clear()
    solver.state._env.clear()
    solver.state._internal_scope.clear()
    solver.state._pointer_flow_graph.nodes.clear()
    solver.state._pointer_flow_graph.edges.clear()
    # A consumer receives a fresh tree and cannot mutate the sealed authority.
    decoded["snapshot"]["entities"].clear()
    assert snapshot.encoded == original
    assert snapshot.data["snapshot"]["entities"]


def test_stable_actual_engine_late_copy_replay_mutation_is_capturable(tmp_path, monkeypatch):
    from pythonstan.analysis.pointer.kcfa.pointer_flow_graph import NormalNode
    solver = _solver(tmp_path)
    variables = _variables(solver)
    state = solver.state
    source, target = variables["source"], variables["target"]
    source_points = state._env[NormalNode(source)]
    target_points = state._env[NormalNode(target)]
    assert source_points.objs_mask & ~target_points.objs_mask
    assert solver._pending_work_count() == 0
    # Test-only mutation keeps real registration but suppresses existing-fact
    # replay. No corrupted behavior is installed in production code.
    monkeypatch.setattr(state, "_add_points_flow_edge", state._pointer_flow_graph.add_edge)
    state._add_var_points_flow(source, target)
    assert solver._pending_work_count() == 0
    snapshot = capture(solver)
    data = snapshot.data["snapshot"]
    assert data["boundary"]["coherent"] is True
    assert data["views"]["node_points"]["coverage"]["registry"] == "complete"
    entities = {entity["id"]: entity for entity in data["entities"]}
    points = {(row["owner"], row["object"], row["lane"])
              for row in data["views"]["node_points"]["rows"]}
    violated = []
    for edge in data["views"]["pfg_edges"]["rows"]:
        if edge["identity_copy"]:
            for owner, obj, lane in points:
                if owner == edge["source"] and (edge["target"], obj, lane) not in points:
                    violated.append((edge, obj, lane))
    assert any(entities[edge["source"]]["attributes"].get("name") == "source" and
               entities[edge["target"]]["attributes"].get("name") == "target"
               for edge, _, _ in violated)


def test_small_capture_honestly_marks_partial(tmp_path):
    solver = _solver(tmp_path)
    snapshot = capture(solver, CaptureRequest(max_entities=2, max_rows=1))
    data = snapshot.data["snapshot"]
    assert len(data["entities"]) <= 2
    assert any(view["coverage"]["registry"] == "partial" for view in data["views"].values())
    assert data["omissions"]
    assert all(link["status"] != "resolved" or link["ref"] <= 2
               for entity in data["entities"] for link in entity["links"].values())


def test_previous_run_table_reset_refuses_stale_capture(tmp_path):
    first = _solver(tmp_path)
    capture(first)
    second = _solver(tmp_path, "source = 999\n")
    capture(second)
    with pytest.raises(InspectorError, match="another run"):
        capture(first)


def test_busy_aborted_and_unobserved_boundaries_are_refused(tmp_path):
    solver = _solver(tmp_path)
    solver._inspector_step_depth = 1
    with pytest.raises(InspectorError, match="busy"):
        capture(solver)
    solver._inspector_step_depth = 0
    solver._inspector_boundary = "aborted_step"
    with pytest.raises(InspectorError, match="unsafe_boundary"):
        capture(solver)
    solver._inspector_enabled = False
    with pytest.raises(InspectorError, match="not enabled"):
        capture(solver)


def test_aborted_work_item_never_becomes_safe_cut(tmp_path, monkeypatch):
    from benchmark.inspector_v1.measure import finite_arena
    solver, _ = finite_arena(2)
    solver.enable_inspector_boundaries()

    def aborted(*args):
        raise RuntimeError("test semantic operation aborted")

    monkeypatch.setattr(solver, "_apply_static", aborted)
    with pytest.raises(RuntimeError, match="aborted"):
        next(solver)
    assert solver._inspector_boundary == "aborted_step"
    with pytest.raises(InspectorError, match="unsafe_boundary"):
        capture(solver)


def test_late_enable_cannot_retroactively_certify_run():
    from benchmark.inspector_v1.measure import finite_arena
    solver, _ = finite_arena(2)
    solver.solve_to_fixpoint()
    with pytest.raises(ValueError, match="before solving"):
        solver.enable_inspector_boundaries()


@pytest.mark.parametrize("failure_site", ["semantic_operation", "observer"])
def test_aborted_run_stays_tainted_after_successful_resumption(monkeypatch, failure_site):
    from benchmark.inspector_v1.measure import finite_arena
    solver, _ = finite_arena(3)
    solver.enable_inspector_boundaries()

    def aborted_observer(event):
        raise RuntimeError("test failure after semantic writes")

    if failure_site == "semantic_operation":
        original = solver._apply_static

        def partial_then_aborted(*args):
            original(*args)
            raise RuntimeError("test failure after semantic writes")

        monkeypatch.setattr(solver, "_apply_static", partial_then_aborted)
    else:
        solver.set_work_step_observer(aborted_observer)
    with pytest.raises(RuntimeError, match="after semantic writes"):
        next(solver)
    assert solver._inspector_tainted is True
    assert solver.state._pointer_flow_graph.edges
    if failure_site == "semantic_operation":
        monkeypatch.setattr(solver, "_apply_static", original)
    else:
        solver.set_work_step_observer(None)
    next(solver)
    assert solver._inspector_boundary == "between_work_items"
    assert solver._inspector_tainted is True
    solver.enable_inspector_boundaries()
    assert solver._inspector_tainted is True
    with pytest.raises(InspectorError, match="earlier aborted work item tainted"):
        capture(solver)


def test_caught_nested_work_abort_cannot_hide_taint(monkeypatch):
    from benchmark.inspector_v1.measure import finite_arena
    solver, _ = finite_arena(3)
    solver.enable_inspector_boundaries()
    nested = False

    def observer(event):
        nonlocal nested
        if nested:
            return
        nested = True
        original = solver._next_work_item

        def partial_then_error():
            original()
            raise RuntimeError("nested operation partially applied")

        monkeypatch.setattr(solver, "_next_work_item", partial_then_error)
        try:
            with pytest.raises(RuntimeError, match="partially applied"):
                next(solver)
        finally:
            monkeypatch.setattr(solver, "_next_work_item", original)

    solver.set_work_step_observer(observer)
    next(solver)
    assert solver._inspector_boundary == "between_work_items"
    assert solver._inspector_tainted is True
    with pytest.raises(InspectorError, match="tainted"):
        capture(solver)


def test_real_call_edges_preserve_raw_endpoints_and_kind(tmp_path, monkeypatch):
    solver = _solver(tmp_path, """def callee(value):
    return value
def caller():
    return callee(7)
result = caller()
""")
    before = _native_fingerprint(solver)

    def forbidden(*args, **kwargs):
        raise AssertionError("derived call container index is not authoritative")

    monkeypatch.setattr(solver.state._call_graph, "get_container_of", forbidden)
    snapshot = capture(solver, CaptureRequest(views=("call_edges",)))
    assert _native_fingerprint(solver) == before
    data = snapshot.data["snapshot"]
    view = data["views"]["call_edges"]
    entities = {entity["id"]: entity for entity in data["entities"]}
    assert view["dimensions"] == ["callsite", "callee", "call_kind"]
    assert view["coverage"]["registry"] == "complete"
    assert len(view["rows"]) == len(solver.state._call_graph.edges) >= 2
    native_kinds = sorted(edge.kind.name.lower() for edge in solver.state._call_graph.edges)
    assert sorted(row["call_kind"] for row in view["rows"]) == native_kinds
    for row in view["rows"]:
        callsite, callee = entities[row["callsite"]], entities[row["callee"]]
        assert callsite["kind"] == "ctx"
        assert callsite["attributes"]["content_kind"] == "callsite"
        assert callsite["links"]["scope"]["status"] == "resolved"
        assert callee["kind"] == "scope"
        assert view["coverage"]["owners"][str(callsite["id"])]["fields"]["edges"] == "complete"
        assert callsite["origin"]["file"] == str(tmp_path / "subject.py")
        assert callsite["origin"]["line"] > 0


def test_call_edge_truncation_preserves_rows_but_not_local_negatives(tmp_path):
    solver = _solver(tmp_path, "def callee():\n    return 1\na = callee()\nb = callee()\n")
    snapshot = capture(solver, CaptureRequest(views=("call_edges",), max_rows=1))
    view = snapshot.data["snapshot"]["views"]["call_edges"]
    assert len(view["rows"]) == 1
    assert view["coverage"]["registry"] == "partial"
    assert all(owner["fields"]["edges"] == "partial" for owner in view["coverage"]["owners"].values())


def test_actual_bound_method_masks_are_not_flattened(tmp_path):
    solver = _solver(tmp_path, """class Example:
    @classmethod
    def cm(cls):
        return 1
    def im(self):
        return 2
class_ref = Example.cm
instance_ref = Example().im
""")
    snapshot = capture(solver)
    rows = snapshot.data["snapshot"]["views"]["node_points"]["rows"]
    assert {"classmethods", "instancemethods"} <= {row["lane"] for row in rows}
    expected = {"classmethods": 0, "instancemethods": 0}
    from pythonstan.analysis.pointer.kcfa.pointer_flow_graph import PointerFlowNode
    for owner, points in solver.state._env.items():
        if isinstance(owner, PointerFlowNode):
            expected["classmethods"] += points.classmethods_mask.bit_count()
            expected["instancemethods"] += points.instancemethods_mask.bit_count()
    for lane, count in expected.items():
        assert sum(row["lane"] == lane for row in rows) == count


def test_large_constant_is_opaque_description_but_exact_relation(tmp_path):
    from pythonstan_inspector.storage import load_bundle, write_bundle
    solver = _solver(tmp_path, "source = 1208925819614629174706176\n")
    snapshot = capture(solver)
    constants = [entity for entity in snapshot.data["snapshot"]["entities"]
                 if entity["attributes"].get("engine_class") == "ConstantObject"]
    assert any(entity["attributes"].get("value_coverage") == "opaque" for entity in constants)
    assert load_bundle(write_bundle(snapshot, tmp_path / "bundle")).data["snapshot"]["views"][
        "node_points"]["coverage"]["registry"] == "complete"


def test_invalid_native_negative_mask_is_bounded_bad_state(tmp_path):
    from pythonstan.analysis.pointer.kcfa.points_to_set import PointsToSet
    from pythonstan.analysis.pointer.kcfa.pointer_flow_graph import NormalNode
    solver = _solver(tmp_path)
    source = _variables(solver)["source"]
    solver.state._env[NormalNode(source)] = PointsToSet(-1, 0, 0)
    snapshot = capture(solver)
    view = snapshot.data["snapshot"]["views"]["node_points"]
    assert view["coverage"]["registry"] == "partial"
    assert any(item["reason"] == "invalid_engine_bitmask"
               for item in snapshot.data["snapshot"]["omissions"])


def test_translator_address_temporaries_are_not_exported(tmp_path):
    import re
    from pythonstan.analysis.pointer.kcfa.variable import VariableKind
    solver = _solver(tmp_path, "mapping = {'key': 1}\ncopy = {**mapping}\n")
    variables = _variables(solver)
    tokens = {token for variable in variables.values()
              if variable.content.kind == VariableKind.TEMPORARY or not variable.content.name.isidentifier()
              for token in re.findall(r"\d{8,}", variable.content.name)}
    assert tokens, "fixture must exercise actual address-bearing translator names"
    snapshot = capture(solver)
    assert all(token.encode() not in snapshot.encoded for token in tokens)
    assert any(entity["attributes"].get("name_coverage") == "opaque"
               for entity in snapshot.data["snapshot"]["entities"])


@pytest.mark.parametrize("metadata", [
    {"oversized": "x" * 65537}, {"integer": 2 ** 80}, {"number": float("inf")},
    {1: "non_string_key"}, {"nodes": [None] * 4097},
])
def test_caller_metadata_is_validated_before_engine_projection(tmp_path, metadata):
    solver = _solver(tmp_path)
    before = _native_fingerprint(solver)
    with pytest.raises(InspectorError, match="metadata"):
        capture(solver, run_metadata=metadata)
    assert _native_fingerprint(solver) == before


def test_caller_metadata_never_invokes_arbitrary_protocols(tmp_path):
    solver = _solver(tmp_path)

    class Untrusted:
        def __repr__(self):
            raise AssertionError("repr must not be called")

        def __iter__(self):
            raise AssertionError("iteration must not be called")

    with pytest.raises(InspectorError, match="non-primitive"):
        capture(solver, run_metadata={"object": Untrusted()})
    cycle = {}
    cycle["cycle"] = cycle
    with pytest.raises(InspectorError, match="depth"):
        capture(solver, run_metadata=cycle)


def test_caller_metadata_ownership_and_declared_byte_fraction(tmp_path):
    solver = _solver(tmp_path)
    metadata = {"run_id": "owned", "inputs": [{"digest": "content_identity"}]}
    snapshot = capture(solver, run_metadata=metadata)
    metadata["inputs"][0]["digest"] = "modified"
    assert snapshot.data["run"]["inputs"][0]["digest"] == "content_identity"
    with pytest.raises(InspectorError, match="metadata.*byte budget"):
        capture(solver, CaptureRequest(max_bytes=4096), {"identity": "x" * 1500})


def test_actual_source_surrogate_constant_is_opaque_and_sealable(tmp_path):
    from pythonstan_inspector.storage import load_bundle, write_bundle
    solver = _solver(tmp_path, "source = '\\ud800'\n")
    snapshot = capture(solver)
    constants = [entity for entity in snapshot.data["snapshot"]["entities"]
                 if entity["attributes"].get("engine_class") == "ConstantObject"]
    assert any(entity["attributes"].get("value_coverage") == "opaque" for entity in constants)
    bundle = load_bundle(write_bundle(snapshot, tmp_path / "surrogate_bundle"))
    assert bundle.data["snapshot"]["views"]["node_points"]["coverage"]["registry"] == "complete"


def test_actual_source_non_bmp_unicode_constant_roundtrip(tmp_path):
    from pythonstan_inspector.storage import load_bundle, write_bundle
    solver = _solver(tmp_path, "source = '\\U0001f680中文'\n")
    snapshot = capture(solver)
    data = load_bundle(write_bundle(snapshot, tmp_path / "unicode_bundle")).data
    assert any(entity["attributes"].get("value") == "🚀中文"
               for entity in data["snapshot"]["entities"])


@pytest.mark.parametrize("metadata", [{"value": "\ud800"}, {"\ud800": "value"}])
def test_surrogate_metadata_is_explicitly_refused(tmp_path, metadata):
    solver = _solver(tmp_path)
    with pytest.raises(InspectorError, match="strings must be valid UTF-8"):
        capture(solver, run_metadata=metadata)


def test_long_constant_descriptions_are_partial_and_never_alignment_keys(tmp_path):
    from pythonstan_inspector.core import _alignment_key, Budget
    from pythonstan_inspector.contract import QueryLimits
    prefix = "x" * 512
    solver = _solver(tmp_path, "first = " + repr(prefix + "A") + "\nsecond = " +
                     repr(prefix + "B") + "\nexact = " + repr(prefix) + "\n")
    snapshot = capture(solver)
    registry = {entity["id"]: entity for entity in snapshot.data["snapshot"]["entities"]}
    constants = [entity for entity in registry.values()
                 if entity["attributes"].get("engine_class") == "ConstantObject"
                 and entity["attributes"].get("value") == prefix]
    partial = [entity for entity in constants if entity["attributes"].get("value_coverage") == "partial"]
    assert len(partial) == 2
    assert any(entity["attributes"].get("description_coverage") == "complete" for entity in constants)
    for entity in partial:
        assert entity["attributes"]["description_coverage"] == "partial"
        assert _alignment_key(entity, registry, Budget(QueryLimits())) is None


def test_long_source_identifiers_do_not_force_same_prefix_alignment(tmp_path):
    from pythonstan_inspector.core import _alignment_key, Budget
    from pythonstan_inspector.contract import QueryLimits
    prefix = "identifier" + "x" * 502
    assert len(prefix) == 512
    solver = _solver(tmp_path, prefix + "A = 1\n" + prefix + "B = 2\n")
    snapshot = capture(solver)
    registry = {entity["id"]: entity for entity in snapshot.data["snapshot"]["entities"]}
    owners = [entity for entity in registry.values()
              if entity["attributes"].get("name") == prefix]
    assert len(owners) >= 4  # independent node and Ctx owners for both names
    for entity in owners:
        assert entity["attributes"]["name_coverage"] == "partial"
        assert entity["attributes"]["description_coverage"] == "partial"
        assert _alignment_key(entity, registry, Budget(QueryLimits())) is None


def test_long_origins_field_names_and_key_types_propagate_description_loss(tmp_path):
    from pythonstan.analysis.pointer.kcfa.context import Ctx
    from pythonstan.analysis.pointer.kcfa.heap_model import Field, FieldKind
    from pythonstan.analysis.pointer.kcfa.pointer_flow_graph import NormalNode
    from pythonstan.analysis.pointer.kcfa.variable import FieldAccess
    solver = _solver(tmp_path)
    variable = _variables(solver)["source"]
    native_object = next(iter(solver.state._heap.objects.values()))
    scope = variable.scope
    scope.module.stmt.filename = "p" * 513
    field = Field(FieldKind.KEY, "k" * 513, key_type="t" * 513)
    field_ctx = Ctx(scope.context, scope, FieldAccess(native_object, field))
    solver.state._env[NormalNode(field_ctx)] = solver.state._env[NormalNode(variable)]
    snapshot = capture(solver)
    records = snapshot.data["snapshot"]["entities"]
    assert any(entity["origin"].get("description_coverage") == "partial" and
               entity["attributes"].get("description_coverage") == "partial"
               for entity in records)
    field_records = [entity for entity in records if entity["attributes"].get("content_kind") == "field"
                     and entity["attributes"].get("field_name") == "k" * 512]
    assert field_records
    assert all(entity["attributes"]["field_name_coverage"] == "partial" and
               entity["attributes"]["field_key_type_coverage"] == "partial" and
               entity["attributes"]["description_coverage"] == "partial" for entity in field_records)
