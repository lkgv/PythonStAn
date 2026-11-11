"""Index equivalence and late-growth witnesses for mapping snapshots."""
import ast

import pytest

from pythonstan.analysis.pointer.kcfa.context import CallStringContext, Scope
from pythonstan.analysis.pointer.kcfa.heap_model import attr, elem, key, wildcard_key
from pythonstan.analysis.pointer.kcfa.object import AbstractObject, AllocKind, AllocSite, DictObject, ModuleObject
from pythonstan.analysis.pointer.kcfa.points_to_set import reset_object_table
from pythonstan.analysis.pointer.kcfa.state import PointerAnalysisState, Worklist
from pythonstan.analysis.pointer.kcfa.variable import FieldAccess
from pythonstan.ir import IRModule
from tests.pointer.kcfa.test_ai_bridge import analyze_source


@pytest.fixture
def model():
    reset_object_table()
    context = CallStringContext((), 1)
    module = IRModule("fields", ast.parse(""), "fields", "fields.py")
    module_object = ModuleObject(context, AllocSite(module, AllocKind.MODULE), module)
    scope = Scope.new(module_object, None, context, module, None)
    state = PointerAnalysisState()
    yield state, scope, context
    reset_object_table()


def legacy_fields(state, obj):
    return tuple(field for owner, field in state._field_accesses if owner == obj)


def test_index_matches_global_order_for_interleaved_slots_and_equal_owners(model):
    state, scope, context = model
    left = AbstractObject(context, AllocSite("left", AllocKind.OBJECT))
    alias = AbstractObject(context, AllocSite("left", AllocKind.OBJECT))
    right = AbstractObject(context, AllocSite("right", AllocKind.OBJECT))
    assert left == alias and left is not alias
    for owner, field in [(left, attr("one")), (right, key(7)), (alias, key("seven")),
                         (right, elem()), (left, wildcard_key()), (alias, attr("one"))]:
        state.set_field(scope, context, owner, field, FieldAccess(owner, field))
        for obj in (left, alias, right):
            assert state.known_fields_for_object(obj) == legacy_fields(state, obj)
    assert state.known_fields_for_object(left) == (attr("one"), key("seven"), wildcard_key())


def test_empty_read_created_slots_and_later_growth_remain_visible(model):
    state, scope, context = model
    mapping = DictObject(context, AllocSite("mapping", AllocKind.DICT))
    slot = state.get_field(scope, context, mapping, key("empty"))
    assert state.get_points_to(slot).is_empty()
    before = state.known_fields_for_object(mapping)
    assert key("empty") in before
    state.get_field(scope, context, mapping, key("late"))
    assert key("late") not in before
    assert key("late") in state.known_fields_for_object(mapping)
    assert state.known_fields_for_object(mapping) == legacy_fields(state, mapping)


def test_index_published_before_recursive_field_callbacks(model):
    state, scope, context = model
    owner = AbstractObject(context, AllocSite("recursive", AllocKind.OBJECT))
    observed = []
    class Subscriber:
        def field_created(self, observed_scope, obj, field):
            observed.append(state.known_fields_for_object(obj))
            assert state.known_fields_for_object(obj) == legacy_fields(state, obj)
            if field == attr("first"):
                state.set_field(scope, context, owner, attr("second"), FieldAccess(owner, attr("second")))
    state._mapping_field_copies = Subscriber()
    state.set_field(scope, context, owner, attr("first"), FieldAccess(owner, attr("first")))
    assert observed == [(attr("first"),), (attr("first"), attr("second"))]


SOURCE = '''
def early(): return 1
def later(): return 2
def unrelated(): return 3
def invoke(callback): callback()
mapping = {"early": early}
noise = {"noise": unrelated}
copied = dict(mapping)
mapping["late"] = later
copied.update(mapping)
for name, callback in copied.items():
    invoke(callback)
'''


@pytest.mark.parametrize("index_sensitive", [False, True])
@pytest.mark.parametrize("intrinsics", [False, True])
@pytest.mark.parametrize("schedule", ["fifo", "lifo"])
def test_mapping_copy_update_items_fixpoint_matches_global_scan(
        tmp_path, monkeypatch, index_sensitive, intrinsics, schedule):
    if schedule == "lifo":
        def pop(self):
            scope, node, points = self.items_list.pop()
            del self.items_dict[node]
            return scope, node, points.val
        monkeypatch.setattr(Worklist, "pop", pop)
    query, indexed_edges = analyze_source(tmp_path, SOURCE, index_sensitive=index_sensitive,
                                         enable_ai_intrinsics=intrinsics)
    assert query.get_statistics()["converged"] == 1
    indexed_constraints = query.get_statistics()["constraints_applied"]
    monkeypatch.setattr(PointerAnalysisState, "known_fields_for_object", legacy_fields)
    legacy_query, scanned_edges = analyze_source(tmp_path, SOURCE, index_sensitive=index_sensitive,
                                                enable_ai_intrinsics=intrinsics)
    assert legacy_query.get_statistics()["converged"] == 1
    assert indexed_edges == scanned_edges
    assert {("subject.invoke", "subject.early"), ("subject.invoke", "subject.later")} <= indexed_edges
    assert ("subject.invoke", "subject.unrelated") not in indexed_edges
    assert legacy_query.get_statistics()["constraints_applied"] == indexed_constraints
