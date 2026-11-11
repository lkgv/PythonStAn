"""Regression tests for concrete drops in the legacy AI implementation.

These checks do not certify the legacy interpreter's Python semantics.
"""

import ast

import pytest

from pythonstan.analysis.pointer.ai.api import AISummary, AISummaryEngine, AnalysisBudget
from pythonstan.analysis.pointer.ai._cfg_adapter import CFGAdapter
from pythonstan.analysis.pointer.ai.interpreter import IRInterpreter
from pythonstan.analysis.pointer.ai.solver import AISolver, SolverConfig, TraceKey
from pythonstan.analysis.pointer.ai.state import AIState, AbsVal, PredicateInfo
from pythonstan.analysis.pointer.ai.summary import SummaryAccumulator
from pythonstan.analysis.pointer.kcfa.context import CallStringContext, SummaryContext
from pythonstan.analysis.pointer.kcfa.object import AbstractObject, AllocKind, AllocSite, summarize_object
from pythonstan.graph.cfg.base_block import BaseBlock
from pythonstan.graph.cfg.cfg import ControlFlowGraph
from pythonstan.graph.cfg.edges import IfEdge, NormalEdge
from pythonstan.ir.ir_statements import Goto, IRAstStmt, JumpIfFalse, Label


class ProvidedCFGSolver(AISolver):
    def __init__(self, cfg, **kwargs):
        self.provided_cfg = cfg
        super().__init__(scope_ir=object(), pta_query=None, budget=AnalysisBudget(), **kwargs)

    def _build_cfg(self):
        return self.provided_cfg


def make_cfg(*blocks):
    cfg = ControlFlowGraph(blocks[0])
    for block in blocks:
        cfg.add_blk(block)
    return cfg


def test_predicate_order_detects_loss_of_a_required_fact():
    plain = AIState().set_var("x", AbsVal.from_str("value"))
    pred = PredicateInfo("dict_contains", "d", key_var="k")
    refined = plain.add_predicate("condition", pred)
    assert refined.leq(plain)
    assert not plain.leq(refined)
    assert not refined.join(plain).leq(refined)


@pytest.mark.parametrize("name", ["condition", "d", "k"])
@pytest.mark.parametrize("weak", [False, True])
def test_assignment_invalidates_predicate_results_and_operands(name, weak):
    pred = PredicateInfo("dict_contains", "d", key_var="k")
    original = AIState().add_predicate("condition", pred)
    update = original.weak_set_var if weak else original.set_var
    updated = update(name, AbsVal.top())
    assert updated.get_predicate("condition") is None
    assert original.get_predicate("condition") == pred


def test_exception_growth_is_visible_to_fixpoint_ordering():
    normal = AIState()
    exceptional = AIState(exn=AbsVal.from_str("ValueError"))
    assert normal.leq(exceptional)
    assert not exceptional.leq(normal)
    assert not normal.join(exceptional).leq(normal)


def test_summarized_addresses_cover_precise_addresses_in_order_and_meet():
    obj = AbstractObject(CallStringContext((), 1), AllocSite("allocation", AllocKind.OBJECT))
    exact = AbsVal.from_addrs(frozenset({obj}))
    summary = AbsVal.from_addrs(frozenset({summarize_object(obj)}))
    assert exact.leq(summary)
    assert not summary.leq(exact)
    assert exact.meet(summary).addrs == exact.addrs
    assert summary.meet(exact).addrs == exact.addrs


def test_widening_keeps_objects_with_different_payloads_at_one_site():
    from dataclasses import dataclass

    @dataclass(frozen=True)
    class PayloadObject(AbstractObject):
        payload: str = ""

    site = AllocSite("allocation", AllocKind.OBJECT)
    context = CallStringContext((), 1)
    left = AbsVal.from_addrs(frozenset({PayloadObject(context, site, "left")}))
    right = AbsVal.from_addrs(frozenset({PayloadObject(context, site, "right")}))
    widened = left.widen(right, max_addrs=1)
    assert left.leq(widened) and right.leq(widened)
    assert {obj.payload for obj in widened.addrs} == {"left", "right"}
    assert all(isinstance(obj.context, SummaryContext) for obj in widened.addrs)


@pytest.mark.parametrize("limit", ["max_iterations", "max_block_visits"])
def test_budget_exhaustion_is_not_reported_as_an_empty_complete_summary(limit):
    entry = BaseBlock(0)
    cfg = make_cfg(entry)
    config = SolverConfig(enable_narrowing=False, **{limit: 0})
    summary = ProvidedCFGSolver(cfg, initial_state=AIState(), config=config).solve()
    assert not summary.is_complete
    assert limit in summary.incomplete_reasons
    assert not summary.is_empty()


def test_partial_effects_are_retained_when_later_blocks_exceed_budget():
    first, second = BaseBlock(0), BaseBlock(1)
    cfg = make_cfg(first, second)
    cfg.add_edge(NormalEdge(first, second))
    solver = ProvidedCFGSolver(
        cfg, initial_state=AIState(),
        config=SolverConfig(max_iterations=1, enable_narrowing=False),
    )
    def process(block_id, trace_key, state):
        solver.summary.add_may_raise("ValueError")
        return [(1, trace_key, state)]
    solver._process_block_with_trace = process
    summary = solver.solve()
    assert not summary.is_complete
    assert summary.may_raise == frozenset({"ValueError"})


