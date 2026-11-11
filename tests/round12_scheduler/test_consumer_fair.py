"""Independent pending-fact conservation and finite service obligations."""
import ast
import random

import pytest

from pythonstan.analysis.pointer.kcfa.config import Config
from pythonstan.analysis.pointer.kcfa.points_to_set import PointsToSet, reset_object_table
from pythonstan.analysis.pointer.kcfa.pointer_flow_graph import GuardNode, NormalNode
from pythonstan.analysis.pointer.kcfa.worklist import ConsumerFairWorklist


def _facts(points):
    # An independent finite set interpretation, not the queue's union helper.
    return {(lane, index) for lane, mask in enumerate((points.objs_mask,
        points.classmethods_mask, points.instancemethods_mask))
        for index in range(mask.bit_length()) if mask & (1 << index)}


def _one(lane, index):
    values = [0, 0, 0]
    values[lane] = 1 << index
    return PointsToSet(*values)


def _node():
    def forbidden(*args):
        raise AssertionError("queue classification executed a guard")
    return GuardNode(forbidden)


def test_pending_facts_equal_an_independent_set_ledger_after_action_sequences():
    queue = ConsumerFairWorklist()
    nodes = [_node() for _ in range(7)]
    pending, first_scopes = {}, {}
    added, delivered = set(), set()
    randomizer = random.Random(120054)
    for action in range(1000):
        node = randomizer.choice(nodes)
        operation = randomizer.randrange(5)
        if operation < 3:
            point = _one(randomizer.randrange(3), randomizer.randrange(11))
            scope = ("scope", action)
            old_item = queue.items_dict.get(node)
            pending.setdefault(node, set()).update(_facts(point))
            first_scopes.setdefault(node, scope)
            added.update((node, fact) for fact in _facts(point))
            if operation == 0:
                queue.add_seed((scope, node, point), cold=True)
            else:
                queue.add((scope, node, point))
            if old_item is not None:
                assert queue.items_dict[node] is old_item
        elif operation == 3:
            queue.promote(node)
        elif pending:
            scope, actual, points = queue.pop()
            assert scope == first_scopes.pop(actual)
            assert _facts(points) == pending.pop(actual)
            delivered.update((actual, fact) for fact in _facts(points))
        assert set(queue.items_dict) == set(pending)
        observed = list(queue.iter_pending())
        assert len(observed) == len(queue) == len(pending)
        assert len({row[1] for row in observed}) == len(observed)
        assert all(row is queue.items_dict[row[1]] for row in observed)
        diagnostic = queue.diagnostics()
        assert diagnostic["pending_hot"] + diagnostic["pending_cold"] == len(pending)
    while pending:
        scope, node, points = queue.pop()
        assert scope == first_scopes.pop(node)
        assert _facts(points) == pending.pop(node)
        delivered.update((node, fact) for fact in _facts(points))
    assert queue.empty() and not queue.items_dict and added == delivered
    with pytest.raises(IndexError):
        queue.pop()


def test_persistent_hot_arrivals_cannot_starve_a_cold_head():
    queue = ConsumerFairWorklist()
    cold = _node()
    queue.add_seed((None, cold, _one(0, 0)), cold=True)
    observed = []
    for index in range(ConsumerFairWorklist.HOT_QUOTA + 1):
        queue.add((None, _node(), _one(0, index + 1)))
        observed.append(queue.pop()[1])
    assert observed[-1] is cold
    assert all(node is not cold for node in observed[:-1])


def test_merges_and_repeated_promotions_cannot_reposition_hot_items_or_recool():
    queue = ConsumerFairWorklist()
    first, second, cold = _node(), _node(), _node()
    queue.add((None, first, _one(0, 0)))
    queue.add((None, second, _one(0, 1)))
    queue.add_seed((None, cold, _one(0, 2)), cold=True)
    original = queue.items_dict[cold]
    assert queue.promote(cold)
    assert not queue.promote(cold)
    queue.add_seed((None, cold, _one(1, 2)), cold=True)
    queue.add((None, first, _one(2, 3)))
    assert queue.items_dict[cold] is original
    assert queue.diagnostics()["pending_cold"] == 0
    rows = [queue.pop() for _ in range(3)]
    assert [row[1] for row in rows] == [first, second, cold]
    assert _facts(rows[0][2]) == {(0, 0), (2, 3)}
    assert _facts(rows[2][2]) == {(0, 2), (1, 2)}


