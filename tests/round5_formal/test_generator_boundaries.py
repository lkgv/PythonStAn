"""Independent continuation witnesses; complete means only the declared model."""
from dataclasses import replace

import pytest

from pythonstan.analysis.pointer.ai.generator_protocol import (
    InvalidContinuation, InvalidGeneratorInput, ProtocolBudget, ProtocolException,
    UnsupportedGeneratorSource, compile_generator_protocol,
)


def step(model, handle, operation="next", **kwargs):
    result = model.resume(handle, operation, **kwargs)
    assert result.status == "complete"
    assert len(result.outcomes) == 1
    assert result.source_sha256 == model.source_sha256
    assert not result.to_dict()["general_python_soundness_proved"]
    return result.outcomes[0]


SOURCE = '''def entered():
    return 10
def cleaned():
    return 20
def subject():
    entered()
    try:
        yield 1
    finally:
        cleaned()
'''


def test_failed_initial_send_preserves_created_state_then_starts_normally():
    model = compile_generator_protocol(SOURCE, "subject")
    handle = model.initial()
    rejected = step(model, handle, "send", value=7)
    assert rejected.kind == "raise" and rejected.exception.kind == "TypeError"
    assert rejected.continuation.status == "created" and not rejected.events
    first = step(model, rejected.continuation)
    assert (first.kind, first.value) == ("yield", 1)
    assert [event.name for event in first.events if event.kind == "call"] == ["entered"]


@pytest.mark.parametrize("operation", ["close", "throw"])
def test_prestart_injection_does_not_enter_body_or_finally(operation):
    model = compile_generator_protocol(SOURCE, "subject")
    kwargs = {"exception": ProtocolException("ValueError", ("stop",))} if operation == "throw" else {}
    outcome = step(model, model.initial(), operation, **kwargs)
    assert outcome.continuation.status == "closed"
    assert not outcome.events
    assert outcome.kind == ("raise" if operation == "throw" else "close")
    if operation == "throw":
        assert outcome.exception.kind == "ValueError"


def test_pending_return_survives_finally_suspension():
    source = '''def subject():
    try:
        yield 1
        return 7
    finally:
        yield 2
'''
    model = compile_generator_protocol(source, "subject")
    first = step(model, model.initial())
    cleanup = step(model, first.continuation)
    assert (cleanup.kind, cleanup.value) == ("yield", 2)
    done = step(model, cleanup.continuation, "send", value=99)
    assert (done.kind, done.value, done.continuation.status) == ("return", 7, "closed")


def test_pending_exception_survives_ignored_close_and_finally_yield():
    source = '''def subject():
    try:
        yield 1
    finally:
        yield 2
'''
    model = compile_generator_protocol(source, "subject")
    first = step(model, model.initial())
    ignored = step(model, first.continuation, "close")
    assert ignored.kind == "raise" and ignored.exception.kind == "RuntimeError"
    assert ignored.continuation.status == "suspended"
    final = step(model, ignored.continuation)
    assert final.kind == "raise" and final.exception.kind == "GeneratorExit"
    assert final.continuation.status == "closed"


def test_return_in_finally_overrides_pending_exception_after_suspension():
    source = '''def subject():
    try:
        yield 1
        raise ValueError("pending")
    finally:
        yield 2
        return 9
'''
    model = compile_generator_protocol(source, "subject")
    first = step(model, model.initial())
    cleanup = step(model, first.continuation)
    assert (cleanup.kind, cleanup.value) == ("yield", 2)
    done = step(model, cleanup.continuation)
    assert (done.kind, done.value) == ("return", 9)


def test_throw_at_suspension_enters_handler_and_runs_finally_later():
    source = '''def cleanup():
    return 0
def subject():
    try:
        yield 1
    except ValueError:
        yield 2
    finally:
        cleanup()
'''
    model = compile_generator_protocol(source, "subject")
    first = step(model, model.initial())
    handled = step(model, first.continuation, "throw", exception=ProtocolException("ValueError"))
    assert (handled.kind, handled.value) == ("yield", 2)
    assert not any(event.name == "cleanup" for event in handled.events)
    final = step(model, handled.continuation)
    assert final.kind == "return"
    assert [event.name for event in final.events if event.kind == "call"] == ["cleanup"]


