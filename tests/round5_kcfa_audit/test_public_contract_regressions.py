"""Adversarial checks for genuine defects hidden by obsolete KCFA fixtures."""
import ast
import pytest
from tests.pointer.kcfa.builders import make_scope, make_site
from tests.pointer.kcfa.test_call_semantics import _analyze
from pythonstan.analysis.pointer.kcfa import AllocKind, AllocSite, PointsToSet, Variable, VariableKind
from pythonstan.analysis.pointer.kcfa.context import Ctx
from pythonstan.analysis.pointer.kcfa.constraints import ConstraintManager, CopyConstraint
from pythonstan.analysis.pointer.kcfa.object import AbstractObject, ConstantObject
from pythonstan.analysis.pointer.kcfa.heap_model import attr
from pythonstan.analysis.pointer.kcfa.state import PointerAnalysisState
from pythonstan.analysis.pointer.kcfa.class_hierarchy import ClassHierarchyManager


def test_same_coordinates_do_not_merge_distinct_ir_allocations():
    first = make_site("same.py", 4, 8)
    second = make_site("same.py", 4, 8)
    assert (first.line, first.col) == (second.line, second.col) == (4, 8)
    assert first != second
    assert AllocSite(first.stmt, first.kind) == first
    assert len({first, second, AllocSite(first.stmt, first.kind)}) == 2


def test_synthetic_allocation_has_no_source_coordinates():
    site = AllocSite("builtin:object", AllocKind.OBJECT)
    assert (site.line, site.col) == (0, 0)


def test_ir_source_location_uses_python_lineno():
    from pythonstan.ir.ir_statements import IRAssign
    statement = IRAssign(ast.parse("\n\nvalue = []").body[0])
    site = AllocSite.from_ir_node(statement, AllocKind.LIST)
    assert (site.line, site.col) == (3, 0)


def test_constraint_registration_is_per_qualified_trigger():
    scope = make_scope("triggers", "module")
    x, y = Variable("x"), Variable("y")
    first, second = Ctx(scope.context, scope, x), Ctx(scope.context, scope, y)
    constraint = CopyConstraint(x, y)
    manager = ConstraintManager()
    assert manager.add(scope, first, constraint)
    assert manager.add(scope, second, constraint)
    assert not manager.add(scope, first, constraint)
    assert len(manager) == 1
    assert manager.get_by_type(CopyConstraint) == [constraint]
    assert manager.get_by_variable(first) == manager.get_by_variable(second) == [constraint]
    assert manager.remove(scope, first, constraint)
    assert not manager.remove(scope, first, constraint)
    assert not manager.get_by_variable(first)
    assert manager.get_by_variable(second) == [constraint]
    assert len(manager) == 1
    assert manager.remove(scope, second, constraint)
    assert len(manager) == 0
    assert list(manager.iter_scoped()) == []
    assert manager.get_by_type(CopyConstraint) == []


def test_constraint_removal_does_not_remove_other_scope():
    left, right = make_scope("left", "module"), make_scope("right", "module")
    x, y = Variable("x"), Variable("y")
    trigger = Ctx(left.context, left, x)
    constraint = CopyConstraint(x, y)
    manager = ConstraintManager()
    manager.add(left, trigger, constraint)
    manager.add(right, trigger, constraint)
    assert manager.remove(left, trigger, constraint)
    assert manager.iter_scoped_by_variable(trigger) == [(right, constraint)]
    assert manager.all() == {(right, constraint)}


def test_statistics_count_variables_fields_and_unreferenced_allocations():
    scope = make_scope("statistics", "module")
    state = PointerAnalysisState()
    owner = AbstractObject(scope.context, make_site())
    value = AbstractObject(scope.context, make_site())
    unreachable = AbstractObject(scope.context, make_site())
    location = state.get_variable(scope, scope.context, Variable("x", VariableKind.GLOBAL))
    state.set_points_to(location, PointsToSet.from_objects([owner, value]))
    field = state.get_field(scope, scope.context, owner, attr("payload"))
    state.set_points_to(field, PointsToSet.singleton(value))
    state._heap.set_obj(scope, scope.context, unreachable.alloc_site, unreachable)
    state.get_field(scope, scope.context, owner, attr("unpopulated"))
    assert state.get_statistics() == {"num_variables": 1, "num_objects": 3, "num_heap_locations": 1, "num_call_edges": 0}


def test_update_bases_can_register_new_class_and_name():
    hierarchy = ClassHierarchyManager()
    child = make_scope("Fresh", "class").obj
    parent = make_scope("Parent", "class").obj
    hierarchy.add_class(parent)
    hierarchy.update_bases(child, [parent])
    assert hierarchy.lookup_class_by_name("Fresh") == {child}
    assert hierarchy.get_mro(child) == [child, parent]


@pytest.mark.parametrize("source,expected", [
    ("token=7\ndef f(x=token):\n return x\nresult=f()\n", 7),
    ("def f(*, token=11):\n return token\nresult=f()\n", 11),
    ("def factory(seed):\n def f(x=seed):\n  return x\n return f\nf=factory(13)\nresult=f()\n", 13),
    ("def callback():\n return 17\ndef f(fn=callback):\n return fn()\nresult=f()\n", 17),
])
def test_default_values_follow_definition_environment(source, expected):
    runtime = {}
    exec(source, runtime)
    assert runtime["result"] == expected
    analysis, module = _analyze(source)
    values = analysis.solver.query().points_to_at("result", module)
    assert {obj.value for obj in values if isinstance(obj, ConstantObject)} == {expected}
    assert analysis.solver.query().get_statistics()["converged"] == 1
