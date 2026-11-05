import ast
from typing import Any, Optional

import pytest

from pythonstan.analysis.pointer.ai.api import AnalysisBudget
from pythonstan.analysis.pointer.ai.solver import AISolver, SolverConfig, TraceKey
from pythonstan.analysis.pointer.ai.state import AIState, AbsVal
from pythonstan.analysis.transform.block_cfg import BlockCFGBuilder
from pythonstan.analysis.pointer.kcfa.object import (
    AllocKind,
    AllocSite,
    AbstractObject,
    summarize_object,
    is_summary_object,
)
from pythonstan.analysis.pointer.kcfa.context import CallSite, CallStringContext, SummaryContext
from pythonstan.analysis.pointer.kcfa.points_to_set import PointsToSet
from pythonstan.analysis.pointer.kcfa.heap_model import attr
from pythonstan.analysis.pointer.kcfa.pta_query_adapter import PtaQueryAdapter
from pythonstan.ir.ir_statements import JumpIfFalse, Label, IRAssign, Goto, IRCall


class _DummyScope:
    """Minimal scope placeholder."""

    qualname = "dummy"


class _DummyPtaQuery:
    """Minimal PTA stub for solver tests."""

    def points_to(self, *args, **kwargs):
        return PointsToSet.empty()

    def field_points_to(self, *args, **kwargs):
        return PointsToSet.empty()

    def get_class_mro(self, *args, **kwargs):
        return []


def _make_call_site(scope_name: str, index: int) -> CallSite:
    call_stmt = IRCall(ast.parse(f"call_{index}()").body[0].value)
    return CallSite(statement=call_stmt, scope_name=scope_name, index=index)

    def may_have_attr(self, *args, **kwargs):
        return True


class CFGBackedSolver(AISolver):
    """Solver that reuses a provided CFG instead of building via World."""

    def __init__(self, cfg, *args, **kwargs):
        self._provided_cfg = cfg
        super().__init__(*args, **kwargs)

    def _build_cfg(self):
        return self._provided_cfg


def _make_assign(name: str, value: int) -> IRAssign:
    assign_ast = ast.Assign(
        targets=[ast.Name(id=name, ctx=ast.Store())],
        value=ast.Constant(value=value),
    )
    ast.fix_missing_locations(assign_ast)
    return IRAssign(assign_ast)


def _build_cfg_from_stmts(stmts):
    builder = BlockCFGBuilder(_DummyScope())
    builder.build_graph(stmts)
    return builder.cfg


def test_jump_if_false_real_ir_splits_and_resolves_labels():
    else_label = Label(1)
    merge_label = Label(2)
    stmts = [
        JumpIfFalse(ast.Name(id="c", ctx=ast.Load()), label=else_label),
        _make_assign("x", 1),
        Goto(merge_label),
        else_label,
        _make_assign("x", 2),
        merge_label,
    ]
    cfg = _build_cfg_from_stmts(stmts)

    jump_block = next(b for b in cfg.blks if any(isinstance(s, JumpIfFalse) for s in b.stmts))
    else_block = cfg.label2blk[else_label]
    merge_block = cfg.label2blk[merge_label]

    config = SolverConfig(merge_points={merge_block.idx}, enable_value_partition=True)
    budget = AnalysisBudget(enable_value_partition=True)
    init_state = AIState().set_var("c", AbsVal())
    solver = CFGBackedSolver(
        cfg=cfg,
        scope_ir=_DummyScope(),
        initial_state=init_state,
        pta_query=_DummyPtaQuery(),
        budget=budget,
        config=config,
    )

    solver.solve()

    # Both branch edges should exist and be trace-partitioned
    trace_blocks = {bid for (bid, _) in solver.trace_store.trace_states.keys()}
    assert else_block.idx in trace_blocks
    assert any(bid != else_block.idx for bid in trace_blocks)  # then branch present
    # Label resolution should have recorded the else edge
    assert (jump_block.idx, else_block.idx) in solver.edge_out_states


