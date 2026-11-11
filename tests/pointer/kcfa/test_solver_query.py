import ast

import pytest

from pythonstan.analysis.pointer.kcfa.class_hierarchy import ClassHierarchyManager
from pythonstan.analysis.pointer.kcfa.context import CallSite, CallStringContext, Ctx, Scope
from pythonstan.analysis.pointer.kcfa.heap_model import attr, key
from pythonstan.analysis.pointer.kcfa.object import (
    AbstractObject,
    AllocKind,
    AllocSite,
    ClassObject,
    ConstantObject,
    DictObject,
    FunctionObject,
    InstanceObject,
    MethodObject,
    ModuleObject,
)
from pythonstan.analysis.pointer.kcfa.points_to_set import PointsToSet, reset_object_table
from pythonstan.analysis.pointer.kcfa.solver import SolverQuery
from pythonstan.analysis.pointer.kcfa.state import PointerAnalysisState
from pythonstan.analysis.pointer.kcfa.unknown_tracker import UnknownTracker
from pythonstan.analysis.pointer.kcfa.variable import Variable, VariableKind
from pythonstan.graph.call_graph import CallEdge, CallKind
from pythonstan.ir.ir_statements import IRCall, IRClass, IRFunc, IRLoadAttr, IRModule, IRStoreAttr
from pythonstan.world import World


@pytest.fixture(autouse=True)
def _reset_pts_table():
    reset_object_table()
    yield
    reset_object_table()


@pytest.fixture()
def world():
    World.setup()
    world_obj = World()
    world_obj.scope_manager.build()
    return world_obj


def _make_context(name: str) -> CallStringContext:
    call_stmt = IRCall(ast.parse(f"{name}()").body[0].value)
    call_site = CallSite(statement=call_stmt, scope_name=name)
    return CallStringContext((call_site,), 1)


def _make_module(name: str) -> IRModule:
    module_ast = ast.parse("x = 1")
    return IRModule(name, module_ast, name, f"{name}.py")


def _make_module_scope(module_ir: IRModule, context: CallStringContext) -> Scope:
    alloc_site = AllocSite.from_ir_node(module_ir, AllocKind.MODULE)
    module_obj = ModuleObject(context, alloc_site, module_ir)
    return Scope.new(module_obj, None, context, module_ir, None)


def _make_query(state: PointerAnalysisState, class_hierarchy=None) -> SolverQuery:
    return SolverQuery(state, {}, UnknownTracker(), class_hierarchy=class_hierarchy)


def test_points_to_at_context_sensitive(world):
    state = PointerAnalysisState()
    module_ir = _make_module("mod")
    world.set_entry_module(module_ir)

    ctx1 = _make_context("ctx1")
    ctx2 = _make_context("ctx2")
    module_scope = _make_module_scope(module_ir, CallStringContext((), 1))
    function_ir = IRFunc("mod.subject", ast.parse("def subject(x):\n    return x\n").body[0])
    function = FunctionObject(module_scope.context, AllocSite.from_ir_node(function_ir, AllocKind.FUNCTION), module_scope, function_ir)
    scope1 = Scope.new(function, module_scope, ctx1, function_ir, module_scope)
    scope2 = Scope.new(function, module_scope, ctx2, function_ir, module_scope)

    obj1 = AbstractObject(ctx1, AllocSite("obj1", AllocKind.OBJECT))
    obj2 = AbstractObject(ctx2, AllocSite("obj2", AllocKind.OBJECT))

    # Function calls have distinct local contexts; module globals do not.
    for target in (scope1, scope2):
        callsite = target.context.call_sites[0]
        state.call_graph.add_edge(CallEdge(
            kind=CallKind.FUNCTION,
            callsite=Ctx(module_scope.context, module_scope, callsite),
            callee=target,
        ))

    var = Variable(name="x", kind=VariableKind.LOCAL)
    ctx_var1 = state.get_variable(scope1, ctx1, var)
    ctx_var2 = state.get_variable(scope2, ctx2, var)
    state.set_points_to(ctx_var1, PointsToSet.singleton(obj1))
    state.set_points_to(ctx_var2, PointsToSet.singleton(obj2))

    query = _make_query(state)

    pts1 = query.points_to_at("x", function_ir, ctx1)
    pts2 = query.points_to_at("x", function_ir, ctx2)
    pts_all = query.points_to_at("x", function_ir, None)

    assert pts1 == PointsToSet.singleton(obj1)
    assert pts2 == PointsToSet.singleton(obj2)
    assert obj1 in pts_all and obj2 in pts_all


