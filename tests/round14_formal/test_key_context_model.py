"""Independent explicit receiver/key refinement arm; no default-policy rewrite."""
from contextlib import redirect_stdout
from dataclasses import replace
import hashlib
from io import StringIO
import json
from pathlib import Path

import pytest

from tests.round12_semantics.test_continuations import edges, event_values, values


GOLD_PATH = Path(__file__).with_name("key_context_obligations.json")
GOLD_SHA = "0e6e6af60d1cc865b4ecb29b6dbe6028a3290a2d6447396e45fdbb57efb17c5f"


def _case(name):
    raw = GOLD_PATH.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == GOLD_SHA
    entry = next(row for row in json.loads(raw)["cases"] if row["id"] == name)
    source = GOLD_PATH.parent.joinpath(entry["source"]).read_bytes()
    assert hashlib.sha256(source).hexdigest() == entry["source_sha256"]
    return entry, source.decode()


def _predict(path, source, *, policy="0-cfa", model="receiver_key_v1", limit=32):
    from pythonstan.analysis.pointer.kcfa.config import Config
    from pythonstan.world import World
    from pythonstan.world.pipeline import Pipeline
    from pythonstan_inspector.adapter import capture
    from pythonstan_inspector.storage import load_bundle, write_bundle

    path.mkdir(exist_ok=True)
    filename = path / "subject.py"
    filename.write_text(source)
    World.setup()
    config = Config(context_policy=policy, keyword_context_model=model,
        keyword_context_limit=limit, max_iterations=50000, work_scheduling_policy="fair",
        enable_ai_intrinsics=False, enable_synthetic_method_contexts=False,
        entry_points=[], log_level="ERROR")
    with redirect_stdout(StringIO()):
        pipeline = Pipeline(config={"filename": str(filename), "project_path": str(path),
            "library_paths": [], "import_level": 0, "mock_libs": False, "time_count": False,
            "analysis": [{"name": "pta", "id": "PointerAnalysis", "prev_analysis": ["ir"],
                          "options": config.to_dict()}]})
        driver = pipeline.analysis_manager.get_analyzer("pta")
        driver = getattr(driver, "driver", driver)
        driver.solver.enable_inspector_boundaries()
        pipeline.run()
    solver = driver.solver
    assert solver._stats["converged"] == 1
    assert solver._stats["pending_work"] == 0
    snapshot = capture(solver, run_metadata={"configuration": config.to_dict(),
        "source_sha256": hashlib.sha256(source.encode()).hexdigest(),
        "source_validation": "pre_post_verified", "inputs": [{"path": str(filename),
            "sha256_before": hashlib.sha256(source.encode()).hexdigest(),
            "sha256_after": hashlib.sha256(filename.read_bytes()).hexdigest()}]})
    bundle = load_bundle(write_bundle(snapshot, path / "bundle"))
    for view in bundle.data["snapshot"]["views"].values():
        assert view["coverage"]["registry"] == "complete"
    return bundle, solver


def _result(data):
    entities = {row["id"]: row for row in data["entities"]}
    return {entities[identity]["attributes"]["value"] for identity in values(data, "result")
            if "value" in entities[identity]["attributes"]}


def _assert_goal(data, case):
    assert {tuple(pair) for pair in case.get("required_edges", ())} <= edges(data)
    assert not ({tuple(pair) for pair in case.get("forbidden_edges", ())} & edges(data))
    assert set(case.get("required_events", ())) <= event_values(data)
    if "required_result" in case:
        assert case["required_result"] in _result(data)


@pytest.mark.parametrize("policy", ["0-cfa", "2-cfa"])
@pytest.mark.parametrize("name", ["two_keys", "helper_allocation", "recursive_helper"])
def test_explicit_context_arm_keeps_key_result_relation_across_helpers_and_allocations(tmp_path, policy, name):
    case, source = _case(name)
    concrete = {}
    exec(source, concrete)
    assert concrete["result"] == case["required_result"]
    assert "poison" not in concrete["events"]
    bundle, solver = _predict(tmp_path, source, policy=policy)
    _assert_goal(bundle.data["snapshot"], case)
    manager = solver._keyword_expansions
    assert manager.partition_buckets
    assert {token.key_value for bucket in manager.partition_buckets.values() for token in bucket.values()} == {"callback", "unused"}


