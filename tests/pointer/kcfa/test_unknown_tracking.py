"""Tests for unknown resolution tracking in pointer analysis."""

import ast
from types import SimpleNamespace
from typing import Optional, Tuple

import pytest
from pythonstan.analysis.pointer.kcfa.config import Config
from pythonstan.analysis.pointer.kcfa.constraints import AllocConstraint, CallConstraint, CopyConstraint, LoadConstraint
from pythonstan.analysis.pointer.kcfa.context import CallSite, CallStringContext, Scope
from pythonstan.analysis.pointer.kcfa.object import (
    AbstractObject,
    AllocKind,
    AllocSite,
    ModuleObject,
    ObjectFactory,
)
from pythonstan.analysis.pointer.kcfa.heap_model import attr
from pythonstan.analysis.pointer.kcfa.points_to_set import PointsToSet, reset_object_table
from pythonstan.analysis.pointer.kcfa.processor.normal_call import NormalCallProcessor
from pythonstan.analysis.pointer.kcfa.solver import PointerSolver
from pythonstan.analysis.pointer.kcfa.state import PointerAnalysisState
from pythonstan.analysis.pointer.kcfa.unknown_tracker import UnknownKind, UnknownTracker
from pythonstan.analysis.pointer.kcfa.variable import VariableFactory, VariableKind
from pythonstan.ir.ir_statements import IRAssign, IRCall, IRImport, IRModule


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
    imported = state.get_variable(scope, context, alloc.target)
    assert {obj.kind for obj in state.get_points_to(imported)} == {AllocKind.UNKNOWN}


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


@pytest.mark.parametrize("callee_name", ["effect_only", "$tmp7", "$call@protocol", "$call_lazy@late"])
def test_unresolved_effect_only_calls_are_reported_for_all_names(callee_name, capsys):
    solver, state = _make_solver()
    scope, context, _ = _make_module_scope()
    state.set_internal_scope(scope.obj, scope)
    callee = VariableFactory().make_variable(callee_name, VariableKind.LOCAL)
    solver.add_constraint(scope, context, CallConstraint(
        callee=callee, args=(), kwargs=frozenset(), target=None,
        call_site=_make_call_site("ordinary_application", 4),
    ))

    solver.solve_to_fixpoint()

    assert solver.query().get_unknown_summary()["unknown_callee_empty"] == 1
    assert capsys.readouterr().out == ""


def test_optional_attribute_hook_is_not_an_unresolved_user_call():
    solver, state = _make_solver()
    scope, context, _ = _make_module_scope()
    state.set_internal_scope(scope.obj, scope)
    callee = VariableFactory().make_variable("$getattr@optional", VariableKind.TEMPORARY)
    solver.add_constraint(scope, context, CallConstraint(
        callee=callee, args=(), kwargs=frozenset(), target=None,
        call_site=_make_call_site(scope.name, 5),
    ))

    solver.solve_to_fixpoint()

    assert solver.query().get_unknown_summary()["total_unknowns"] == 0


@pytest.mark.parametrize("track_unknowns", [True, False])
def test_iteration_budget_is_incomplete_and_does_not_finalize_calls(track_unknowns, caplog):
    solver, state = _make_solver(Config(max_iterations=1, track_unknowns=track_unknowns))
    scope, context, _ = _make_module_scope()
    state.set_internal_scope(scope.obj, scope)
    factory = VariableFactory()
    callee = factory.make_variable("later", VariableKind.LOCAL)
    solver.add_constraint(scope, context, CallConstraint(
        callee=callee, args=(), kwargs=frozenset(), target=None,
        call_site=_make_call_site(scope.name, 6),
    ))
    solver.add_constraint(scope, context, AllocConstraint(
        target=callee, alloc_site=AllocSite(IRAssign(ast.parse("later = 1").body[0]), AllocKind.OBJECT),
    ))

    solver.solve_to_fixpoint()

    stats = solver.query().get_statistics()
    assert stats["converged"] == 0
    assert stats["iteration_limit_reached"] == 1
    assert stats["pending_work"] > 0
    assert stats.get("unknown_solver_limit", 0) == int(track_unknowns)
    assert stats.get("unknown_callee_empty", 0) == 0
    assert "Stopped before convergence" in caplog.text
    assert "Converged after" not in caplog.text


def test_fixpoint_reached_at_exact_budget_is_complete():
    solver, state = _make_solver(Config(max_iterations=1))
    scope, context, _ = _make_module_scope()
    state.set_internal_scope(scope.obj, scope)
    factory = VariableFactory()
    solver.add_constraint(scope, context, CopyConstraint(
        source=factory.make_variable("unbound", VariableKind.LOCAL),
        target=factory.make_variable("copy", VariableKind.LOCAL),
    ))

    solver.solve_to_fixpoint()

    stats = solver.query().get_statistics()
    assert stats["iterations"] == 1
    assert stats["converged"] == 1
    assert stats["iteration_limit_reached"] == 0
    assert stats["pending_work"] == 0
    assert stats.get("unknown_solver_limit", 0) == 0


def test_effect_only_unmodeled_builtin_is_not_hidden_by_callability():
    solver, state = _make_solver()
    scope, context, _ = _make_module_scope()
    state.set_internal_scope(scope.obj, scope)
    callee = VariableFactory().make_variable("external_effect", VariableKind.LOCAL)
    solver.add_constraint(scope, context, CallConstraint(
        callee=callee, args=(), kwargs=frozenset(), target=None,
        call_site=_make_call_site(scope.name, 7),
    ))
    unresolved = ObjectFactory.create_builtin_function("external_effect", context)
    solver.handle_new_points_to(state.get_variable(scope, context, callee), scope, PointsToSet.singleton(unresolved))

    solver.solve_to_fixpoint()

    assert solver.query().get_unknown_summary()["unknown_callee_unmodeled"] == 1