def test_attr_queries(world):
    state = PointerAnalysisState()
    module_ir = _make_module("mod")
    world.set_entry_module(module_ir)

    ctx = _make_context("ctx")
    scope = _make_module_scope(module_ir, ctx)
    state.set_internal_scope(scope.obj, scope)

    value_obj = AbstractObject(ctx, AllocSite("val", AllocKind.OBJECT))
    var = Variable(name="y", kind=VariableKind.GLOBAL)
    ctx_var = state.get_variable(scope, ctx, var)
    state.set_points_to(ctx_var, PointsToSet.singleton(value_obj))

    load_stmt = IRLoadAttr(ast.parse("y = x.attr").body[0])
    store_stmt = IRStoreAttr(ast.parse("x.attr = y").body[0])

    query = _make_query(state)

    assert query.load_attr_result(load_stmt, module_ir, ctx) == PointsToSet.singleton(value_obj)
    assert query.store_attr_value(store_stmt, module_ir, ctx) == PointsToSet.singleton(value_obj)


def test_call_graph_queries(world):
    state = PointerAnalysisState()
    module_ir = _make_module("mod")
    world.set_entry_module(module_ir)

    ctx = _make_context("ctx")
    scope = _make_module_scope(module_ir, ctx)
    state.set_internal_scope(scope.obj, scope)

    func_ast = ast.parse("def f():\n    return 1\n").body[0]
    func_ir = IRFunc("mod.f", func_ast)
    func_obj = FunctionObject(ctx, AllocSite.from_ir_node(func_ir, AllocKind.FUNCTION), scope, func_ir)
    callee_scope = Scope.new(func_obj, scope.module, ctx, func_ir, scope)

    call_stmt = IRCall(ast.parse("f()").body[0].value)
    call_site = CallSite(statement=call_stmt, scope_name="__main__")
    call_edge = CallEdge(kind=CallKind.FUNCTION, callsite=Ctx(ctx, scope, call_site), callee=callee_scope)
    state.call_graph.add_edge(call_edge)

    query = _make_query(state)

    assert func_obj in query.callees_of(call_stmt, module_ir, ctx)
    assert func_ir in query.callees_as_ir(call_stmt, module_ir, ctx)
    assert (call_stmt, module_ir) in query.callers_of(func_ir)


def test_receiver_objects(world):
    state = PointerAnalysisState()
    module_ir = _make_module("mod")
    world.set_entry_module(module_ir)

    ctx = _make_context("ctx")
    scope = _make_module_scope(module_ir, ctx)
    state.set_internal_scope(scope.obj, scope)

    recv_obj = AbstractObject(ctx, AllocSite("recv", AllocKind.OBJECT))
    recv_var = Variable(name="obj", kind=VariableKind.GLOBAL)
    recv_ctx_var = state.get_variable(scope, ctx, recv_var)
    state.set_points_to(recv_ctx_var, PointsToSet.singleton(recv_obj))

    load_stmt = IRLoadAttr(ast.parse("tmp = obj.m").body[0])
    call_stmt = IRCall(ast.parse("tmp()").body[0].value)
    state.scope_manager.set_ir(module_ir, "ir", [load_stmt, call_stmt])

    query = _make_query(state)
    pts = query.receiver_objects(call_stmt, module_ir, ctx)

    assert recv_obj in pts


def test_chained_queries(world):
    state = PointerAnalysisState()
    module_ir = _make_module("mod")
    world.set_entry_module(module_ir)

    ctx = _make_context("ctx")
    scope = _make_module_scope(module_ir, ctx)
    state.set_internal_scope(scope.obj, scope)

    base_obj = AbstractObject(ctx, AllocSite("base", AllocKind.OBJECT))
    value_obj = AbstractObject(ctx, AllocSite("value", AllocKind.OBJECT))
    state.obj_scope[base_obj] = scope
    field_var = state.get_field(scope, ctx, base_obj, attr("foo"))
    state.set_points_to(field_var, PointsToSet.singleton(value_obj))

    dict_obj = DictObject(ctx, AllocSite("dict", AllocKind.DICT))
    dict_val = AbstractObject(ctx, AllocSite("dictval", AllocKind.OBJECT))
    state.obj_scope[dict_obj] = scope
    dict_field = state.get_field(scope, ctx, dict_obj, key("k"))
    state.set_points_to(dict_field, PointsToSet.singleton(dict_val))

    query = _make_query(state)

    base_pts = PointsToSet.singleton(base_obj)
    assert value_obj in query.field_of_objects(base_pts, "foo")

    dict_pts = PointsToSet.singleton(dict_obj)
    assert dict_val in query.subscript_of_objects(dict_pts, "k")


