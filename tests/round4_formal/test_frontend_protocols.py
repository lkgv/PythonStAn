"""Independent adversarial source tests for round-four protocol repairs."""
import ast
from dataclasses import replace

import pytest

from pythonstan.analysis.pointer.kcfa.config import Config
from pythonstan.world.pipeline import Pipeline


def _analyze(tmp_path, source, context_policy="2-cfa", return_query=False, **options):
    filename = tmp_path / "adversarial.py"
    filename.write_text(source)
    pipeline = Pipeline(config={
        "filename": str(filename), "project_path": str(tmp_path), "library_paths": [],
        "import_level": 0, "mock_libs": False, "time_count": False,
        "analysis": [{"name": "pta", "id": "PointerAnalysis", "prev_analysis": ["ir"],
                      "options": Config(context_policy=context_policy, max_iterations=10000,
                                        log_level="ERROR", enable_ai_intrinsics=False,
                                        **options).to_dict()}],
    })
    pipeline.run()
    query = pipeline.analysis_manager.get_results("pta").query()
    assert query.get_statistics()["converged"] == 1
    if return_query:
        return query
    return {(edge.callsite.scope.stmt.get_qualname(), edge.callee.stmt.get_qualname())
            for edge in query.call_graph().edges}


@pytest.mark.parametrize("context_policy", ["0-cfa", "2-cfa"])
def test_with_protocol_bypasses_instance_interceptor_and_binds_enter_value(tmp_path, context_policy):
    source = '''
def actual_callback():
    return "actual"
def spoof_callback():
    return "spoof"
class Token:
    def run(self):
        return actual_callback()
class Manager:
    def __getattribute__(self, name):
        return spoof_callback
    def __enter__(self):
        return Token()
    def __exit__(self, kind, value, traceback):
        return False
with Manager() as token:
    result = token.run()
'''
    namespace = {}
    exec(source, namespace)
    assert namespace["result"] == "actual"
    edges = _analyze(tmp_path, source, context_policy)
    assert {
        ("adversarial", "adversarial.Manager.__enter__"),
        ("adversarial", "adversarial.Manager.__exit__"),
        ("adversarial", "adversarial.Token.run"),
        ("adversarial.Token.run", "adversarial.actual_callback"),
    } <= edges
    assert not any(callee == "adversarial.spoof_callback" for _, callee in edges)


@pytest.mark.parametrize("context_policy", ["0-cfa", "2-cfa"])
def test_decorator_factory_keeps_container_and_keyword_closure_arguments(tmp_path, context_policy):
    source = '''
def first():
    return "first"
def second():
    return "second"
def factory(callbacks, chosen):
    def decorate(function):
        def wrapper():
            callbacks[0]()
            return chosen()
        return wrapper
    return decorate
@factory([first], chosen=second)
def entry():
    return "undecorated"
result = entry()
'''
    namespace = {}
    exec(source, namespace)
    assert namespace["result"] == "second"
    edges = _analyze(tmp_path, source, context_policy)
    assert {
        ("adversarial", "adversarial.factory.decorate.wrapper"),
        ("adversarial.factory.decorate.wrapper", "adversarial.first"),
        ("adversarial.factory.decorate.wrapper", "adversarial.second"),
    } <= edges
    assert ("adversarial", "adversarial.entry") not in edges


@pytest.mark.parametrize("context_policy", ["0-cfa", "2-cfa"])
def test_inherited_descriptor_uses_accessed_subclass_owner(tmp_path, context_policy):
    source = '''
def parent_callback():
    return "parent"
def child_callback():
    return "child"
class Proxy:
    def __get__(self, instance, owner):
        return owner.callback
class Parent:
    callback = parent_callback
    dynamic = Proxy()
class Child(Parent):
    callback = child_callback
result = Child.dynamic()
'''
    namespace = {}
    exec(source, namespace)
    assert namespace["result"] == "child"
    edges = _analyze(tmp_path, source, context_policy)
    assert ("adversarial", "adversarial.Proxy.__get__") in edges
    assert ("adversarial", "adversarial.child_callback") in edges


