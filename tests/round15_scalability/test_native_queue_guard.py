"""Native queue conservation and guarded-boundary controls for Round15.

Operation counts are an explicit CPython mechanism probe, not speed gold.
Neither custom target code nor a third-party project is executed.
"""
from collections import deque
from dataclasses import replace
import random

import pytest

from pythonstan.analysis.pointer.kcfa import bootstrap
from pythonstan.analysis.pointer.kcfa import state as native_state
from pythonstan.analysis.pointer.kcfa.context import Ctx
from pythonstan.analysis.pointer.kcfa.pointer_flow_graph import (
    GuardNode, NormalNode, PointerFlowEdge, PointerFlowKind,
)
from pythonstan.analysis.pointer.kcfa.points_to_set import PointsToSet, reset_object_table
from pythonstan.analysis.pointer.kcfa.variable import Variable
from tests.round14_bootstrap.test_guarded_publication import _driver, _global, _scope, _seed


@pytest.fixture(autouse=True)
def _arena():
    reset_object_table()
    yield
    reset_object_table()


def _facts(points):
    return {(lane, bit) for lane, mask in enumerate((points.objs_mask,
            points.classmethods_mask, points.instancemethods_mask))
            for bit in range(mask.bit_length()) if mask & (1 << bit)}


def _point(lane, bit):
    masks = [0, 0, 0]
    masks[lane] = 1 << bit
    return PointsToSet(*masks)


def _node():
    return GuardNode(lambda *_: pytest.fail("queue operations must not execute a guard"))


def test_fifo_pending_union_and_delivery_follow_an_independent_three_lane_ledger():
    queue = native_state.Worklist()
    nodes = [_node() for _ in range(7)]
    randomizer = random.Random(15091)
    pending, first, order = {}, {}, deque()
    added, delivered = set(), set()
    for step in range(400):
        if pending and randomizer.randrange(4) == 0:
            scope, node, points = queue.pop()
            assert node is order.popleft()
            assert scope is first.pop(node)
            assert _facts(points) == pending.pop(node)
            delivered.update((node, fact) for fact in _facts(points))
        else:
            node = randomizer.choice(nodes)
            points = _point(randomizer.randrange(3), randomizer.randrange(9))
            scope = object()
            old = queue.items_dict.get(node)
            if node not in pending:
                order.append(node)
                first[node] = scope
                pending[node] = set()
            pending[node].update(_facts(points))
            added.update((node, fact) for fact in _facts(points))
            assert queue.add((scope, node, points)) is None
            if old is not None:
                assert queue.items_dict[node] is old
        assert len(queue) == len(pending) == len(queue.items_list)
        assert [item[1] for item in queue.items_list] == list(order)
    while pending:
        scope, node, points = queue.pop()
        assert node is order.popleft() and scope is first.pop(node)
        assert _facts(points) == pending.pop(node)
        delivered.update((node, fact) for fact in _facts(points))
    assert added == delivered and queue.empty()


def test_fresh_equal_keys_keep_first_node_scope_box_and_fifo_position():
    _, scope = _scope()
    ctx = Ctx(scope.context, scope, Variable("target"))
    first, equal = NormalNode(ctx), NormalNode(replace(ctx))
    assert first == equal and first is not equal
    queue = native_state.Worklist()
    queue.add((scope, first, _point(0, 1)))
    original = queue.items_dict[first]
    later = _node()
    queue.add((None, later, _point(1, 2)))
    queue.add((object(), equal, _point(2, 3)))
    assert queue.items_dict[first] is original
    output = queue.pop()
    assert output[0] is scope and output[1] is first
    assert _facts(output[2]) == {(0, 1), (2, 3)}
    assert queue.pop()[1] is later


def test_pure_hash_collisions_do_not_merge_distinct_nodes():
    class Collision(GuardNode):
        def __hash__(self):
            return 17
    left, right = Collision(None), Collision(None)
    queue = native_state.Worklist()
    queue.add((None, left, _point(0, 0)))
    queue.add((None, right, _point(1, 0)))
    queue.add((None, left, _point(2, 0)))
    assert len(queue) == 2
    assert _facts(queue.pop()[2]) == {(0, 0), (2, 0)}
    assert _facts(queue.pop()[2]) == {(1, 0)}


def test_duplicate_native_queue_requests_allocate_only_one_box(monkeypatch):
    allocations = []
    box = native_state.Box
    monkeypatch.setattr(native_state, "Box", lambda value: allocations.append(value) or box(value))
    queue, node = native_state.Worklist(), _node()
    for lane in range(3):
        queue.add((None, node, _point(lane, lane)))
    assert len(allocations) == 1
    assert _facts(queue.pop()[2]) == {(0, 0), (1, 1), (2, 2)}


def test_duplicate_native_queue_requests_do_one_lookup_per_merge():
    hashes = []
    class Counted(GuardNode):
        def __hash__(self):
            hashes.append(True)
            return id(self)
    queue, node = native_state.Worklist(), Counted(None)
    for lane in range(3):
        queue.add((None, node, _point(lane, lane)))
    assert len(hashes) == 4  # First lookup+insert, then one lookup for each merge.


