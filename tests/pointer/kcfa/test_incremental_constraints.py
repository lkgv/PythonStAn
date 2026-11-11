"""A rule added after its inputs must see the same facts as an early rule."""

import ast

import pytest

from pythonstan.analysis.pointer.kcfa.config import Config
from pythonstan.analysis.pointer.kcfa.constraints import (
    AllocConstraint, AttrReadConstraint, AttrWriteConstraint, CallConstraint, LoadConstraint,
    LoadSubscrConstraint, StoreConstraint, StoreSubscrConstraint,
)
from pythonstan.analysis.pointer.kcfa.context import CallSite, CallStringContext, Scope
from pythonstan.analysis.pointer.kcfa.heap_model import attr, key
from pythonstan.analysis.pointer.kcfa.object import AbstractObject, AllocKind, AllocSite, ConstantObject, ModuleObject
from pythonstan.analysis.pointer.kcfa.points_to_set import PointsToSet, reset_object_table
from pythonstan.analysis.pointer.kcfa.processor import AttributeSemanticsProcessor, ComposeProcessor, ContainerProcessor
from pythonstan.analysis.pointer.kcfa.processor.processor import Processor
from pythonstan.analysis.pointer.kcfa.solver import PointerSolver
from pythonstan.analysis.pointer.kcfa.state import PointerAnalysisState
from pythonstan.analysis.pointer.kcfa.variable import VariableFactory
from pythonstan.ir.ir_statements import IRCall, IRModule


@pytest.fixture
def model():
    reset_object_table()
    context = CallStringContext((), 1)
    module = IRModule("incremental", ast.parse(""), "incremental", "incremental.py")
    obj = ModuleObject(context, AllocSite(module, AllocKind.MODULE), module)
    scope = Scope.new(obj, None, context, module, None)
    state = PointerAnalysisState()
    state.set_internal_scope(obj, scope)
    solver = PointerSolver(state, Config(), ComposeProcessor([
        AttributeSemanticsProcessor(), ContainerProcessor(),
    ]))
    yield solver, state, scope, context, VariableFactory()
    reset_object_table()


@pytest.mark.parametrize("late", [False, True])
@pytest.mark.parametrize("form", ["field", "attribute", "subscript"])
def test_reads_replay_preexisting_base_and_index(model, late, form):
    solver, state, scope, context, factory = model
    base, index, target = [factory.make_variable(name) for name in ("base", "index", "target")]
    owner = AbstractObject(context=context, alloc_site=AllocSite("owner", AllocKind.OBJECT))
    value = AbstractObject(context=context, alloc_site=AllocSite("payload", AllocKind.OBJECT))
    field = key(0) if form == "subscript" else attr("payload")
    site = CallSite(IRCall(ast.parse("read()").body[0].value), scope.name)
    constraint = {
        "field": LoadConstraint(base, field, target),
        "attribute": AttrReadConstraint(base, field, target, site),
        "subscript": LoadSubscrConstraint(base=base, index=index, target=target),
    }[form]
    if not late:
        solver.add_constraint(scope, context, constraint)
    solver.handle_new_points_to(state.get_variable(scope, context, base), scope, PointsToSet.singleton(owner))
    solver.handle_new_points_to(state.get_field(scope, context, owner, field), scope, PointsToSet.singleton(value))
    index_obj = ConstantObject(context, AllocSite("zero", AllocKind.CONSTANT), 0)
    solver.handle_new_points_to(state.get_variable(scope, context, index), scope, PointsToSet.singleton(index_obj))
    solver.solve_to_fixpoint()
    if late:
        solver.add_constraint(scope, context, constraint)
        solver.add_constraint(scope, context, constraint)
        assert len(solver._constraint_replays) == 1
        solver.solve_to_fixpoint()

    assert value in state.get_points_to(state.get_variable(scope, context, target))


@pytest.mark.parametrize("late", [False, True])
@pytest.mark.parametrize("form", ["field", "attribute", "subscript"])
def test_writes_replay_preexisting_base_and_index(model, late, form):
    solver, state, scope, context, factory = model
    base, index, source = [factory.make_variable(name) for name in ("base", "index", "source")]
    owner = AbstractObject(context=context, alloc_site=AllocSite("owner", AllocKind.OBJECT))
    value = AbstractObject(context=context, alloc_site=AllocSite("payload", AllocKind.OBJECT))
    field = key(0) if form == "subscript" else attr("payload")
    site = CallSite(IRCall(ast.parse("write()").body[0].value), scope.name)
    constraint = {
        "field": StoreConstraint(base, field, source),
        "attribute": AttrWriteConstraint(base, field, source, site),
        "subscript": StoreSubscrConstraint(base, index, source),
    }[form]
    if not late:
        solver.add_constraint(scope, context, constraint)
    solver.handle_new_points_to(state.get_variable(scope, context, base), scope, PointsToSet.singleton(owner))
    solver.handle_new_points_to(state.get_variable(scope, context, source), scope, PointsToSet.singleton(value))
    index_obj = ConstantObject(context, AllocSite("zero", AllocKind.CONSTANT), 0)
    solver.handle_new_points_to(state.get_variable(scope, context, index), scope, PointsToSet.singleton(index_obj))
    solver.solve_to_fixpoint()
    if late:
        solver.add_constraint(scope, context, constraint)
        solver.solve_to_fixpoint()

    assert value in state.get_points_to(state.get_field(scope, context, owner, field))


def test_late_calls_keep_the_original_callee_and_receive_future_targets(model):
    solver, state, scope, context, factory = model

    class RecordingProcessor(Processor):
        def __init__(self):
            self.calls = []

        def handle_call(self, solver, target, scope, constraint, callee_obj):
            self.calls.append((constraint.callee, callee_obj))
            return True

    processor = RecordingProcessor()
    solver.processor = processor
    callee = factory.make_variable("callback")
    trigger = state.get_variable(scope, context, callee)
    first = AbstractObject(context=context, alloc_site=AllocSite("first", AllocKind.OBJECT))
    second = AbstractObject(context=context, alloc_site=AllocSite("second", AllocKind.OBJECT))
    solver.handle_new_points_to(trigger, scope, PointsToSet.singleton(first))
    solver.solve_to_fixpoint()
    site = CallSite(IRCall(ast.parse("callback()").body[0].value), scope.name)
    solver.add_constraint(scope, context, CallConstraint(callee, (), frozenset(), None, site))
    solver.handle_new_points_to(trigger, scope, PointsToSet.singleton(second))
    solver.solve_to_fixpoint()

    assert set(processor.calls) == {(callee, first), (callee, second)}
    assert len(processor.calls) == 2


@pytest.mark.parametrize("late", [False, True])
def test_reused_abstract_allocation_reaches_each_target(model, late):
    solver, state, scope, context, factory = model
    first, second = [factory.make_variable(name) for name in ("first", "second")]
    site = AllocSite(IRCall(ast.parse("object()").body[0].value), AllocKind.OBJECT)
    solver.add_constraint(scope, context, AllocConstraint(first, site))
    if late:
        solver.solve_to_fixpoint()
    solver.add_constraint(scope, context, AllocConstraint(second, site))
    solver.solve_to_fixpoint()

    first_points = state.get_points_to(state.get_variable(scope, context, first))
    second_points = state.get_points_to(state.get_variable(scope, context, second))
    assert not first_points.is_empty()
    assert first_points == second_points
