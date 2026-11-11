"""Regressions extracted from real-project super receiver crashes.

CPython is used only by tests as an independent witness. Source analysis never
executes target modules, and these may regressions do not certify exact MROs.
"""
import ast

import pytest

from pythonstan.analysis.pointer.kcfa import Config, PointerAnalysisState, PointerSolver
from pythonstan.analysis.pointer.kcfa.constraints import SuperResolveConstraint
from pythonstan.analysis.pointer.kcfa.context import CallStringContext, Scope
from pythonstan.analysis.pointer.kcfa.heap_model import attr
from pythonstan.analysis.pointer.kcfa.object import (
    AbstractObject, AllocKind, AllocSite, ClassObject, ConstantObject,
    FunctionObject, InstanceObject, MethodObject, ModuleObject, ObjectFactory, SuperObject,
)
from pythonstan.analysis.pointer.kcfa.points_to_set import PointsToSet, reset_object_table
from pythonstan.analysis.pointer.kcfa.processor import SuperResolveProcessor
from pythonstan.analysis.pointer.kcfa.variable import Variable, VariableKind
from pythonstan.ir.ir_statements import IRClass, IRFunc, IRModule
from pythonstan.world import World
from pythonstan.world.pipeline import Pipeline


def analyze(tmp_path, source, policy):
    path = tmp_path / "supercases.py"
    path.write_text(source)
    World.setup()
    pipeline = Pipeline(config={
        "filename": str(path), "project_path": str(tmp_path), "library_paths": [],
        "import_level": 0, "mock_libs": False, "time_count": False,
        "analysis": [{"name": "pta", "id": "PointerAnalysis", "prev_analysis": ["ir"],
                      "options": Config(context_policy=policy, enable_ai_intrinsics=False,
                                        log_level="ERROR", max_iterations=20000).to_dict()}],
    })
    pipeline.run()
    query = pipeline.analysis_manager.get_results("pta").query()
    assert query.get_statistics()["converged"] == 1
    return query, World().get_entry_module()


@pytest.mark.parametrize("policy", ["0-cfa", "2-cfa"])
@pytest.mark.parametrize("source, expected", [
    ('''
class Parent:
    def value(self): return 31
class Child(Parent):
    def value(self): return super(Child, self).value()
result = Child().value()
''', 31),
    ('''
class Parent:
    @classmethod
    def value(cls): return cls.marker
class Child(Parent):
    @classmethod
    def value(cls): return super().value()
class Grandchild(Child):
    marker = 47
result = Grandchild.value()
''', 47),
    ('''
class ParentMeta(type):
    def value(cls): return cls.marker
class Meta(ParentMeta):
    def value(cls): return super(Meta, cls).value()
class Container(metaclass=Meta):
    marker = 53
result = Container.value()
''', 53),
])
def test_source_instance_classmethod_and_metaclass_super(tmp_path, policy, source, expected):
    namespace = {}
    exec(source, namespace)
    assert namespace["result"] == expected
    query, module = analyze(tmp_path, source, policy)
    values = {obj.value for obj in query.points_to_at("result", module) if isinstance(obj, ConstantObject)}
    assert expected in values
    assert any("super_mro_suffix" in item["effects"] for item in query.get_residual_effects())
    assert not query.get_semantic_status()["certificate_eligible"]


@pytest.fixture
def model():
    reset_object_table()
    context = CallStringContext((), 1)
    ir = IRModule("supercases", ast.parse(""), filename="supercases.py")
    obj = ModuleObject(context, AllocSite(ir, AllocKind.MODULE), ir)
    scope = Scope.new(obj, None, context, ir, None)
    state = PointerAnalysisState()
    state.set_internal_scope(obj, scope)
    solver = PointerSolver(state, Config(max_iterations=20000), SuperResolveProcessor())
    yield solver, state, scope, context
    reset_object_table()


def make_class(model, name, base=None):
    _, state, scope, context = model
    base_name = base.ir.get_qualname() if base else None
    node = ast.parse(f"class {name}{'(' + base_name + ')' if base else ''}: pass").body[0]
    ir = IRClass(name, node)
    obj = ClassObject(context, AllocSite(ir, AllocKind.CLASS), scope, ir)
    internal = Scope.new(obj, scope, context, ir, scope)
    state.set_internal_scope(obj, internal)
    if base:
        state.set_points_to(state.get_variable(scope, context, Variable(base_name, VariableKind.GLOBAL)),
                            PointsToSet.singleton(base))
    return obj


@pytest.mark.parametrize("late", [False, True])
def test_every_class_receiver_pair_survives_late_argument_growth(model, late):
    solver, state, scope, context = model
    classes = [make_class(model, name) for name in ("First", "Second")]
    receivers = [InstanceObject(context, AllocSite(name, AllocKind.INSTANCE), cls)
                 for name, cls in zip(("one", "two"), classes)]
    receivers.append(classes[1])
    target, cls_var, receiver_var = [Variable(name) for name in ("super_result", "class_arg", "receiver_arg")]
    constraint = SuperResolveConstraint(target, cls_var, receiver_var)
    target_ctx = state.get_variable(scope, context, target)
    allocation = ObjectFactory.create_super(context, "super allocation")
    solver.handle_new_points_to(target_ctx, scope, PointsToSet.singleton(allocation))
    solver.solve_to_fixpoint()
    if not late:
        solver.add_constraint(scope, context, constraint)
    arrivals = [(cls_var, classes[0]), (receiver_var, receivers[0]),
                (cls_var, classes[1]), (receiver_var, receivers[1]), (receiver_var, receivers[2])]
    for argument, value in arrivals:
        solver.handle_new_points_to(state.get_variable(scope, context, argument), scope, PointsToSet.singleton(value))
        solver.solve_to_fixpoint()
    if late:
        solver.add_constraint(scope, context, constraint)
        solver.solve_to_fixpoint()
    pairs = {(obj.current_class, obj.instance_obj) for obj in state.get_points_to(target_ctx)
             if isinstance(obj, SuperObject) and obj.current_class is not None}
    assert pairs == {(cls, receiver) for cls in classes for receiver in receivers}


