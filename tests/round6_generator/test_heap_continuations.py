"""CPython is an independent test oracle, never a production proof input."""
import ast
import itertools
from pathlib import Path

import pytest

from pythonstan.analysis.pointer.ai.certified.closed_heap_effects import (
    Budget, ClosedHeapMachine, Env, ExceptionValue, Function, Signal,
    Unsupported, _locals, analyze_closed_source,
)
from pythonstan.analysis.pointer.ai.certified.heap_continuations import (
    HeapGenerator, create_heap_generator, has_scoped_yield, resume_heap_generator,
)


PROGRAMS = [
    '''events = []
def g():
    events.append("before")
    value = yield "ready"
    events.append(value)
    return "done"
''',
    '''events = []
def g():
    try:
        events.append("before")
        yield "ready"
    except ValueError:
        events.append("caught")
        yield "recovery"
    finally:
        events.append("cleanup")
''',
    '''events = []
def g():
    try:
        value = yield "ready"
        return value
    finally:
        events.append("finally")
        yield "cleanup"
        events.append("finished")
''',
    '''events = []
def g():
    try:
        yield "ready"
    except GeneratorExit:
        events.append("ignored-close")
        value = yield "still-alive"
        events.append(value)
''',
    '''events = []
def g():
    try:
        raise ValueError("inner")
    except ValueError:
        events.append("handler")
        yield "ready"
        raise
''',
    '''events = []
def g():
    for x in [1, 2]:
        try:
            events.append(x)
            yield x
            continue
        finally:
            events.append("finally")
    else:
        events.append("exhausted")
    return "done"
''',
    '''events = []
def g():
    yield "ready"
    raise StopIteration("escaped")
''',
    '''events = []
def helper():
    events.append("helper")
    raise
def g():
    try:
        yield "ready"
    except ValueError:
        yield "handling"
        helper()
''',
]

OPERATIONS = [
    ("next", None), ("send", None), ("send", "sent"),
    ("throw", "ValueError"), ("throw", "StopIteration"),
    ("throw", "GeneratorExit"), ("close", None),
]


def direct_model(source):
    """Bind a source generator without depending on the enclosing call adapter."""
    machine = ClosedHeapMachine(source, Budget())
    for statement in machine.tree.body:
        if isinstance(statement, ast.FunctionDef) and has_scoped_yield(statement):
            function = Function(statement, machine.env, (), {}, _locals(statement), qualname=statement.name)
            machine.env.bind(statement.name, function)
        else:
            machine.stmt(statement, machine.env)
    function = machine.env.lookup("g")
    env = Env(machine.env, local_names=function.local_names, function=True, qualname="g")
    return machine, create_heap_generator(machine, function, env)


def observe_model(machine, generator, operation):
    kind, value = operation
    args = [ExceptionValue(value, ("injected",))] if kind == "throw" else [value] if kind == "send" else []
    try:
        result = resume_heap_generator(machine, generator, kind, args)
    except Signal as signal:
        assert signal.kind == "raise"
        if signal.value.kind == "StopIteration":
            return ("stop", machine.export(signal.value.args[0]) if signal.value.args else None)
        return ("raise", signal.value.kind)
    return ("close" if kind == "close" else "yield", machine.export(result))


def observe_python(generator, operation):
    kind, value = operation
    try:
        if kind == "next":
            result = next(generator)
        elif kind == "send":
            result = generator.send(value)
        elif kind == "throw":
            exception = {"ValueError": ValueError, "StopIteration": StopIteration, "GeneratorExit": GeneratorExit}[value]
            result = generator.throw(exception("injected"))
        else:
            result = generator.close()
    except StopIteration as exception:
        return ("stop", exception.value)
    except BaseException as exception:
        return ("raise", type(exception).__name__)
    return ("close" if kind == "close" else "yield", result)


@pytest.mark.parametrize("source", PROGRAMS)
def test_heap_protocol_three_operation_differential(source):
    # 8 x 7^3 = 2744 traces, 8232 operation observations, with heap projections
    # compared after every operation. These are finite development witnesses.
    for operations in itertools.product(OPERATIONS, repeat=3):
        native = {}
        exec(source, native)
        concrete = native["g"]()
        machine, modeled = direct_model(source)
        for operation in operations:
            expected = observe_python(concrete, operation)
            actual = observe_model(machine, modeled, operation)
            assert actual == expected, (source, operations, operation, actual, expected)
            assert machine.export(machine.env.lookup("events")) == native["events"], (source, operations, operation)
        # Avoid native GC cleanup noise; finalization itself is excluded from
        # this explicit-operation comparison and suspended closed entries reject.
        for _ in range(3):
            try:
                concrete.close()
            except BaseException:
                continue