def test_edge_out_state_join_on_same_edge():
    # Directly exercise _set_edge_out_state join behavior
    cfg = _build_cfg_from_stmts([Label(0)])
    solver = CFGBackedSolver(
        cfg=cfg,
        scope_ir=_DummyScope(),
        initial_state=AIState(),
        pta_query=_DummyPtaQuery(),
        budget=AnalysisBudget(),
        config=SolverConfig(),
    )

    true_state = AIState().set_var("c", AbsVal(bool_val=True))
    false_state = AIState().set_var("c", AbsVal(bool_val=False))

    solver._set_edge_out_state(1, 2, true_state)
    solver._set_edge_out_state(1, 2, false_state)

    edge_state = solver.edge_out_states[(1, 2)]
    assert edge_state.get_var("c").bool_val is None


def test_addr_widening_summarizes_to_summary_object():
    site = AllocSite(stmt="alloc", kind=AllocKind.OBJECT)
    cs1 = _make_call_site("fn", 1)
    cs2 = _make_call_site("fn", 2)
    ctx1 = CallStringContext((cs1,), 2)
    ctx2 = CallStringContext((cs2,), 2)
    obj1 = AbstractObject(context=ctx1, alloc_site=site)
    obj2 = AbstractObject(context=ctx2, alloc_site=site)

    val1 = AbsVal.from_addrs(frozenset({obj1}))
    val2 = AbsVal.from_addrs(frozenset({obj2}))

    widened = val1.widen(val2, max_addrs=1)

    assert len(widened.addrs) == 1
    summary_obj = next(iter(widened.addrs))
    assert is_summary_object(summary_obj)
    assert summarize_object(obj1) == summary_obj == summarize_object(obj2)


def test_pta_query_adapter_unions_summary_field_points_to():
    site = AllocSite(stmt="alloc", kind=AllocKind.OBJECT)
    ctx_a = CallStringContext((_make_call_site("fn", 1),), 2)
    ctx_b = CallStringContext((_make_call_site("fn", 2),), 2)
    obj_a = AbstractObject(context=ctx_a, alloc_site=site)
    obj_b = AbstractObject(context=ctx_b, alloc_site=site)
    summary_obj = summarize_object(obj_a)

    class FakeState:
        def __init__(self):
            self._heap = type("H", (), {"objects": {(None, None, None): obj_a, (1, 1, 1): obj_b}})()
            self.field_pts = {}

        def get_field(self, scope: Any, context: Any, obj: AbstractObject, field: Any):
            return (obj, field)

        def get_points_to(self, field_var: Any) -> PointsToSet:
            return self.field_pts.get(field_var, PointsToSet.empty())

    state = FakeState()
    adapter = PtaQueryAdapter(state)

    field_key = attr("x")
    obj_x = AbstractObject(context=ctx_a, alloc_site=site)
    obj_y = AbstractObject(context=ctx_b, alloc_site=site)
    state.field_pts[(obj_a, field_key)] = PointsToSet.singleton(obj_x)
    state.field_pts[(obj_b, field_key)] = PointsToSet.singleton(obj_y)

    pts = adapter.field_points_to(summary_obj, field_key, scope=None, context=None)
    assert obj_x in pts
    assert obj_y in pts


