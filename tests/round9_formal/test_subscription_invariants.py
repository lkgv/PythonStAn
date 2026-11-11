"""Independent invariants for subscription indexing and replay optimization."""
import ast

import pytest

from pythonstan.analysis.pointer.kcfa.config import Config
from pythonstan.analysis.pointer.kcfa.constraints import ConstraintManager, CopyConstraint, LoadConstraint
from pythonstan.analysis.pointer.kcfa.context import CallStringContext, Scope
from pythonstan.analysis.pointer.kcfa.heap_model import attr
from pythonstan.analysis.pointer.kcfa.object import AbstractObject, AllocKind, AllocSite, ModuleObject
from pythonstan.analysis.pointer.kcfa.points_to_set import PointsToSet, reset_object_table
from pythonstan.analysis.pointer.kcfa.processor.processor import Processor
from pythonstan.analysis.pointer.kcfa.solver import PointerSolver
from pythonstan.analysis.pointer.kcfa.state import PointerAnalysisState
from pythonstan.analysis.pointer.kcfa.variable import Variable
from pythonstan.ir.ir_statements import IRModule


def test_canonical_rule_survives_partial_trigger_removal_and_readdition():
    manager = ConstraintManager()
    rule = CopyConstraint(Variable("x"), Variable("y"))
    scope = "same defining scope"
    first, second = Variable("first"), Variable("second")
    assert manager.add(scope, first, rule)
    assert manager.add(scope, second, rule)
    assert not manager.add(scope, first, rule)
    assert len(manager) == 1
    assert manager.get_by_type(CopyConstraint) == [rule]
    assert manager.remove(scope, first, rule)
    assert not manager.remove(scope, first, rule)
    assert manager.get_by_type(CopyConstraint) == [rule]
    assert manager.iter_scoped_by_variable(second) == [(scope, rule)]
    assert manager.add(scope, first, rule)
    assert len(manager) == 1
    assert manager.remove(scope, second, rule)
    assert manager.get_by_type(CopyConstraint) == [rule]
    assert manager.remove(scope, first, rule)
    assert len(manager) == 0
    assert manager.get_by_type(CopyConstraint) == []
    assert manager.add(scope, first, rule)
    assert len(manager) == 1


def test_membership_does_not_merge_equal_syntax_from_distinct_scopes_or_mutate_snapshot():
    manager = ConstraintManager()
    trigger = Variable("shared")
    rule = CopyConstraint(Variable("x"), Variable("y"))
    later = CopyConstraint(Variable("x"), Variable("z"))
    assert manager.add("left scope", trigger, rule)
    snapshot = manager.iter_scoped_by_variable(trigger)
    assert manager.add("right scope", trigger, rule)
    assert manager.add("left scope", trigger, later)
    assert snapshot == [("left scope", rule)]
    assert manager.iter_scoped_by_variable(trigger) == [
        ("left scope", rule), ("right scope", rule), ("left scope", later)]
    assert len(manager) == 3
    assert manager.remove("left scope", trigger, rule)
    assert ("right scope", rule) in manager.all()
    assert len(manager) == 2


@pytest.fixture
def model():
    reset_object_table()
    context = CallStringContext((), 1)
    ir = IRModule("subscriptions", ast.parse(""), filename="subscriptions.py")
    module = ModuleObject(context, AllocSite(ir, AllocKind.MODULE), ir)
    scope = Scope.new(module, None, context, ir, None)
    state = PointerAnalysisState()
    state.set_internal_scope(module, scope)
    solver = PointerSolver(state, Config(max_iterations=1000, track_unknowns=False, log_level="ERROR"), Processor())
    yield solver, state, scope, context
    reset_object_table()


@pytest.mark.parametrize("late_rule", [False, True])
def test_rule_index_optimization_preserves_prior_and_future_heap_facts(model, late_rule):
    solver, state, scope, context = model
    base, target = Variable("base"), Variable("target")
    variable = state.get_variable(scope, context, base)
    result = state.get_variable(scope, context, target)
    first = AbstractObject(context, AllocSite("first owner", AllocKind.OBJECT))
    second = AbstractObject(context, AllocSite("second owner", AllocKind.OBJECT))
    values = [AbstractObject(context, AllocSite(label, AllocKind.OBJECT))
              for label in ("prior value", "late field value", "new owner value")]
    rule = LoadConstraint(base, attr("payload"), target)
    if not late_rule:
        solver.add_constraint(scope, context, rule)
    solver.handle_new_points_to(variable, scope, PointsToSet.singleton(first))
    field = state.get_write_field(scope, context, first, attr("payload"))
    solver.handle_new_points_to(field, scope, PointsToSet.singleton(values[0]))
    solver.solve_to_fixpoint()
    if late_rule:
        solver.add_constraint(scope, context, rule)
        solver.add_constraint(scope, context, rule)
    solver.solve_to_fixpoint()
    assert values[0] in state.get_points_to(result)
    solver.handle_new_points_to(field, scope, PointsToSet.singleton(values[1]))
    solver.handle_new_points_to(variable, scope, PointsToSet.singleton(second))
    second_field = state.get_write_field(scope, context, second, attr("payload"))
    solver.handle_new_points_to(second_field, scope, PointsToSet.singleton(values[2]))
    solver.solve_to_fixpoint()
    assert set(values) <= set(state.get_points_to(result))
    assert solver._stats["converged"] == 1
