"""Independent finite obligations for the opt-in eager inert bootstrap.

The text gold precedes candidate implementation. Native helper constructors
are reused, while expected store contents and callback observations are
specified here rather than obtained from the candidate transfer function.
"""
from dataclasses import replace
from types import SimpleNamespace

import pytest

from pythonstan.analysis.pointer.kcfa.analysis import PointerAnalysis
from pythonstan.analysis.pointer.kcfa.config import Config
from pythonstan.analysis.pointer.kcfa.object import ObjectFactory
from pythonstan.analysis.pointer.kcfa.points_to_set import PointsToSet, reset_object_table
from pythonstan.analysis.pointer.kcfa.pointer_flow_graph import NormalNode
from pythonstan.analysis.pointer.kcfa.processor.processor import Processor
from pythonstan.analysis.pointer.kcfa.solver import PointerSolver
from pythonstan.analysis.pointer.kcfa.state import PointerAnalysisState
from pythonstan.analysis.pointer.kcfa.variable import Variable, VariableKind
from tests.round13_scheduler.test_bootstrap_obligations import _scopes


NAMES = set("""iter next aiter anext len enumerate zip map filter range reversed sorted sum min max all any
list dict tuple set frozenset str int float bool bytes bytearray memoryview slice complex isinstance
issubclass type hasattr getattr setattr delattr vars dir globals locals callable super print input open
object abs round divmod pow repr ascii format bin hex oct chr ord hash id property classmethod staticmethod""".split())


@pytest.fixture(autouse=True)
def isolated_epoch():
    reset_object_table()
    yield
    reset_object_table()


def _driver(policy="eager_inert", processor=None):
    state = PointerAnalysisState()
    config = Config(builtin_bootstrap_policy=policy, max_iterations=10000,
                    work_scheduling_policy="fair", log_level="ERROR", track_unknowns=False)
    solver = PointerSolver(state, config, processor or Processor())
    return SimpleNamespace(state=state, solver=solver, kcfa_config=config)


def _location(driver, scope, name, kind=VariableKind.GLOBAL):
    return driver.state.get_variable(scope, scope.context, Variable(name, kind))


def _names(points):
    return {obj.function_name for obj in points}


def _seed(driver, context, scopes):
    cache = None
    for scope in scopes:
        cache = PointerAnalysis._initialize_builtins(driver, scope, context, cache)
    return cache


def test_eager_publication_keeps_both_stores_and_future_pfg_consumers():
    from pythonstan.analysis.pointer.kcfa.bootstrap import bootstrap_statistics

    context, scopes = _scopes(count=2)
    driver = _driver()
    _seed(driver, context, scopes)
    for scope in scopes:
        source = _location(driver, scope, "len")
        assert _names(driver.state.get_points_to(source)) == {"len"}
        assert _names(driver.state.get_points_to(NormalNode(source))) == {"len"}
        sink = _location(driver, scope, "late_sink", VariableKind.LOCAL)
        driver.state._add_var_points_flow(source, sink)
    driver.solver.solve_to_fixpoint()
    assert driver.solver._stats["converged"] == 1
    for scope in scopes:
        assert _names(driver.state.get_points_to(_location(driver, scope, "late_sink", VariableKind.LOCAL))) == {"len"}
    metrics = bootstrap_statistics(driver.solver)
    assert metrics["bootstrap_publications"] == 2 * len(NAMES)
    assert metrics["logical_work_items"] == metrics["selected_work_items"] + metrics["bootstrap_publications"]


def test_two_module_stores_remain_distinct_after_shared_templates_and_late_write():
    context, scopes = _scopes(count=2)
    driver = _driver()
    _seed(driver, context, scopes)
    first, second = (_location(driver, scope, "len") for scope in scopes)
    extra = PointsToSet.singleton(ObjectFactory.create_builtin_function("replacement_len", context))
    driver.solver.handle_new_points_to(first, scopes[0], extra)
    sink = _location(driver, scopes[0], "late_sink", VariableKind.LOCAL)
    driver.state._add_var_points_flow(first, sink)
    driver.solver.solve_to_fixpoint()
    assert _names(driver.state.get_points_to(first)) == {"len", "replacement_len"}
    assert _names(driver.state.get_points_to(sink)) == {"len", "replacement_len"}
    assert _names(driver.state.get_points_to(second)) == {"len"}


def test_late_native_load_replays_old_receiver_and_keeps_future_field_growth():
    from pythonstan.analysis.pointer.kcfa.constraints import LoadConstraint
    from pythonstan.analysis.pointer.kcfa.heap_model import attr

    context, scopes = _scopes(count=1)
    driver = _driver()
    _seed(driver, context, scopes)
    source = _location(driver, scopes[0], "len")
    builtin, = driver.state.get_points_to(source)
    field = driver.state.get_field(scopes[0], context, builtin, attr("witness"))
    old = PointsToSet.singleton(ObjectFactory.create_builtin_function("old_value", context))
    driver.solver.handle_new_points_to(field, scopes[0], old)
    rule = LoadConstraint(Variable("len", VariableKind.GLOBAL), attr("witness"), Variable("output"))
    driver.solver.add_constraint(scopes[0], context, rule)
    driver.solver.solve_to_fixpoint()
    output = _location(driver, scopes[0], "output", VariableKind.LOCAL)
    assert _names(driver.state.get_points_to(output)) == {"old_value"}
    later = PointsToSet.singleton(ObjectFactory.create_builtin_function("later_value", context))
    driver.solver.handle_new_points_to(field, scopes[0], later)
    driver.solver.solve_to_fixpoint()
    assert _names(driver.state.get_points_to(output)) == {"old_value", "later_value"}