def test_guard_not_inverts_truthiness():
    else_label = Label(1)
    merge_label = Label(2)
    cond = ast.UnaryOp(op=ast.Not(), operand=ast.Name(id="c", ctx=ast.Load()))
    ast.fix_missing_locations(cond)
    stmts = [
        JumpIfFalse(ast.Name(id="c", ctx=ast.Load()), label=else_label),  # patched after CFG build
        _make_assign("x", 1),
        Goto(merge_label),
        else_label,
        _make_assign("x", 2),
        merge_label,
    ]
    cfg = _build_cfg_from_stmts(stmts)
    # Patch the JumpIfFalse test to the real expression (builder only accepts ast.Name)
    jump_stmt = next(s for b in cfg.blks for s in b.stmts if isinstance(s, JumpIfFalse))
    jump_stmt.test = cond

    jump_block = next(b for b in cfg.blks if any(isinstance(s, JumpIfFalse) for s in b.stmts))
    else_block = cfg.label2blk[else_label]

    config = SolverConfig(merge_points={merge_label.idx}, enable_value_partition=True)
    budget = AnalysisBudget(enable_value_partition=True)
    init_state = AIState().set_var("c", AbsVal())
    solver = CFGBackedSolver(
        cfg=cfg,
        scope_ir=_DummyScope(),
        initial_state=init_state,
        pta_query=_DummyPtaQuery(),
        budget=budget,
        config=config,
    )

    solver.solve()

    edge_states = {(pred, succ): st for (pred, succ), st in solver.edge_out_states.items() if pred == jump_block.idx}
    assert (jump_block.idx, else_block.idx) in edge_states
    # One successor is the jump (cond false -> c truthy), the other is fallthrough (cond true -> c falsy)
    jump_state = edge_states[(jump_block.idx, else_block.idx)]
    fallthrough_state = next(st for (pred, succ), st in edge_states.items() if succ != else_block.idx)
    assert jump_state.get_var("c").bool_val is True
    assert fallthrough_state.get_var("c").bool_val is False


def test_none_check_refines_truthiness_and_none_flag():
    else_label = Label(10)
    merge_label = Label(11)
    cond = ast.Compare(
        left=ast.Name(id="c", ctx=ast.Load()),
        ops=[ast.Is()],
        comparators=[ast.Constant(value=None)],
    )
    ast.fix_missing_locations(cond)
    stmts = [
        JumpIfFalse(ast.Name(id="c", ctx=ast.Load()), label=else_label),  # patched after CFG build
        _make_assign("x", 1),
        Goto(merge_label),
        else_label,
        _make_assign("x", 2),
        merge_label,
    ]
    cfg = _build_cfg_from_stmts(stmts)
    jump_stmt = next(s for b in cfg.blks for s in b.stmts if isinstance(s, JumpIfFalse))
    jump_stmt.test = cond

    jump_block = next(b for b in cfg.blks if any(isinstance(s, JumpIfFalse) for s in b.stmts))
    else_block = cfg.label2blk[else_label]

    config = SolverConfig(merge_points={merge_label.idx}, enable_value_partition=True)
    budget = AnalysisBudget(enable_value_partition=True)
    init_state = AIState().set_var("c", AbsVal())
    solver = CFGBackedSolver(
        cfg=cfg,
        scope_ir=_DummyScope(),
        initial_state=init_state,
        pta_query=_DummyPtaQuery(),
        budget=budget,
        config=config,
    )

    solver.solve()

    edge_states = {(pred, succ): st for (pred, succ), st in solver.edge_out_states.items() if pred == jump_block.idx}
    true_branch_state = next(st for (pred, succ), st in edge_states.items() if succ != else_block.idx)  # cond true fallthrough (c is None)
    false_branch_state = edge_states[(jump_block.idx, else_block.idx)]  # cond false jump (c is not None)

    c_true = true_branch_state.get_var("c")
    c_false = false_branch_state.get_var("c")

    assert c_true.may_be_none is True
    assert c_true.bool_val is False  # None is falsy
    assert c_false.may_be_none is False
    assert c_false.bool_val is None  # Do not force truthiness


def test_summary_tagging_requires_wrapper():
    site = AllocSite(stmt="alloc", kind=AllocKind.OBJECT)
    truncated_ctx = CallStringContext((), 0)  # Already depth 0 but not summary-tagged
    obj = AbstractObject(context=truncated_ctx, alloc_site=site)
    assert not is_summary_object(obj)

    summary_obj = summarize_object(obj)
    assert is_summary_object(summary_obj)
    assert isinstance(summary_obj.context, SummaryContext)
    # Ensure summarize_object is idempotent and does not flip non-summary objects spuriously
    assert summarize_object(summary_obj) == summary_obj


