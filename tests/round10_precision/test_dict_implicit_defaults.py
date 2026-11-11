"""Independent return/heap witnesses for native dictionary default methods."""

import ast

import pytest

from pythonstan.analysis.pointer.kcfa.builtin_api_handler import BuiltinAPIHandler
from pythonstan.analysis.pointer.kcfa.config import Config
from pythonstan.analysis.pointer.kcfa.constraints import CallConstraint, ResidualEffectConstraint
from pythonstan.analysis.pointer.kcfa.context import CallSite, CallStringContext, Scope
from pythonstan.analysis.pointer.kcfa.object import (
    AllocKind, AllocSite, BuiltinMethodObject, ConstantObject, DictObject, FunctionObject, ModuleObject,
)
from pythonstan.analysis.pointer.kcfa.state import PointerAnalysisState, Worklist
from pythonstan.analysis.pointer.kcfa.variable import Variable
from pythonstan.ir import IRCall, IRModule
from pythonstan.world import World
from tests.pointer.kcfa.test_ai_bridge import analyze_source as _analyze_source


def analyze_source(tmp_path, source, **options):
    # Uninvoked synthetic methods must not supply a callback edge that masks a
    # missing native transfer or a missing source override invocation.
    return _analyze_source(tmp_path, source, enable_synthetic_method_contexts=False, **options)


def values_at(query, name):
    return {obj.value for obj in query.points_to_at(name, World().get_entry_module())
            if isinstance(obj, ConstantObject)}


@pytest.mark.parametrize("index_sensitive", [False, True])
@pytest.mark.parametrize("intrinsics", [False, True])
@pytest.mark.parametrize("context_policy", ["0-cfa", "1-cfa", "2-cfa"])
def test_omitted_defaults_recover_none_return_and_discarded_heap_effect(
        tmp_path, index_sensitive, intrinsics, context_policy):
    source = '''
mapping = {}
missing = mapping.get("missing")
inserted = mapping.setdefault("inserted")
stored = mapping["inserted"]
discarded = {}
discarded.setdefault("side_effect")
effect = discarded["side_effect"]
'''
    concrete = {}
    exec(source, concrete)
    assert all(concrete[name] is None for name in ("missing", "inserted", "stored", "effect"))
    query, _ = analyze_source(tmp_path, source, index_sensitive=index_sensitive,
                              enable_ai_intrinsics=intrinsics, context_policy=context_policy)
    for name in ("missing", "inserted", "stored", "effect"):
        assert None in values_at(query, name), name
    assert query.get_statistics()["converged"] == 1


@pytest.mark.parametrize("index_sensitive", [False, True])
@pytest.mark.parametrize("intrinsics", [False, True])
@pytest.mark.parametrize("schedule", ["fifo", "lifo"])
def test_existing_late_and_default_callbacks_survive_aliases_and_worklist_orders(
        tmp_path, monkeypatch, index_sensitive, intrinsics, schedule):
    if schedule == "lifo":
        def pop(self):
            scope, node, points = self.items_list.pop()
            del self.items_dict[node]
            return scope, node, points.val
        monkeypatch.setattr(Worklist, "pop", pop)
    query, edges = analyze_source(tmp_path, '''
def present(): return "present"
def late(): return "late"
def fallback(): return "fallback"
def unrelated(): return "unrelated"
def lookup(routes, name): return routes.get(name)
def with_default(routes, name): return routes.get(name, fallback)
def set_default(routes, name): return routes.setdefault(name, fallback)
def name(): return "late"
mapping = {"known": present}
noise = {"late": unrelated}
alias = mapping
known = alias.get("known")
known()
existing = alias.setdefault("known")
existing()
pending = lookup(alias, name())
pending()
explicit = with_default(alias, name())
explicit()
setter = set_default(alias, name())
setter()
mapping["late"] = late
''', index_sensitive=index_sensitive, enable_ai_intrinsics=intrinsics)
    assert {("subject", "subject.present"), ("subject", "subject.late"),
            ("subject", "subject.fallback")} <= edges
    assert ("subject", "subject.unrelated") not in edges
    # The abstraction cannot prove key membership. Keeping the implicit None
    # alongside an existing value is required by this deliberately weak join.
    assert None in values_at(query, "known")
    assert None in values_at(query, "existing")
    assert None in values_at(query, "pending")
    for result in ("explicit", "setter"):
        functions = {obj.ir.get_qualname()
                     for obj in query.points_to_at(result, World().get_entry_module())
                     if isinstance(obj, FunctionObject)}
        assert {"subject.late", "subject.fallback"} <= functions, result
    assert query.get_statistics()["converged"] == 1


