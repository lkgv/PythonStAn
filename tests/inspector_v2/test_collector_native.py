"""Finite consumed-key precision controls for expanded keyword collectors.

Source tests use production lowering/solver, adapter and sealed bundles. The
small-state tests are controlled abstract metadata growth, not concrete Python
execution: in particular they deliberately exercise both private key fields.
"""
import ast

import pytest

from pythonstan.analysis.pointer.kcfa.config import Config
from pythonstan.analysis.pointer.kcfa.context import CallStringContext, Scope
from pythonstan.analysis.pointer.kcfa.native_mapping import NATIVE_MAPPING_KEY_FIELDS
from pythonstan.analysis.pointer.kcfa.object import (
    AbstractObject, AllocKind, AllocSite, ConstantObject, DictObject, ModuleObject,
    summarize_object,
)
from pythonstan.analysis.pointer.kcfa.points_to_set import PointsToSet, reset_object_table
from pythonstan.analysis.pointer.kcfa.pointer_flow_graph import GuardNode
from pythonstan.analysis.pointer.kcfa.processor.heap_mapping import copy_mapping_fields
from pythonstan.analysis.pointer.kcfa.processor.processor import Processor
from pythonstan.analysis.pointer.kcfa.solver import PointerSolver
from pythonstan.analysis.pointer.kcfa.state import PointerAnalysisState
from pythonstan.ir import IRModule
from tests.round12_semantics.test_continuations import edges, event_values, values
from tests.round14_formal.test_key_context_model import _predict, _result


CONSUMED_SOURCE = """def take(*, consumed, **kw):
    return next(iter(kw.keys()))
result = take(**{'consumed': 'poison', 'x': 1})
"""


def _concrete(source):
    namespace = {}
    exec(source, namespace)
    return namespace


def _literals(data, variable):
    entities = {row["id"]: row for row in data["entities"]}
    return {entities[identity]["attributes"]["value"]
            for identity in values(data, variable)
            if "value" in entities[identity]["attributes"]}


@pytest.mark.parametrize("signature", ["consumed, **kw", "*, consumed, **kw"])
@pytest.mark.parametrize("view, expected", [("keys", "x"), ("values", 1)])
def test_expanded_collector_excludes_consumed_names(tmp_path, signature, view, expected):
    source = CONSUMED_SOURCE.replace("*, consumed, **kw", signature).replace("kw.keys()", "kw." + view + "()")
    assert _concrete(source)["result"] == expected
    bundle, _ = _predict(tmp_path, source, model="none")
    assert _result(bundle.data["snapshot"]) == {expected}


def test_expanded_positional_only_name_remains_a_keyword(tmp_path):
    source = """def take(x, /, **kw):
    return next(iter(kw.keys()))
result = take(0, **{'x': 1})
"""
    assert _concrete(source)["result"] == "x"
    bundle, _ = _predict(tmp_path, source, model="none")
    assert _result(bundle.data["snapshot"]) == {"x"}


def test_copy_and_original_mapping_keep_consumed_name(tmp_path):
    source = """def take(*, consumed, **kw):
    return next(iter(kw.keys()))
mapping = {'consumed': 'poison', 'x': 1}
result = take(**mapping)
original_name = next(iter(mapping.keys()))
copied_name = next(iter(mapping.copy().keys()))
"""
    concrete = _concrete(source)
    assert concrete["result"] == "x"
    assert concrete["original_name"] == concrete["copied_name"] == "consumed"
    bundle, _ = _predict(tmp_path, source, model="none")
    data = bundle.data["snapshot"]
    assert _result(data) == {"x"}
    # The abstraction does not promise insertion order. Both source/copy keys
    # must remain represented after only the collector relation filters one.
    assert _literals(data, "original_name") == {"consumed", "x"}
    assert _literals(data, "copied_name") == {"consumed", "x"}


def test_expanded_collector_keeps_chosen_call_result_and_effect(tmp_path):
    source = """events = []
def chosen():
    events.append('chosen')
    return 7
def poison():
    events.append('poison')
    return 99
def forward(*, consumed, **kwargs):
    def names():
        yield from kwargs.keys()
    name = next(names())
    return kwargs[name]()
result = forward(**{'consumed': poison, 'callback': chosen})
"""
    concrete = _concrete(source)
    assert concrete["result"] == 7 and concrete["events"] == ["chosen"]
    bundle, _ = _predict(tmp_path, source, model="none")
    data = bundle.data["snapshot"]
    assert _result(data) == {7}
    assert ("forward", "chosen") in edges(data)
    assert ("forward", "poison") not in edges(data)
    assert event_values(data) == {"chosen"}


