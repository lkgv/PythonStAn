"""Independent event, fairness and replay obligations for the solver scheduler.

These tests exercise production queue removal and notifications.  The first
tests replace transfer bodies only to construct an indefinitely replenished
queue without allocating an unbounded source program.  Later tests keep real
points-to transfers and verify facts, not scheduling counters alone.
"""
import ast
from types import SimpleNamespace

import pytest

from pythonstan.analysis.pointer.kcfa.config import Config
from pythonstan.analysis.pointer.kcfa.constraints import CopyConstraint, LoadConstraint
from pythonstan.analysis.pointer.kcfa.context import CallStringContext, Scope
from pythonstan.analysis.pointer.kcfa.heap_model import attr
from pythonstan.analysis.pointer.kcfa.object import AbstractObject, AllocKind, AllocSite, ModuleObject
from pythonstan.analysis.pointer.kcfa.points_to_set import PointsToSet, reset_object_table
from pythonstan.analysis.pointer.kcfa.pointer_flow_graph import (
    InheritanceSelectorNode, NormalNode, PointerFlowEdge, PointerFlowKind,
)
from pythonstan.analysis.pointer.kcfa.processor.processor import Processor
from pythonstan.analysis.pointer.kcfa.solver import PointerSolver
from pythonstan.analysis.pointer.kcfa.state import PointerAnalysisState
from pythonstan.analysis.pointer.kcfa.variable import Variable
from pythonstan.ir import IRModule


def model(policy="fair", budget=1000, processor=None):
    context = CallStringContext((), 1)
    ir = IRModule("formal_schedule", ast.parse(""), filename="formal_schedule.py")
    module = ModuleObject(context, AllocSite(ir, AllocKind.MODULE), ir)
    scope = Scope.new(module, None, context, ir, None)
    state = PointerAnalysisState()
    state.set_internal_scope(module, scope)
    solver = PointerSolver(state, Config(max_iterations=budget, track_unknowns=False,
        log_level="ERROR", work_scheduling_policy=policy), processor or Processor())
    variable = lambda name: state.get_variable(scope, context, Variable(name))
    value = lambda name: AbstractObject(context, AllocSite(name, AllocKind.OBJECT))
    return solver, state, scope, context, variable, value


@pytest.fixture(autouse=True)
def clean_object_table():
    reset_object_table()
    yield
    reset_object_table()


def test_continuously_nonempty_kinds_get_service_within_four_selections(monkeypatch):
    solver, state, scope, context, variable, value = model(budget=40)
    seen = []
    static = (scope, context, "static")
    replay = (scope, variable("trigger"), "constraint", PointsToSet.empty())
    invocation = object()
    call = SimpleNamespace(callee=Variable("native"))
    builtin = value("builtin token")
    point = (scope, NormalNode(variable("point")), PointsToSet.singleton(value("point token")))
    state._static_constraints.append(static)
    solver._constraint_replays.append(replay)
    solver._builtin_invocations[invocation] = (scope, context, call, builtin)
    solver._queued_builtin_replays.add(invocation)
    solver._builtin_replays.append(invocation)
    state._worklist.add(point)

    def run_static(*_):
        seen.append("S"); state._static_constraints.append(static)
        return state
    def run_constraint(*_):
        seen.append("R"); solver._constraint_replays.append(replay)
    def run_builtin(*_):
        seen.append("B")
        # Removal before evaluation is part of the dirty-notification contract.
        assert invocation not in solver._queued_builtin_replays
        solver._queued_builtin_replays.add(invocation)
        solver._builtin_replays.append(invocation)
    def run_point(*_):
        seen.append("P"); state._worklist.add(point)
        return state, 1, 0
    monkeypatch.setattr(solver, "_apply_static", run_static)
    monkeypatch.setattr(solver, "_apply_constraint", run_constraint)
    monkeypatch.setattr(solver, "_apply_call", run_builtin)
    monkeypatch.setattr(solver, "_apply_dynamic", run_point)
    for _ in range(40):
        next(solver)
    assert seen == list("SRBP") * 10
    assert solver._iteration == 40
    with pytest.raises(StopIteration):
        next(solver)
    assert solver._stats["converged"] == 0
    assert solver._stats["iteration_limit_reached"] == 1


def test_fifo_item_is_not_overtaken_by_new_same_kind_work(monkeypatch):
    solver, state, scope, context, *_ = model(budget=6)
    seen = []
    state._static_constraints.append((scope, context, "oldest"))
    state._static_constraints.append((scope, context, "next"))
    def transfer(owner, ctx, token):
        seen.append(token)
        state._static_constraints.append((owner, ctx, "new-" + token))
        return state
    monkeypatch.setattr(solver, "_apply_static", transfer)
    for _ in range(6): next(solver)
    assert seen == ["oldest", "next", "new-oldest", "new-next", "new-new-oldest", "new-new-next"]


def test_point_pending_merge_preserves_original_fifo_position(monkeypatch):
    solver, state, scope, _, variable, value = model(budget=4)
    first, later = variable("first"), variable("later")
    a, b, c = value("a"), value("b"), value("c")
    solver.handle_new_points_to(first, scope, PointsToSet.singleton(a))
    solver.handle_new_points_to(later, scope, PointsToSet.singleton(c))
    solver.handle_new_points_to(first, scope, PointsToSet.singleton(b))
    seen = []
    original = solver._apply_dynamic
    def observe(owner, node, points):
        seen.append((node, set(points)))
        return original(owner, node, points)
    monkeypatch.setattr(solver, "_apply_dynamic", observe)
    solver.solve_to_fixpoint()
    assert seen == [(NormalNode(first), {a, b}), (NormalNode(later), {c})]
    assert {a, b} == set(state.get_points_to(first))


