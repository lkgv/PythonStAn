"""Independent concrete traces check the static continuation transfer.

Only this test oracle executes source. The production analyzer parses and walks
AST nodes without exec/eval. Tests never populate a proposed static answer.
"""
import builtins
from dataclasses import replace
from itertools import product
import inspect
import textwrap

import pytest

from pythonstan.analysis.pointer.ai.generator_protocol import (
    Continuation, InvalidContinuation, InvalidGeneratorInput, ProtocolBudget,
    ProtocolException, UnsupportedGeneratorSource, compile_generator_protocol,
)


def check_sequence(source, operations, arguments=None):
    source = textwrap.dedent(source)
    model = compile_generator_protocol(source, "subject")
    state = model.initial(arguments or {})
    namespace = {}
    exec(compile(source, "<concrete-oracle>", "exec"), namespace)
    generator = namespace["subject"](**(arguments or {}))
    observations = []
    try:
        for operation, payload in operations:
            if operation == "throw":
                exception = ProtocolException(payload)
                result = model.resume(state, operation, exception=exception)
            elif operation == "send":
                result = model.resume(state, operation, value=payload)
            else:
                result = model.resume(state, operation)
            assert result.status == "complete", result
            outcome, = result.outcomes
            before_status = inspect.getgeneratorstate(generator)
            try:
                if operation == "next": value = next(generator)
                elif operation == "send": value = generator.send(payload)
                elif operation == "throw": value = generator.throw(getattr(builtins, payload)())
                else: value = generator.close()
                kind, error = ("close" if operation == "close" else "yield"), None
            except StopIteration as exc:
                # throw before entry or after closure propagates the injected
                # exception without entering a generator frame (PEP 479).
                if operation == "throw" and payload == "StopIteration" and before_status in ("GEN_CREATED", "GEN_CLOSED"):
                    kind, value, error = "raise", None, "StopIteration"
                else:
                    kind, value, error = "return", exc.value, None
            except BaseException as exc:
                kind, value, error = "raise", None, type(exc).__name__
            assert (outcome.kind, outcome.value, outcome.exception.kind if outcome.exception else None) == (kind, value, error), (operations, operation, before_status)
            actual_status = {"GEN_CREATED": "created", "GEN_SUSPENDED": "suspended", "GEN_CLOSED": "closed"}[inspect.getgeneratorstate(generator)]
            assert outcome.continuation.status == actual_status
            observations.append(outcome)
            state = outcome.continuation
    finally:
        try:
            generator.close()
        except BaseException:
            pass
    return observations


PROGRAMS = [
    '''
    def subject():
        yield 1
        yield 2
        return 3
    ''',
    '''
    def subject():
        value = yield "ready"
        yield value
        return 7
    ''',
    '''
    def subject():
        try:
            yield 1
            return 7
        except ValueError:
            yield 2
            return 8
        finally:
            yield 9
    ''',
    '''
    def subject():
        try:
            yield 1
            raise ValueError("bad")
        except ValueError as error:
            yield 2
            raise
        finally:
            marker = 3
    ''',
    '''
    def subject():
        try:
            yield 1
        except BaseException:
            yield 2
        return 8
    ''',
    '''
    def subject():
        try:
            try:
                yield 1
                raise ValueError()
            finally:
                yield 2
        except ValueError:
            yield 3
        return 4
    ''',
    '''
    def subject():
        try:
            yield 1
        except ValueError:
            return 2
        else:
            yield 3
        finally:
            yield 4
            return 5
    ''',
    '''
    def helper():
        raise
    def subject():
        try:
            yield 1
        except ValueError:
            yield 2
            helper()
    ''',
]
OPS = (("next", None), ("send", None), ("send", 11), ("throw", "ValueError"),
       ("throw", "StopIteration"), ("throw", "GeneratorExit"), ("close", None))


@pytest.mark.parametrize("source", PROGRAMS)
def test_all_three_operation_prefixes_match_concrete_python(source):
    for operations in product(OPS, repeat=3):
        check_sequence(source, operations)


