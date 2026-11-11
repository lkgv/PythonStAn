"""Tests for call semantics (defaults, __new__, __call__)."""

import ast

from pythonstan.analysis import AnalysisConfig
from pythonstan.analysis.pointer.kcfa.analysis import PointerAnalysis
from pythonstan.analysis.pointer.kcfa.config import Config
from pythonstan.analysis.pointer.kcfa.object import ConstantObject, ModuleObject
from pythonstan.analysis.transform.three_address import ThreeAddressTransformer
from pythonstan.analysis.transform.ir import IRTransformer
from pythonstan.ir import IRModule
from pythonstan.world import World


def _analyze(code: str):
    world = World()
    world.setup()
    world.scope_manager.build()

    tree = ast.parse(code)
    ir_module = IRModule(qualname="test", module=tree, name="test", filename="test.py")
    world.set_entry_module(ir_module)

    tac_transformer = ThreeAddressTransformer()
    tac_module = tac_transformer.visit(tree)
    world.scope_manager.set_ir(ir_module, "three address form", tac_module)

    ir_transformer = IRTransformer(ir_module)
    ir_transformer.process_stmts(tac_module.body)
    ir_stmts = ir_transformer.get_stmts()
    world.scope_manager.set_ir(ir_module, "ir", ir_stmts)

    kcfa_config = Config(
        context_policy="2-cfa",
        max_iterations=50000,
        verbose=False,
        log_level="WARNING",
        track_unknowns=True,
        build_class_hierarchy=True,
        use_mro_resolution=True,
        index_sensitive=True,
    )
    analysis_config = AnalysisConfig(
        name="test_pointer_analysis",
        id="PointerAnalysis",
        description="Test pointer analysis",
        prev_analysis=["ir"],
        options=kcfa_config.to_dict(),
    )

    analysis = PointerAnalysis(analysis_config)
    analysis.analyze(ir_module, {})
    return analysis, ir_module


def _module_scope(state, ir_module):
    for obj, scope in state._internal_scope.items():
        if isinstance(obj, ModuleObject) and obj.ir is ir_module:
            return scope
    raise AssertionError("Module scope not found")


def _points_to_var(state, scope, var_factory, name: str):
    from pythonstan.analysis.pointer.kcfa.variable import VariableKind
    var = var_factory.make_variable(name, VariableKind.GLOBAL)
    ctx_var = state.get_variable(scope, scope.context, var)
    return state.get_points_to(ctx_var)


def _pts_has_constant(pts, value):
    return any(isinstance(obj, ConstantObject) and obj.value == value for obj in pts)


def test_default_positional_argument():
    code = """
def f(x=1):
    return x

pos_default = f()
"""
    analysis, ir_module = _analyze(code)
    state = analysis.state
    scope = _module_scope(state, ir_module)
    pts = _points_to_var(state, scope, analysis.solver.variable_factory, "pos_default")
    assert _pts_has_constant(pts, 1)


def test_default_kwonly_argument():
    code = """
def f(*, x=2):
    return x

kw_default = f()
"""
    analysis, ir_module = _analyze(code)
    state = analysis.state
    scope = _module_scope(state, ir_module)
    pts = _points_to_var(state, scope, analysis.solver.variable_factory, "kw_default")
    assert _pts_has_constant(pts, 2)


def test_mixed_args_kwargs_defaults():
    code = """
def f(a, b=10, *, c=20, d=30):
    return b

def g(a, b=10, *, c=20, d=30):
    return d

mixed_pos = f(1, c=3)
mixed_kw = g(1, c=3)
"""
    analysis, ir_module = _analyze(code)
    state = analysis.state
    scope = _module_scope(state, ir_module)
    pts_pos = _points_to_var(state, scope, analysis.solver.variable_factory, "mixed_pos")
    pts_kw = _points_to_var(state, scope, analysis.solver.variable_factory, "mixed_kw")
    assert _pts_has_constant(pts_pos, 10)
    assert _pts_has_constant(pts_kw, 30)


def test_class_call_uses_new_result():
    code = """
sentinel = "token"

class C:
    def __new__(cls):
        return sentinel

new_result = C()
"""
    analysis, ir_module = _analyze(code)
    state = analysis.state
    scope = _module_scope(state, ir_module)
    pts_sentinel = _points_to_var(state, scope, analysis.solver.variable_factory, "sentinel")
    pts_result = _points_to_var(state, scope, analysis.solver.variable_factory, "new_result")
    assert not pts_sentinel.intersection(pts_result).is_empty()


def test_callable_fallback_non_instance():
    code = """
def target():
    return 7

obj = target
call_result = obj()
"""
    analysis, ir_module = _analyze(code)
    state = analysis.state
    scope = _module_scope(state, ir_module)
    pts = _points_to_var(state, scope, analysis.solver.variable_factory, "call_result")
    assert _pts_has_constant(pts, 7)
