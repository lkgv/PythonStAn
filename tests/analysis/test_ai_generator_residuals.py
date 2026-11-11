"""Generator continuation gaps must remain visible in outward AI summaries."""

import ast

import pytest

from pythonstan.analysis.pointer.ai.api import AISummary, AnalysisBudget
from pythonstan.analysis.pointer.ai.domains.iterators import GeneratorSemantics, GEN_CLOSED_KEY
from pythonstan.analysis.pointer.ai.state import AIState, AbsVal
from pythonstan.analysis.pointer.ai.summary import SummaryAccumulator
from pythonstan.analysis.pointer.kcfa.context import CallStringContext
from pythonstan.analysis.pointer.kcfa.object import AbstractObject, AllocKind, AllocSite
from pythonstan.ir.ir_statements import IRFunc


def function(name):
    return AbstractObject(CallStringContext(), AllocSite(
        IRFunc(name, ast.parse(f"def {name}():\n    pass\n").body[0]), AllocKind.FUNCTION))


def allocated_generator():
    sem = GeneratorSemantics(None, AnalysisBudget(), None, CallStringContext())
    func = function("generator_body")
    state, value, addr, _ = sem.alloc_generator_obj("site", CallStringContext(), AbsVal.from_addrs(frozenset({func})), [], AIState())
    return sem, state, value, addr


@pytest.mark.parametrize("operation", ["next", "send", "throw", "close"])
def test_protocol_without_continuation_cannot_produce_complete_summary(operation, monkeypatch):
    sem, state, value, _ = allocated_generator()
    monkeypatch.setattr(sem, "_analyze_generator_body", lambda *args: (AbsVal.from_str("yielded"), AISummary()))
    accumulator = SummaryAccumulator()
    if operation == "send":
        sem.eval_send(value, AbsVal.none_val(), state, accumulator)
    else:
        getattr(sem, f"eval_{operation}")(value, state, accumulator)
    summary = accumulator.to_summary()
    assert not summary.is_complete
    assert summary.incomplete_reasons
    assert {effect.kind for effect in summary.unknown_effects} >= {"may_call", "may_write"}


def test_caught_throw_does_not_claim_generator_closed():
    def concrete():
        try:
            yield "start"
        except ValueError:
            yield "recovered"
        yield "later"
    concrete_gen = concrete()
    assert next(concrete_gen) == "start"
    assert concrete_gen.throw(ValueError) == "recovered"
    assert next(concrete_gen) == "later"
    concrete_gen.close()

    sem, state, value, addr = allocated_generator()
    accumulator = SummaryAccumulator()
    after, result, updates = sem.eval_throw(value, state, accumulator)
    assert not after.get_obj(addr).slots.get_str(GEN_CLOSED_KEY).val.get_bool_lattice().is_true()
    assert result.addr_top
    assert updates == ()
    assert not accumulator.to_summary().is_complete


def test_close_can_fail_and_leave_generator_suspended():
    def concrete():
        try:
            yield "start"
        except GeneratorExit:
            yield "still open"
    concrete_gen = concrete()
    next(concrete_gen)
    with pytest.raises(RuntimeError):
        concrete_gen.close()
    assert concrete_gen.gi_frame is not None
    concrete_gen.close()

    sem, state, value, addr = allocated_generator()
    accumulator = SummaryAccumulator()
    after, _, updates = sem.eval_close(value, state, accumulator)
    assert not after.get_obj(addr).slots.get_str(GEN_CLOSED_KEY).val.get_bool_lattice().is_true()
    assert updates == ()
    summary = accumulator.to_summary()
    assert summary.may_raise
    assert not summary.is_complete


@pytest.mark.parametrize("reasons", [("budget_exhausted",), ()])
def test_nested_incomplete_without_return_stays_incomplete(reasons):
    accumulator = SummaryAccumulator()
    GeneratorSemantics._merge_summary(accumulator, AISummary(is_complete=False, incomplete_reasons=reasons))
    summary = accumulator.to_summary()
    assert not summary.is_complete
    assert summary.incomplete_reasons == (reasons or ("nested_generator_summary_incomplete",))


def test_cached_yield_value_does_not_skip_effect_observations(monkeypatch):
    sem, state, value, _ = allocated_generator()
    monkeypatch.setattr(sem, "_read_cached_yield", lambda obj: AbsVal.from_str("previous yield"))
    callback = function("effect")
    calls = []
    def observe_body(*args):
        calls.append(True)
        return AbsVal.from_str("current yield"), AISummary(
            calls=(("effect-site", frozenset({callback})),),
            is_complete=False, incomplete_reasons=("body_partial",))
    monkeypatch.setattr(sem, "_analyze_generator_body", observe_body)
    accumulator = SummaryAccumulator()
    _, result, _ = sem.eval_next(value, state, accumulator)
    assert calls == [True]
    assert result.str_const == frozenset({"previous yield", "current yield"})
    summary = accumulator.to_summary()
    assert any(callback in targets for _, targets in summary.calls)
    assert "body_partial" in summary.incomplete_reasons


def test_missing_generator_body_does_not_look_complete():
    sem, state, value, addr = allocated_generator()
    func = AbstractObject(CallStringContext(), AllocSite("unavailable", AllocKind.OBJECT))
    _, summary = sem._analyze_generator_body(func, next(iter(value.addrs)), state.get_obj(addr), AbsVal.none_val(), state)
    assert not summary.is_complete
    assert summary.incomplete_reasons == ("missing_generator_body",)


def test_context_entry_marks_incomplete_before_a_body_return():
    from pythonstan.analysis.transform.ir import IRTransformer
    from pythonstan.analysis.transform.three_address import ThreeAddressTransformer
    from pythonstan.analysis.pointer.ai.interpreter import IRInterpreter
    from pythonstan.ir.ir_statements import IRContextEnter, IRModule

    tree = ast.parse("with manager:\n    return value\n")
    tac = ThreeAddressTransformer().visit(tree)
    transformer = IRTransformer(IRModule("with_return", "with_return.py", tree))
    transformer.process_stmts(tac.body)
    entry = next(stmt for stmt in transformer.get_stmts() if isinstance(stmt, IRContextEnter))
    accumulator = SummaryAccumulator()
    IRInterpreter(None, AnalysisBudget()).transfer(entry, AIState(), accumulator)
    assert not accumulator.to_summary().is_complete
