"""Necessary composite-key dispatch/effect obligations, independent of predictions.

The FastAPI dependency cache uses a tuple whose first element is a callable.
These local source witnesses isolate recursive native tuple protocols, disable
synthetic method roots, and inspect actual containing-operation call edges.
They do not replace the complete fourteen-project evaluation.
"""
from contextlib import contextmanager
from dataclasses import replace

import pytest

from pythonstan.analysis.pointer.kcfa.config import Config
from pythonstan.analysis.pointer.kcfa.constraints import CallConstraint
from pythonstan.analysis.pointer.kcfa.heap_model import elem, key
from pythonstan.analysis.pointer.kcfa.object import ConstantObject, InstanceObject, TupleObject
from pythonstan.analysis.pointer.kcfa.points_to_set import PointsToSet
from pythonstan.analysis.pointer.kcfa.variable import Variable, VariableKind
from pythonstan.world import World
from pythonstan.world.pipeline import Pipeline
from pythonstan_inspector.adapter import capture
from pythonstan_inspector.checker import COPY_CLOSURE
from pythonstan_inspector.contract import CaptureRequest, QueryLimits, QueryRequest
from pythonstan_inspector.core import query as inspect
from pythonstan_inspector.storage import Bundle
from tests.analysis.legacy_ai_support import isolated_world


HASH_TEMPLATE = '''events = []
def hash_effect():
    events.append("hash")
    return 13
def unrelated_effect():
    events.append("unrelated")
    return 17
class Dependency:
    def __hash__(self): return hash_effect()
class Unrelated:
    def __hash__(self): return unrelated_effect()
def lookup(cache, key): return cache.get(key)
def insert(cache, key): return cache.setdefault(key)
unrelated = Unrelated()
key = KEY_EXPRESSION
cache = {}
missing = lookup(cache, key)
inserted = insert(cache, key)
observed_effect = events[0]
'''

EQUALITY_SOURCE = '''events = []
def hash_effect():
    events.append("hash")
    return 13
def equality_effect():
    events.append("equal")
    return True
class Dependency:
    def __hash__(self): return hash_effect()
    def __eq__(self, other): return equality_effect()
def lookup(cache, key): return cache.get(key)
stored_key = (Dependency(), (), "request")
query_key = (Dependency(), (), "request")
cache = {stored_key: "payload"}
result = lookup(cache, query_key)
observed_effect = events[0]
'''


@contextmanager
def analyzed(tmp_path, source, policy="fair", index_sensitive=True, intrinsics=False,
             context_policy="1-cfa"):
    """Keep the production object-table lifetime through capture and reads."""
    filename = tmp_path / "subject.py"
    filename.write_text(source)
    config = Config(max_iterations=50000, log_level="ERROR", context_policy=context_policy,
        index_sensitive=index_sensitive, enable_ai_intrinsics=intrinsics,
        enable_synthetic_method_contexts=False, work_scheduling_policy=policy)
    with isolated_world():
        pipeline = Pipeline(config={"filename": str(filename), "project_path": str(tmp_path),
            "library_paths": [], "import_level": 0, "mock_libs": False, "time_count": False,
            "analysis": [{"name": "pta", "id": "PointerAnalysis", "prev_analysis": ["ir"],
                          "options": config.to_dict()}]})
        driver = pipeline.analysis_manager.get_analyzer("pta")
        actual = getattr(driver, "driver", driver)
        actual.solver.enable_inspector_boundaries()
        pipeline.run()
        solver = actual.solver
        snapshot = capture(solver, CaptureRequest(views=("node_points", "ctx_points", "pfg_edges", "call_edges")))
        result = pipeline.analysis_manager.get_results("pta").query()
        yield result, Bundle(snapshot, b"{}")


def _pairs(result):
    return {(edge.callsite.scope.stmt.get_qualname(), edge.callee.stmt.get_qualname())
            for edge in result.call_graph().edges}


def _event_values(result):
    module = World().get_entry_module()
    return {item.value for container in result.points_to_at("events", module)
            for item in result.list_elements(container) if isinstance(item, ConstantObject)}


def _offline_pairs(bundle):
    request = QueryRequest("show", {"view": "call_edges"},
                           limits=QueryLimits(max_rows=2000, max_visited=20000, max_bytes=2000000))
    answer = inspect(bundle, request)
    assert answer["query_complete"] is True
    assert answer["coverage"]["registry"] == "complete"
    entities = {item["id"]: item for item in bundle.data["snapshot"]["entities"]}
    return {(entities[row["callsite"]]["origin"]["scope"],
             entities[row["callee"]]["origin"]["qualname"])
            for row in answer["evidence"]}