def test_heap_alias_is_live_between_resumptions():
    source = '''events = []
def g():
    events.append("start")
    yield events
    events.append("end")
'''
    machine, generator = direct_model(source)
    value = resume_heap_generator(machine, generator, "next")
    assert value is machine.env.lookup("events")
    value.values.append("caller")
    assert observe_model(machine, generator, ("next", None)) == ("stop", None)
    assert machine.export(value) == ["start", "caller", "end"]


def test_interleaved_generators_have_distinct_frames_and_shared_heap():
    source = '''events = []
def g():
    value = yield "ready"
    events.append(value)
    yield value
'''
    machine, first = direct_model(source)
    fn = machine.env.lookup("g")
    second = create_heap_generator(machine, fn, Env(machine.env, local_names=fn.local_names, function=True))
    assert resume_heap_generator(machine, first, "next") == "ready"
    assert resume_heap_generator(machine, second, "next") == "ready"
    assert resume_heap_generator(machine, second, "send", ["b"]) == "b"
    assert resume_heap_generator(machine, first, "send", ["a"]) == "a"
    assert machine.export(machine.env.lookup("events")) == ["b", "a"]


def test_ambient_exception_is_not_saved_in_suspended_frame():
    source = '''events = []
def g():
    yield "ready"
    raise
'''
    machine, generator = direct_model(source)
    machine.active_exception = ExceptionValue("ValueError", ("caller",))
    assert resume_heap_generator(machine, generator, "next") == "ready"
    machine.active_exception = None
    assert observe_model(machine, generator, ("next", None)) == ("raise", "RuntimeError")


def test_owner_and_registration_are_required():
    machine, generator = direct_model(PROGRAMS[0])
    other, _ = direct_model(PROGRAMS[0])
    with pytest.raises(Unsupported, match="foreign"):
        resume_heap_generator(other, generator, "next")
    forged = HeapGenerator(machine, generator.function, generator.env)
    with pytest.raises(Unsupported, match="unregistered"):
        resume_heap_generator(machine, forged, "next")


@pytest.mark.parametrize("body", ["yield from [1, 2]", "result = 1 + (yield 2)", "with manager:\n        yield 1"])
def test_unsupported_suspension_is_explicit(body):
    machine, generator = direct_model("events = []\ndef g():\n    " + body + "\n")
    with pytest.raises(Unsupported, match="suspension"):
        resume_heap_generator(machine, generator, "next")


def test_local_scope_scanner_does_not_mark_outer_function():
    outer = ast.parse("def f():\n    def g():\n        yield 1\n    return g\n").body[0]
    assert not has_scoped_yield(outer)
    assert has_scoped_yield(outer.body[0])


@pytest.mark.parametrize("name", ["pluggy_firstresult_wrappers", "pluggy_wrapper_exception_recovery"])
def test_actual_frozen_pluggy_heap_effects(name):
    root = Path(__file__).resolve().parents[2]
    source = (root / "benchmark/realworld_hardcases/plugins/sources" / (name + ".py")).read_text()
    native = {}
    exec(source, native)
    report = analyze_closed_source(source, ["events", "result"])
    assert report["status"] == "closed_model_completed", report
    assert report["observations"] == {key: native[key] for key in ("events", "result")}


def test_suspended_generator_finalization_remains_residual():
    report = analyze_closed_source('''events = []
def g():
    try:
        yield 1
    finally:
        events.append("cleanup")
x = g()
next(x)
''', ["events"])
    assert report["status"] == "unsupported"
    assert "suspend" in report["diagnostic"].lower() or "finaliz" in report["diagnostic"].lower()


