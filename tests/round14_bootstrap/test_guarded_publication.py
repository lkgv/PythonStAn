"""Bootstrap acceptance controls authored before native implementation."""
import ast

import pytest

from pythonstan.analysis.pointer.kcfa.analysis import PointerAnalysis
from pythonstan.analysis.pointer.kcfa.config import Config
from pythonstan.analysis.pointer.kcfa.constraints import CopyConstraint
from pythonstan.analysis.pointer.kcfa.context import CallStringContext, Scope
from pythonstan.analysis.pointer.kcfa.object import AllocKind, AllocSite, ModuleObject, ObjectFactory
from pythonstan.analysis.pointer.kcfa.points_to_set import PointsToSet, reset_object_table
from pythonstan.analysis.pointer.kcfa.pointer_flow_graph import NormalNode
from pythonstan.analysis.pointer.kcfa.processor.processor import Processor
from pythonstan.analysis.pointer.kcfa.solver import PointerSolver
from pythonstan.analysis.pointer.kcfa.state import PointerAnalysisState
from pythonstan.analysis.pointer.kcfa.variable import Variable, VariableKind
from pythonstan.ir import IRModule


@pytest.fixture(autouse=True)
def clean_arena():
    reset_object_table()
    yield
    reset_object_table()


def _scope():
    context = CallStringContext((), 1)
    module = IRModule("guarded_bootstrap", ast.parse(""), filename="guarded_bootstrap.py")
    obj = ModuleObject(context, AllocSite(module, AllocKind.MODULE), module)
    return context, Scope.new(obj, None, context, module, None)


def _driver(policy="eager_inert", processor=None, budget=300):
    from types import SimpleNamespace
    config = Config.from_dict({"builtin_bootstrap_policy": policy,
        "work_scheduling_policy": "fair", "max_iterations": budget,
        "log_level": "ERROR", "track_unknowns": False})
    state = PointerAnalysisState()
    solver = PointerSolver(state, config, processor or Processor())
    return SimpleNamespace(state=state, solver=solver, kcfa_config=config)


def _global(driver, scope, name):
    return driver.state.get_variable(scope, scope.context, Variable(name, VariableKind.GLOBAL))


def _seed(driver, scope):
    return PointerAnalysis._initialize_builtins(driver, scope, scope.context)


def _stats(driver):
    return driver.solver.query().get_statistics()


def test_policy_defaults_and_roundtrips_without_changing_inventory():
    assert Config().builtin_bootstrap_policy == "queued"
    for policy in ("queued", "eager_inert"):
        config = Config.from_dict({"builtin_bootstrap_policy": policy})
        assert Config.from_dict(config.to_dict()) == config
    with pytest.raises(ValueError, match="builtin_bootstrap_policy"):
        Config.from_dict({"builtin_bootstrap_policy": "omit_unused"})


def test_both_stores_late_pfg_and_raw_watches_keep_old_facts_and_deduplicate():
    _, scope = _scope()
    driver = _driver()
    templates = _seed(driver, scope)
    count = len(templates.seeds)
    source = _global(driver, scope, "len")
    points = driver.state.get_points_to(source)
    assert points and points == driver.state.get_points_to(NormalNode(source))
    assert driver.state._worklist.empty()
    sink = driver.state.get_variable(scope, scope.context, Variable("late_sink"))
    driver.state._add_var_points_flow(source, sink)
    assert len(driver.state._worklist) == 1
    token = ("raw_past_fact_watch",)
    driver.solver._watch_builtin_variable(source, token)
    driver.solver._watch_builtin_variable(_global(driver, scope, "dict"), token)
    driver.solver._watch_builtin_variable(source, token)
    assert list(driver.solver._builtin_replays) == [token]
    # Simulate its pending token being removed. Re-registering an already
    # observed dependency during invocation replay must not self-requeue.
    driver.solver._builtin_replays.clear()
    driver.solver._queued_builtin_replays.clear()
    driver.solver._watch_builtin_variable(source, token)
    assert not driver.solver._builtin_replays
    driver.solver.solve_to_fixpoint()
    assert driver.state.get_points_to(sink) == points
    stats = _stats(driver)
    assert stats["bootstrap_publications"] == count
    assert stats["selected_work_items"] == stats["iterations"] == 1
    assert stats["logical_work_items"] == count + 1