@pytest.mark.parametrize("method", ["get", "setdefault"])
@pytest.mark.parametrize("arguments", ["", '"key", fallback, fallback',
                                       '"key", default=fallback', 'key="key"'])
def test_definitely_invalid_native_arity_retains_raise_obligation_without_effects(
        tmp_path, method, arguments):
    query, edges = analyze_source(tmp_path, f'''
def fallback(): return "fallback"
mapping = {{}}
invalid = mapping.{method}({arguments})
invalid()
after = mapping.get("key")
after()
''')
    assert not query.points_to_at("invalid", World().get_entry_module())
    assert ("subject", "subject.fallback") not in edges
    effects = query.get_residual_effects()
    assert any(f"dict.{method} requires one or two positional-only arguments" in row["reason"]
               and "may_raise" in row["effects"] for row in effects)


def test_source_overrides_accept_keywords_without_native_argument_rejection(tmp_path):
    query, edges = analyze_source(tmp_path, '''
def callback(): return "callback"
class Custom(dict):
    def get(self, *, key): return callback
    def setdefault(self, *, key, default): return default
mapping = Custom()
mapping.get(key="key")()
mapping.setdefault(key="key", default=callback)()
''')
    assert {("subject", "subject.Custom.get"), ("subject", "subject.Custom.setdefault"),
            ("subject", "subject.callback")} <= edges
    assert not any("dict.get requires" in row["reason"] or "dict.setdefault requires" in row["reason"]
                   for row in query.get_residual_effects())


def handler_fixture(expression):
    context = CallStringContext((), 1)
    module = IRModule("native", ast.parse(""), "native", "native.py")
    module_obj = ModuleObject(context, AllocSite(module, AllocKind.MODULE), module)
    scope = Scope.new(module_obj, None, context, module, None)
    state = PointerAnalysisState()
    handler = BuiltinAPIHandler(state, Config())
    statement = IRCall(ast.parse(expression).body[0].value)
    call = CallConstraint(Variable("method"),
                          tuple(Variable(name) for name, _ in statement.get_args()),
                          frozenset((name if name is not None else "**", Variable(value))
                                    for name, value in statement.get_keywords()),
                          Variable("result"), CallSite(statement, "native"))
    receiver = DictObject(context, AllocSite("mapping", AllocKind.DICT))
    method = BuiltinMethodObject(context, AllocSite("method", AllocKind.BUILTIN),
                                 "get", receiver, Variable("mapping"))
    return handler, state, scope, context, call, method


def test_implicit_none_allocation_is_stable_under_replays_and_distinct_sites():
    handler, state, scope, context, call, method = handler_fixture("method(key)")
    for _ in range(30):
        handler.handle_builtin_call(scope, context, call, method)
    objects = [obj for obj in state._heap.objects.values() if isinstance(obj, ConstantObject)]
    assert len(objects) == 1 and objects[0].value is None
    # Even identical display coordinates and text must retain statement identity.
    other_statement = IRCall(ast.parse("method(key)").body[0].value)
    other_call = CallConstraint(call.callee, call.args, call.kwargs, call.target,
                                CallSite(other_statement, "native"))
    handler.handle_builtin_call(scope, context, other_call, method)
    objects = [obj for obj in state._heap.objects.values() if isinstance(obj, ConstantObject)]
    assert len(objects) == 2


@pytest.mark.parametrize("expression", ["method(*args)", "method(key, **options)",
                                        "method(*args, **options)"])
def test_unknown_argument_expansions_are_obligations_not_invalid_arity_proofs(expression):
    handler, _, scope, context, call, method = handler_fixture(expression)
    constraints = handler.handle_builtin_call(scope, context, call, method)
    residuals = [c for c in constraints if isinstance(c, ResidualEffectConstraint)]
    assert residuals and "argument_expansion" in residuals[0].effects
    assert not any("requires one or two" in c.reason for c in residuals)
    assert any(not isinstance(c, ResidualEffectConstraint) for c in constraints)
