import ast
from dataclasses import dataclass
from typing import Dict, FrozenSet, Optional

import pytest

from pythonstan.world import World
from pythonstan.ir.ir_statements import IRFunc, IRYield, IRReturn, IRLoadAttr, IRCall
from pythonstan.analysis.pointer.ai.api import AnalysisBudget
from pythonstan.analysis.pointer.ai.interpreter import IRInterpreter
from pythonstan.analysis.pointer.ai.state import AIState, AbsVal
from pythonstan.analysis.pointer.ai.summary import SummaryAccumulator
from pythonstan.analysis.pointer.kcfa.context import CallStringContext
from pythonstan.analysis.pointer.kcfa.heap_model import FieldKind, attr
from pythonstan.analysis.pointer.kcfa.object import AllocKind, AllocSite, AbstractObject, MethodObject


@dataclass
class _DummyPtaQuery:
    field_map: Dict[tuple, FrozenSet[AbstractObject]]

    def points_to(self, var, scope, context):
        return frozenset()

    def field_points_to(self, obj, field, scope, context):
        return self.field_map.get((obj, field), frozenset())

    def get_class_mro(self, cls_obj):
        return [cls_obj]

    def may_have_attr(self, obj, attr_name, scope, context):
        return True

    def select_call_context(self, callsite_id, argc, caller_ctx, receiver_obj, params=None):
        return None


def _make_ir_func(name: str, params):
    fn = ast.FunctionDef(
        name=name,
        args=ast.arguments(
            posonlyargs=[],
            args=[ast.arg(arg=param, annotation=None) for param in params],
            kwonlyargs=[],
            kw_defaults=[],
            defaults=[],
            vararg=None,
            kwarg=None,
        ),
        body=[ast.Pass()],
        decorator_list=[],
    )
    return IRFunc(qualname=name, fn=fn)


def _make_function_obj(scope_ir: IRFunc, context: Optional[CallStringContext] = None) -> AbstractObject:
    ctx = context or CallStringContext()
    alloc_site = AllocSite(stmt=scope_ir, kind=AllocKind.FUNCTION)
    return AbstractObject(context=ctx, alloc_site=alloc_site)


def _make_method_obj(
    scope_ir: IRFunc,
    class_obj: AbstractObject,
    instance_obj: AbstractObject,
    context: Optional[CallStringContext] = None,
) -> MethodObject:
    ctx = context or CallStringContext()
    alloc_site = AllocSite(stmt=scope_ir, kind=AllocKind.METHOD)
    return MethodObject(
        context=ctx,
        alloc_site=alloc_site,
        container_scope=None,
        ir=scope_ir,
        class_obj=class_obj,
        instance_obj=instance_obj,
    )


@pytest.fixture(autouse=True)
def _reset_world():
    World.setup()
    World().scope_manager.build()


def _register_ir(scope_ir: IRFunc, stmts):
    World().scope_manager.set_ir(scope_ir, "ir", stmts)


def _run_statements(interpreter, state, summary, stmts):
    for idx, stmt in enumerate(stmts):
        interpreter.set_current_location(0, idx)
        result = interpreter.transfer(stmt, state, summary)
        assert result.states
        state = result.states[0][1]
    return state


def test_generator_next_yield_flow_adds_call_edge():
    ctx = CallStringContext()
    budget = AnalysisBudget()

    gen_ir = _make_ir_func("gen", ["f"])
    yield_stmt = IRYield(
        ast.Expr(
            value=ast.Yield(value=ast.Name(id="f", ctx=ast.Load()))
        )
    )
    _register_ir(gen_ir, [yield_stmt])

    f_ir = _make_ir_func("f", [])
    gen_obj = _make_function_obj(gen_ir, ctx)
    f_obj = _make_function_obj(f_ir, ctx)

    pta = _DummyPtaQuery(field_map={})
    interpreter = IRInterpreter(pta, budget, scope_ir=_make_ir_func("main", []), call_context=ctx)
    summary = SummaryAccumulator()

    state = AIState()
    state = state.set_var("gen", AbsVal.from_addrs(frozenset({gen_obj})))
    state = state.set_var("f", AbsVal.from_addrs(frozenset({f_obj})))

    call_gen = IRCall(
        ast.Assign(
            targets=[ast.Name(id="g", ctx=ast.Store())],
            value=ast.Call(
                func=ast.Name(id="gen", ctx=ast.Load()),
                args=[ast.Name(id="f", ctx=ast.Load())],
                keywords=[],
            ),
        )
    )
    call_next = IRCall(
        ast.Assign(
            targets=[ast.Name(id="x", ctx=ast.Store())],
            value=ast.Call(
                func=ast.Name(id="next", ctx=ast.Load()),
                args=[ast.Name(id="g", ctx=ast.Load())],
                keywords=[],
            ),
        )
    )
    call_x = IRCall(
        ast.Call(
            func=ast.Name(id="x", ctx=ast.Load()),
            args=[],
            keywords=[],
        )
    )

    state = _run_statements(interpreter, state, summary, [call_gen, call_next, call_x])

    assert f_obj in state.get_var("x").addrs
    calls = summary.to_summary().calls
    assert any(f_obj in callee_addrs for _, callee_addrs in calls)