def test_late_raw_watch_coalesces_one_past_notification_and_does_not_self_reschedule():
    context, scopes = _scopes(count=1)
    eager, queued = _driver(), _driver("queued")
    for driver in (eager, queued):
        _seed(driver, context, scopes)
    token = ("finite_adversarial_raw_watch",)
    for driver in (eager, queued):
        source = _location(driver, scopes[0], "len")
        driver.solver._watch_builtin_variable(source, token)
        driver.solver._watch_builtin_variable(source, token)
    # Publish the queued control's seed only; token dispatch itself is not
    # needed to compare its notification with the eager registration seam.
    source = _location(queued, scopes[0], "len")
    points = PointsToSet.singleton(ObjectFactory.create_builtin_function("len", context))
    queued.solver._apply_dynamic(scopes[0], NormalNode(source), points)
    assert list(eager.solver._builtin_replays) == list(queued.solver._builtin_replays) == [token]
    eager.solver._builtin_replays.clear()
    eager.solver._queued_builtin_replays.clear()
    eager.solver._watch_builtin_variable(_location(eager, scopes[0], "len"), token)
    assert not eager.solver._builtin_replays


class _Hook(Processor):
    def __init__(self):
        self.events = []

    def handle_pts(self, solver, target, scope, points):
        self.events.append((target.content.name,
                            bool(solver.state.get_points_to(NormalNode(target))),
                            bool(solver.state.get_points_to(target))))
        if target.content.name == "len":
            sink = solver.state.get_variable(scope, scope.context, Variable("hook_sink"))
            solver.state._add_var_points_flow(target, sink)
        return False


def test_custom_hook_falls_back_and_retains_original_two_store_order():
    from pythonstan.analysis.pointer.kcfa.bootstrap import bootstrap_statistics

    context, scopes = _scopes(count=1)
    hook = _Hook()
    driver = _driver(processor=hook)
    _seed(driver, context, scopes)
    assert not hook.events
    assert bootstrap_statistics(driver.solver)["bootstrap_publications"] == 0
    driver.solver.solve_to_fixpoint()
    assert ("len", True, False) in hook.events
    assert _names(driver.state.get_points_to(_location(driver, scopes[0], "hook_sink", VariableKind.LOCAL))) == {"len"}


def test_instance_address_hook_declines_eager_and_keeps_first_request_order():
    from pythonstan.analysis.pointer.kcfa.bootstrap import bootstrap_statistics

    context, scopes = _scopes(count=1)
    driver = _driver()
    ordinary = driver.state.get_variable

    def address_hook(scope, passed_context, variable):
        PointsToSet.singleton(ObjectFactory.create_builtin_function("address:" + variable.name, passed_context))
        return ordinary(scope, passed_context, variable)

    driver.state.get_variable = address_hook
    cache = _seed(driver, context, scopes)
    assert bootstrap_statistics(driver.solver)["bootstrap_publications"] == 0
    assert [points.objs_mask for _, points in cache.seeds] == [
        1 << (2 * index + 1) for index in range(len(NAMES))]


@pytest.mark.parametrize("poison", ["missing", "wrong-variable", "wrong-object", "multiple-objects"])
@pytest.mark.parametrize("policy", ["queued", "eager_inert"])
def test_poisoned_exact_cache_records_reconstruct_pure_requested_inventory(poison, policy):
    context, scopes = _scopes(count=2)
    driver = _driver(policy)
    cache = PointerAnalysis._initialize_builtins(driver, scopes[0], context)
    rows = list(cache.seeds)
    if poison == "missing":
        rows.pop()
    else:
        variable, points = rows[0]
        if poison == "wrong-variable":
            rows[0] = (Variable("not_the_requested_builtin", VariableKind.GLOBAL), points)
        else:
            extra = PointsToSet.singleton(ObjectFactory.create_builtin_function("poison", context))
            rows[0] = (variable, extra if poison == "wrong-object" else points.union(extra))
    poisoned = replace(cache, seeds=tuple(rows))
    PointerAnalysis._initialize_builtins(driver, scopes[1], context, poisoned)
    driver.solver.solve_to_fixpoint()
    for name in NAMES:
        assert _names(driver.state.get_points_to(_location(driver, scopes[1], name))) == {name}
    assert not driver.state.get_points_to(_location(driver, scopes[1], "not_the_requested_builtin"))


def test_pending_request_is_not_lost_when_eager_admission_declines():
    context, scopes = _scopes(count=1)
    driver = _driver()
    source = _location(driver, scopes[0], "len")
    extra = PointsToSet.singleton(ObjectFactory.create_builtin_function("pending_value", context))
    driver.solver.handle_new_points_to(source, scopes[0], extra)
    _seed(driver, context, scopes)
    driver.solver.solve_to_fixpoint()
    assert _names(driver.state.get_points_to(source)) == {"len", "pending_value"}
    assert _names(driver.state.get_points_to(NormalNode(source))) == {"len", "pending_value"}


def test_old_arena_cache_does_not_decode_an_unrelated_reused_bit():
    context, scopes = _scopes(count=2)
    old = _driver()
    cache = PointerAnalysis._initialize_builtins(old, scopes[0], context)
    reset_object_table()
    PointsToSet.singleton(ObjectFactory.create_builtin_function("unrelated", context))
    fresh = _driver()
    PointerAnalysis._initialize_builtins(fresh, scopes[1], context, cache)
    fresh.solver.solve_to_fixpoint()
    for name in NAMES:
        assert _names(fresh.state.get_points_to(_location(fresh, scopes[1], name))) == {name}
