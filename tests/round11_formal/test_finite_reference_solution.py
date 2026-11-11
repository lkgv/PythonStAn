"""Independent reachability oracle for a finite monotone points fragment.

The oracle computes a graph's reflexive/transitive reachability, without using
the production worklist or PFG.  It checks all source-variable facts, including
empty/unreachable variables, after differently timed edge registration.
"""
import random

import pytest

from pythonstan.analysis.pointer.kcfa.constraints import CopyConstraint
from pythonstan.analysis.pointer.kcfa.points_to_set import PointsToSet
from tests.round11_formal.test_scheduler_event_boundaries import clean_object_table, model


def graph_case(seed):
    randomizer = random.Random(seed)
    names = ["v" + str(i) for i in range(9)]
    # Cyclic component, self-loop, diamond, plus a disconnected final node.
    edges = {(0, 1), (1, 2), (2, 0), (2, 3), (2, 4), (3, 5), (4, 5), (5, 5)}
    for _ in range(12):
        edges.add((randomizer.randrange(8), randomizer.randrange(8)))
    edges = list(sorted(edges)); randomizer.shuffle(edges)
    seeds = {0: {"alpha"}, 3: {"beta"}, 6: {"gamma"}}
    expected = {name: set() for name in names}
    # Independent specification: a seed reaches exactly nodes having a path
    # from its source; a missing path does not gain facts from another object.
    for source, facts in seeds.items():
        reachable = {source}
        for _ in range(len(names)):
            reachable |= {target for start, target in edges if start in reachable}
        for target in reachable:
            expected[names[target]].update(facts)
    return names, edges, seeds, expected


@pytest.mark.parametrize("seed", range(12))
@pytest.mark.parametrize("policy", ["legacy", "fair"])
def test_complete_solution_matches_independent_reachability_for_late_edges(seed, policy):
    solver, state, scope, context, variable, value = model(policy, budget=10000)
    names, edges, seeds, expected = graph_case(seed)
    objects = {name: value(name) for name in ("alpha", "beta", "gamma")}
    for index, facts in seeds.items():
        solver.handle_new_points_to(variable(names[index]), scope,
                                   PointsToSet.from_objects(objects[name] for name in facts))
    # Process a seeded prefix before registering some edges. The actual PFG
    # registration must replay these older facts as well as future deltas.
    next(solver)
    for order, (source, target) in enumerate(edges):
        solver.add_constraint(scope, context, CopyConstraint(
            source=variable(names[source]).content, target=variable(names[target]).content))
        if order % 3 == 0:
            next(solver)
    solver.solve_to_fixpoint()
    assert solver._stats["converged"] == 1
    assert solver._stats["pending_work"] == 0
    reverse = {obj: name for name, obj in objects.items()}
    actual = {name: {reverse[obj] for obj in state.get_points_to(variable(name))} for name in names}
    assert actual == expected


def test_passive_observer_payload_is_detached_from_solver_bookkeeping():
    solver, state, scope, context, variable, value = model("fair", budget=100)
    source, payload = variable("source"), value("payload")
    solver.handle_new_points_to(source, scope, PointsToSet.singleton(payload))
    events = []
    def observer(event):
        events.append((event["kind"], event["iteration"]))
        # Mutating a received observation is not mutation of canonical queues,
        # cursor or counters. Callback access to the solver itself is trusted.
        event["cursor"] = 200
        event["pending_work_by_kind"]["points_to"] = 900
    solver.set_work_step_observer(observer)
    solver.solve_to_fixpoint()
    assert events == [("points_to", 1)]
    assert solver._work_scheduler_cursor in range(4)
    assert solver._pending_work_by_kind()["points_to"] == 0
    assert solver._stats["work_items_points_to"] == 1
    assert payload in state.get_points_to(source)


def test_fair_continuation_preserves_original_budget_after_completed_prefix():
    solver, state, scope, context, variable, value = model("fair", budget=2)
    solver.handle_new_points_to(variable("first"), scope, PointsToSet.singleton(value("first")))
    solver.solve_to_fixpoint()
    assert solver._iteration == 1
    solver.handle_new_points_to(variable("second"), scope, PointsToSet.singleton(value("second")))
    solver.handle_new_points_to(variable("third"), scope, PointsToSet.singleton(value("third")))
    solver.solve_to_fixpoint()
    assert solver._iteration == 2
    assert solver._stats["converged"] == 0
    assert solver._stats["iteration_limit_reached"] == 1
    assert solver._stats["pending_work"] == 1
