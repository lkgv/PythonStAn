"""Finite controls and counterexamples for immutable bootstrap templates.

The ordinary control intentionally discards returned templates; the candidate
uses the production run-local cache. Deliberately unsafe helpers are negative
witnesses, not alternative supported solvers.
"""
import ast
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from pythonstan.analysis.pointer.kcfa.analysis import PointerAnalysis
from pythonstan.analysis.pointer.kcfa.config import Config
from pythonstan.analysis.pointer.kcfa.context import CallStringContext, Scope
from pythonstan.analysis.pointer.kcfa.object import AllocKind, AllocSite, ModuleObject, ObjectFactory
from pythonstan.analysis.pointer.kcfa.points_to_set import PointsToSet, reset_object_table
from pythonstan.analysis.pointer.kcfa.pointer_flow_graph import NormalNode
from pythonstan.analysis.pointer.kcfa.processor.processor import Processor
from pythonstan.analysis.pointer.kcfa.solver import PointerSolver
from pythonstan.analysis.pointer.kcfa.state import PointerAnalysisState
from pythonstan.analysis.pointer.kcfa.variable import Variable, VariableKind
from pythonstan.ir import IRModule


BUILTIN_COUNT = 65  # Existing 63 plus the required aiter/anext seed support.


@pytest.fixture(autouse=True)
def isolated_object_epoch():
    reset_object_table()
    yield
    reset_object_table()


def _scopes(depth=1, count=3, context=None):
    context = context if context is not None else CallStringContext((), depth)
    scopes = []
    for index in range(count):
        module = IRModule("bootstrap_" + str(index), ast.parse(""),
                          filename="bootstrap_" + str(index) + ".py")
        obj = ModuleObject(context, AllocSite(module, AllocKind.MODULE), module)
        scopes.append(Scope.new(obj, None, context, module, None))
    return context, scopes


def _driver(policy="fifo", processor=None):
    state = PointerAnalysisState()
    config = Config(points_work_policy=policy, work_scheduling_policy="fair",
                    max_iterations=2000, log_level="ERROR", track_unknowns=False)
    solver = PointerSolver(state, config, processor or Processor())
    return SimpleNamespace(state=state, solver=solver, kcfa_config=config)


def _global(driver, scope, name):
    return driver.state.get_variable(scope, scope.context,
                                    Variable(name, VariableKind.GLOBAL))


def _pending(driver):
    return list(driver.state._worklist.items_list)


def _ordinary_seeds(driver, context, scopes):
    for scope in scopes:
        PointerAnalysis._initialize_builtins(driver, scope, context)


def _shared_template_seeds(driver, context, scopes):
    templates = None
    for scope in scopes:
        templates = PointerAnalysis._initialize_builtins(driver, scope, context, templates)
    return templates


class _PtsWitness(Processor):
    def __init__(self):
        self.publications = []
        self.producer_events = []

    def handle_new_points_to(self, solver, target, scope, points):
        self.producer_events.append((target, scope, points))
        return False

    def handle_pts(self, solver, target, scope, points):
        # This precise two-store ordering is an observable existing hook:
        # NormalNode is published; the separate Ctx is not yet updated.
        self.publications.append((target, scope, points,
            solver.state.get_points_to(NormalNode(target)),
            solver.state.get_points_to(target)))
        if target.content.name == "len":
            sink = solver.state.get_variable(scope, scope.context, Variable("hook_sink"))
            solver.state._add_var_points_flow(target, sink)
        return False


