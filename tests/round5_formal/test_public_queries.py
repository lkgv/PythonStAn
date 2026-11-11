"""Public read-only queries must expose facts produced by the same pipeline."""
from pythonstan.analysis.pointer.kcfa.object import FunctionObject
from pythonstan.analysis.pointer.kcfa.pointer_flow_graph import NormalNode
from pythonstan.analysis.pointer.kcfa.variable import VariableKind
from pythonstan.ir import IRModule
from pythonstan.ir.ir_statements import IRCall, IRFunc
from tests.pointer.kcfa.test_ai_bridge import analyze_source


def test_module_globals_and_explicit_context_queries_agree(tmp_path):
    query, _ = analyze_source(tmp_path, '''
def selected():
    return 1
callback = selected
result = callback()
''', enable_ai_intrinsics=False)
    scope = next(scope for scope in query.get_reachable_scopes() if isinstance(scope, IRModule))
    internal = query._state.get_scopes_for_ir(scope)
    assert internal
    aggregate = query.points_to_at("callback", scope)
    assert {obj.ir.name for obj in aggregate if isinstance(obj, FunctionObject)} == {"selected"}
    for actual in internal:
        contextual = query.points_to_at("callback", scope, actual.context)
        assert contextual
        assert set(contextual) <= set(aggregate)


def test_module_callsite_queries_match_the_actual_call_graph(tmp_path):
    query, _ = analyze_source(tmp_path, '''
def selected():
    return 1
result = selected()
''', enable_ai_intrinsics=False)
    scope = next(scope for scope in query.get_reachable_scopes() if isinstance(scope, IRModule))
    calls = [stmt for stmt in query._state.scope_manager.get_ir(scope, "ir") if isinstance(stmt, IRCall)]
    assert calls
    assert any({function.name for function in query.callees_as_ir(stmt, scope)} == {"selected"} for stmt in calls)


def test_public_query_resolves_function_locals_and_synthetic_names(tmp_path):
    query, _ = analyze_source(tmp_path, '''
def selected():
    return 1
def identity(callback):
    local = callback
    return local
result = identity(selected)
number = 42
''', enable_ai_intrinsics=False)
    scopes = query.get_reachable_scopes()
    module = next(scope for scope in scopes if isinstance(scope, IRModule))
    function = next(scope for scope in scopes if isinstance(scope, IRFunc) and scope.name == "identity")
    for name in ("callback", "local"):
        assert query._resolve_var_kind(name, function) == VariableKind.LOCAL
        assert {obj.ir.name for obj in query.points_to_at(name, function)
                if isinstance(obj, FunctionObject)} == {"selected"}
    for scope in (module, function):
        assert query._resolve_var_kind("$tmp_0", scope) == VariableKind.TEMPORARY
        assert query._resolve_var_kind("$const_0", scope) == VariableKind.GLOBAL
    assert query._resolve_var_kind("result", module) == VariableKind.GLOBAL
    # Use actual allocated synthetic constants, not only a classification unit
    # check; a wrong kind would create a fresh empty query slot.
    constants = [node.var for node, points in query._state._env.items()
                 if isinstance(node, NormalNode)
                 and getattr(node.var.content, "name", "").startswith("$const") and points]
    assert constants
    for ctxvar in constants:
        assert query.points_to_at(ctxvar.content.name, module)