@pytest.mark.parametrize("is_complete", [False, True])
def test_ai_intrinsic_applies_effects_only_after_completion(is_complete):
    solver, state = _make_solver()
    scope, context, _ = _make_module_scope()
    state.set_internal_scope(scope.obj, scope)
    factory = VariableFactory()
    target = factory.make_variable("result", VariableKind.LOCAL)
    call = CallConstraint(
        callee=factory.make_variable("getattr", VariableKind.LOCAL),
        args=(), kwargs=frozenset(), target=target,
        call_site=_make_call_site(scope.name, 8),
    )
    payload = AbstractObject(context=context, alloc_site=AllocSite("payload", AllocKind.OBJECT))
    owner = AbstractObject(context=context, alloc_site=AllocSite("owner", AllocKind.OBJECT))
    invocations = []

    def analyze_intrinsic(**kwargs):
        invocations.append(kwargs)
        return SimpleNamespace(
            is_complete=is_complete,
            ret={payload},
            writes=[SimpleNamespace(obj_addrs={owner}, field_key=attr("field"), value_addrs={payload})],
        )

    solver._ai_engine = SimpleNamespace(analyze_intrinsic=analyze_intrinsic)
    from pythonstan.analysis.composition.native_intrinsics import LegacyIntrinsicProvider
    solver.set_intrinsic_provider(LegacyIntrinsicProvider(
        engine=solver._ai_engine,adapter=getattr(solver,"_ai_adapter",None)))
    handled = solver._try_ai_intrinsic(
        scope, context, call, ObjectFactory.create_builtin_function("getattr", context),
    )
    solver.solve_to_fixpoint()

    assert invocations
    assert handled == is_complete
    assert (payload in state.get_points_to(state.get_variable(scope, context, target))) == is_complete
    assert (payload in state.get_points_to(state.get_field(scope, context, owner, attr("field")))) == is_complete


@pytest.mark.parametrize("track_unknowns", [False, True])
def test_unresolved_returns_and_derived_loads_are_not_empty(track_unknowns):
    solver, state = _make_solver(Config(track_unknowns=track_unknowns))
    scope, context, module = _make_module_scope()
    state.set_internal_scope(scope.obj, scope)
    factory = VariableFactory()
    missing, result, derived, other = [factory.make_variable(name, VariableKind.GLOBAL) for name in ("missing", "result", "derived", "other")]
    solver.add_constraint(scope, context, CallConstraint(
        missing, (), frozenset(), result, _make_call_site(scope.name, 9),
    ))
    solver.add_constraint(scope, context, LoadConstraint(result, attr("attribute"), derived))
    concrete = AbstractObject(context, AllocSite("concrete", AllocKind.OBJECT))
    solver.handle_new_points_to(state.get_variable(scope, context, other), scope, PointsToSet.singleton(concrete))

    solver.solve_to_fixpoint()

    query = solver.query()
    for variable in (result, derived):
        objects = state.get_points_to(state.get_variable(scope, context, variable))
        assert objects and all(obj.kind == AllocKind.UNKNOWN for obj in objects)
    assert query.may_alias("result", "other", module, context)
    assert query.get_residual_effects()[0]["return_target"] == "result"
    assert "may_mutate_heap" in query.get_residual_effects()[0]["effects"]
    assert query.get_statistics()["converged"] == 1
    if not track_unknowns:
        assert query.get_unknown_summary()["total_unknowns"] == 0


def test_ai_heap_dependency_replays_when_late_field_values_arrive():
    from pythonstan.analysis.pointer.kcfa.pta_query_adapter import PtaQueryAdapter

    solver, state = _make_solver()
    scope, context, _ = _make_module_scope()
    state.set_internal_scope(scope.obj, scope)
    factory = VariableFactory()
    target = factory.make_variable("result")
    owner = AbstractObject(context, AllocSite("owner", AllocKind.OBJECT))
    payload = AbstractObject(context, AllocSite("payload", AllocKind.OBJECT))
    call = CallConstraint(factory.make_variable("getattr"), (), frozenset(), target, _make_call_site(scope.name, 10))
    solver._ai_adapter = PtaQueryAdapter(state)

    def analyze_intrinsic(**_kwargs):
        objects = solver._ai_adapter.field_points_to(owner, attr("field"), scope, context)
        return SimpleNamespace(is_complete=True, ret=frozenset(objects), writes=())

    solver._ai_engine = SimpleNamespace(analyze_intrinsic=analyze_intrinsic)
    from pythonstan.analysis.composition.native_intrinsics import LegacyIntrinsicProvider
    solver.set_intrinsic_provider(LegacyIntrinsicProvider(
        engine=solver._ai_engine,adapter=getattr(solver,"_ai_adapter",None)))
    assert not solver._try_ai_intrinsic(scope, context, call, ObjectFactory.create_builtin_function("getattr", context))
    field = state.get_field(scope, context, owner, attr("field"))
    solver.handle_new_points_to(field, scope, PointsToSet.singleton(payload))
    solver.solve_to_fixpoint()

    assert payload in state.get_points_to(state.get_variable(scope, context, target))
    assert solver.query().get_statistics()["builtin_dependency_replays"] > 0