@pytest.mark.parametrize("policy", ["fifo", "consumer_fair"])
@pytest.mark.parametrize("depth", [0, 1, 2])
def test_shared_immutable_templates_preserve_nodes_hooks_and_complete_raw_state(policy, depth):
    context, scopes = _scopes(depth)
    witnesses = [_PtsWitness(), _PtsWitness()]
    left, right = (_driver(policy, processor) for processor in witnesses)
    _ordinary_seeds(left, context, scopes)
    _shared_template_seeds(right, context, scopes)
    old_items, shared_items = _pending(left), _pending(right)
    assert len(old_items) == len(shared_items) == BUILTIN_COUNT * len(scopes)
    assert {"aiter", "anext"} <= {node.var.content.name for _, node, _ in old_items}
    assert [(scope, node, box.val) for scope, node, box in old_items] == [
        (scope, node, box.val) for scope, node, box in shared_items]
    assert len({node for _, node, _ in shared_items}) == BUILTIN_COUNT * len(scopes)
    assert len({id(box.val) for _, _, box in shared_items}) == BUILTIN_COUNT
    # Builtin initialization has historically bypassed producer interception.
    assert all(not witness.producer_events for witness in witnesses)
    for driver in (left, right):
        driver.solver.solve_to_fixpoint()
        assert driver.solver._stats["converged"] == 1
        assert driver.state.get_points_to(_global(driver, scopes[0], "len"))
    assert witnesses[0].publications == witnesses[1].publications
    assert left.state._env == right.state._env
    assert left.state._heap.heap == right.state._heap.heap
    assert left.state.pointer_flow_graph.edges == right.state.pointer_flow_graph.edges
    assert left.state.constraints._constraints == right.state.constraints._constraints
    assert left.solver._builtin_dependencies == right.solver._builtin_dependencies
    assert left.solver._stats["iterations"] == right.solver._stats["iterations"]
    # Sharing immutable values must not merge distinct module-global stores.
    extra = PointsToSet.singleton(ObjectFactory.create_builtin_function("user_len", context))
    for driver in (left, right):
        source = _global(driver, scopes[0], "len")
        driver.solver.handle_new_points_to(source, scopes[0], extra)
        later = driver.state.get_variable(scopes[0], context, Variable("late_sink"))
        driver.state._add_var_points_flow(source, later)
        driver.solver.solve_to_fixpoint()
        assert extra.intersection(driver.state.get_points_to(source)) == extra
        assert extra.intersection(driver.state.get_points_to(_global(driver, scopes[1], "len"))).is_empty()
        assert driver.state.get_points_to(later) == driver.state.get_points_to(source)
    assert left.state._env == right.state._env
    assert witnesses[0].publications == witnesses[1].publications


def _unsafe_prepublish_without_delivery(driver):
    for scope, node, box in _pending(driver):
        driver.state.set_points_to(node, box.val)
        driver.state.set_points_to(node.var, box.val)


def test_no_current_consumers_does_not_make_custom_processor_publication_inert():
    context, scopes = _scopes(count=1)
    witnesses = [_PtsWitness(), _PtsWitness()]
    old, unsafe = (_driver(processor=processor) for processor in witnesses)
    for driver in (old, unsafe):
        _ordinary_seeds(driver, context, scopes)
        source = _global(driver, scopes[0], "len")
        assert not driver.state.constraints._by_variable.get(source)
        assert not driver.state.pointer_flow_graph.succs.get(NormalNode(source))
        assert not driver.solver._builtin_dependencies.get(source)
    _unsafe_prepublish_without_delivery(unsafe)
    for driver in (old, unsafe):
        driver.solver.solve_to_fixpoint()
    # Even retaining all queued items does not help: both-store prepublication
    # makes every queued delta empty and suppresses the publication hook.
    assert witnesses[0].publications and not witnesses[1].publications
    old_sink = old.state.get_variable(scopes[0], context, Variable("hook_sink"))
    unsafe_sink = unsafe.state.get_variable(scopes[0], context, Variable("hook_sink"))
    assert old.state.get_points_to(old_sink)
    assert unsafe.state.get_points_to(unsafe_sink).is_empty()


def test_raw_late_builtin_watch_has_no_past_fact_replay_contract():
    context, scopes = _scopes(count=1)
    old, early = _driver(), _driver()
    source_old, source_early = (_global(driver, scopes[0], "len") for driver in (old, early))
    point = PointsToSet.singleton(ObjectFactory.create_builtin_function("len", context))
    token = ("adversarial_raw_subscriber",)
    old.solver._watch_builtin_variable(source_old, token)
    old.solver._apply_dynamic(scopes[0], NormalNode(source_old), point)
    early.solver._apply_dynamic(scopes[0], NormalNode(source_early), point)
    early.solver._watch_builtin_variable(source_early, token)
    assert list(old.solver._builtin_replays) == [token]
    assert not early.solver._builtin_replays
    assert old.state._env == early.state._env
    # The same populated stores do not establish equality of future consumers.


def test_ctx_only_publication_cannot_replace_processed_normal_node_store():
    context, scopes = _scopes(count=1)
    driver = _driver()
    source = _global(driver, scopes[0], "len")
    sink = driver.state.get_variable(scopes[0], context, Variable("late_sink"))
    point = PointsToSet.singleton(ObjectFactory.create_builtin_function("len", context))
    driver.state.set_points_to(source, point)
    driver.state._add_var_points_flow(source, sink)
    assert driver.state.get_points_to(source) == point
    assert driver.state.get_points_to(NormalNode(source)).is_empty()
    assert driver.state._worklist.empty()
    assert driver.state.get_points_to(sink).is_empty()
    driver.solver._apply_dynamic(scopes[0], NormalNode(source), point)
    driver.solver.solve_to_fixpoint()
    assert driver.state.get_points_to(sink) == point