@pytest.mark.parametrize("key_expression", ["(Dependency(), (), 'request')", "((Dependency(),), (), 'request')"])
@pytest.mark.parametrize("policy", ["legacy", "fair"])
@pytest.mark.parametrize("index_sensitive", [False, True])
def test_native_tuple_key_hash_recovers_nested_operation_calls_and_effects(
        tmp_path, key_expression, policy, index_sensitive):
    source = HASH_TEMPLATE.replace("KEY_EXPRESSION", key_expression)
    concrete = {}
    exec(compile(source, "<trusted-composite-key-witness>", "exec"), concrete)
    assert concrete["events"] == ["hash", "hash"]
    with analyzed(tmp_path, source, policy, index_sensitive) as (result, bundle):
        assert result.get_statistics()["converged"] == 1
        pairs = _pairs(result)
        assert _offline_pairs(bundle) == pairs
        # Stability of the existing registered copy rules is a separate claim;
        # it cannot discharge the missing recursive native protocol rules.
        closure = inspect(bundle, QueryRequest("check", {"checker": COPY_CLOSURE}, claim=COPY_CLOSURE))
        assert closure["check_status"] == "passed"
        required = {("subject.lookup", "subject.Dependency.__hash__"),
                    ("subject.insert", "subject.Dependency.__hash__"),
                    ("subject.Dependency.__hash__", "subject.hash_effect")}
        assert required <= pairs, {"missing_required_dispatch": sorted(required - pairs),
                                  "registered_copy_closure": closure["check_status"]}
        assert "hash" in _event_values(result)
        assert ("subject.lookup", "subject.Unrelated.__hash__") not in pairs
        assert ("subject.insert", "subject.Unrelated.__hash__") not in pairs
        assert any("native_mapping_key_protocol" in item["effects"]
                   for item in result.get_residual_effects())
        assert result.get_semantic_status()["whole_program_soundness_proved"] is False


@pytest.mark.parametrize("policy", ["legacy", "fair"])
@pytest.mark.parametrize("index_sensitive", [False, True])
def test_native_tuple_key_comparison_recovers_element_equality_and_effects(
        tmp_path, policy, index_sensitive):
    concrete = {}
    exec(compile(EQUALITY_SOURCE, "<trusted-tuple-equality-witness>", "exec"), concrete)
    assert concrete["result"] == "payload"
    assert "equal" in concrete["events"]
    with analyzed(tmp_path, EQUALITY_SOURCE, policy, index_sensitive) as (result, bundle):
        assert result.get_statistics()["converged"] == 1
        pairs = _pairs(result)
        assert _offline_pairs(bundle) == pairs
        required = {("subject.lookup", "subject.Dependency.__eq__"),
                    ("subject.Dependency.__eq__", "subject.equality_effect")}
        assert required <= pairs, {"missing_required_dispatch": sorted(required - pairs)}
        assert "equal" in _event_values(result)


@pytest.mark.parametrize("policy", ["legacy", "fair"])
@pytest.mark.parametrize("index_sensitive", [False, True])
def test_tuple_hash_subscription_retains_late_components_and_abstract_cycles(
        tmp_path, policy, index_sensitive):
    """Controlled state-growth fixture, separate from the concrete source run.

    The injected self reference tests finite abstract-graph traversal; it is
    not claimed to be a directly constructible immutable concrete self-tuple.
    A concrete companion source establishes the leaf callback obligation.
    """
    source = '''events = []
def hash_effect():
    events.append("hash")
    return 13
class Dependency:
    def __hash__(self): return hash_effect()
def lookup(cache, key): return cache.get(key)
leaf = Dependency()
key = (None,)
cache = {}
lookup(cache, key)
'''
    concrete = {}
    exec(compile(source.replace("key = (None,)", "key = (leaf,)"),
                 "<trusted-late-component-companion>", "exec"), concrete)
    assert concrete["events"] == ["hash"]
    with analyzed(tmp_path, source, policy, index_sensitive) as (result, _):
        before = _pairs(result)
        assert ("subject.lookup", "subject.Dependency.__hash__") not in before
        module = World().get_entry_module()
        tuple_object = next(obj for obj in result.points_to_at("key", module) if isinstance(obj, TupleObject))
        leaf = next(obj for obj in result.points_to_at("leaf", module) if isinstance(obj, InstanceObject))
        manager = result._state._native_mapping_protocols
        solver, state = manager.solver, result._state
        scope = state.get_scope_for_ir(module)
        field = key(0) if index_sensitive else elem()
        component = state.get_field(scope, scope.context, tuple_object, field)
        solver.handle_new_points_to(component, scope, PointsToSet.from_objects((leaf, tuple_object)))
        solver.solve_to_fixpoint()
        assert result.get_statistics()["converged"] == 1
        required = {("subject.lookup", "subject.Dependency.__hash__"),
                    ("subject.Dependency.__hash__", "subject.hash_effect")}
        assert required <= _pairs(result)
        assert "hash" in _event_values(result)
        after = Bundle(capture(solver, CaptureRequest(views=("call_edges",))), b"{}")
        assert _offline_pairs(after) == _pairs(result)