CLOSED_WITNESSES = [
    '''events = []
def g():
    try:
        yield "ready"
    finally:
        events.append("cleanup")
result = []
gen = g()
result.append(next(gen))
result.append(gen.close())
result.append(next(gen, "done"))
''',
    '''events = []
def g():
    try:
        value = yield "ready"
        return value
    finally:
        yield "cleanup"
gen = g()
result = [next(gen), gen.send("saved")]
try:
    next(gen)
except StopIteration as caught:
    result.append(caught.value)
''',
    '''events = []
def g():
    for value in [1, 2, 3]:
        try:
            yield value
            if value == 2:
                break
        finally:
            events.append(value)
    else:
        events.append("else")
    return "done"
gen = g()
result = [next(gen), next(gen)]
try:
    next(gen)
except StopIteration as caught:
    result.append(caught.value)
''',
    '''events = []
def selector():
    try:
        raise
    except ValueError:
        return ValueError
    except RuntimeError:
        return TypeError
def g():
    try:
        raise ValueError("body")
    except selector():
        yield "caught"
gen = g()
result = [next(gen), next(gen, "done")]
''',
    '''events = []
def g():
    try:
        yield "ready"
    except GeneratorExit:
        yield "ignored"
        events.append("after")
gen = g()
result = [next(gen)]
try:
    gen.close()
except RuntimeError:
    result.append("close failed")
result.append(next(gen, "done"))
''',
    '''events = []
def g():
    try:
        yield "ready"
    finally:
        events.append("cleanup")
        raise RuntimeError("generator ignored GeneratorExit")
gen = g()
result = [next(gen)]
try:
    gen.close()
except RuntimeError:
    result.append("closed")
result.append(next(gen, "done"))
''',
    '''events = []
def g():
    yield "ready"
    try:
        next(gen)
    except ValueError:
        events.append("reentrant")
    yield "after"
gen = g()
result = [next(gen), next(gen)]
gen.close()
''',
    '''events = []
class CustomStop(StopIteration):
    pass
def g():
    yield "ready"
    raise CustomStop("converted")
gen = g()
result = [next(gen)]
try:
    next(gen)
except RuntimeError:
    result.append("converted")
result.append(next(gen, "done"))
''',
    '''events = []
def g():
    yield "ready"
    events.append("body")
gen = g()
result = []
try:
    gen.send(1)
except TypeError:
    result.append("not started")
result.append(next(gen))
gen.close()
''',
    '''events = []
def g():
    value = yield "first"
    events.append(value)
    yield "second"
first = g()
second = g()
result = [next(first), next(second), second.send("B"), first.send("A")]
first.close()
second.close()
''',
]


@pytest.mark.parametrize("source", CLOSED_WITNESSES)
def test_integrated_closed_heap_protocol_witnesses(source):
    native = {}
    exec(source, native)
    report = analyze_closed_source(source, ["events", "result"])
    assert report["status"] == "closed_model_completed", report
    assert report["observations"] == {key: native[key] for key in ("events", "result")}


def test_valid_multi_argument_throw_stays_residual():
    source = '''def g():
    try:
        yield 1
    except ValueError:
        return 7
gen = g()
next(gen)
result = []
try:
    gen.throw(ValueError, "message")
except TypeError:
    result.append("wrong TypeError")
except StopIteration as error:
    result.append(error.value)
'''
    native = {}
    exec(source, native)
    assert native["result"] == [7]
    report = analyze_closed_source(source, ["result"])
    assert report["status"] == "unsupported"
    assert "throw" in report["diagnostic"]


@pytest.mark.parametrize("source", PROGRAMS)
def test_deeper_heap_protocol_traces(source):
    import random
    randomizer = random.Random(61228)
    traces = [tuple([("next", None)] + [randomizer.choice(OPERATIONS) for _ in range(7)]) for _ in range(128)]
    # 1024 length-eight traces across the eight source programs.
    for operations in traces:
        native = {}
        exec(source, native)
        concrete = native["g"]()
        machine, modeled = direct_model(source)
        for operation in operations:
            assert observe_model(machine, modeled, operation) == observe_python(concrete, operation), (source, operations)
            assert machine.export(machine.env.lookup("events")) == native["events"], (source, operations)
        for _ in range(3):
            try:
                concrete.close()
            except BaseException:
                continue


@pytest.mark.parametrize("operation", ["gen.send()", "gen.send(1, 2)", "gen.throw()", 'gen.throw("bad")', "gen.close(1)", "gen.__next__(1)"])
def test_argument_errors_precede_reentrant_generator_error(operation):
    source = '''events = []
def g():
    try:
        OPERATION
    except TypeError:
        events.append("argument")
    except ValueError:
        events.append("reentrant")
    yield 1
gen = g()
result = [next(gen)]
gen.close()
'''.replace("OPERATION", operation)
    native = {}
    exec(source, native)
    assert native["events"] == ["argument"]
    report = analyze_closed_source(source, ["events", "result"])
    assert report["status"] == "closed_model_completed", report
    assert report["observations"] == {key: native[key] for key in ("events", "result")}