def test_python_specific_queries(world):
    state = PointerAnalysisState()
    module_ir = _make_module("mod")
    world.set_entry_module(module_ir)

    ctx = _make_context("ctx")
    scope = _make_module_scope(module_ir, ctx)
    state.set_internal_scope(scope.obj, scope)

    class_hierarchy = ClassHierarchyManager()

    class_a_ast = ast.parse("class A: pass").body[0]
    class_b_ast = ast.parse("class B(A): pass").body[0]
    class_a_ir = IRClass("mod.A", class_a_ast)
    class_b_ir = IRClass("mod.B", class_b_ast)

    class_a_obj = ClassObject(ctx, AllocSite.from_ir_node(class_a_ir, AllocKind.CLASS), scope, class_a_ir)
    class_b_obj = ClassObject(ctx, AllocSite.from_ir_node(class_b_ir, AllocKind.CLASS), scope, class_b_ir)
    class_hierarchy._bases[class_a_obj] = []
    class_hierarchy._bases[class_b_obj] = [class_a_obj]

    instance_obj = InstanceObject(ctx, AllocSite("inst", AllocKind.INSTANCE), class_b_obj)
    method_ast = ast.parse("def m(self):\n    return 1\n").body[0]
    method_ir = IRFunc("mod.B.m", method_ast)
    method_obj = MethodObject(ctx, AllocSite.from_ir_node(method_ir, AllocKind.METHOD), scope, method_ir, class_b_obj, instance_obj)

    func_ast = ast.parse("def inner():\n    return x\n").body[0]
    func_ir = IRFunc("mod.inner", func_ast)
    func_obj = FunctionObject(ctx, AllocSite.from_ir_node(func_ir, AllocKind.FUNCTION), scope, func_ir)
    state._heap.set_obj(scope, ctx, func_obj.alloc_site, func_obj)

    captured_var = Variable(name="x", kind=VariableKind.CELL)
    captured_ctx_var = state.get_variable(scope, ctx, captured_var)
    captured_obj = ConstantObject(ctx, AllocSite("const", AllocKind.CONSTANT), 1)
    state.set_points_to(captured_ctx_var, PointsToSet.singleton(captured_obj))
    state.set_cell_vars(func_obj, {"x": captured_ctx_var})

    query = _make_query(state, class_hierarchy=class_hierarchy)

    mro = query.get_class_mro(class_b_obj)
    assert mro[0] == class_b_obj
    assert class_a_obj in mro
    assert query.get_instance_class(instance_obj) == class_b_obj
    assert query.get_bound_receiver(method_obj) == instance_obj
    assert query.get_captured_vars(func_ir, ctx)["x"] == PointsToSet.singleton(captured_obj)


def test_deprecated_methods(world):
    state = PointerAnalysisState()
    module_ir = _make_module("mod")
    world.set_entry_module(module_ir)

    ctx = _make_context("ctx")
    scope = _make_module_scope(module_ir, ctx)
    state.set_internal_scope(scope.obj, scope)

    obj = AbstractObject(ctx, AllocSite("obj", AllocKind.OBJECT))
    val = AbstractObject(ctx, AllocSite("val", AllocKind.OBJECT))

    state.obj_scope[obj] = scope
    field_var = state.get_field(scope, ctx, obj, attr("name"))
    state.set_points_to(field_var, PointsToSet.singleton(val))

    var = Variable(name="x", kind=VariableKind.LOCAL)
    ctx_var = state.get_variable(scope, ctx, var)
    state.set_points_to(ctx_var, PointsToSet.singleton(obj))

    query = _make_query(state)

    with pytest.warns(DeprecationWarning):
        assert obj in query.points_to(var)

    with pytest.warns(DeprecationWarning):
        assert val in query.get_field(obj, attr("name"))