@pytest.mark.parametrize("source", PROGRAMS)
def test_deeper_finally_and_throw_sequences_match_python(source):
    for operation in OPS:
        check_sequence(source, [("next", None), ("next", None), operation,
                                ("next", None), ("throw", "ValueError"), ("close", None)])


def test_prefix_helper_calls_are_ordered_and_not_replayed_after_resume():
    source = '''
    def before():
        return None
    def after():
        return None
    def cleanup():
        return None
    def subject():
        before()
        try:
            received = yield "ready"
            after()
            return received
        finally:
            cleanup()
    '''
    results = check_sequence(source, [("next", None), ("send", "result"), ("next", None)])
    assert [[e.name for e in result.events if e.kind == "call"] for result in results] == [["before"], ["after", "cleanup"], []]


def test_throw_at_suspension_selects_handler_and_runs_cleanup_once():
    source = '''
    def caught(): return None
    def cleanup(): return None
    def subject():
        try:
            yield "ready"
        except ValueError:
            caught()
            return "recovered"
        finally:
            cleanup()
    '''
    results = check_sequence(source, [("next", None), ("throw", "ValueError"), ("next", None)])
    assert results[1].value == "recovered"
    assert [e.name for e in results[1].events if e.kind == "call"] == ["caught", "cleanup"]


def test_except_name_is_deleted_and_bare_raise_does_not_escape_handler_scope():
    source = '''
    def subject():
        try:
            yield 1
        except ValueError as error:
            yield 2
        yield error
    '''
    results = check_sequence(source, [("next", None), ("throw", "ValueError"), ("next", None)])
    assert results[-1].exception.kind == "UnboundLocalError"


def test_return_pending_across_finally_yield_survives_a_normal_resume():
    source = '''
    def subject():
        try:
            yield 1
            return "original"
        finally:
            yield "cleanup"
    '''
    results = check_sequence(source, [("next", None), ("next", None), ("next", None)])
    assert [result.value for result in results] == [1, "cleanup", "original"]


def test_source_bound_handles_reject_cross_model_and_forged_metadata():
    source = "def subject():\n    yield 1\n"
    a = compile_generator_protocol(source, "subject")
    b = compile_generator_protocol(source, "subject")
    handle = a.initial()
    with pytest.raises(InvalidContinuation): b.resume(handle)
    with pytest.raises(InvalidContinuation): a.resume(replace(handle))
    with pytest.raises(InvalidContinuation): a.resume(Continuation(handle.source_sha256, handle.function, "closed", handle.state_id))


def test_finite_input_alternatives_keep_branch_and_return_correlations():
    model = compile_generator_protocol('''
def subject(flag, payload=3):
    if flag:
        sent = yield payload
        return sent
    else:
        yield "other"
        return "done"
''', "subject")
    states = model.initial_alternatives({"flag": (False, True)})
    yielded = [model.resume(state).outcomes[0] for state in states]
    assert [o.value for o in yielded] == ["other", 3]
    returned = [model.resume(o.continuation, "send", value=9).outcomes[0] for o in yielded]
    assert [o.value for o in returned] == ["done", 9]


@pytest.mark.parametrize("source", [
    "def subject():\n    while True:\n        yield 1\n",
    "def subject():\n    yield from (1, 2)\n",
    "async def subject():\n    yield 1\n",
    "def subject():\n    yield unknown()\n",
    "def subject():\n    values = []\n    yield values\n",
    "def subject():\n    yield object.member\n",
    "def subject():\n    yield subject()\n",
    "def subject(ValueError):\n    try:\n        yield 1\n    except ValueError:\n        pass\n",
    "def subject(x, x):\n    yield x\n",
])
def test_unsupported_source_cannot_issue_a_partial_model(source):
    with pytest.raises(UnsupportedGeneratorSource):
        compile_generator_protocol(source, "subject")


