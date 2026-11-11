"""Source facts, open obligations and configured debug observations survive filtering."""
import ast
import logging

import pytest

from benchmark.round10_solver.enqueue_experiment import CASES, analyze_source
from benchmark.round10_solver.duplicate_work_experiment import run as duplicate_work
from pythonstan.analysis.pointer.kcfa.analysis import _source_scope_key
from pythonstan.analysis.pointer.kcfa.config import Config
from pythonstan.analysis.pointer.kcfa.context import CallStringContext, Scope
from pythonstan.analysis.pointer.kcfa.object import AbstractObject, AllocKind, AllocSite, ModuleObject
from pythonstan.analysis.pointer.kcfa.points_to_set import PointsToSet, reset_object_table
from pythonstan.analysis.pointer.kcfa.processor.processor import Processor
from pythonstan.analysis.pointer.kcfa.solver import PointerSolver
from pythonstan.analysis.pointer.kcfa.state import PointerAnalysisState
from pythonstan.analysis.pointer.kcfa.variable import Variable
from pythonstan.ir.ir_statements import IRFunc, IRModule


@pytest.mark.parametrize("case_id", CASES)
@pytest.mark.parametrize("schedule", ["fifo", "lifo"])
@pytest.mark.parametrize("index_sensitive", [False, True])
@pytest.mark.parametrize("intrinsics", [False, True])
def test_completed_source_and_field_facts_equal_counterfactual(
        tmp_path, case_id, schedule, index_sensitive, intrinsics):
    options = dict(schedule=schedule, index_sensitive=index_sensitive, intrinsics=intrinsics)
    old = analyze_source(tmp_path, CASES[case_id], filtered=False, **options)
    new = analyze_source(tmp_path, CASES[case_id], filtered=True, **options)
    assert old["statistics"]["converged"] == new["statistics"]["converged"] == 1
    assert old["statistics"]["pending_work"] == new["statistics"]["pending_work"] == 0
    assert old["signature"] == new["signature"]
    assert old["edges"] == new["edges"]
    assert not new["signature"]["semantic_status"]["whole_program_soundness_proved"]
    if case_id == "mapping_late_growth" and intrinsics:
        assert ("subject.invoke", "subject.early") in new["edges"]
        assert ("subject.invoke", "subject.later") in new["edges"]
        assert ("subject.invoke", "subject.unrelated") not in new["edges"]
    if case_id == "dynamic_bound_method":
        assert ("subject.dispatch", "subject.Service.handle") in new["edges"]
    if case_id == "generator_and_closure":
        assert ("subject.decorate.wrapper", "subject.callback") in new["edges"]
    if case_id == "unresolved_obligation":
        assert new["signature"]["semantic_status"]["residual_obligation_count"] > 0


@pytest.mark.parametrize("filtered", [False, True])
def test_unchanged_small_budget_keeps_incomplete_status(tmp_path, filtered):
    result = analyze_source(tmp_path, CASES["mapping_late_growth"], filtered=filtered, max_iterations=1)
    statistics = result["statistics"]
    assert statistics["iterations"] == 1
    assert statistics["iteration_limit_reached"] == 1
    assert statistics["pending_work"] > 0
    assert not result["signature"]["semantic_status"]["constraint_fixpoint_reached"]
    assert not result["signature"]["semantic_status"]["certificate_eligible"]


def test_seed_source_key_handles_module_and_function_without_address_tiebreak():
    # Definition order and source location distinguish same-name redefinitions.
    node = ast.parse("def f(): return 1\ndef f(): return 2\n")
    first, second = [IRFunc("subject.f", function) for function in node.body]
    assert not hasattr(first, "get_filename")
    assert sorted([second, first], key=_source_scope_key) == [first, second]
    replica = IRFunc("subject.f", ast.parse("def f(): return 1\n").body[0])
    assert first is not replica
    assert _source_scope_key(first) == _source_scope_key(replica)
    assert _source_scope_key(IRModule("subject", node, filename="subject.py"))[0] == "subject"


def test_fixed_identity_arena_drops_only_completed_noops():
    result = duplicate_work(ticks=128)
    assert result["exact_environment_equal"]
    assert result["producer_notifications_equal"]
    assert result["producer_notifications_per_arm"] == 257
    old, new = result["arms"]["unfiltered"], result["arms"]["filtered"]
    assert old["converged"] == new["converged"] == 1
    assert old["iterations"] == 257 and new["iterations"] == 129
    assert old["points_to_noop_work_items"] == 128
    assert new["points_to_noop_work_items"] == 0
    assert new["points_to_enqueue_processed_skips"] == 128


def test_disabled_info_skips_expensive_summaries_but_preserves_debug_snapshots(monkeypatch):
    reset_object_table()
    try:
        context = CallStringContext((), 1)
        module = IRModule("logging", ast.parse(""), filename="logging.py")
        module_object = ModuleObject(context, AllocSite(module, AllocKind.MODULE), module)
        scope = Scope.new(module_object, None, context, module, None)
        state = PointerAnalysisState()
        state.set_internal_scope(module_object, scope)
        snapshots, iterations = [], []
        class Monitor:
            enabled = True
            def set_iteration(self, iteration):
                iterations.append(iteration)
            def record_iteration_snapshot(self, **values):
                snapshots.append(values)
        solver = PointerSolver(state, Config(log_level="ERROR", enable_debug_monitor=True,
            debug_log_interval=1), Processor(), debug_monitor=Monitor())
        def forbidden(*args, **kwargs):
            raise AssertionError("Disabled INFO summary inspected constraint groups")
        monkeypatch.setattr(state.constraints, "get_by_type", forbidden)
        logger = logging.getLogger("pythonstan.analysis.pointer.kcfa.solver")
        monkeypatch.setattr(logger, "level", logging.ERROR)
        target = state.get_variable(scope, context, Variable("target"))
        value = AbstractObject(context, AllocSite("value", AllocKind.OBJECT))
        solver.handle_new_points_to(target, scope, PointsToSet.singleton(value))
        solver.solve_to_fixpoint()
        assert iterations == [1]
        assert len(snapshots) == 1
        assert snapshots[0]["worklist_size"] == 0
        assert value in state.get_points_to(target)
        assert solver._stats["converged"] == 1
    finally:
        reset_object_table()