def test_ordinary_delta_promotes_cold_without_changing_canonical_scope():
    queue = ConsumerFairWorklist()
    node = _node()
    first_scope, other_scope = object(), object()
    queue.add_seed((first_scope, node, _one(0, 0)), cold=True)
    queue.add((other_scope, node, _one(1, 1)))
    assert queue.diagnostics()["pending_cold"] == 0
    scope, actual, points = queue.pop()
    assert scope is first_scope and actual is node
    assert _facts(points) == {(0, 0), (1, 1)}


@pytest.mark.parametrize("bad", [None, True, [], "consumer", "", 3])
def test_points_work_policy_is_validated(bad):
    with pytest.raises(ValueError, match="points_work_policy"):
        Config(points_work_policy=bad)


def test_configuration_roundtrip_keeps_fifo_default_and_explicit_policy():
    assert Config().points_work_policy == "fifo"
    for policy in ("fifo", "consumer_fair"):
        config = Config(points_work_policy=policy)
        assert Config.from_dict(config.to_dict()) == config


def test_reused_consumer_state_cannot_silently_claim_fifo_configuration():
    from pythonstan.analysis.pointer.kcfa.processor.processor import Processor
    from pythonstan.analysis.pointer.kcfa.solver import PointerSolver
    reset_object_table()
    try:
        solver, _, _, _ = _small_solver()
        with pytest.raises(ValueError, match="worklist|policy|state"):
            PointerSolver(solver.state, Config(points_work_policy="fifo"), Processor())
    finally:
        reset_object_table()


def _arena():
    from pythonstan.analysis.pointer.kcfa.context import CallStringContext, Scope
    from pythonstan.analysis.pointer.kcfa.object import AbstractObject, AllocKind, AllocSite, ModuleObject
    from pythonstan.ir import IRModule
    context = CallStringContext((), 1)
    module = IRModule("consumer_control", ast.parse(""), filename="consumer_control.py")
    module_obj = ModuleObject(context, AllocSite(module, AllocKind.MODULE), module)
    scope = Scope.new(module_obj, None, context, module, None)
    payloads = [AbstractObject(context, AllocSite("payload" + str(i), AllocKind.OBJECT)) for i in range(32)]
    return context, module_obj, scope, payloads


def _run_arena(arena, policy, budget):
    from pythonstan.analysis.pointer.kcfa.constraints import CopyConstraint
    from pythonstan.analysis.pointer.kcfa.processor.processor import Processor
    from pythonstan.analysis.pointer.kcfa.solver import PointerSolver
    from pythonstan.analysis.pointer.kcfa.state import PointerAnalysisState
    from pythonstan.analysis.pointer.kcfa.variable import Variable
    context, module_obj, scope, payloads = arena
    state = PointerAnalysisState()
    state.set_internal_scope(module_obj, scope)
    solver = PointerSolver(state, Config(work_scheduling_policy="fair",
        points_work_policy=policy, log_level="ERROR", max_iterations=budget,
        track_unknowns=False), Processor())
    targets = []
    for index, obj in enumerate(payloads):
        source_var, target_var = Variable("source" + str(index)), Variable("target" + str(index))
        source = state.get_variable(scope, context, source_var)
        targets.append(state.get_variable(scope, context, target_var))
        solver.add_constraint(scope, context, CopyConstraint(source_var, target_var))
        item = (scope, NormalNode(source), PointsToSet.singleton(obj))
        if policy == "consumer_fair":
            state._worklist.add_seed(item, cold=True)
        else:
            state._worklist.add(item)
    solver.solve_to_fixpoint()
    return solver, sum(bool(state.get_points_to(target)) for target in targets)


def test_same_budget_prefix_progress_and_entire_completed_environment_equality():
    reset_object_table()
    try:
        arena = _arena()
        fifo, fifo_targets = _run_arena(arena, "fifo", 16)
        consumer, consumer_targets = _run_arena(arena, "consumer_fair", 16)
        assert fifo._stats["iterations"] == consumer._stats["iterations"] == 16
        assert fifo._stats["converged"] == consumer._stats["converged"] == 0
        assert fifo._stats["pending_work"] > 0 and consumer._stats["pending_work"] > 0
        assert consumer_targets > fifo_targets
        completed = [_run_arena(arena, policy, 320)[0] for policy in ("fifo", "consumer_fair")]
        assert all(solver._stats["converged"] == 1 for solver in completed)
        left, right = (solver.state for solver in completed)
        # Native identities are shared between arms: this compares ALL env
        # entries, including node/Ctx mirrors, without names or exclusions.
        assert left._env == right._env
        assert left._pointer_flow_graph.edges == right._pointer_flow_graph.edges
        assert left._heap.heap == right._heap.heap
        assert left._heap.objects == right._heap.objects
        assert left.call_graph.edges == right.call_graph.edges
        assert left.constraints._constraints == right.constraints._constraints
        assert left.constraints._by_variable == right.constraints._by_variable
    finally:
        reset_object_table()