def test_templates_are_pure_requests_not_merged_points_or_module_metadata():
    context, scopes = _scopes(count=2)
    old, shared = _driver(), _driver()
    extra = PointsToSet.singleton(ObjectFactory.create_builtin_function("user_len", context))
    for driver in (old, shared):
        for scope in scopes:
            PointerAnalysis._initialize_module_metadata(driver, scope, context)
        driver.solver.handle_new_points_to(_global(driver, scopes[0], "len"), scopes[0], extra)
    _ordinary_seeds(old, context, scopes)
    _shared_template_seeds(shared, context, scopes)
    assert [(scope, node, box.val) for scope, node, box in _pending(old)] == [
        (scope, node, box.val) for scope, node, box in _pending(shared)]
    for driver in (old, shared):
        driver.solver.solve_to_fixpoint()
        assert extra.intersection(driver.state.get_points_to(_global(driver, scopes[0], "len"))) == extra
        assert extra.intersection(driver.state.get_points_to(_global(driver, scopes[1], "len"))).is_empty()
        names = [driver.state.get_points_to(_global(driver, scope, "__name__")) for scope in scopes]
        assert names[0] != names[1]
    assert old.state._env == shared.state._env


def test_late_native_constraint_registration_replays_and_retains_future_growth():
    from pythonstan.analysis.pointer.kcfa.constraints import LoadConstraint
    from pythonstan.analysis.pointer.kcfa.heap_model import attr

    class RuleWitness(Processor):
        def __init__(self):
            self.deliveries = []

        def handle_constraint(self, solver, target, scope, constraint, points):
            self.deliveries.append((target, scope, constraint, points))
            return True

    context, scopes = _scopes(count=1)
    processor = RuleWitness()
    driver = _driver(processor=processor)
    _ordinary_seeds(driver, context, scopes)
    driver.solver.solve_to_fixpoint()
    source_var = Variable("len", VariableKind.GLOBAL)
    source = _global(driver, scopes[0], "len")
    original = driver.state.get_points_to(source)
    rule = LoadConstraint(source_var, attr("witness"), Variable("output"))
    driver.solver.add_constraint(scopes[0], context, rule)
    assert len(driver.solver._constraint_replays) == 1
    driver.solver.solve_to_fixpoint()
    assert processor.deliveries == [(source, scopes[0], rule, original)]
    extra = PointsToSet.singleton(ObjectFactory.create_builtin_function("other", context))
    driver.solver.handle_new_points_to(source, scopes[0], extra)
    driver.solver.solve_to_fixpoint()
    assert processor.deliveries[-1] == (source, scopes[0], rule, extra)
    assert len(processor.deliveries) == 2


def test_singleton_template_cannot_survive_object_table_epoch_reset():
    context, _ = _scopes(count=1)
    old = PointsToSet.singleton(ObjectFactory.create_builtin_function("len", context))
    reset_object_table()
    unrelated = PointsToSet.singleton(ObjectFactory.create_builtin_function("dict", context))
    # Reusing a bare old mask may silently decode a different object; no
    # exception or empty-set result is guaranteed to expose an unsafe cache.
    assert list(old) == list(unrelated)
    fresh = PointsToSet.singleton(ObjectFactory.create_builtin_function("len", context))
    assert old != fresh and list(old) != list(fresh)


def test_real_factory_and_singleton_construction_counts_fall_without_removing_work():
    context, scopes = _scopes(count=4)
    construction_counts = []
    for seed in (_ordinary_seeds, _shared_template_seeds):
        driver = _driver()
        with patch.object(ObjectFactory, "create_builtin_function",
                          wraps=ObjectFactory.create_builtin_function) as factory:
            with patch.object(PointsToSet, "singleton", wraps=PointsToSet.singleton) as singleton:
                seed(driver, context, scopes)
        pending = _pending(driver)
        inventory = {node.var.content.name for scope, node, _ in pending if scope is scopes[0]}
        expected = len(inventory) * len(scopes) if seed is _ordinary_seeds else len(inventory)
        assert factory.call_count == singleton.call_count == expected
        assert len(pending) == len(inventory) * len(scopes)
        construction_counts.append(factory.call_count)
    assert construction_counts[0] == construction_counts[1] * len(scopes)