@pytest.mark.parametrize("context_policy", ["0-cfa", "1-cfa", "2-cfa"])
@pytest.mark.parametrize("index_sensitive", [False, True])
@pytest.mark.parametrize("intrinsics", [False, True])
def test_nested_tuple_native_hash_configuration_matrix(
        tmp_path, context_policy, index_sensitive, intrinsics):
    source = HASH_TEMPLATE.replace("KEY_EXPRESSION", "((Dependency(),), (), 'request')")
    with analyzed(tmp_path, source, index_sensitive=index_sensitive, intrinsics=intrinsics,
                  context_policy=context_policy) as (result, bundle):
        assert result.get_statistics()["converged"] == 1
        required = {("subject.lookup", "subject.Dependency.__hash__"),
                    ("subject.insert", "subject.Dependency.__hash__"),
                    ("subject.Dependency.__hash__", "subject.hash_effect")}
        assert required <= _pairs(result)
        assert "hash" in _event_values(result)
        assert _offline_pairs(bundle) == _pairs(result)


@pytest.mark.parametrize("arrival", ["left_first", "right_first", "coalesced"])
@pytest.mark.parametrize("index_sensitive", [False, True])
def test_tuple_equality_join_retains_both_late_input_directions(tmp_path, arrival, index_sensitive):
    source = '''events = []
class Stored:
    def __hash__(self): return 13
    def __eq__(self, other): return other.deliver()
class Query:
    def __hash__(self): return 13
    def __eq__(self, other): return False
    def deliver(self):
        events.append("joined")
        return True
def lookup(cache, key): return cache.get(key)
stored_leaf = Stored()
query_leaf = Query()
stored_key = (None,)
query_key = (None,)
cache = {stored_key: "payload"}
result = lookup(cache, query_key)
'''
    concrete = {}
    companion = source.replace("stored_key = (None,)", "stored_key = (stored_leaf,)")
    companion = companion.replace("query_key = (None,)", "query_key = (query_leaf,)")
    exec(compile(companion, "<trusted-late-tuple-join-companion>", "exec"), concrete)
    assert concrete["events"] == ["joined"]
    with analyzed(tmp_path, source, index_sensitive=index_sensitive) as (result, _):
        assert ("subject.Stored.__eq__", "subject.Query.deliver") not in _pairs(result)
        module = World().get_entry_module()
        state = result._state
        solver = state._native_mapping_protocols.solver
        scope = state.get_scope_for_ir(module)
        updates = {}
        for label, leaf_name, tuple_name in (("left", "stored_leaf", "stored_key"),
                                             ("right", "query_leaf", "query_key")):
            leaf = next(obj for obj in result.points_to_at(leaf_name, module) if isinstance(obj, InstanceObject))
            container = next(obj for obj in result.points_to_at(tuple_name, module) if isinstance(obj, TupleObject))
            slot = state.get_field(scope, scope.context, container, key(0) if index_sensitive else elem())
            updates[label] = (slot, leaf)
        order = ("right", "left") if arrival == "right_first" else ("left", "right")
        for label in order:
            slot, leaf = updates[label]
            solver.handle_new_points_to(slot, scope, PointsToSet.singleton(leaf))
            if arrival != "coalesced":
                solver.solve_to_fixpoint()
        solver.solve_to_fixpoint()
        assert result.get_statistics()["converged"] == 1
        assert ("subject.Stored.__eq__", "subject.Query.deliver") in _pairs(result)
        assert "joined" in _event_values(result)


@pytest.mark.parametrize("index_sensitive", [False, True])
def test_tuple_primitive_components_do_not_enumerate_scalar_cross_products(tmp_path, index_sensitive):
    count = 30
    source = ("stored = (" + ",".join(str(value) for value in range(count)) + ")\n"
              "query = (" + ",".join(str(value + count) for value in range(count)) + ")\n"
              "cache = {stored: 'payload'}\nresult = cache.get(query)\n")
    with analyzed(tmp_path, source, index_sensitive=index_sensitive) as (result, _):
        stats = result._state._native_mapping_protocols.statistics
        assert stats["tuple_equality_observers"] > 0
        assert stats["tuple_equality_candidate_pairs"] == 0
        assert not _pairs(result)
        assert result.get_statistics()["converged"] == 1


