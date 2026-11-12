"""Actual optional debug hooks remain lazy and preserve host callback calls."""

import ast
from collections.abc import Callable

import pytest

from pythonstan.analysis.pointer.kcfa import debug_boundary
from pythonstan.analysis.pointer.kcfa.config import Config
from pythonstan.analysis.pointer.kcfa.context import CallStringContext, Scope
from pythonstan.analysis.pointer.kcfa.object import (
    AbstractObject,
    AllocKind,
    AllocSite,
    ModuleObject,
)
from pythonstan.analysis.pointer.kcfa.points_to_set import PointsToSet, reset_object_table
from pythonstan.analysis.pointer.kcfa.processor.processor import Processor
from pythonstan.analysis.pointer.kcfa.solver import PointerSolver
from pythonstan.analysis.pointer.kcfa.state import PointerAnalysisState
from pythonstan.analysis.pointer.kcfa.variable import Variable
from pythonstan.ir.ir_statements import IRModule


class PartialMonitor:
    enabled = True

    def __init__(self) -> None:
        self.iterations: list[int] = []
        self.snapshots: list[dict[str, int]] = []

    def set_iteration(self, iteration: int) -> None:
        self.iterations.append(iteration)

    def record_iteration_snapshot(self, **values: int) -> None:
        self.snapshots.append(values)


def _run_points(monitor: object) -> tuple[PointerSolver, PartialMonitor | None]:
    context = CallStringContext((), 1)
    module = IRModule("debug_hooks", ast.parse(""), filename="debug_hooks.py")
    module_object = ModuleObject(context, AllocSite(module, AllocKind.MODULE), module)
    scope = Scope.new(module_object, None, context, module, None)
    state = PointerAnalysisState()
    state.set_internal_scope(module_object, scope)
    solver = PointerSolver(
        state,
        Config(log_level="ERROR", enable_debug_monitor=True, debug_log_interval=1),
        Processor(),
        debug_monitor=monitor,
    )
    target = state.get_variable(scope, context, Variable("target"))
    value = AbstractObject(context, AllocSite("value", AllocKind.OBJECT))
    solver.handle_new_points_to(target, scope, PointsToSet.singleton(value))
    solver.solve_to_fixpoint()
    assert value in state.get_points_to(target)
    assert solver._stats["converged"] == 1
    assert solver._debug_monitor is monitor
    return solver, monitor if isinstance(monitor, PartialMonitor) else None


def test_actual_solver_keeps_partial_monitor_and_callback_order() -> None:
    reset_object_table()
    try:
        monitor = PartialMonitor()
        _, observed = _run_points(monitor)
        assert observed is monitor
        assert monitor.iterations == [1]
        assert len(monitor.snapshots) == 1
        assert monitor.snapshots[0]["worklist_size"] == 0
    finally:
        reset_object_table()


def test_falsey_monitor_requires_no_other_attributes_or_hooks() -> None:
    class FalseyMonitor:
        def __bool__(self) -> bool:
            return False

    reset_object_table()
    try:
        _run_points(FalseyMonitor())
    finally:
        reset_object_table()


def test_falsey_feature_value_does_not_require_missing_snapshot_hook() -> None:
    class DisabledSnapshot:
        enabled: tuple[()] = ()

        def __init__(self) -> None:
            self.iterations: list[int] = []

        def set_iteration(self, iteration: int) -> None:
            self.iterations.append(iteration)

    reset_object_table()
    try:
        monitor = DisabledSnapshot()
        _run_points(monitor)
        assert monitor.iterations == [1]
    finally:
        reset_object_table()


def test_hook_descriptor_is_captured_once_at_the_actual_call() -> None:
    class LateMonitor:
        def __init__(self) -> None:
            self.lookups = 0
            self.values: list[int] = []

        @property
        def set_iteration(self) -> Callable[[int], object]:
            self.lookups += 1
            return self.values.append

    monitor = LateMonitor()
    assert monitor.lookups == 0
    debug_boundary.set_iteration(monitor, 7)
    assert monitor.lookups == 1
    assert monitor.values == [7]


def test_malformed_hook_retains_python_argument_binding_failure() -> None:
    class WrongSignature:
        def set_iteration(self) -> None:
            raise AssertionError("Wrong-signature hook body must not run")

    with pytest.raises(TypeError):
        debug_boundary.set_iteration(WrongSignature(), 3)