def test_implicit_super_subscribes_to_delayed_class_cell_and_positional_only_receiver(model):
    solver, state, module_scope, context = model
    classes = [make_class(model, name) for name in ("First", "Second")]
    ir = IRFunc("method", ast.parse("def method(receiver, /): return super()").body[0])
    function = FunctionObject(context, AllocSite(ir, AllocKind.FUNCTION), module_scope, ir)
    scope = Scope.new(function, module_scope, context, ir, module_scope)
    target = Variable("super_result")
    constraint = SuperResolveConstraint(target, None, None, implicit=True)
    target_ctx = state.get_variable(scope, context, target)
    solver.add_constraint(scope, context, constraint)
    solver.handle_new_points_to(target_ctx, scope, PointsToSet.singleton(ObjectFactory.create_super(context, "implicit super")))
    solver.solve_to_fixpoint()
    receiver_var = state.get_variable(scope, context, Variable("receiver"))
    class_cell = state.get_variable(scope, context, Variable("__class__", VariableKind.CELL))
    receivers = [InstanceObject(context, AllocSite(name, AllocKind.INSTANCE), cls)
                 for name, cls in zip(("one", "two"), classes)]
    for variable, value in [(receiver_var, receivers[0]), (class_cell, classes[0]),
                            (receiver_var, receivers[1]), (class_cell, classes[1])]:
        solver.handle_new_points_to(variable, scope, PointsToSet.singleton(value))
        solver.solve_to_fixpoint()
    pairs = {(obj.current_class, obj.instance_obj) for obj in state.get_points_to(target_ctx)
             if isinstance(obj, SuperObject) and obj.current_class is not None}
    assert pairs == {(cls, receiver) for cls in classes for receiver in receivers}


def test_one_argument_super_preserves_unbound_resolution(model):
    solver, state, scope, context = model
    cls = make_class(model, "Unbound")
    target, class_arg = Variable("super_result"), Variable("class_arg")
    target_ctx = state.get_variable(scope, context, target)
    solver.add_constraint(scope, context, SuperResolveConstraint(target, class_arg, None))
    solver.handle_new_points_to(target_ctx, scope, PointsToSet.singleton(ObjectFactory.create_super(context, "unbound super")))
    solver.handle_new_points_to(state.get_variable(scope, context, class_arg), scope, PointsToSet.singleton(cls))
    solver.solve_to_fixpoint()
    assert any(isinstance(obj, SuperObject) and obj.current_class == cls and obj.instance_obj is None
               for obj in state.get_points_to(target_ctx))


@pytest.mark.parametrize("receiver_kind", ["instance", "class", "unbound", "unknown"])
def test_super_field_binding_keeps_receiver_alternatives_and_own_storage_separate(model, receiver_kind):
    solver, state, scope, context = model
    parent = make_class(model, "Parent")
    child = make_class(model, "Child", parent)
    method_ir = IRFunc("method", ast.parse("def method(self): pass").body[0], is_method=True)
    method = MethodObject(context, AllocSite(method_ir, AllocKind.METHOD),
                          state.get_internal_scope(parent), method_ir, parent, None)
    parent_field = state.get_field(scope, context, parent, attr("method"))
    solver.handle_new_points_to(parent_field, scope, PointsToSet.singleton(method))
    receiver = {"instance": InstanceObject(context, AllocSite("receiver", AllocKind.INSTANCE), child),
                "class": child, "unbound": None,
                "unknown": AbstractObject(context, AllocSite("unknown", AllocKind.OBJECT))}[receiver_kind]
    if receiver is not None:
        own = state.get_write_field(scope, context, receiver, attr("method"))
        sentinel = ConstantObject(context, AllocSite("own sentinel", AllocKind.CONSTANT), 991)
        solver.handle_new_points_to(own, scope, PointsToSet.singleton(sentinel))
    solver.solve_to_fixpoint()
    super_obj = ObjectFactory.create_super(context, "super allocation", child, receiver)
    field = state.get_field(scope, context, super_obj, attr("method"))
    solver.solve_to_fixpoint()
    points = state.get_points_to(field)
    assert points
    assert all(not isinstance(obj, ConstantObject) for obj in points)
    methods = [obj for obj in points if isinstance(obj, MethodObject)]
    if receiver_kind == "instance":
        assert any(obj.instance_obj == receiver for obj in methods)
    elif receiver_kind == "class":
        assert any(obj.instance_obj is None and obj.class_obj == child for obj in methods)
        assert any(obj.instance_obj == child for obj in methods)
    else:
        assert any(obj.instance_obj is None for obj in methods)
