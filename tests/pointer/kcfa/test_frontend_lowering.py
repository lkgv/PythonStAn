"""Source-to-IR coverage and residual obligations, independent of framework names."""
import ast

import pytest

from pythonstan.analysis.transform.ir import IRTransformer
from pythonstan.analysis.transform.three_address import ThreeAddressTransformer
from pythonstan.ir.ir_statements import IRContextEnter, IRContextExit, IRModule, JumpIfFalse
from tests.pointer.kcfa.test_ai_bridge import analyze_source


@pytest.mark.parametrize("context_policy", ["0-cfa", "2-cfa"])
def test_nested_with_preserves_cleanup_and_return_callbacks(tmp_path, context_policy):
    query, edges = analyze_source(tmp_path, '''
def entered():
    return "entered"
def exited():
    return "exited"
def body():
    return "body"
class Outer:
    def __enter__(self):
        entered()
        return self
    def __exit__(self, exc_type, exc, tb):
        exited()
        return False
class Inner(Outer):
    pass
def invoke():
    with Outer() as first, Inner() as second:
        return body()
result = invoke()
''', context_policy=context_policy, enable_ai_intrinsics=False)
    assert {
        ("subject.invoke", "subject.Outer.__enter__"),
        ("subject.invoke", "subject.Outer.__exit__"),
        ("subject.invoke", "subject.body"),
        ("subject.Outer.__enter__", "subject.entered"),
        ("subject.Outer.__exit__", "subject.exited"),
    } <= edges
    assert any("cleanup_order" in residual["effects"] for residual in query.get_residual_effects())
    assert query.get_statistics()["converged"] == 1


@pytest.mark.parametrize("track_unknowns", [False, True])
def test_exception_suppression_is_explicit_residual(tmp_path, track_unknowns):
    query, edges = analyze_source(tmp_path, '''
def cleanup():
    return "cleaned"
def after():
    return "after"
class Manager:
    def __enter__(self):
        return self
    def __exit__(self, exc_type, exc, tb):
        cleanup()
        return True
with Manager():
    raise ValueError()
after()
''', track_unknowns=track_unknowns, enable_ai_intrinsics=False)
    assert ("subject.Manager.__exit__", "subject.cleanup") in edges
    assert ("subject", "subject.after") in edges
    assert any("exception_suppression" in r["effects"] for r in query.get_residual_effects())


@pytest.mark.parametrize("context_policy", ["0-cfa", "2-cfa"])
def test_async_with_awaits_enter_value_for_as_binding(tmp_path, context_policy):
    source = '''
events = []
def body_callback():
    events.append("body")
    return "body"
def exit_callback():
    events.append("exit")
    return "exit"
class Token:
    def run(self):
        return body_callback()
class AsyncManager:
    async def __aenter__(self):
        return Token()
    async def __aexit__(self, kind, value, traceback):
        return exit_callback()
async def invoke():
    async with AsyncManager() as token:
        return token.run()
pending = invoke()
'''
    namespace = {}
    exec(source, namespace)
    assert namespace["events"] == []
    try:
        namespace["pending"].send(None)
    except StopIteration:
        pass
    assert namespace["events"] == ["body", "exit"]
    # Closed-source negative has no synthetic method-body entry assumptions.
    _, dormant = analyze_source(tmp_path, source, context_policy=context_policy,
        enable_ai_intrinsics=False, enable_synthetic_method_contexts=False)
    assert ("subject.invoke", "subject.AsyncManager.__aenter__") not in dormant
    source += "try:\n    pending.send(None)\nexcept StopIteration:\n    pass\n"
    query, edges = analyze_source(tmp_path, source,
        context_policy=context_policy, enable_ai_intrinsics=False)
    assert {
        ("subject.invoke", "subject.AsyncManager.__aenter__"),
        ("subject.invoke", "subject.AsyncManager.__aexit__"),
        ("subject.invoke", "subject.Token.run"),
        ("subject.Token.run", "subject.body_callback"),
        ("subject.AsyncManager.__aexit__", "subject.exit_callback"),
    } <= edges
    assert any("cancellation" in r["effects"] for r in query.get_residual_effects())


def test_unknown_manager_does_not_erase_body(tmp_path):
    query, edges = analyze_source(tmp_path, '''
def body_callback():
    return 1
manager = missing_factory()
with manager:
    body_callback()
''', enable_ai_intrinsics=False)
    assert ("subject", "subject.body_callback") in edges
    assert query.get_residual_effects()


def test_ir_retains_if_guard_and_nested_with_boundaries():
    source = "if flag:\n    with first, second:\n        callback()\nelse:\n    fallback()\n"
    tac = ThreeAddressTransformer().visit(ast.parse(source))
    module = IRModule("protocol", "protocol.py", ast.parse(source))
    transformer = IRTransformer(module)
    transformer.process_stmts(tac.body)
    statements = transformer.get_stmts()
    assert isinstance(statements[0], JumpIfFalse)
    entries = [s for s in statements if isinstance(s, IRContextEnter)]
    exits = [s for s in statements if isinstance(s, IRContextExit)]
    assert len(entries) == len(exits) == 2
    assert [exit.entry for exit in exits] == list(reversed(entries))
    assert statements[0].label in statements