def test_missing_cfg_and_internal_error_fallback_are_explicitly_incomplete():
    summary = ProvidedCFGSolver(None, initial_state=AIState()).solve()
    assert not summary.is_complete and "missing_cfg" in summary.incomplete_reasons
    fallback = AISummaryEngine(None)._conservative_summary()
    assert not fallback.is_complete and not fallback.is_empty()


def test_summary_merge_and_accumulator_preserve_incompleteness():
    partial = AISummary(is_complete=False, incomplete_reasons=("max_iterations",))
    merged = AISummary().merge(partial)
    assert not merged.is_complete and merged.incomplete_reasons == partial.incomplete_reasons
    accumulator = SummaryAccumulator()
    accumulator.mark_incomplete("unknown_call")
    assert not SummaryAccumulator().merge(accumulator).to_summary().is_complete


def test_unsupported_statement_is_not_a_complete_noop():
    accumulator = SummaryAccumulator()
    IRInterpreter(None, AnalysisBudget()).transfer(object(), AIState(), accumulator)
    assert accumulator.to_summary().incomplete_reasons == ("unsupported_statement",)


def test_opaque_ast_statement_is_not_a_complete_noop():
    accumulator = SummaryAccumulator()
    statement = IRAstStmt(ast.parse("exec(source)").body[0])
    IRInterpreter(None, AnalysisBudget()).transfer(statement, AIState(), accumulator)
    assert accumulator.to_summary().incomplete_reasons == ("opaque_ast_statement",)


def test_incomplete_callee_result_is_not_cached(monkeypatch):
    from pythonstan.analysis.pointer.ai import solver as solver_module

    calls = []
    def solve(_solver):
        calls.append(True)
        return AISummary(is_complete=False, incomplete_reasons=("max_iterations",))
    monkeypatch.setattr(solver_module.AISolver, "solve", solve)
    engine = AISummaryEngine(None)
    scope = object()
    engine.analyze_callee(scope, None, [])
    engine.analyze_callee(scope, None, [])
    assert len(calls) == 2


def test_fallthrough_uses_normal_edge_even_if_jump_edge_was_inserted_first():
    label = Label(10)
    condition = ast.Name(id="condition", ctx=ast.Load())
    branch = BaseBlock(0, [JumpIfFalse(condition, label)])
    jump, fallthrough = BaseBlock(1), BaseBlock(2)
    cfg = make_cfg(branch, jump, fallthrough)
    cfg.add_edge(IfEdge(branch, jump, condition, False))
    cfg.add_edge(NormalEdge(branch, fallthrough))
    cfg.add_label(label, jump)
    assert CFGAdapter(cfg).get_fallthrough(branch.idx) == fallthrough.idx


def test_goto_uses_its_explicit_target():
    label = Label(10)
    branch = BaseBlock(0, [Goto(label)])
    other, target = BaseBlock(1), BaseBlock(2)
    cfg = make_cfg(branch, other, target)
    cfg.add_edge(NormalEdge(branch, other))
    cfg.add_edge(NormalEdge(branch, target))
    cfg.add_label(label, target)
    solver = ProvidedCFGSolver(cfg, initial_state=AIState())
    successors = solver._process_block(0, AIState())
    assert [block for block, _ in successors] == [2]


def test_eviction_requeues_a_previously_processed_merged_trace():
    blocks = [BaseBlock(i) for i in range(4)]
    cfg = make_cfg(*blocks)
    for src, dst in [(0, 1), (0, 2), (2, 1), (2, 3), (3, 1)]:
        cfg.add_edge(NormalEdge(blocks[src], blocks[dst]))
    solver = ProvidedCFGSolver(
        cfg, initial_state=AIState(),
        config=SolverConfig(enable_trace_partitioning=True, max_traces_per_block=2,
                            max_total_traces=100, enable_narrowing=False),
    )
    observed = []
    def state_for(text):
        return AIState().set_var("x", AbsVal.from_str(text))
    def process(block_id, trace_key, state):
        if block_id == 0:
            return [(1, TraceKey(1), state_for("a")), (2, TraceKey(4), state)]
        if block_id == 2:
            return [(1, TraceKey(2), state_for("b")), (3, TraceKey(5), state)]
        if block_id == 3:
            return [(1, TraceKey(3), state_for("c"))]
        observed.append(state.get_var("x").str_const)
        return []
    solver._process_block_with_trace = process
    assert solver.solve().is_complete
    assert frozenset({"a", "b"}) in observed


def test_partitioned_loop_merge_points_use_widening(monkeypatch):
    entry = BaseBlock(0)
    cfg = make_cfg(entry)
    cfg.add_edge(NormalEdge(entry, entry))
    solver = ProvidedCFGSolver(
        cfg, initial_state=AIState().set_var("x", AbsVal.from_str("seed")),
        config=SolverConfig(enable_trace_partitioning=True, merge_points={0},
                            loop_unroll=1, max_iterations=30, enable_narrowing=False),
    )
    widen_calls = []
    original_widen = AIState.widen
    def widen(left, right):
        widen_calls.append(True)
        return original_widen(left, right)
    monkeypatch.setattr(AIState, "widen", widen)
    visits = []
    def process(block_id, trace_key, state):
        visits.append(True)
        value = state.get_var("x").join(AbsVal.from_str(str(len(visits))))
        return [(0, TraceKey(len(visits)), state.set_var("x", value))]
    solver._process_block_with_trace = process
    assert solver.solve().is_complete
    assert widen_calls
    assert solver.block_states[0].get_var("x").str_const is None
