"""Independent may-fact and subscription obligations for enqueue filtering."""
import ast

import pytest

from pythonstan.analysis.pointer.kcfa.config import Config
from pythonstan.analysis.pointer.kcfa.constraints import CopyConstraint, LoadConstraint
from pythonstan.analysis.pointer.kcfa.context import CallStringContext, Scope
from pythonstan.analysis.pointer.kcfa.heap_model import attr
from pythonstan.analysis.pointer.kcfa.object import AbstractObject, AllocKind, AllocSite, ModuleObject
from pythonstan.analysis.pointer.kcfa.points_to_set import PointsToSet, reset_object_table
from pythonstan.analysis.pointer.kcfa.pointer_flow_graph import GuardNode, NormalNode, PointerFlowEdge, PointerFlowKind
from pythonstan.analysis.pointer.kcfa.processor.processor import Processor
from pythonstan.analysis.pointer.kcfa.solver import PointerSolver
from pythonstan.analysis.pointer.kcfa.state import PointerAnalysisState
from pythonstan.analysis.pointer.kcfa.variable import Variable
from pythonstan.ir.ir_statements import IRModule


@pytest.fixture
def model():
    reset_object_table()
    context = CallStringContext((), 1)
    ir = IRModule("independent_delta", ast.parse(""), filename="independent_delta.py")
    module = ModuleObject(context, AllocSite(ir, AllocKind.MODULE), ir)
    scope = Scope.new(module, None, context, ir, None)
    state = PointerAnalysisState()
    state.set_internal_scope(module, scope)
    solver = PointerSolver(state, Config(max_iterations=1000, track_unknowns=False, log_level="ERROR"), Processor())
    def variable(name):
        return state.get_variable(scope, context, Variable(name))
    def value(name):
        return AbstractObject(context, AllocSite(name, AllocKind.OBJECT))
    yield solver, state, scope, context, variable, value
    reset_object_table()


def test_context_only_seed_is_not_a_processed_pfg_fact(model):
    solver, state, scope, context, var, obj = model
    source, target = var("source"), var("target")
    value = obj("ctx-only source object")
    state.set_points_to(source, PointsToSet.singleton(value))
    assert state.get_points_to(NormalNode(source)).is_empty()
    solver.add_constraint(scope, context, CopyConstraint(Variable("source"), Variable("target")))
    solver.handle_new_points_to(source, scope, PointsToSet.singleton(value))
    solver.solve_to_fixpoint()
    assert value in state.get_points_to(NormalNode(source))
    assert value in state.get_points_to(target)


def test_pending_facts_are_not_lost_when_overlapping_requests_arrive(model):
    solver, state, scope, context, var, obj = model
    source, target = var("source"), var("target")
    a, b = obj("a"), obj("b")
    solver.add_constraint(scope, context, CopyConstraint(Variable("source"), Variable("target")))
    solver.handle_new_points_to(source, scope, PointsToSet.singleton(a))
    solver.handle_new_points_to(source, scope, PointsToSet.from_objects([a, b]))
    assert state.get_points_to(NormalNode(source)).is_empty()
    solver.solve_to_fixpoint()
    assert {a, b} <= set(state.get_points_to(target))


def test_processor_hook_on_processed_repeat_can_add_a_new_edge(model):
    solver, state, scope, _, var, obj = model
    source, later = var("source"), var("hook-created-edge")
    value = obj("hook object")
    calls = []
    class Hook(Processor):
        def handle_new_points_to(self, solver, target, owner, points):
            calls.append((target, set(points)))
            if target == source and len(calls) == 2:
                state._add_var_points_flow(source, later)
            return False
    solver.processor = Hook()
    solver.handle_new_points_to(source, scope, PointsToSet.singleton(value))
    solver.solve_to_fixpoint()
    solver.handle_new_points_to(source, scope, PointsToSet.singleton(value))
    solver.solve_to_fixpoint()
    assert len(calls) == 2
    assert value in state.get_points_to(later)


def test_guard_observation_runs_before_a_processed_target_delta_is_filtered(model):
    solver, state, scope, _, var, obj = model
    first, second, output = var("first"), var("second"), var("out")
    value = obj("same value through two source nodes")
    observations = []
    def observe(edge, points):
        observations.append((edge.source, set(points)))
        return points
    guard = GuardNode(observe)
    state._add_points_flow_edge(PointerFlowEdge(guard, NormalNode(output), PointerFlowKind.NORMAL))
    state._add_points_flow_edge(PointerFlowEdge(NormalNode(first), guard, PointerFlowKind.NORMAL))
    state._add_points_flow_edge(PointerFlowEdge(NormalNode(second), guard, PointerFlowKind.NORMAL))
    solver.handle_new_points_to(first, scope, PointsToSet.singleton(value))
    solver.solve_to_fixpoint()
    solver.handle_new_points_to(second, scope, PointsToSet.singleton(value))
    solver.solve_to_fixpoint()
    # Installing an edge invokes its transfer even on an empty source.  Count
    # actual value observations separately from those construction callbacks.
    nonempty = [(source, points) for source, points in observations if points]
    assert {source for source, _ in nonempty} == {NormalNode(first), NormalNode(second)}
    assert len(nonempty) == 2
    assert value in state.get_points_to(output)


@pytest.mark.parametrize("register_before", [False, True])
def test_late_rule_and_field_dependencies_survive_processed_duplicate(model, register_before):
    solver, state, scope, context, var, obj = model
    base = var("base")
    owner = obj("mapping owner")
    old, late = obj("old payload"), obj("late payload")
    rule = LoadConstraint(Variable("base"), attr("payload"), Variable("target"))
    if register_before:
        solver.add_constraint(scope, context, rule)
    solver.handle_new_points_to(base, scope, PointsToSet.singleton(owner))
    field = state.get_write_field(scope, context, owner, attr("payload"))
    solver.handle_new_points_to(field, scope, PointsToSet.singleton(old))
    solver.solve_to_fixpoint()
    solver.handle_new_points_to(base, scope, PointsToSet.singleton(owner))
    if not register_before:
        solver.add_constraint(scope, context, rule)
    solver.solve_to_fixpoint()
    assert old in state.get_points_to(var("target"))
    solver.handle_new_points_to(field, scope, PointsToSet.from_objects([old, late]))
    solver.solve_to_fixpoint()
    assert {old, late} <= set(state.get_points_to(var("target")))
    solver.add_constraint(scope, context, LoadConstraint(Variable("base"), attr("payload"), Variable("later_target")))
    solver.solve_to_fixpoint()
    assert {old, late} <= set(state.get_points_to(var("later_target")))
