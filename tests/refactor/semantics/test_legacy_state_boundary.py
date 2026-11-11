"""Controlled local-only memory and public narrowing regression witnesses."""

from pathlib import Path

from pythonstan.analysis.pointer.ai.api import AISummaryEngine, AnalysisBudget, ArgBinding
from pythonstan.analysis.pointer.ai.state import AIState
from pythonstan.analysis.pointer.ai.value_domain import AbsVal
from pythonstan.analysis.pointer.kcfa.heap_model import key, elem
from tests.analysis.legacy_ai_support import analyze_program


def test_caller_local_overlay_reaches_nested_callee_without_pta_masking(tmp_path):
    source = """def reader(d):
    return d[0]
def direct(d):
    return d[0]
def outer(d):
    return reader(d)
d = ['before']
a = direct(d)
b = outer(d)
"""
    with analyze_program(tmp_path, source, ai_options={"strict_exceptions": True, "enable_narrowing": False}) as program:
        # 'after' occurs nowhere in source or Native facts. It exists solely
        # in this supported incoming AI-state argument.
        objects = frozenset(program.points("d"))
        binding = ArgBinding("d", objects, AbsVal.from_addrs(objects))
        budget = AnalysisBudget(strict_exceptions=True, enable_narrowing=False)
        incoming = AIState.from_arg_bindings([binding], program.adapter, budget)
        for obj in objects:
            incoming = incoming.write_field_value(obj, key("0"), AbsVal.from_str("after"))
            incoming = incoming.write_field_value(obj, elem(), AbsVal.from_str("after"))
        for name in ("subject.direct", "subject.outer"):
            scope = program.scope(name)
            context = program.query._state.get_scopes_for_ir(scope)[0]
            summary = AISummaryEngine(program.adapter, budget).analyze_callee(
                scope_ir=scope, call_context=context.context, arg_bindings=[binding],
                caller_scope=context, caller_state=incoming,
            )
            assert summary.is_complete, summary.incomplete_reasons
            assert "after" in summary.ret_value.get_const_strs()


def test_pure_scalar_callee_write_changes_the_caller_state(tmp_path):
    source = """def write(d, value):
    d[0] = value
def outer(d, value):
    write(d, value)
    return d[0]
d = ['before']
result = outer(d, 'before')
"""
    with analyze_program(tmp_path, source, ai_options={"strict_exceptions": True, "enable_narrowing": False}) as program:
        objects = frozenset(program.points("d"))
        bindings = [ArgBinding("d", objects, AbsVal.from_addrs(objects)),
                    ArgBinding("value", frozenset(), AbsVal.from_str("after"))]
        budget = AnalysisBudget(strict_exceptions=True, enable_narrowing=False)
        scope = program.scope("subject.outer")
        context = program.query._state.get_scopes_for_ir(scope)[0]
        summary = AISummaryEngine(program.adapter, budget).analyze_callee(
            scope_ir=scope, call_context=context.context, arg_bindings=bindings,
            caller_scope=context,
        )
        assert summary.is_complete, summary.incomplete_reasons
        assert "after" in summary.ret_value.get_const_strs()


def test_builtin_exception_keyword_and_args_retain_ai_only_caller_values(tmp_path):
    source = """def keyword(value):
    error = AttributeError('message', name=value)
    return error.name
def positional(value):
    return ValueError(value).args[0]
a = keyword('before')
b = positional('before')
"""
    with analyze_program(tmp_path, source, pointer_options={"enable_synthetic_method_contexts": False},
                         ai_options={"strict_exceptions": True, "enable_narrowing": False}) as program:
        for name in ("subject.keyword", "subject.positional"):
            scope = program.scope(name)
            context = program.query._state.get_scopes_for_ir(scope)[0]
            result = AISummaryEngine(program.adapter, AnalysisBudget(strict_exceptions=True, enable_narrowing=False)).analyze_callee(
                scope_ir=scope, call_context=context.context,
                arg_bindings=[ArgBinding("value", frozenset(), AbsVal.from_str("after"))], caller_scope=context)
            assert result.is_complete, result.incomplete_reasons
            assert "after" in result.ret_value.get_const_strs(), result
            assert "TypeError" not in result.may_raise


