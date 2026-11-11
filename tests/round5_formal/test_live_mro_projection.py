"""Public MRO views use live source-bound bases and retain ambiguity."""
import pytest

from pythonstan.analysis.pointer.ai.domains.classinfo import ClassInfoCache
from pythonstan.analysis.pointer.kcfa.object import ClassObject, ObjectFactory
from pythonstan.analysis.pointer.kcfa.processor.class_protocol import METACLASS_FIELD
from pythonstan.analysis.pointer.kcfa.points_to_set import PointsToSet
from pythonstan.analysis.pointer.kcfa.pta_query_adapter import PtaQueryAdapter
from pythonstan.ir import IRModule
from tests.pointer.kcfa.test_ai_bridge import analyze_source


def named_class(query, name):
    module = next(scope for scope in query.get_reachable_scopes() if isinstance(scope, IRModule))
    return next(obj for obj in query.points_to_at(name, module) if isinstance(obj, ClassObject))


@pytest.mark.parametrize("source,expected", [
    ("class Animal: pass\nclass Dog(Animal): pass\n", ["Dog", "Animal"]),
    ("class A: pass\nclass B(A): pass\nclass C(A): pass\nclass D(B,C): pass\n", ["D", "B", "C", "A"]),
])
def test_public_and_ai_queries_derive_actual_ordered_bases(tmp_path, source, expected):
    query, _ = analyze_source(tmp_path, source, enable_ai_intrinsics=False)
    cls = named_class(query, expected[0])
    for projection in (query.get_class_mro(cls), PtaQueryAdapter(query._state).get_class_mro(cls)):
        assert projection.order_resolved
        assert [obj.ir.name for obj in projection] == expected


def test_merged_factory_bases_are_all_retained_without_claiming_an_order(tmp_path):
    query, edges = analyze_source(tmp_path, '''
def first(): return 1
def second(): return 2
class A:
    callback = first
class B:
    callback = second
def factory(base):
    class Child(base): pass
    return Child
Left = factory(A)
Right = factory(B)
Left.callback()
Right.callback()
''', context_policy="0-cfa", enable_ai_intrinsics=False, track_unknowns=False)
    projection = query.get_class_mro(named_class(query, "Left"))
    assert not projection.order_resolved
    assert {obj.ir.name for obj in projection} == {"Child", "A", "B"}
    assert {("subject", "subject.first"), ("subject", "subject.second")} <= edges
    assert any("unresolved_mro_order" in effect["effects"] for effect in query.get_residual_effects())


def test_late_base_candidates_invalidate_live_projection_without_cache_staleness(tmp_path):
    query, _ = analyze_source(tmp_path, '''
class A: pass
class B: pass
Base = A
class Child(Base): pass
''', enable_ai_intrinsics=False)
    child, other = named_class(query, "Child"), named_class(query, "B")
    adapter = PtaQueryAdapter(query._state)
    cache = ClassInfoCache(adapter, None)
    first = cache.get_mro(child)
    assert first.order_resolved and [obj.ir.name for obj in first] == ["Child", "A"]
    variable = child.ir._kcfa_base_variables[0]
    owner = child.container_scope
    binding = query._state.get_variable(owner, owner.context, variable)
    points = query._state.get_points_to(binding).union(PointsToSet.singleton(other))
    query._state.set_points_to(binding, points)
    expanded = cache.get_mro(child)
    assert not expanded.order_resolved
    assert {obj.ir.name for obj in expanded} == {"Child", "A", "B"}
    assert cache.get_info(child).mro is None


@pytest.mark.parametrize("source,name", [
    ("class Child(external_base): pass\n", "Child"),
    ('Child = type("Child", (), {})\n', "Child"),
    ("class Meta(type): pass\nclass Child(metaclass=Meta): pass\n", "Child"),
    ("class Child(metaclass=external): pass\n", "Child"),
    ("class Child(metaclass=external()): pass\n", "Child"),
])
def test_unresolved_or_custom_class_order_is_explicit_residual(tmp_path, source, name):
    query, _ = analyze_source(tmp_path, source, enable_ai_intrinsics=False, track_unknowns=False)
    projection = query.get_class_mro(named_class(query, name))
    assert not projection.order_resolved
    assert projection.residual_reasons
    assert any("unresolved_mro_order" in effect["effects"] for effect in query.get_residual_effects())


def test_missing_explicit_metaclass_dependency_is_subscribed_and_recomputed(tmp_path):
    query, _ = analyze_source(tmp_path, "class Child(metaclass=external): pass\n", enable_ai_intrinsics=False)
    child = named_class(query, "Child")
    observed = []
    adapter = PtaQueryAdapter(query._state, on_field_read=observed.append)
    initial = adapter.get_class_mro(child)
    assert not initial.order_resolved
    owner = child.container_scope
    metadata = query._state.get_field(owner, owner.context, child, METACLASS_FIELD)
    assert metadata in observed
    ordinary_type = ObjectFactory.create_builtin_class("type", child.context)
    query._state.set_points_to(metadata, PointsToSet.singleton(ordinary_type))
    current = adapter.get_class_mro(child)
    assert current.order_resolved and list(current) == [child]