def test_returned_templates_from_an_old_object_arena_fall_back_to_fresh_construction():
    context, scopes = _scopes(count=2)
    old = _driver()
    templates = PointerAnalysis._initialize_builtins(old, scopes[0], context)
    reset_object_table()
    PointsToSet.singleton(ObjectFactory.create_builtin_function("unrelated", context))
    fresh = _driver()
    with patch.object(ObjectFactory, "create_builtin_function",
                      wraps=ObjectFactory.create_builtin_function) as factory:
        replacement = PointerAnalysis._initialize_builtins(fresh, scopes[1], context, templates)
    assert factory.call_count == len(templates.seeds)
    assert replacement is not templates
    assert replacement.object_table_generation != templates.object_table_generation
    for _, node, box in _pending(fresh):
        assert [obj.function_name for obj in box.val] == [node.var.content.name]


@pytest.mark.parametrize("custom", [False, True])
def test_distinct_or_custom_contexts_retain_ordinary_construction(custom):
    class CustomContext(CallStringContext):
        pass

    context = CustomContext((), 1) if custom else CallStringContext((), 1)
    context, scopes = _scopes(count=2, context=context)
    driver = _driver()
    first = PointerAnalysis._initialize_builtins(driver, scopes[0], context)
    next_context = context if custom else CallStringContext((), 1)
    assert custom or next_context is not context
    with patch.object(ObjectFactory, "create_builtin_function",
                      wraps=ObjectFactory.create_builtin_function) as factory:
        second = PointerAnalysis._initialize_builtins(driver, scopes[1], next_context, first)
    assert factory.call_count == BUILTIN_COUNT
    if custom:
        assert first is None and second is None
    else:
        assert second is not first
    assert len(driver.state._worklist) == 2 * BUILTIN_COUNT


@pytest.mark.parametrize("kind", ["call", "object", "type", "receiver", "param", "hybrid", "summary"])
def test_native_context_variants_reuse_only_their_own_run_templates(kind):
    from pythonstan.analysis.pointer.kcfa.context import (
        ObjectContext, TypeContext, ReceiverContext, ParamContext, HybridContext, SummaryContext)

    contexts = {"call": CallStringContext(), "object": ObjectContext(),
        "type": TypeContext(), "receiver": ReceiverContext(), "param": ParamContext(),
        "hybrid": HybridContext(), "summary": SummaryContext(CallStringContext())}
    context, scopes = _scopes(count=2, context=contexts[kind])
    driver = _driver()
    first = PointerAnalysis._initialize_builtins(driver, scopes[0], context)
    with patch.object(ObjectFactory, "create_builtin_function",
                      wraps=ObjectFactory.create_builtin_function) as factory:
        second = PointerAnalysis._initialize_builtins(driver, scopes[1], context, first)
    assert second is first and factory.call_count == 0
    assert len(driver.state._worklist) == 2 * BUILTIN_COUNT


def test_summary_wrapper_does_not_certify_a_custom_inner_context():
    from pythonstan.analysis.pointer.kcfa.context import SummaryContext

    class CustomContext(CallStringContext):
        pass

    context, scopes = _scopes(count=2, context=SummaryContext(CustomContext()))
    driver = _driver()
    with patch.object(ObjectFactory, "create_builtin_function",
                      wraps=ObjectFactory.create_builtin_function) as factory:
        templates = _shared_template_seeds(driver, context, scopes)
    assert templates is None and factory.call_count == 2 * BUILTIN_COUNT


def test_first_request_keeps_address_hook_before_singleton_object_interning():
    context, scopes = _scopes(count=1)
    driver = _driver()
    get_variable = driver.state.get_variable

    def address_hook(scope, passed_context, variable):
        PointsToSet.singleton(ObjectFactory.create_builtin_function(
            "address_hook:" + variable.name, passed_context))
        return get_variable(scope, passed_context, variable)

    with patch.object(driver.state, "get_variable", side_effect=address_hook):
        templates = PointerAnalysis._initialize_builtins(driver, scopes[0], context)
    # Hook objects take the even IDs; original builtin singleton construction
    # takes the odd IDs. Hoisting singletons over address resolution changes
    # this arena order even before worklist processing starts.
    assert [points.objs_mask for _, points in templates.seeds] == [
        1 << (2 * index + 1) for index in range(len(templates.seeds))]