@pytest.fixture
def arena():
    reset_object_table()
    context = CallStringContext((), 1)
    ir = IRModule("collector_key_copy", ast.parse(""), filename="collector_key_copy.py")
    module = ModuleObject(context, AllocSite(ir, AllocKind.MODULE), ir)
    scope = Scope.new(module, None, context, ir, None)
    state = PointerAnalysisState()
    state.set_internal_scope(module, scope)
    solver = PointerSolver(state, Config(max_iterations=1000, track_unknowns=False,
                           log_level="ERROR"), Processor())
    source = DictObject(context, AllocSite("source", AllocKind.DICT))
    target = DictObject(context, AllocSite("collector", AllocKind.DICT))
    state.obj_scope[source] = state.obj_scope[target] = scope
    yield solver, state, scope, context, source, target
    reset_object_table()


@pytest.mark.parametrize("field", NATIVE_MAPPING_KEY_FIELDS)
@pytest.mark.parametrize("register_before", [False, True])
def test_private_key_filter_retains_old_late_and_conservative_keys(arena, field, register_before):
    solver, state, scope, context, source, target = arena
    source_slot = state.get_field(scope, context, source, field)
    target_slot = state.get_field(scope, context, target, field)

    class OpaqueString(str):
        pass

    def constant(name, value):
        return ConstantObject(context, AllocSite(name, AllocKind.CONSTANT), value)

    consumed = constant("old consumed", "consumed")
    kept = constant("old kept", "x")
    unknown = AbstractObject(context, AllocSite("opaque key", AllocKind.UNKNOWN))
    summary = summarize_object(constant("summary consumed", "consumed"))
    opaque_string = constant("str subclass", OpaqueString("consumed"))
    nonstring = constant("integer key", 4)
    old = {consumed, kept, unknown, summary, opaque_string, nonstring}
    excluded = frozenset({"consumed"})
    if register_before:
        copy_mapping_fields(state, scope, source, target, excluded, True)
    solver.handle_new_points_to(source_slot, scope, PointsToSet.from_objects(old))
    solver.solve_to_fixpoint()
    if not register_before:
        copy_mapping_fields(state, scope, source, target, excluded, True)
        solver.solve_to_fixpoint()
    assert set(state.get_points_to(target_slot)) == old - {consumed}
    late_consumed = constant("late consumed", "consumed")
    late_kept = constant("late kept", "y")
    late_unknown = AbstractObject(context, AllocSite("late opaque key", AllocKind.UNKNOWN))
    late = {late_consumed, late_kept, late_unknown}
    solver.handle_new_points_to(source_slot, scope, PointsToSet.from_objects(late))
    solver.solve_to_fixpoint()
    assert set(state.get_points_to(target_slot)) == (old | late) - {consumed, late_consumed}
    assert set(state.get_points_to(source_slot)) == old | late
    assert solver._stats["converged"] == 1 and solver._stats["pending_work"] == 0


@pytest.mark.parametrize("excluded", [frozenset(), frozenset({"consumed"})])
def test_repeated_mapping_relation_does_not_create_guards(arena, excluded):
    solver, state, scope, context, source, target = arena
    copy_mapping_fields(state, scope, source, target, excluded, True)
    solver.solve_to_fixpoint()
    before = set(state.pointer_flow_graph.nodes), set(state.pointer_flow_graph.edges)
    before_guards = {node for node in before[0] if isinstance(node, GuardNode)}
    for _ in range(10):
        copy_mapping_fields(state, scope, source, target, excluded, True)
    solver.solve_to_fixpoint()
    assert set(state.pointer_flow_graph.nodes) == before[0]
    assert set(state.pointer_flow_graph.edges) == before[1]
    assert {node for node in state.pointer_flow_graph.nodes if isinstance(node, GuardNode)} == before_guards


@pytest.mark.parametrize("field", NATIVE_MAPPING_KEY_FIELDS)
def test_empty_exclusion_copies_all_exact_names(arena, field):
    solver, state, scope, context, source, target = arena
    names = {ConstantObject(context, AllocSite(name, AllocKind.CONSTANT), name)
             for name in ("consumed", "x")}
    source_slot = state.get_field(scope, context, source, field)
    target_slot = state.get_field(scope, context, target, field)
    solver.handle_new_points_to(source_slot, scope, PointsToSet.from_objects(names))
    solver.solve_to_fixpoint()
    copy_mapping_fields(state, scope, source, target, frozenset(), True)
    solver.solve_to_fixpoint()
    assert set(state.get_points_to(target_slot)) == names
