"""Independent shared-heap suspension witnesses; CPython is test-only oracle."""
import hashlib

import pytest

from pythonstan.analysis.pointer.ai.certified.closed_heap_effects import (
    Budget, SEMANTICS_VERSION, analyze_closed_source, check_closed_effects,
)


def candidate(source, result):
    return {"source_sha256": hashlib.sha256(source.encode()).hexdigest(),
            "semantics_version": SEMANTICS_VERSION, "globals": {"result": result}}


SUPPORTED = [
    pytest.param('''events = []
def g(box):
    box.append("start")
    chosen = yield box
    box.append(chosen)
    yield box
it = g(events)
alias = next(it)
alias.append("caller")
after = it.send("sent")
snapshot = list(after)
it.close()
result = [snapshot, alias is events]
''', [["start", "caller", "sent"], True], id="yielded_alias_and_caller_mutation"),
    pytest.param('''events = []
def g(label):
    events.append(label)
    token = yield label
    events.append(token)
    yield label
first = g("a")
second = g("b")
next(first)
next(second)
second.send("b2")
first.send("a2")
first.close()
second.close()
result = events
''', ["a", "b", "b2", "a2"], id="interleaved_frames_share_one_heap"),
    pytest.param('''events = []
def g():
    value = "old"
    try:
        value = yield "ready"
    except ValueError:
        events.append(value)
    finally:
        events.append("cleanup")
it = g()
next(it)
try:
    it.throw(ValueError())
except StopIteration:
    pass
result = events
''', ["old", "cleanup"], id="throw_bypasses_yield_assignment"),
    pytest.param('''events = []
def g():
    try:
        return "done"
    finally:
        token = yield "cleanup"
        events.append(token)
it = g()
first = next(it)
try:
    it.send("resume")
except StopIteration as stop:
    result = [first, stop.value, events]
''', ["cleanup", "done", ["resume"]], id="pending_return_survives_finally_yield"),
    pytest.param('''events = []
def g():
    try:
        yield "ready"
    finally:
        try:
            yield "cleanup"
        except ValueError:
            events.append("throw")
        events.append("finish")
it = g()
next(it)
try:
    it.close()
except RuntimeError:
    events.append("refused")
try:
    it.throw(ValueError())
except GeneratorExit:
    events.append("original_exit")
result = events
''', ["refused", "throw", "finish", "original_exit"], id="close_yield_keeps_actual_suspension"),
    pytest.param('''events = []
holder = []
def g():
    try:
        next(holder[0])
    except ValueError:
        events.append("busy")
    yield "ready"
holder.append(g())
next(holder[0])
holder[0].close()
result = events
''', ["busy"], id="reentrant_resume_is_frame_local_value_error"),
    pytest.param('''events = []
def g():
    try:
        raise ValueError()
    except ValueError:
        yield "ready"
        try:
            raise
        except ValueError:
            events.append("local")
        yield "again"
    try:
        raise
    except RuntimeError:
        events.append("no_ambient")
    except TypeError:
        events.append("leaked_ambient")
it = g()
next(it)
try:
    raise TypeError()
except TypeError:
    it.send(None)
try:
    next(it)
except StopIteration:
    pass
result = events
''', ["local", "no_ambient"], id="local_exception_preserved_ambient_not_persisted"),
    pytest.param('''def matcher():
    try:
        raise
    except ValueError:
        return ValueError
    except RuntimeError:
        return TypeError
def g():
    try:
        raise ValueError()
    except matcher():
        yield "caught"
it = g()
result = next(it)
it.close()
''', "caught", id="generator_handler_selector_sees_pending_exception"),
    pytest.param('''events = []
def g():
    for value in [1, 2, 3]:
        try:
            token = yield value
            if token:
                break
        finally:
            events.append(value)
    else:
        events.append("exhausted")
it = g()
next(it)
it.send(False)
try:
    it.send(True)
except StopIteration:
    pass
result = events
''', [1, 2], id="loop_break_unwinds_finally_and_skips_else"),
]


@pytest.mark.parametrize("source, expected", SUPPORTED)
def test_heap_continuation_matches_independent_python_execution(source, expected):
    namespace = {}
    exec(source, namespace)
    assert namespace["result"] == expected
    result = check_closed_effects(source, candidate(source, expected))
    assert result["status"] == "conditional_verified", result
    assert result["runtime_observations_used"] is False
    assert result["full_python_soundness_proved"] is False
    assert result["kcfa_results_certified"] is False


def test_implicit_temporary_generator_finalization_never_certifies_omitted_cleanup():
    source = '''events = []
def g():
    try:
        yield 1
    finally:
        events.append("closed")
next(g())
result = list(events)
'''
    namespace = {}
    exec(source, namespace)
    assert namespace["result"] == ["closed"]
    result = check_closed_effects(source, candidate(source, []))
    assert result["status"] != "conditional_verified"
    assert result["conditional_effects_verified"] == 0


def test_unentered_generator_does_not_run_cleanup():
    source = '''events = []
def g():
    try:
        yield 1
    finally:
        events.append("closed")
it = g()
it.close()
result = events
'''
    result = check_closed_effects(source, candidate(source, []))
    assert result["status"] == "conditional_verified", result


def test_generator_budget_exhaustion_never_verifies_heap_prefix():
    source = '''events = []
def g():
    while True:
        events.append(1)
        yield 1
it = g()
for item in it:
    pass
result = events
'''
    result = check_closed_effects(source, candidate(source, [1]), budget=Budget(max_steps=100))
    assert result["status"] == "unknown", result
    assert result["conditional_effects_verified"] == 0
    assert result["normal_module_completion"] is False