def test_with_captures_manager_and_runs_exit_before_suppressed_continuation(tmp_path):
    source = """class Manager:
    def __init__(self, box):
        self.box = box
    def __enter__(self):
        return self
    def __exit__(self, kind, value, traceback):
        self.box[0] = 'after'
        return True
def outer():
    box = ['before']
    manager = Manager(box)
    with manager:
        manager = None
        raise ValueError()
    return box[0]
result = outer()
"""
    with analyze_program(tmp_path, source,
                         pointer_options={"enable_synthetic_method_contexts": False},
                         ai_options={"plan_scopes": ["subject.outer"],
                         "strict_exceptions": True, "enable_narrowing": False}) as program:
        summary = program.ai.get_summary("subject.outer")
        assert summary.is_complete, summary.incomplete_reasons
        assert "after" in summary.ret_value.get_const_strs()
        targets = {callee.ir.get_qualname() for _, callees in summary.calls
                   for callee in callees if hasattr(callee, "ir")}
        assert "subject.Manager.__enter__" in targets
        assert "subject.Manager.__exit__" in targets
        assert not summary.may_raise
        assert "ValueError" in summary.prefix_exceptions


LOOP = """def outer():
    i = 0
    while i < 3:
        i = i + 1
    return i
result = outer()
"""


def loop_summary(path, narrowing):
    path.mkdir()
    with analyze_program(path, LOOP, ai_options={
        "plan_scopes": ["subject.outer"], "strict_exceptions": True,
        "enable_narrowing": narrowing, "narrowing_iterations": 8,
        "solver_options": {"widen_delay": 0, "loop_unroll": 0},
    }) as program:
        return program.ai.get_summary("subject.outer")


def test_checked_narrowing_rebuilds_the_public_summary(tmp_path):
    widened = loop_summary(tmp_path / "wide", False)
    narrowed = loop_summary(tmp_path / "narrow", True)
    assert widened.is_complete and narrowed.is_complete
    assert widened.ret_value.get_int_lattice().get_consts() is None
    assert widened.ret_value.get_int_lattice().lo == 3
    assert narrowed.ret_value.get_int_lattice().get_consts() == frozenset({3})
    assert narrowed.exit_state.get_var("i").get_int_lattice().get_consts() == frozenset({3})


def test_invalid_narrowed_entry_cannot_replace_the_established_result(tmp_path, monkeypatch):
    from pythonstan.analysis.pointer.ai._narrowing import NarrowingPass

    original = NarrowingPass.run

    def corrupt_entry(self, iterations):
        original(self, iterations)
        entry = self.cfg_adapter.get_entry_block_id()
        state = self.block_states[entry].copy()
        state.env["i"] = AbsVal.from_int(999)
        self.block_states[entry] = state

    monkeypatch.setattr(NarrowingPass, "run", corrupt_entry)
    result = loop_summary(tmp_path / "invalid", True)
    assert result.is_complete
    assert result.ret_value.get_int_lattice().lo == 3
    assert result.ret_value.get_int_lattice().hi is None
    assert result.ret_value.get_int_lattice().get_consts() is None


def test_nested_ai_calls_share_runtime_work_ledger_without_bypassing_strict_mode(tmp_path):
    source = "def inner(x):\n    return x\ndef outer():\n    return inner('after')\nresult=outer()\n"

    class Ledger:
        def __init__(self, remaining):
            self.remaining = remaining
            self.consumed = 0

        def consume(self, *, kind):
            assert kind == "abstract_transfer"
            if self.remaining == 0:
                return False
            self.remaining -= 1
            self.consumed += 1
            return True

    with analyze_program(tmp_path, source, ai_options={"strict_exceptions": True, "enable_narrowing": False}) as program:
        scope = program.scope("subject.outer")
        context = program.query._state.get_scopes_for_ir(scope)[0]
        budget = AnalysisBudget(strict_exceptions=True, enable_narrowing=False)
        for limit in (0, 2):
            ledger = Ledger(limit)
            result = AISummaryEngine(program.adapter, budget, work_ledger=ledger).analyze_callee(
                scope_ir=scope, call_context=context.context, arg_bindings=[], caller_scope=context,
            )
            assert not result.is_complete
            assert "shared_work_budget" in result.incomplete_reasons
            assert ledger.consumed == limit
        ledger = Ledger(100)
        result = AISummaryEngine(program.adapter, budget, work_ledger=ledger).analyze_callee(
            scope_ir=scope, call_context=context.context, arg_bindings=[], caller_scope=context,
        )
        assert result.is_complete, result.incomplete_reasons
        assert result.ret_value.get_const_strs() == frozenset({"after"})
        assert ledger.consumed > 2