@pytest.mark.parametrize("policy", ["0-cfa", "2-cfa"])
def test_default_none_preserves_original_imprecision_in_a_separate_arm(tmp_path, policy):
    _, source = _case("two_keys")
    bundle, solver = _predict(tmp_path, source, policy=policy, model="none")
    assert ("consume", "chosen") in edges(bundle.data["snapshot"])
    # This is a measured precision frontier, not revised desired source gold.
    assert ("consume", "poison") in edges(bundle.data["snapshot"])
    assert not solver._keyword_expansions.partition_buckets
    assert not any(row["kind"] == "context_partition" for row in bundle.data["snapshot"]["entities"])
    assert bundle.data["run"]["configuration"]["keyword_context_model"] == "none"


@pytest.mark.parametrize("limit", [1, 32])
def test_exact_quota_overflow_has_one_join_without_losing_any_callback(tmp_path, limit):
    case, source = _case("quota_33")
    concrete = {}
    exec(source, concrete)
    assert {"chosen", "poison", "replacement"} <= set(concrete["events"])
    bundle, solver = _predict(tmp_path, source, limit=limit)
    _assert_goal(bundle.data["snapshot"], case)
    manager = solver._keyword_expansions
    assert len(manager.partition_buckets) == 1
    assert len(next(iter(manager.partition_buckets.values()))) == limit
    assert len(manager.summary_partitions) == 1
    assert next(iter(manager.summary_partitions.values())).key_kind == "summary"
    assert any("keyword_partition_join" in row.get("effects", ()) for row in solver.state._residual_effects.values())


def _globals(solver):
    from pythonstan.analysis.pointer.kcfa.context import Ctx
    from pythonstan.analysis.pointer.kcfa.variable import Variable
    return {node.content.name: node for node in solver.state._env
            if isinstance(node, Ctx) and isinstance(node.content, Variable)
            and node.content.kind.value == "global"}


def _recapture(solver, path):
    from pythonstan_inspector.adapter import capture
    from pythonstan_inspector.storage import load_bundle, write_bundle
    solver.solve_to_fixpoint()
    assert solver._stats["converged"] == 1
    return load_bundle(write_bundle(capture(solver), path)).data["snapshot"]


@pytest.mark.parametrize("policy", ["0-cfa", "2-cfa"])
def test_late_key_keeps_existing_partition_precise_and_replays_new_getitem(tmp_path, policy):
    from pythonstan.analysis.pointer.kcfa.heap_model import attr, elem
    from pythonstan.analysis.pointer.kcfa.object import AllocKind, AllocSite, ConstantObject
    from pythonstan.analysis.pointer.kcfa.points_to_set import PointsToSet

    case, source = _case("late_keys")
    companion = {}
    exec(source.replace('self.names = ["callback"]', 'self.names = ["callback", "unused"]'), companion)
    assert companion["result"] == 7 and "poison" not in companion["events"]
    bundle, solver = _predict(tmp_path, source, policy=policy)
    _assert_goal(bundle.data["snapshot"], case)
    assert "unused" not in event_values(bundle.data["snapshot"])
    owner = _globals(solver)["payload"]
    receiver, = solver.state.get_points_to(owner)
    names_field = solver.state.get_field(owner.scope, owner.context, receiver, attr("names"))
    names, = solver.state.get_points_to(names_field)
    elements = solver.state.get_field(owner.scope, owner.context, names, elem())
    added = ConstantObject(owner.context, AllocSite("controlled late key", AllocKind.CONSTANT), "unused")
    solver.handle_new_points_to(elements, owner.scope, PointsToSet.singleton(added))
    after = _recapture(solver, tmp_path / "after")
    assert {"callback", "unused", "chosen"} <= event_values(after)
    assert ("consume", "poison") not in edges(after)


@pytest.mark.parametrize("policy", ["0-cfa", "2-cfa"])
def test_late_getitem_preserves_old_and_new_callback_values(tmp_path, policy):
    from pythonstan.analysis.pointer.kcfa.heap_model import attr

    case, source = _case("late_getitem")
    companion = {}
    exec(source.replace('result = consume(**payload)', 'Payload.__getitem__ = later_getitem\nresult = consume(**payload)'), companion)
    assert companion["result"] == 8 and "replacement" in companion["events"]
    bundle, solver = _predict(tmp_path, source, policy=policy)
    _assert_goal(bundle.data["snapshot"], case)
    locations = _globals(solver)
    receiver, = solver.state.get_points_to(locations["payload"])
    owner = locations["payload"]
    slot = solver.state.get_field(owner.scope, owner.context, receiver.class_obj, attr("__getitem__"))
    solver.handle_new_points_to(slot, owner.scope, solver.state.get_points_to(locations["later_getitem"]))
    after = _recapture(solver, tmp_path / "after")
    assert {("consume", "chosen"), ("consume", "replacement")} <= edges(after)
    assert {"chosen", "replacement", "late-getitem"} <= event_values(after)
    assert ("consume", "poison") not in edges(after)