def _small_solver():
    from pythonstan.analysis.pointer.kcfa.processor.processor import Processor
    from pythonstan.analysis.pointer.kcfa.solver import PointerSolver
    from pythonstan.analysis.pointer.kcfa.state import PointerAnalysisState
    context, module_obj, scope, payloads = _arena()
    state = PointerAnalysisState()
    state.set_internal_scope(module_obj, scope)
    solver = PointerSolver(state, Config(work_scheduling_policy="fair",
        points_work_policy="consumer_fair", log_level="ERROR", max_iterations=500,
        track_unknowns=False), Processor())
    return solver, context, scope, payloads


def test_late_pfg_consumer_promotes_empty_pending_source_then_replays_facts():
    from pythonstan.analysis.pointer.kcfa.variable import Variable
    reset_object_table()
    try:
        solver, context, scope, payloads = _small_solver()
        state = solver.state
        source = state.get_variable(scope, context, Variable("source"))
        target = state.get_variable(scope, context, Variable("target"))
        points = PointsToSet.singleton(payloads[0])
        state._worklist.add_seed((scope, NormalNode(source), points), cold=True)
        assert state.get_points_to(source).is_empty()
        state._add_var_points_flow(source, target)
        assert state._worklist.diagnostics()["pending_cold"] == 0
        solver.solve_to_fixpoint()
        assert state.get_points_to(target) == points
        # An already-processed source still obeys native late-edge replay.
        later = state.get_variable(scope, context, Variable("later"))
        state._add_var_points_flow(source, later)
        solver.solve_to_fixpoint()
        assert state.get_points_to(later) == points
    finally:
        reset_object_table()


def test_late_dynamic_subscription_promotes_pending_source_and_preserves_replay():
    from pythonstan.analysis.pointer.kcfa.constraints import LoadConstraint
    from pythonstan.analysis.pointer.kcfa.heap_model import attr
    from pythonstan.analysis.pointer.kcfa.variable import Variable
    reset_object_table()
    try:
        solver, context, scope, payloads = _small_solver()
        state = solver.state
        source_var, target_var = Variable("source"), Variable("target")
        source = state.get_variable(scope, context, source_var)
        target = state.get_variable(scope, context, target_var)
        field = state.get_field(scope, context, payloads[0], attr("value"))
        result_points = PointsToSet.singleton(payloads[1])
        state._worklist.add((None, NormalNode(field), result_points))
        state._worklist.add_seed((scope, NormalNode(source), PointsToSet.singleton(payloads[0])), cold=True)
        solver.add_constraint(scope, context, LoadConstraint(source_var, attr("value"), target_var))
        assert state._worklist.diagnostics()["pending_cold"] == 0
        solver.solve_to_fixpoint()
        assert state.get_points_to(target) == result_points
        later_var = Variable("later")
        later = state.get_variable(scope, context, later_var)
        solver.add_constraint(scope, context, LoadConstraint(source_var, attr("value"), later_var))
        assert solver._constraint_replays
        solver.solve_to_fixpoint()
        assert state.get_points_to(later) == result_points
    finally:
        reset_object_table()


def test_builtin_dependency_registration_promotes_its_pending_argument():
    from pythonstan.analysis.pointer.kcfa.constraints import CallConstraint
    from pythonstan.analysis.pointer.kcfa.context import CallSite
    from pythonstan.analysis.pointer.kcfa.object import ObjectFactory
    from pythonstan.analysis.pointer.kcfa.variable import Variable
    from pythonstan.ir import IRCall
    reset_object_table()
    try:
        solver, context, scope, payloads = _small_solver()
        source_var = Variable("source")
        source = solver.state.get_variable(scope, context, source_var)
        solver.state._worklist.add_seed((scope, NormalNode(source), PointsToSet.singleton(payloads[0])), cold=True)
        statement = IRCall(ast.parse("output = len(source)").body[0])
        call = CallConstraint(Variable("len"), (source_var,), frozenset(), Variable("output"),
                              CallSite(statement, "consumer_control", 0))
        invocation = solver._register_builtin_dependencies(scope, context, call,
            ObjectFactory.create_builtin_function("len", context))
        assert solver.state._worklist.diagnostics()["pending_cold"] == 0
        assert invocation in solver._builtin_dependencies[source]
    finally:
        reset_object_table()
