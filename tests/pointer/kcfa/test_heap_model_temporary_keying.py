import ast

from pythonstan.analysis.pointer.kcfa.context import (
    CallSite,
    CallStringContext,
    Scope,
)
from pythonstan.analysis.pointer.kcfa.heap_model import HeapModel
from pythonstan.analysis.pointer.kcfa.object import (
    AllocKind,
    AllocSite,
    FunctionObject,
    ModuleObject,
)
from pythonstan.analysis.pointer.kcfa.variable import Variable, VariableKind
from pythonstan.ir.ir_statements import IRCall, IRFunc, IRModule


def _make_call_context(label: str, index: int) -> CallStringContext:
    call_expr = ast.parse("f()").body[0].value
    ir_call = IRCall(call_expr)
    call_site = CallSite(statement=ir_call, scope_name=label, index=index)
    return CallStringContext((call_site,), 2)


def _make_module_scope():
    module_ast = ast.parse("pass")
    module_ir = IRModule(
        "test_module",
        module_ast,
        name="test_module",
        filename="test_module.py",
    )
    module_ctx = CallStringContext((), 0)
    module_alloc = AllocSite(module_ir, AllocKind.MODULE)
    module_obj = ModuleObject(module_ctx, module_alloc, module_ir)
    module_scope = Scope.new(module_obj, None, module_ctx, module_ir, None)
    return module_scope


def _make_function_ir(module_scope: Scope):
    func_ast = ast.parse("def f():\n    return None").body[0]
    func_ir = IRFunc(f"{module_scope.stmt.get_qualname()}.f", func_ast)
    func_alloc = AllocSite(func_ir, AllocKind.FUNCTION)
    func_obj = FunctionObject(
        module_scope.context,
        func_alloc,
        module_scope,
        func_ir,
    )
    return func_ir, func_obj


def test_shared_return_temporary_is_function_scoped():
    heap = HeapModel()
    module_scope = _make_module_scope()
    func_ir, func_obj = _make_function_ir(module_scope)
    ctx1 = _make_call_context("caller1", 1)
    ctx2 = _make_call_context("caller2", 2)
    scope1 = Scope.new(func_obj, module_scope, ctx1, func_ir, module_scope)
    scope2 = Scope.new(func_obj, module_scope, ctx2, func_ir, module_scope)
    temp_var = Variable("$return", VariableKind.TEMPORARY)
    assert heap._get_var_key(scope1, ctx1, temp_var) == (func_obj,)
    assert heap._get_var_key(scope2, ctx2, temp_var) == (func_obj,)


def test_other_temporaries_are_context_sensitive():
    heap = HeapModel()
    module_scope = _make_module_scope()
    func_ir, func_obj = _make_function_ir(module_scope)
    ctx1 = _make_call_context("caller1", 1)
    ctx2 = _make_call_context("caller2", 2)
    scope1 = Scope.new(func_obj, module_scope, ctx1, func_ir, module_scope)
    scope2 = Scope.new(func_obj, module_scope, ctx2, func_ir, module_scope)
    temp_var = Variable("$call@site", VariableKind.TEMPORARY)
    assert heap._get_var_key(scope1, ctx1, temp_var) != heap._get_var_key(
        scope2,
        ctx2,
        temp_var,
    )


def test_dollar_prefixed_locals_are_not_shared():
    heap = HeapModel()
    module_scope = _make_module_scope()
    func_ir, func_obj = _make_function_ir(module_scope)
    ctx1 = _make_call_context("caller1", 1)
    ctx2 = _make_call_context("caller2", 2)
    scope1 = Scope.new(func_obj, module_scope, ctx1, func_ir, module_scope)
    scope2 = Scope.new(func_obj, module_scope, ctx2, func_ir, module_scope)
    local_var = Variable("$call@site", VariableKind.LOCAL)
    assert heap._get_var_key(scope1, ctx1, local_var) != heap._get_var_key(
        scope2,
        ctx2,
        local_var,
    )