def test_generator_send_value_flows_into_yield_target():
    ctx = CallStringContext()
    budget = AnalysisBudget()

    gen_ir = _make_ir_func("gen", ["f"])
    yield_stmt = IRYield(
        ast.Assign(
            targets=[ast.Name(id="x", ctx=ast.Store())],
            value=ast.Yield(value=ast.Name(id="f", ctx=ast.Load())),
        )
    )
    call_x = IRCall(
        ast.Call(
            func=ast.Name(id="x", ctx=ast.Load()),
            args=[],
            keywords=[],
        )
    )
    _register_ir(gen_ir, [yield_stmt, call_x])

    f_ir = _make_ir_func("f", [])
    h_ir = _make_ir_func("h", [])
    gen_obj = _make_function_obj(gen_ir, ctx)
    f_obj = _make_function_obj(f_ir, ctx)
    h_obj = _make_function_obj(h_ir, ctx)

    pta = _DummyPtaQuery(field_map={})
    interpreter = IRInterpreter(pta, budget, scope_ir=_make_ir_func("main", []), call_context=ctx)
    summary = SummaryAccumulator()

    state = AIState()
    state = state.set_var("gen", AbsVal.from_addrs(frozenset({gen_obj})))
    state = state.set_var("f", AbsVal.from_addrs(frozenset({f_obj})))
    state = state.set_var("h", AbsVal.from_addrs(frozenset({h_obj})))

    call_gen = IRCall(
        ast.Assign(
            targets=[ast.Name(id="g", ctx=ast.Store())],
            value=ast.Call(
                func=ast.Name(id="gen", ctx=ast.Load()),
                args=[ast.Name(id="f", ctx=ast.Load())],
                keywords=[],
            ),
        )
    )
    load_send = IRLoadAttr(
        ast.Assign(
            targets=[ast.Name(id="send_meth", ctx=ast.Store())],
            value=ast.Attribute(
                value=ast.Name(id="g", ctx=ast.Load()),
                attr="send",
                ctx=ast.Load(),
            ),
        )
    )
    call_send = IRCall(
        ast.Assign(
            targets=[ast.Name(id="m", ctx=ast.Store())],
            value=ast.Call(
                func=ast.Name(id="send_meth", ctx=ast.Load()),
                args=[ast.Name(id="h", ctx=ast.Load())],
                keywords=[],
            ),
        )
    )

    state = _run_statements(interpreter, state, summary, [call_gen, load_send, call_send])

    calls = summary.to_summary().calls
    assert any(h_obj in callee_addrs for _, callee_addrs in calls)


def test_next_default_joins_with_yielded_value():
    ctx = CallStringContext()
    budget = AnalysisBudget()

    gen_ir = _make_ir_func("gen", ["f"])
    yield_stmt = IRYield(
        ast.Expr(
            value=ast.Yield(value=ast.Name(id="f", ctx=ast.Load()))
        )
    )
    _register_ir(gen_ir, [yield_stmt])

    f_ir = _make_ir_func("f", [])
    d_ir = _make_ir_func("default_fn", [])
    gen_obj = _make_function_obj(gen_ir, ctx)
    f_obj = _make_function_obj(f_ir, ctx)
    d_obj = _make_function_obj(d_ir, ctx)

    pta = _DummyPtaQuery(field_map={})
    interpreter = IRInterpreter(pta, budget, scope_ir=_make_ir_func("main", []), call_context=ctx)
    summary = SummaryAccumulator()

    state = AIState()
    state = state.set_var("gen", AbsVal.from_addrs(frozenset({gen_obj})))
    state = state.set_var("f", AbsVal.from_addrs(frozenset({f_obj})))
    state = state.set_var("d", AbsVal.from_addrs(frozenset({d_obj})))

    call_gen = IRCall(
        ast.Assign(
            targets=[ast.Name(id="g", ctx=ast.Store())],
            value=ast.Call(
                func=ast.Name(id="gen", ctx=ast.Load()),
                args=[ast.Name(id="f", ctx=ast.Load())],
                keywords=[],
            ),
        )
    )
    call_next = IRCall(
        ast.Assign(
            targets=[ast.Name(id="x", ctx=ast.Store())],
            value=ast.Call(
                func=ast.Name(id="next", ctx=ast.Load()),
                args=[ast.Name(id="g", ctx=ast.Load()), ast.Name(id="d", ctx=ast.Load())],
                keywords=[],
            ),
        )
    )

    state = _run_statements(interpreter, state, summary, [call_gen, call_next])

    x_val = state.get_var("x")
    assert f_obj in x_val.addrs
    assert d_obj in x_val.addrs


@pytest.mark.parametrize("enable_ai_intrinsics", [False, True])
def test_iterator_protocol_iter_next_precise(tmp_path, enable_ai_intrinsics):
    """Real source-to-solver regression for the previously skipped hook chain."""
    from pythonstan.analysis.pointer.kcfa.object import ConstantObject
    from pythonstan.ir import IRModule
    from tests.pointer.kcfa.test_ai_bridge import analyze_source
    query, edges = analyze_source(tmp_path, '''
def chosen(): return 7
def unused(): return 99
class Iterator:
    def __iter__(self): return self
    def __next__(self): return chosen
callback = next(iter(Iterator()))
result = callback()
''', enable_ai_intrinsics=enable_ai_intrinsics)
    assert {("subject", "subject.Iterator.__iter__"),
            ("subject", "subject.Iterator.__next__"),
            ("subject", "subject.chosen")} <= edges
    assert not any(callee == "subject.unused" for _, callee in edges)
    module = next(scope for scope in query.get_reachable_scopes() if isinstance(scope, IRModule))
    values = query.points_to_at("result", module)
    assert any(isinstance(value, ConstantObject) and value.value == 7 for value in values)
    assert query.get_statistics()["converged"] == 1
    assert any("iterator_exhaustion" in residual["effects"] for residual in query.get_residual_effects())