@pytest.mark.parametrize("policy", ["legacy", "fair"])
def test_constraint_registered_inside_current_trigger_processing_sees_current_fact(policy):
    solver, state, scope, context, variable, value = model(policy)
    base, payload = variable("base"), value("payload")
    receiver = value("receiver")
    rule = LoadConstraint(Variable("base"), attr("payload"), Variable("loaded"))
    class RegisterInside(Processor):
        def handle_pts(self, running, trigger, owner, delta):
            if trigger == base:
                running.add_constraint(scope, context, rule)
    solver.processor = RegisterInside()
    field = state.get_write_field(scope, context, receiver, attr("payload"))
    solver.handle_new_points_to(field, scope, PointsToSet.singleton(payload))
    solver.handle_new_points_to(base, scope, PointsToSet.singleton(receiver))
    solver.solve_to_fixpoint()
    assert payload in state.get_points_to(variable("loaded"))
    assert solver._stats["converged"] == 1


@pytest.mark.parametrize("policy", ["legacy", "fair"])
def test_late_edge_and_rule_replay_already_processed_and_future_facts(policy):
    solver, state, scope, context, variable, value = model(policy)
    source, first = variable("source"), value("first")
    solver.handle_new_points_to(source, scope, PointsToSet.singleton(first))
    solver.solve_to_fixpoint()
    # A new edge must carry already processed source facts, without replaying
    # the source through processed-only delta filtering.
    solver.add_constraint(scope, context, CopyConstraint(Variable("source"), Variable("copy")))
    solver.solve_to_fixpoint()
    assert first in state.get_points_to(variable("copy"))
    second = value("second")
    solver.handle_new_points_to(source, scope, PointsToSet.singleton(second))
    solver.solve_to_fixpoint()
    assert {first, second} <= set(state.get_points_to(variable("copy")))


@pytest.mark.parametrize("policy", ["legacy", "fair"])
def test_dirty_builtin_notification_during_its_replay_is_not_lost(policy, monkeypatch):
    solver, state, scope, context, variable, value = model(policy)
    trigger, call = variable("argument"), SimpleNamespace(callee=Variable("native"))
    invocation = object()
    solver._builtin_dependencies[trigger] = {invocation}
    solver._builtin_invocations[invocation] = (scope, context, call, value("builtin"))
    solver._queued_builtin_replays.add(invocation)
    solver._builtin_replays.append(invocation)
    observations = []
    def transfer(*_):
        observations.append(len(observations))
        if len(observations) == 1:
            solver._schedule_builtin_dependents(trigger)
    monkeypatch.setattr(solver, "_apply_call", transfer)
    solver.solve_to_fixpoint()
    assert observations == [0, 1]
    assert not solver._queued_builtin_replays
    assert solver._stats["converged"] == 1


@pytest.mark.parametrize("policy", ["legacy", "fair"])
def test_exact_budget_finalization_cannot_certify_new_pending_work(policy, monkeypatch):
    solver, state, scope, context, variable, value = model(policy, budget=1)
    state._static_constraints.append((scope, context, "last budgeted work"))
    monkeypatch.setattr(solver, "_apply_static", lambda *_: state)
    calls = []
    def finalize():
        calls.append(True)
        solver.handle_new_points_to(variable("unknown_result"), scope, PointsToSet.singleton(value("unknown")))
    monkeypatch.setattr(solver, "_finalize_unknowns_at_fixpoint", finalize)
    next(solver)
    with pytest.raises(StopIteration): next(solver)
    assert calls == [True]
    assert solver._iteration == 1
    assert solver._stats["converged"] == 0
    assert solver._stats["iteration_limit_reached"] == 1
    assert solver._stats["pending_work"] == 1


@pytest.mark.parametrize("policy", ["legacy", "fair"])
@pytest.mark.parametrize("earlier_arrives_first", [False, True])
def test_inheritance_may_provider_join_is_independent_of_arrival_order(policy, earlier_arrives_first):
    solver, state, scope, _, variable, value = model(policy)
    earlier, later, selected = variable("earlier"), variable("later"), variable("selected")
    first_value, second_value = value("earlier may-value"), value("later may-value")
    selector = InheritanceSelectorNode()
    for index, source in enumerate((earlier, later)):
        edge = PointerFlowEdge(NormalNode(source), selector, PointerFlowKind.NORMAL)
        selector.add_edge(edge, index)
        state._add_points_flow_edge(edge)
    state._add_points_flow_edge(PointerFlowEdge(selector, NormalNode(selected), PointerFlowKind.NORMAL))
    arrivals = [(earlier, first_value), (later, second_value)]
    if not earlier_arrives_first: arrivals.reverse()
    for target, obj in arrivals:
        solver.handle_new_points_to(target, scope, PointsToSet.singleton(obj))
        solver.solve_to_fixpoint()
    # Earlier nonempty may-values do not prove must-presence. A schedule cannot
    # prune the later provider merely because its facts arrived second.
    assert {first_value, second_value} <= set(state.get_points_to(selected))