def test_escaping_stopiteration_is_transformed_but_closed_throw_is_not():
    source = '''def subject():
    yield 1
    raise StopIteration("body")
'''
    model = compile_generator_protocol(source, "subject")
    first = step(model, model.initial())
    escaped = step(model, first.continuation)
    assert escaped.kind == "raise" and escaped.exception.kind == "RuntimeError"
    injected = step(model, escaped.continuation, "throw", exception=ProtocolException("StopIteration", ("closed",)))
    assert injected.kind == "raise" and injected.exception.kind == "StopIteration"


def test_newborn_throw_stopiteration_does_not_enter_the_generator_frame():
    model = compile_generator_protocol(SOURCE, "subject")
    injected = step(model, model.initial(), "throw", exception=ProtocolException("StopIteration", ("newborn",)))
    assert injected.kind == "raise" and injected.exception.kind == "StopIteration"
    assert injected.continuation.status == "closed" and not injected.events


def test_ambient_exception_is_current_resume_input_and_does_not_leak_to_next_resume():
    source = "def subject():\n    yield 1\n    raise\n"
    model = compile_generator_protocol(source, "subject")
    first = step(model, model.initial())
    reraised = step(model, first.continuation, ambient_exception=ProtocolException("ValueError", ("caller",)))
    assert reraised.kind == "raise" and reraised.exception.kind == "ValueError"
    another = step(model, model.initial(), ambient_exception=ProtocolException("ValueError", ("old caller",)))
    no_active_handler = step(model, another.continuation)
    assert no_active_handler.kind == "raise" and no_active_handler.exception.kind == "RuntimeError"


def test_internal_suspended_handler_exception_precedes_ambient_caller_exception():
    source = '''def subject():
    try:
        raise KeyError("internal")
    except KeyError:
        yield 1
        raise
'''
    model = compile_generator_protocol(source, "subject")
    first = step(model, model.initial())
    resumed = step(model, first.continuation, ambient_exception=ProtocolException("ValueError", ("caller",)))
    assert resumed.kind == "raise" and resumed.exception.kind == "KeyError"


def test_continuation_identity_is_bound_to_the_issuing_model_not_just_its_hash():
    first = compile_generator_protocol(SOURCE, "subject")
    second = compile_generator_protocol(SOURCE, "subject")
    issued = first.initial()
    with pytest.raises(InvalidContinuation):
        second.resume(issued)
    with pytest.raises(InvalidContinuation):
        first.resume(replace(issued))


def test_finite_input_product_covers_both_guard_outcomes_without_merging():
    source = '''def subject(flag):
    if flag:
        yield "true"
    else:
        yield "false"
'''
    model = compile_generator_protocol(source, "subject")
    states = model.initial_alternatives({"flag": (False, True)})
    assert len(states) == 2
    assert {step(model, state).value for state in states} == {"true", "false"}


def test_budget_exhaustion_preserves_resumable_residual_without_partial_complete():
    model = compile_generator_protocol(SOURCE, "subject", budget=ProtocolBudget(max_steps=1))
    issued = model.initial()
    result = model.resume(issued)
    assert result.status == "unknown" and not result.outcomes
    assert result.residual is issued
    assert result.to_dict()["external_heap_effect"] == "unproved"


def test_host_custom_values_are_rejected_without_invoking_truth_or_equality():
    class HostValue:
        def __bool__(self):
            raise AssertionError("host truthiness executed")
        def __eq__(self, other):
            raise AssertionError("host equality executed")
    model = compile_generator_protocol("def subject(value):\n    yield value\n", "subject")
    with pytest.raises(InvalidGeneratorInput):
        model.initial({"value": HostValue()})


@pytest.mark.parametrize("source", [
    "def subject():\n    yield from (1, 2)\n",
    "def subject():\n    for x in (1, 2):\n        yield x\n",
    "def subject():\n    values = []\n    values.append(1)\n    yield 1\n",
])
def test_unmodeled_delegation_loops_and_heap_mutation_are_explicitly_rejected(source):
    with pytest.raises(UnsupportedGeneratorSource):
        compile_generator_protocol(source, "subject")