def test_raise_operand_and_cause_calls_are_not_erased(tmp_path):
    query, edges = analyze_source(tmp_path, """
def make_error():
    return ValueError()
def make_cause():
    return TypeError()
raise make_error() from make_cause()
""", enable_ai_intrinsics=False)
    assert ("subject", "subject.make_error") in edges
    assert ("subject", "subject.make_cause") in edges
    assert any("exception_routing" in residual["effects"] for residual in query.get_residual_effects())


def test_boolean_or_guard_reaches_cfg_as_name(tmp_path):
    query, edges = analyze_source(tmp_path, """
def left():
    return True
def right():
    return False
def branch():
    return 1
if left() or right():
    branch()
""", enable_ai_intrinsics=False)
    assert ("subject", "subject.left") in edges
    assert ("subject", "subject.right") in edges
    assert ("subject", "subject.branch") in edges
    assert query.get_statistics()["converged"] == 1


@pytest.mark.parametrize("context_policy", ["0-cfa", "2-cfa"])
@pytest.mark.parametrize("function_kind", ["async", "generator"])
@pytest.mark.parametrize("capture_origin", ["global", "enclosing"])
def test_bound_suspended_method_installs_branch_capture_before_constraints(
    tmp_path, context_policy, function_kind, capture_origin
):
    if capture_origin == "global":
        prefix = "events = []\ndef callback():\n    events.append('called')\n    return 7\n"
        definition = "class Worker:\n"
        suffix = "worker = Worker()\n"
        indent = "    "
        callback_name = "callback"
        expected = "subject.callback"
    else:
        prefix = "events = []\ndef factory(callback):\n"
        definition = "    class Worker:\n"
        suffix = "    return Worker()\ndef target():\n    events.append('called')\n    return 7\nworker = factory(target)\n"
        indent = "        "
        callback_name = "callback"
        expected = "subject.target"
    declaration = "async def" if function_kind == "async" else "def"
    effect = "return" if function_kind == "async" else "yield"
    source = prefix + definition + (
        f"{indent}{declaration} run(self, choose):\n"
        f"{indent}    if choose:\n"
        f"{indent}        {effect} 0\n"
        f"{indent}    else:\n"
        f"{indent}        {effect} {callback_name}()\n"
    ) + suffix + "pending = worker.run(False)\n"
    namespace = {}
    exec(source, namespace)
    assert namespace["events"] == []
    try:
        namespace["pending"].send(None) if function_kind == "async" else next(namespace["pending"])
    except StopIteration:
        pass
    assert namespace["events"] == ["called"]
    method = "subject.Worker.run" if capture_origin == "global" else "subject.factory.Worker.run"
    _, dormant = analyze_source(tmp_path, source, context_policy=context_policy,
        enable_ai_intrinsics=False, enable_synthetic_method_contexts=False)
    assert (method, expected) not in dormant
    if function_kind == "generator":
        source += "result = next(pending)\n"
    else:
        source += "try:\n    pending.send(None)\nexcept StopIteration:\n    pass\n"
    query, edges = analyze_source(tmp_path, source, context_policy=context_policy,
                                  enable_ai_intrinsics=False)
    assert (method, expected) in edges
    assert query.get_statistics()["converged"] == 1


@pytest.mark.parametrize("function_kind", ["async", "generator"])
def test_suspended_body_translation_failure_remains_residual_without_diagnostics(
    tmp_path, monkeypatch, function_kind
):
    from pythonstan.analysis.pointer.kcfa.ir_translator import IRTranslator
    original = IRTranslator.translate_function

    def reject_body(self, function):
        if function.name == "broken":
            raise ValueError("injected translator failure")
        return original(self, function)

    monkeypatch.setattr(IRTranslator, "translate_function", reject_body)
    source = ("events = []\nasync def broken():\n    events.append('entered')\n    return 1\n" if function_kind == "async"
              else "events = []\ndef broken():\n    events.append('entered')\n    yield 1\n")
    source += "pending = broken()\n"
    namespace = {}
    exec(source, namespace)
    assert namespace["events"] == []
    try:
        namespace["pending"].send(None) if function_kind == "async" else next(namespace["pending"])
    except StopIteration:
        pass
    assert namespace["events"] == ["entered"]
    dormant, _ = analyze_source(tmp_path, source, track_unknowns=False, enable_ai_intrinsics=False)
    assert not any("body translation failed" in r["reason"] for r in dormant.get_residual_effects())
    action = "pending.send(None)" if function_kind == "async" else "next(pending)"
    source += f"try:\n    {action}\nexcept StopIteration:\n    pass\n"
    query, _ = analyze_source(tmp_path, source, track_unknowns=False, enable_ai_intrinsics=False)
    assert query.get_unknown_summary()["total_unknowns"] == 0
    assert any("body translation failed" in r["reason"] for r in query.get_residual_effects())