def test_custom_publication_hook_falls_back_and_keeps_two_store_order():
    class Witness(Processor):
        def __init__(self):
            self.events = []

        def handle_pts(self, solver, target, scope, points):
            self.events.append((target, solver.state.get_points_to(NormalNode(target)),
                                solver.state.get_points_to(target)))
            if target.content.name == "len":
                sink = solver.state.get_variable(scope, scope.context, Variable("hook_sink"))
                solver.state._add_var_points_flow(target, sink)
            return False

    _, scope = _scope()
    witness = Witness()
    driver = _driver(processor=witness)
    templates = _seed(driver, scope)
    assert len(driver.state._worklist) == len(templates.seeds)
    assert not driver.state._env and not witness.events
    driver.solver.solve_to_fixpoint()
    assert driver.state.get_points_to(driver.state.get_variable(scope, scope.context, Variable("hook_sink")))
    assert all(normal and not ctx for target, normal, ctx in witness.events)
    assert _stats(driver)["bootstrap_publications"] == 0


def test_pending_location_stays_queued_with_its_original_request():
    context, scope = _scope()
    driver = _driver()
    source = _global(driver, scope, "len")
    application = PointsToSet.singleton(ObjectFactory.create_builtin_function("application_len", context))
    driver.state._worklist.add((scope, NormalNode(source), application))
    first_box = driver.state._worklist.items_dict[NormalNode(source)][2]
    templates = _seed(driver, scope)
    assert len(driver.state._worklist) == 1
    assert driver.state._worklist.items_dict[NormalNode(source)][2] is first_box
    assert driver.state.get_points_to(source).is_empty()
    driver.solver.solve_to_fixpoint()
    points = driver.state.get_points_to(source)
    assert {obj.function_name for obj in points} == {"len", "application_len"}
    assert points == driver.state.get_points_to(NormalNode(source))
    assert _stats(driver)["bootstrap_publications"] == len(templates.seeds) - 1


@pytest.mark.parametrize("blocked", ["observer", "inspector", "taint"])
def test_diagnostics_and_tainted_cuts_decline_early_publication(blocked):
    _, scope = _scope()
    driver = _driver()
    if blocked == "observer":
        driver.solver.set_work_step_observer(lambda observation: None)
    elif blocked == "inspector":
        driver.solver.enable_inspector_boundaries()
    else:
        driver.solver._inspector_tainted = True
    templates = _seed(driver, scope)
    assert len(driver.state._worklist) == len(templates.seeds)
    assert not driver.state._env
    assert _stats(driver)["bootstrap_publications"] == 0


def test_late_processor_mutation_refuses_even_an_empty_continuation_and_is_sticky():
    _, scope = _scope()
    driver = _driver()
    _seed(driver, scope)
    driver.solver.processor.handle_pts = lambda *args: None
    with pytest.raises(RuntimeError, match="bootstrap|contract|mutation"):
        next(driver.solver)
    del driver.solver.processor.handle_pts
    with pytest.raises(RuntimeError, match="bootstrap|contract|mutation|taint"):
        next(driver.solver)
    assert _stats(driver)["converged"] == 0


def test_selected_and_bootstrap_work_are_separate_and_complete_logical_work_is_equal():
    _, scope = _scope()
    drivers = [_driver(policy) for policy in ("queued", "eager_inert")]
    for driver in drivers:
        driver.solver.add_constraint(scope, scope.context, CopyConstraint(
            Variable("len", VariableKind.GLOBAL), Variable("output")))
        _seed(driver, scope)
        driver.solver.solve_to_fixpoint()
        assert _stats(driver)["converged"] == 1
    queued, eager = drivers
    left, right = _stats(queued), _stats(eager)
    assert queued.state._env == eager.state._env
    assert queued.state.pointer_flow_graph.edges == eager.state.pointer_flow_graph.edges
    assert left["bootstrap_publications"] == 0 < right["bootstrap_publications"]
    assert left["selected_work_items"] > right["selected_work_items"]
    assert left["logical_work_items"] == right["logical_work_items"]


def test_seed_frontier_can_exceed_selected_budget_only_with_explicit_logical_work():
    _, scope = _scope()
    queued, eager = (_driver(policy, budget=2) for policy in ("queued", "eager_inert"))
    for driver in (queued, eager):
        driver.solver.add_constraint(scope, scope.context, CopyConstraint(
            Variable("len", VariableKind.GLOBAL), Variable("output")))
        templates = _seed(driver, scope)
        driver.solver.solve_to_fixpoint()
    left, right = _stats(queued), _stats(eager)
    assert left["selected_work_items"] == right["selected_work_items"] == 2
    assert left["converged"] == 0 and right["converged"] == 1
    assert right["bootstrap_publications"] == len(templates.seeds) > 2
    assert right["logical_work_items"] == 2 + len(templates.seeds)
    assert left["logical_work_items"] == 2
    for variable, points in templates.seeds:
        address = eager.state.get_variable(scope, scope.context, variable)
        assert eager.state.get_points_to(address) == eager.state.get_points_to(NormalNode(address)) == points