def test_value_partition_budget_no_split_when_cap_zero():
    else_label = Label(20)
    merge_label = Label(21)
    cond = ast.Name(id="c", ctx=ast.Load())
    ast.fix_missing_locations(cond)
    stmts = [
        JumpIfFalse(cond, label=else_label),
        _make_assign("x", 1),
        Goto(merge_label),
        else_label,
        _make_assign("x", 2),
        merge_label,
    ]
    cfg = _build_cfg_from_stmts(stmts)

    jump_block = next(b for b in cfg.blks if any(isinstance(s, JumpIfFalse) for s in b.stmts))
    else_block = cfg.label2blk[else_label]

    config = SolverConfig(
        merge_points={merge_label.idx},
        enable_value_partition=True,
        max_value_partitions=0,  # force no-split
    )
    budget = AnalysisBudget(enable_value_partition=True)
    init_state = AIState().set_var("c", AbsVal())
    solver = CFGBackedSolver(
        cfg=cfg,
        scope_ir=_DummyScope(),
        initial_state=init_state,
        pta_query=_DummyPtaQuery(),
        budget=budget,
        config=config,
    )

    solver.solve()

    edge_states = {(pred, succ): st for (pred, succ), st in solver.edge_out_states.items() if pred == jump_block.idx}
    # Both branches should see the unsplit input state (no refinement)
    assert edge_states[(jump_block.idx, else_block.idx)].get_var("c").bool_val is None
    other_state = next(st for (pred, succ), st in edge_states.items() if succ != else_block.idx)
    assert other_state.get_var("c").bool_val is None


def test_merge_point_inference_collapses_traces():
    else_label = Label(30)
    merge_label = Label(31)
    stmts = [
        JumpIfFalse(ast.Name(id="c", ctx=ast.Load()), label=else_label),
        _make_assign("x", 1),
        Goto(merge_label),
        else_label,
        _make_assign("x", 2),
        merge_label,
    ]
    cfg = _build_cfg_from_stmts(stmts)
    merge_block = cfg.label2blk[merge_label]

    config = SolverConfig(
        enable_trace_partitioning=True,
        infer_merge_points=True,
        merge_points=set(),
        enable_value_partition=True,
    )
    budget = AnalysisBudget(enable_value_partition=True)
    init_state = AIState().set_var("c", AbsVal())
    solver = CFGBackedSolver(
        cfg=cfg,
        scope_ir=_DummyScope(),
        initial_state=init_state,
        pta_query=_DummyPtaQuery(),
        budget=budget,
        config=config,
    )

    solver.solve()

    merge_trace_keys = [
        tkey for (bid, tkey) in solver.trace_store.trace_states.keys() if bid == merge_block.idx
    ]
    assert len(merge_trace_keys) == 1
    assert merge_trace_keys[0] == TraceKey.initial()


def test_trace_visits_are_per_trace_when_partitioned():
    else_label = Label(40)
    merge_label = Label(41)
    stmts = [
        JumpIfFalse(ast.Name(id="c", ctx=ast.Load()), label=else_label),
        _make_assign("x", 1),
        Goto(merge_label),
        else_label,
        _make_assign("x", 2),
        merge_label,
    ]
    cfg = _build_cfg_from_stmts(stmts)
    merge_block = cfg.label2blk[merge_label]

    config = SolverConfig(
        enable_trace_partitioning=True,
        merge_points=set(),  # keep traces distinct at the join
        max_block_visits=1,  # would have skipped the 2nd trace previously
        enable_value_partition=True,
    )
    budget = AnalysisBudget(enable_value_partition=True)
    init_state = AIState().set_var("c", AbsVal())
    solver = CFGBackedSolver(
        cfg=cfg,
        scope_ir=_DummyScope(),
        initial_state=init_state,
        pta_query=_DummyPtaQuery(),
        budget=budget,
        config=config,
    )

    solver.solve()

    merge_states = [
        st for (bid, _), st in solver.trace_store.trace_states.items() if bid == merge_block.idx
    ]
    assert len(merge_states) == 2
    bool_vals = {st.get_var("c").bool_val for st in merge_states}
    assert bool_vals == {True, False}