def test_resource_exhaustion_is_unknown_and_retains_original_continuation():
    model = compile_generator_protocol("def subject():\n    yield 1 + 2\n", "subject", budget=ProtocolBudget(max_steps=1))
    original = model.initial()
    result = model.resume(original)
    assert result.status == "unknown"
    assert result.outcomes == ()
    assert result.residual is original
    assert original.status == "created"


def test_oversized_values_do_not_allocate_an_unbounded_host_sequence():
    model = compile_generator_protocol("def subject():\n    yield 'x' * 100000000\n", "subject")
    result = model.resume(model.initial())
    assert result.status == "unknown"


def test_input_objects_cannot_execute_user_truthiness():
    class Hostile:
        def __bool__(self):
            pytest.fail("input callback executed")
    model = compile_generator_protocol("def subject(flag):\n    if flag:\n        yield 1\n", "subject")
    with pytest.raises(InvalidGeneratorInput):
        model.initial({"flag": Hostile()})


def test_no_target_source_exec_or_eval_in_production_kernel():
    import ast
    from pathlib import Path
    import pythonstan.analysis.pointer.ai.generator_protocol as module
    tree = ast.parse(Path(module.__file__).read_text())
    assert not any(isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id in {"exec", "eval"} for n in ast.walk(tree))


def test_unmodeled_builtin_name_is_rejected_instead_of_claiming_nameerror():
    with pytest.raises(UnsupportedGeneratorSource, match="unmodeled global/builtin"):
        compile_generator_protocol("def subject():\n    yield len\n", "subject")


def test_specialized_exception_keyword_constructor_remains_unknown():
    model = compile_generator_protocol("def subject():\n    yield NameError(name='missing')\n", "subject")
    result = model.resume(model.initial())
    assert result.status == "unknown"
    assert result.outcomes == ()


def test_large_negative_repeat_count_contains_cpython_overflow():
    source = "def subject():\n    yield 'x' * -100000000000000000000000000000000\n"
    check_sequence(source, [("next", None), ("next", None)])


def test_closed_local_callee_alias_cycle_is_unknown_at_depth_budget():
    source = """
def first():
    second = first
    return second()
def second():
    return 1
def subject():
    yield first()
"""
    model = compile_generator_protocol(source, "subject", budget=ProtocolBudget(max_call_depth=2))
    result = model.resume(model.initial())
    assert result.status == "unknown"
    assert "depth budget" in result.diagnostics[0]


def test_generator_bare_raise_inherits_explicit_caller_exception_context():
    source = "def subject():\n    yield 1\n    raise\n"
    model = compile_generator_protocol(source, "subject")
    first = model.resume(model.initial()).outcomes[0].continuation
    result = model.resume(first, ambient_exception=ProtocolException("ValueError", ("caller",)))
    assert result.outcomes[0].exception == ProtocolException("ValueError", ("caller",))
    namespace = {}
    exec(source, namespace)
    generator = namespace["subject"]()
    next(generator)
    try:
        raise ValueError("caller")
    except ValueError:
        with pytest.raises(ValueError, match="caller"):
            next(generator)


def test_caller_exception_is_not_stored_across_yield():
    source = "def subject():\n    yield 1\n    raise\n"
    model = compile_generator_protocol(source, "subject")
    first = model.resume(model.initial(), ambient_exception=ProtocolException("ValueError")).outcomes[0].continuation
    result = model.resume(first)
    assert result.outcomes[0].exception.kind == "RuntimeError"
    namespace = {}
    exec(source, namespace)
    generator = namespace["subject"]()
    try:
        raise ValueError()
    except ValueError:
        next(generator)
    with pytest.raises(RuntimeError, match="No active exception"):
        next(generator)


def test_internal_handler_exception_overrides_caller_context():
    source = "def subject():\n    try:\n        raise TypeError()\n    except TypeError:\n        yield 1\n        raise\n"
    model = compile_generator_protocol(source, "subject")
    first = model.resume(model.initial(), ambient_exception=ProtocolException("ValueError")).outcomes[0].continuation
    result = model.resume(first, ambient_exception=ProtocolException("KeyError"))
    assert result.outcomes[0].exception.kind == "TypeError"