@pytest.mark.parametrize("declaration", ["def", "async def"])
def test_decorator_tac_preserves_early_name_capture_factories_defaults_and_application_order(declaration):
    from pythonstan.analysis.transform.three_address import ThreeAddressTransformer

    source = '''
trace = []
def original(function):
    trace.append("apply:original")
    return function
def replacement(function):
    trace.append("apply:replacement")
    return function
selected = original
def factory():
    global selected
    trace.append("eval:factory")
    selected = replacement
    def decorate(function):
        trace.append("apply:factory")
        return function
    return decorate
def default():
    trace.append("eval:default")
    return None
@selected
@factory()
DECLARATION entry(value=default()):
    return value
'''.replace("DECLARATION", declaration)
    ordinary = {}
    exec(source, ordinary)
    transformed = ThreeAddressTransformer().visit(ast.parse(source))
    lowered = {}
    exec(compile(transformed, "<lowered-decorator-order>", "exec"), lowered)
    expected = ["eval:factory", "eval:default", "apply:factory", "apply:original"]
    assert ordinary["trace"] == expected
    assert lowered["trace"] == expected


def test_disabling_unknown_diagnostics_cannot_hide_context_protocol_residual(tmp_path):
    source = '''
class Manager:
    def __enter__(self):
        return self
    def __exit__(self, kind, value, traceback):
        return True
with Manager():
    raise ValueError()
'''
    query = _analyze(tmp_path, source, return_query=True, track_unknowns=False)
    assert query.get_unknown_summary()["total_unknowns"] == 0
    residuals = query.get_residual_effects()
    assert any("exception_suppression" in residual["effects"] for residual in residuals)
    status = query.get_semantic_status()
    assert not status["unknown_diagnostics_enabled"]
    assert not status["certificate_eligible"]
    assert not status["whole_program_soundness_proved"]


@pytest.mark.parametrize("context_policy", ["0-cfa", "2-cfa"])
def test_coalesced_callsite_keeps_each_argument_and_return_target(tmp_path, monkeypatch, context_policy):
    from pythonstan.analysis.pointer.kcfa.constraints import CallConstraint
    from pythonstan.analysis.pointer.kcfa.ir_translator import IRTranslator

    original = IRTranslator._translate_call
    shared = []

    def coalesce(self, statement):
        constraints = original(self, statement)
        result = []
        for constraint in constraints:
            if isinstance(constraint, CallConstraint) and constraint.callee.name == "relay":
                if not shared:
                    shared.append(constraint.call_site)
                # Implicit protocol expansions can share a source callsite but
                # have distinct actuals/results. Coalescing the two ordinary
                # calls simulates that obligation without coupling the test to
                # descriptor-specific synthetic variable names.
                constraint = replace(constraint, call_site=shared[0])
            result.append(constraint)
        return result

    monkeypatch.setattr(IRTranslator, "_translate_call", coalesce)
    source = '''
def first():
    return 1
def second():
    return 2
def relay(callback):
    return callback
left = relay(first)
right = relay(second)
def consume_left():
    return left()
def consume_right():
    return right()
consume_left()
consume_right()
'''
    edges = _analyze(tmp_path, source, context_policy)
    assert shared
    assert ("adversarial.consume_left", "adversarial.first") in edges
    assert ("adversarial.consume_right", "adversarial.second") in edges


def test_nested_translation_failure_remains_residual_when_diagnostics_disabled(tmp_path, monkeypatch):
    from pythonstan.analysis.pointer.kcfa.ir_translator import IRTranslator

    original = IRTranslator.translate_function

    def failing(self, function):
        if function.name == "untranslated":
            raise RuntimeError("injected translator failure")
        return original(self, function)

    monkeypatch.setattr(IRTranslator, "translate_function", failing)
    query = _analyze(tmp_path, "def untranslated():\n    pass\nuntranslated()\n",
                     return_query=True, track_unknowns=False)
    assert query.get_unknown_summary()["total_unknowns"] == 0
    assert any("translat" in residual["reason"].lower()
               for residual in query.get_residual_effects())
    assert not query.get_semantic_status()["certificate_eligible"]