def test_custom_mapping_keeps_legacy_allocation_and_callback_order(monkeypatch):
    events = []
    class Store(dict):
        def __contains__(self, key):
            events.append("contains")
            return super().__contains__(key)
        def __getitem__(self, key):
            events.append("getitem")
            return super().__getitem__(key)
        def __setitem__(self, key, value):
            events.append("setitem")
            return super().__setitem__(key, value)
        def get(self, *args):
            pytest.fail("native fast path must not dispatch a custom get hook")
    box = native_state.Box
    monkeypatch.setattr(native_state, "Box", lambda value: events.append("box") or box(value))
    queue, node = native_state.Worklist(), _node()
    queue.items_dict = Store()
    queue.add((None, node, _point(0, 1)))
    queue.add((None, node, _point(1, 2)))
    assert events == ["box", "contains", "setitem", "box", "contains", "getitem"]
    assert _facts(queue.pop()[2]) == {(0, 1), (1, 2)}


def test_corrupt_present_none_is_not_silently_treated_as_absent():
    queue, node = native_state.Worklist(), _node()
    queue.items_dict[node] = None
    with pytest.raises(TypeError):
        queue.add((None, node, _point(0, 0)))
    assert queue.items_dict[node] is None and not queue.items_list


def test_after_work_without_saved_contract_does_not_read_an_admission_signature(monkeypatch):
    _, scope = _scope()
    driver = _driver()
    driver.solver._work_iteration_started = True
    owner = _global(driver, scope, "len")
    monkeypatch.setattr(bootstrap, "_signature", lambda *_: pytest.fail("late decline must stay cheap"))
    assert bootstrap.try_publish_builtin_seed(driver.solver, scope, owner, PointsToSet.empty()) is False


def test_after_work_with_saved_contract_still_refuses_mutation_before_decline():
    _, scope = _scope()
    driver = _driver()
    rows = _seed(driver, scope)
    variable, points = rows.seeds[0]
    owner = driver.state.get_variable(scope, scope.context, variable)
    driver.solver._work_iteration_started = True
    driver.solver.processor.handle_pts = lambda *_: None
    with pytest.raises(RuntimeError, match="bootstrap contract"):
        bootstrap.try_publish_builtin_seed(driver.solver, scope, owner, points)
    assert driver.solver._bootstrap_invalid and driver.solver._inspector_tainted


def test_fully_admitted_seed_checks_remove_only_adjacent_repetition(monkeypatch):
    _, scope = _scope()
    driver = _driver()
    count = 0
    signature = bootstrap._signature
    def observed(solver):
        nonlocal count
        count += 1
        return signature(solver)
    monkeypatch.setattr(bootstrap, "_signature", observed)
    rows = _seed(driver, scope)
    driver.solver.solve_to_fixpoint()
    publications = len(rows.seeds)
    assert driver.solver._bootstrap_publications == publications
    assert driver.solver._iteration == 0 and driver.solver._pending_work_count() == 0
    assert count == 3 * publications + 2


@pytest.mark.parametrize("entry", ["public", "private", "iter"])
def test_mutation_before_empty_continuation_or_reset_is_refused_and_sticky(entry):
    _, scope = _scope()
    driver = _driver()
    _seed(driver, scope)
    old = driver.state._env
    driver.state._env = dict(old)
    operation = {"public": lambda: next(driver.solver),
                 "private": driver.solver._next_work_item,
                 "iter": lambda: iter(driver.solver)}[entry]
    with pytest.raises(RuntimeError, match="bootstrap contract"):
        operation()
    driver.state._env = old
    with pytest.raises(RuntimeError, match="bootstrap contract"):
        operation()


def test_inspector_guard_runs_before_boundary_metadata_changes():
    _, scope = _scope()
    driver = _driver()
    _seed(driver, scope)
    driver.solver._inspector_enabled = True  # Controlled unsupported late mutation.
    boundary = driver.solver._inspector_boundary
    with pytest.raises(RuntimeError, match="bootstrap contract"):
        next(driver.solver)
    assert driver.solver._inspector_boundary == boundary
    assert driver.solver._inspector_step_depth == 0


def test_public_entry_refuses_sealed_mutation_before_replaced_private_dispatch():
    _, scope = _scope()
    driver = _driver()
    _seed(driver, scope)
    old_env = driver.state._env
    driver.state._env = dict(old_env)
    calls = []
    driver.solver._next_work_item = lambda: calls.append("foreign_private") or "unsafe"
    with pytest.raises(RuntimeError, match="bootstrap contract"):
        next(driver.solver)
    assert not calls
    driver.state._env = old_env
    del driver.solver._next_work_item
    with pytest.raises(RuntimeError, match="bootstrap contract"):
        next(driver.solver)


@pytest.mark.parametrize("entry", ["public", "private"])
def test_mutation_inside_selected_transfer_is_rejected_by_the_retained_postcheck(entry):
    from pythonstan.analysis.pointer.kcfa.object import ObjectFactory
    _, scope = _scope()
    driver = _driver()
    _seed(driver, scope)
    source = _global(driver, scope, "source")
    old_env = driver.state._env
    def mutate(_edge, points):
        driver.state._env = dict(driver.state._env)
        return points
    guard = GuardNode(mutate)
    driver.state.pointer_flow_graph.add_edge(PointerFlowEdge(NormalNode(source), guard, PointerFlowKind.NORMAL))
    points = PointsToSet.singleton(ObjectFactory.create_builtin_function("payload", scope.context))
    driver.solver.handle_new_points_to(source, scope, points)
    operation = (lambda: next(driver.solver)) if entry == "public" else driver.solver._next_work_item
    with pytest.raises(RuntimeError, match="bootstrap contract"):
        operation()
    assert driver.state.get_points_to(source) == points
    assert driver.solver._bootstrap_invalid and driver.solver._inspector_tainted
    driver.state._env = old_env
    with pytest.raises(RuntimeError, match="bootstrap contract"):
        operation()