def test_known_tuple_positions_do_not_invent_cross_position_callbacks(tmp_path):
    source = '''events = []
class Stored:
    def __hash__(self): return 13
    def __eq__(self, other): return other.deliver()
class Query:
    def __hash__(self): return 13
    def __eq__(self, other): return False
    def deliver(self):
        events.append("wrong position")
        return True
stored_key = (Stored(), "fixed")
query_key = (None, Query())
cache = {stored_key: "payload"}
result = cache.get(query_key)
'''
    concrete = {}
    exec(compile(source, "<trusted-position-isolation>", "exec"), concrete)
    assert concrete["events"] == []
    with analyzed(tmp_path, source, index_sensitive=True) as (result, _):
        assert result.get_statistics()["converged"] == 1
        assert ("subject.Stored.__eq__", "subject.Query.deliver") not in _pairs(result)


def test_tuple_element_equality_preserves_user_truth_callback_effect(tmp_path):
    source = '''events = []
def truth_effect():
    events.append("truth")
    return True
class Truth:
    def __bool__(self): return truth_effect()
class Dependency:
    def __hash__(self): return 13
    def __eq__(self, other): return Truth()
stored_key = (Dependency(),)
query_key = (Dependency(),)
cache = {stored_key: "payload"}
result = cache.get(query_key)
'''
    concrete = {}
    exec(compile(source, "<trusted-tuple-truth-witness>", "exec"), concrete)
    assert concrete["events"] == ["truth"]
    with analyzed(tmp_path, source) as (result, bundle):
        assert result.get_statistics()["converged"] == 1
        assert ("subject.Truth.__bool__", "subject.truth_effect") in _pairs(result)
        assert "truth" in _event_values(result)
        assert _offline_pairs(bundle) == _pairs(result)


@pytest.mark.parametrize("policy", ["legacy", "fair"])
def test_late_varargs_argument_family_publishes_new_tuple_position_to_hash(tmp_path, policy):
    """Actual binding-rule growth at one site, not a hand-edited tuple DTO."""
    source = '''events = []
def hash_effect():
    events.append("hash")
    return 13
def unrelated_effect(): return 17
class Dependency:
    def __hash__(self): return hash_effect()
class Unrelated:
    def __hash__(self): return unrelated_effect()
def collect(*args): return args
def lookup(cache, key): return cache.get(key)
leaf = Dependency()
unrelated = Unrelated()
key = collect()
cache = {}
lookup(cache, key)
'''
    concrete = {}
    exec(compile(source.replace("key = collect()", "key = collect(leaf)"),
                 "<trusted-new-vararg-family-companion>", "exec"), concrete)
    assert concrete["events"] == ["hash"]
    with analyzed(tmp_path, source, policy=policy) as (result, _):
        assert ("subject.lookup", "subject.Dependency.__hash__") not in _pairs(result)
        state = result._state
        solver = state._native_mapping_protocols.solver
        module = World().get_entry_module()
        scope = state.get_scope_for_ir(module)
        original, = [constraint for constraint in state.constraints.get_by_type(CallConstraint)
                     if constraint.callee.name == "collect" and not constraint.args]
        # An explicitly supplied later argument family shares the operation's
        # canonical callsite/context, as entry-shape growth can. Binding must
        # publish its new position through the tuple producer's aggregate.
        later = replace(original, args=(Variable("leaf", VariableKind.GLOBAL),))
        solver.add_constraint(scope, scope.context, later)
        solver.solve_to_fixpoint()
        assert result.get_statistics()["converged"] == 1
        required = {("subject.lookup", "subject.Dependency.__hash__"),
                    ("subject.Dependency.__hash__", "subject.hash_effect")}
        assert required <= _pairs(result), {"missing": sorted(required - _pairs(result))}
        assert "hash" in _event_values(result)
        assert ("subject.lookup", "subject.Unrelated.__hash__") not in _pairs(result)


@pytest.mark.parametrize("index_sensitive", [False, True])
def test_tuple_constructor_over_varargs_retains_native_key_component_effects(tmp_path, index_sensitive):
    source = HASH_TEMPLATE.replace("KEY_EXPRESSION", "collect(Dependency())")
    source = source.replace("def lookup(", "def collect(*args): return tuple(args)\ndef lookup(")
    concrete = {}
    exec(compile(source, "<trusted-vararg-tuple-constructor>", "exec"), concrete)
    assert concrete["events"] == ["hash", "hash"]
    with analyzed(tmp_path, source, index_sensitive=index_sensitive) as (result, bundle):
        assert result.get_statistics()["converged"] == 1
        required = {("subject.lookup", "subject.Dependency.__hash__"),
                    ("subject.insert", "subject.Dependency.__hash__"),
                    ("subject.Dependency.__hash__", "subject.hash_effect")}
        assert required <= _pairs(result)
        assert "hash" in _event_values(result)
        assert _offline_pairs(bundle) == _pairs(result)
