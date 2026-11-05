"""Tests for unknown resolution tracking in pointer analysis."""

import ast
from typing import Optional, Tuple

import pytest
from pythonstan.analysis.pointer.kcfa.config import Config
from pythonstan.analysis.pointer.kcfa.constraints import AllocConstraint, CallConstraint
from pythonstan.analysis.pointer.kcfa.context import CallSite, CallStringContext, Scope
from pythonstan.analysis.pointer.kcfa.object import (
    AbstractObject,
    AllocKind,
    AllocSite,
    ModuleObject,
    ObjectFactory,
)
from pythonstan.analysis.pointer.kcfa.points_to_set import PointsToSet, reset_object_table
from pythonstan.analysis.pointer.kcfa.processor.normal_call import NormalCallProcessor
from pythonstan.analysis.pointer.kcfa.solver import PointerSolver
from pythonstan.analysis.pointer.kcfa.state import PointerAnalysisState
from pythonstan.analysis.pointer.kcfa.unknown_tracker import UnknownKind, UnknownTracker
from pythonstan.analysis.pointer.kcfa.variable import VariableFactory, VariableKind
from pythonstan.ir.ir_statements import IRCall, IRImport, IRModule


@pytest.fixture(autouse=True)
def _reset_pts_table():
    reset_object_table()
    yield
    reset_object_table()


def _make_call_site(scope_name: str, index: int = 0) -> CallSite:
    call_stmt = IRCall(ast.parse("callsite()").body[0].value)
    return CallSite(statement=call_stmt, scope_name=scope_name, index=index)


def _make_module_scope() -> Tuple[Scope, CallStringContext, IRModule]:
    module_ast = ast.parse("")
    module_ir = IRModule("test_module", module_ast, "test_module", "test_module.py")
    context = CallStringContext((), 1)
    alloc_site = AllocSite(stmt=module_ir, kind=AllocKind.MODULE)
    module_obj = ModuleObject(context=context, alloc_site=alloc_site, ir=module_ir)
    scope = Scope.new(module_obj, None, context, module_ir, None)
    return scope, context, module_ir


def _make_solver(config: Optional[Config] = None) -> Tuple[PointerSolver, PointerAnalysisState]:
    config = config or Config()
    state = PointerAnalysisState()
    solver = PointerSolver(state, config, processor=NormalCallProcessor())
    return solver, state


class TestUnknownTracker:
    """Test UnknownTracker class."""

    def test_record_unknown(self):
        tracker = UnknownTracker()
        tracker.record(
            UnknownKind.CALLEE_EMPTY,
            "test.py:42",
            "Empty callee points-to set"
        )
        assert len(tracker.records) == 1
        assert tracker.records[0].kind == UnknownKind.CALLEE_EMPTY

    def test_record_dedupes_by_key(self):
        tracker = UnknownTracker()
        tracker.record(UnknownKind.CALLEE_EMPTY, "loc", "msg", context="ctx")
        tracker.record(UnknownKind.CALLEE_EMPTY, "loc", "msg2", context="ctx")
        summary = tracker.get_summary()
        assert summary["total_unknowns"] == 1
        assert summary["unknown_callee_empty"] == 1

    def test_record_respects_track_unknowns(self):
        tracker = UnknownTracker(track_unknowns=False)
        tracker.record(UnknownKind.CALLEE_EMPTY, "loc", "msg")
        summary = tracker.get_summary()
        assert summary["total_unknowns"] == 0


def test_fixpoint_unresolved_call_reports_unknown():
    config = Config(track_unknowns=True)
    solver, state = _make_solver(config)
    scope, context, _ = _make_module_scope()
    state.set_internal_scope(scope.obj, scope)
    factory = VariableFactory()

    callee_var = factory.make_variable("func", VariableKind.LOCAL)
    target_var = factory.make_variable("result", VariableKind.LOCAL)
    call = CallConstraint(
        callee=callee_var,
        args=(),
        kwargs=frozenset(),
        target=target_var,
        call_site=_make_call_site(scope.name, 1),
    )
    solver.add_constraint(scope, context, call)
    solver.solve_to_fixpoint()

    unknown_summary = solver.query().get_unknown_summary()
    assert unknown_summary.get("unknown_callee_empty", 0) >= 1


def test_deferred_non_callable_ignores_late_callable():
    config = Config(track_unknowns=True)
    solver, state = _make_solver(config)
    scope, context, _ = _make_module_scope()
    state.set_internal_scope(scope.obj, scope)
    factory = VariableFactory()

    callee_var = factory.make_variable("$call@test", VariableKind.TEMPORARY)
    call = CallConstraint(
        callee=callee_var,
        args=(),
        kwargs=frozenset(),
        target=None,
        call_site=_make_call_site(scope.name, 2),
    )
    solver.add_constraint(scope, context, call)

    ctx_callee = state.get_variable(scope, context, callee_var)
    non_callable = AbstractObject(context=context, alloc_site=AllocSite("noncallable", AllocKind.OBJECT))
    callable_obj = ObjectFactory.create_builtin_function("len", context)

    solver.handle_new_points_to(ctx_callee, scope, PointsToSet.singleton(non_callable))
    solver.handle_new_points_to(ctx_callee, scope, PointsToSet.singleton(callable_obj))
    solver.solve_to_fixpoint()

    unknown_summary = solver.query().get_unknown_summary()
    assert unknown_summary.get("unknown_callee_non_callable", 0) == 0


def test_import_not_found_records_correct_kind():
    class _DummyModuleGraph:
        def get_succ_module(self, _src, _stmt):
            return None

    class _DummyScopeManager:
        def __init__(self):
            self.module_graph = _DummyModuleGraph()

    config = Config(track_unknowns=True)
    solver, state = _make_solver(config)
    scope, context, _ = _make_module_scope()
    state.set_internal_scope(scope.obj, scope)
    state._scope_manager = _DummyScopeManager()
    factory = VariableFactory()

    import_stmt = IRImport(ast.parse("import missing_mod").body[0])
    alloc_site = AllocSite(stmt=import_stmt, kind=AllocKind.MODULE)
    alloc = AllocConstraint(target=factory.make_variable("missing_mod", VariableKind.LOCAL), alloc_site=alloc_site)
    solver.add_constraint(scope, context, alloc)
    solver.solve_to_fixpoint()

    unknown_summary = solver.query().get_unknown_summary()
    assert unknown_summary.get("unknown_import_not_found", 0) >= 1


def test_track_unknowns_disabled_suppresses_records():
    config = Config(track_unknowns=False)
    solver, state = _make_solver(config)
    scope, context, _ = _make_module_scope()
    state.set_internal_scope(scope.obj, scope)
    factory = VariableFactory()

    callee_var = factory.make_variable("func", VariableKind.LOCAL)
    call = CallConstraint(
        callee=callee_var,
        args=(),
        kwargs=frozenset(),
        target=None,
        call_site=_make_call_site(scope.name, 3),
    )
    solver.add_constraint(scope, context, call)
    solver.solve_to_fixpoint()

    summary = solver.query().get_unknown_summary()
    assert summary["total_unknowns"] == 0