def test_unknown_key_join_preserves_all_concrete_companion_callbacks(tmp_path):
    from pythonstan.analysis.pointer.kcfa.object import AbstractObject, AllocKind, AllocSite
    from pythonstan.analysis.pointer.kcfa.points_to_set import PointsToSet

    _, source = _case("unknown_key")
    for key, expected in (("callback", "chosen"), ("unused", "poison")):
        concrete = {}
        exec(source.replace('incoming_key = "callback"', 'incoming_key = ' + repr(key)), concrete)
        assert expected in concrete["events"]
    before, solver = _predict(tmp_path, source)
    assert ("consume", "chosen") in edges(before.data["snapshot"])
    location = _globals(solver)["incoming_key"]
    unknown = AbstractObject(location.context, AllocSite("controlled opaque key", AllocKind.UNKNOWN))
    solver.handle_new_points_to(location, location.scope, PointsToSet.singleton(unknown))
    after = _recapture(solver, tmp_path / "after")
    assert {("consume", "chosen"), ("consume", "poison")} <= edges(after)
    assert {"chosen", "poison"} <= event_values(after)
    assert solver._keyword_expansions.summary_partitions


def test_offline_typed_partition_metadata_and_flat_summary_replacement(tmp_path):
    from pythonstan.analysis.pointer.kcfa.context import SummaryContext
    from pythonstan.analysis.pointer.kcfa.object import truncate_context
    from pythonstan.analysis.pointer.kcfa.protocol_context import ProtocolKeyContext, apply_protocol_partition, split_protocol_context
    from pythonstan_inspector.contract import QueryRequest
    from pythonstan_inspector.core import query

    _, source = _case("two_keys")
    bundle, solver = _predict(tmp_path, source, policy="2-cfa")
    assert bundle.data["run"]["configuration"]["keyword_context_model"] == "receiver_key_v1"
    response = query(bundle, QueryRequest("show", {"kind": "context_partition"}))
    assert response["query_complete"] is True
    partitions = response["evidence"]
    assert {row["attributes"]["key_value"] for row in partitions} == {"callback", "unused"}
    entities = {row["id"]: row for row in bundle.data["snapshot"]["entities"]}
    for row in partitions:
        assert row["attributes"]["context_model"] == "receiver_key_v1"
        for link in ("operation", "receiver_source"):
            assert row["links"][link]["status"] == "resolved"
            assert entities[row["links"][link]["ref"]]["kind"] == "origin"
    contexts = [row for row in entities.values() if row["attributes"].get("engine_class") == "ProtocolKeyContext"]
    assert contexts
    assert all(row["links"]["partition"]["ref"] in {part["id"] for part in partitions} for row in contexts)
    token = next(iter(next(iter(solver._keyword_expansions.partition_buckets.values())).values()))
    replacement = replace(token, key_value="replacement-partition")
    base = solver.context_selector.empty_context()
    context = SummaryContext(apply_protocol_partition(base, token))
    for _ in range(100):
        context = apply_protocol_partition(context, replacement)
        assert isinstance(context, SummaryContext)
        assert isinstance(context.inner, ProtocolKeyContext)
        assert not isinstance(context.inner.inner, (SummaryContext, ProtocolKeyContext))
    normalized, actual_token = split_protocol_context(truncate_context(context))
    assert isinstance(normalized, SummaryContext)
    assert actual_token == replacement
    assert all(part.operation.index == 0 for bucket in solver._keyword_expansions.partition_buckets.values() for part in bucket.values())


@pytest.mark.parametrize("policy", ["0-cfa", "2-cfa"])
def test_real_selector_calls_and_allocations_preserve_outer_summary_and_one_token(tmp_path, policy):
    from pythonstan.analysis.pointer.kcfa.context import SummaryContext
    from pythonstan.analysis.pointer.kcfa.context_selector import ContextSelector, parse_policy
    from pythonstan.analysis.pointer.kcfa.protocol_context import ProtocolKeyContext, apply_protocol_partition, split_protocol_context

    _, source = _case("two_keys")
    _, solver = _predict(tmp_path, source, policy=policy)
    token = next(iter(next(iter(solver._keyword_expansions.partition_buckets.values())).values()))
    selector = ContextSelector(parse_policy(policy))
    context = SummaryContext(apply_protocol_partition(selector.empty_context(), token))
    for selected in (selector.select_call_context(token.operation, context),
                     selector.select_alloc_context(context, token.receiver_source)):
        assert isinstance(selected, SummaryContext)
        assert isinstance(selected.inner, ProtocolKeyContext)
        base, partition = split_protocol_context(selected)
        assert isinstance(base, SummaryContext)
        assert partition == token


def test_partition_builder_erases_receiver_context_and_joins_synthetic_sources(tmp_path):
    from pythonstan.analysis.pointer.kcfa.context import CallStringContext, SummaryContext
    from pythonstan.analysis.pointer.kcfa.object import AllocKind, AllocSite, ConstantObject
    from pythonstan.analysis.pointer.kcfa.protocol_context import apply_protocol_partition

    _, source = _case("two_keys")
    _, solver = _predict(tmp_path, source)
    owner = _globals(solver)["payload"]
    receiver, = solver.state.get_points_to(owner)
    manager = solver._keyword_expansions
    token = next(iter(next(iter(manager.partition_buckets.values())).values()))
    name = ConstantObject(owner.context, AllocSite("independent exact name", AllocKind.CONSTANT), "callback")
    altered_context = CallStringContext((), 2)
    altered = replace(receiver, context=altered_context)
    assert manager._partition(owner.scope, token.operation, receiver, name) == manager._partition(
        owner.scope, token.operation, altered, name)
    synthetic_a = replace(receiver, alloc_site=AllocSite(("recipe", owner.scope, receiver), AllocKind.INSTANCE))
    synthetic_b = replace(receiver, alloc_site=AllocSite(("other", altered_context), AllocKind.INSTANCE))
    summary_a = manager._partition(owner.scope, token.operation, synthetic_a, name)
    summary_b = manager._partition(owner.scope, token.operation, synthetic_b, name)
    assert summary_a == summary_b
    assert summary_a.receiver_source is None and summary_a.key_kind == "summary"
    summarized_name = replace(name, context=SummaryContext(apply_protocol_partition(owner.context, token)))
    summarized = manager._partition(owner.scope, token.operation, receiver, summarized_name)
    assert summarized.key_kind == "summary"


def test_partition_constructor_rejects_contextual_recipes_and_invalid_wrappers(tmp_path):
    from pythonstan.analysis.pointer.kcfa.context import SummaryContext
    from pythonstan.analysis.pointer.kcfa.object import AllocKind, AllocSite
    from pythonstan.analysis.pointer.kcfa.protocol_context import ProtocolKeyContext

    _, source = _case("two_keys")
    _, solver = _predict(tmp_path, source)
    token = next(iter(next(iter(solver._keyword_expansions.partition_buckets.values())).values()))
    base = solver.context_selector.empty_context()
    invalid = [
        {"receiver_source": AllocSite(("contextual", base), AllocKind.DICT)},
        {"operation": replace(token.operation, scope_name=base)},
        {"operation": replace(token.operation, index=base)},
        {"key_kind": []}, {"key_kind": 7}, {"receiver_source": None},
    ]
    for fields in invalid:
        with pytest.raises(ValueError):
            replace(token, **fields)
    with pytest.raises(ValueError):
        ProtocolKeyContext(SummaryContext(base), token)
    with pytest.raises(ValueError):
        ProtocolKeyContext(ProtocolKeyContext(base, token), token)


@pytest.mark.parametrize("policy", ["0-cfa", "2-cfa"])
@pytest.mark.parametrize("name", ["descriptor_function", "descriptor_callable"])
def test_actual_descriptor_returned_callable_receives_semantic_partition(tmp_path, policy, name):
    path = GOLD_PATH.with_name("key_context_descriptor_amendment.json")
    raw = path.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == "498e9f59bc73adba1d8cff58e607e5cb93c260b64c5a1b29cfbb60eea9f99f89"
    case = next(row for row in json.loads(raw)["cases"] if row["id"] == name)
    source_bytes = path.parent.joinpath(case["source"]).read_bytes()
    assert hashlib.sha256(source_bytes).hexdigest() == case["source_sha256"]
    source = source_bytes.decode()
    concrete = {}
    exec(source, concrete)
    assert concrete["result"] == 7
    assert "descriptor" in concrete["events"] and "poison" not in concrete["events"]
    bundle, _ = _predict(tmp_path, source, policy=policy)
    _assert_goal(bundle.data["snapshot"], case)
