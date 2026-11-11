"""Independent hostile and bounded concrete checks for the source checker.

The differential experiment is bug-finding evidence, never a proof of the
abstract transfer rules. Its line budget observes prefixes of divergent runs.
"""

import ast
import copy
import sys
import textwrap

import pytest

from pythonstan.analysis.pointer.ai.certified.checker import (
    FunctionSummary, InferenceUnknown, ProgramCertificate, certificate_to_dict,
    check_source, infer_candidate, source_digest,
)


class _PrefixBudget(BaseException):
    pass


def _concrete_prefix(source, function_name, args, max_steps=120):
    """Observe calls/exceptions independently by executing source in CPython."""
    filename = "<certified-differential>"
    namespace = {}
    exec(compile(source, filename, "exec"), namespace)
    events = set()
    exceptions = set()
    depth = 0
    steps = 0

    def trace(frame, event, arg):
        nonlocal depth, steps
        if frame.f_code.co_filename != filename:
            return trace
        steps += 1
        if steps > max_steps:
            raise _PrefixBudget()
        if event == "call":
            if depth:
                events.add("call:" + frame.f_code.co_name)
            depth += 1
        elif event == "return":
            depth -= 1
        elif event == "exception":
            name = arg[0].__name__
            events.add("raise:" + name)
            exceptions.add(name)
        return trace

    previous = sys.gettrace()
    outcome = "normal"
    try:
        sys.settrace(trace)
        namespace[function_name](*args)
    except _PrefixBudget:
        outcome = "prefix"
    except BaseException as exc:
        outcome = "exception"
        exceptions.add(type(exc).__name__)
    finally:
        sys.settrace(previous)
    return events, exceptions, outcome


def _check_observations(source, entry="entry", args=()):
    certificate = infer_candidate(source)
    result = check_source(source, certificate)
    assert result.certified, result.diagnostics
    predicted = certificate.summary_map()[entry]
    events, exceptions, outcome = _concrete_prefix(source, entry, args)
    assert events <= predicted.events, (source, args, events - predicted.events)
    assert exceptions <= predicted.exceptions, (source, args, exceptions - predicted.exceptions)
    if outcome == "normal":
        assert predicted.normal_exit, (source, args)
    return events, exceptions, outcome


def test_hostile_recursive_vector_cannot_hide_divergent_prefix_calls():
    source = "def entry():\n    first()\ndef first():\n    second()\ndef second():\n    first()\n"
    candidate = certificate_to_dict(infer_candidate(source))
    assert not candidate["summaries"]["entry"]["normal_exit"]
    for function_name in candidate["summaries"]:
        for event in candidate["summaries"][function_name]["events"]:
            forged = copy.deepcopy(candidate)
            forged["summaries"][function_name]["events"].remove(event)
            assert not check_source(source, forged).certified, (function_name, event)
    events, _, outcome = _check_observations(source)
    assert outcome == "prefix" and {"call:first", "call:second"} <= events


def test_effect_before_infinite_loop_is_required_without_an_exit():
    source = "def effect():\n    pass\ndef entry():\n    effect()\n    while True:\n        pass\n"
    candidate = certificate_to_dict(infer_candidate(source))
    assert candidate["summaries"]["entry"]["events"] == ["call:effect"]
    assert not candidate["summaries"]["entry"]["normal_exit"]
    candidate["summaries"]["entry"]["events"] = []
    assert check_source(source, candidate).status == "rejected"
    assert _check_observations(source)[2] == "prefix"


@pytest.mark.parametrize("condition", [
    "False and unbound is None",
    "True or unbound is None",
    "x is None and unbound is None",
    "x is None or unbound is None",
    "x is None is unbound",
    "x is not None is not unbound",
    "not (x is None and unbound is None)",
    "(x is None or True) and (False or unbound is None)",
])
def test_short_circuit_and_chained_identity_concrete_observations(condition):
    source = f"""def sink():
    pass
def entry(x):
    if False:
        unbound = None
    if {condition}:
        sink()
"""
    for value in (None, True, object()):
        _check_observations(source, args=(value,))


@pytest.mark.parametrize("body", [
    """while x is None:
    sink()
    break
else:
    raise ValueError
""",
    """while x is None:
    x = True
    continue
else:
    sink()
""",
    """while x is None:
    while False:
        pass
    else:
        sink()
        break
else:
    raise RuntimeError
""",
    """while x is None:
    x = True
    while False:
        pass
    else:
        continue
else:
    sink()
""",
    """while x is None:
    if x is x:
        return sink()
    raise KeyError
else:
    sink()
""",
    """while x is None:
    local = None
    break
return local
""",
])
def test_loop_control_binding_and_else_observations(body):
    source = "def sink():\n    pass\ndef entry(x):\n" + textwrap.indent(body, "    ")
    for value in (None, True, object()):
        _check_observations(source, args=(value,))


def test_argument_evaluation_stops_before_call_after_exception():
    source = """def fails():
    raise ValueError
def later():
    raise KeyError
def target(a, b):
    raise RuntimeError
def entry():
    target(fails(), later())
"""
    events, exceptions, outcome = _check_observations(source)
    assert outcome == "exception"
    assert events == {"call:fails", "raise:ValueError"}
    assert exceptions == {"ValueError"}


@pytest.mark.parametrize("assignment", ["y = None", "y = x", "pass"])
@pytest.mark.parametrize("condition", ["x is None", "x is x", "False", "True"])
@pytest.mark.parametrize("after", ["return y", "sink(y)", "raise ValueError(y)"])
def test_generated_binding_branch_programs_against_concrete_execution(assignment, condition, after):
    source = f"""def sink(value):
    pass
def entry(x):
    if False:
        y = None
    if {condition}:
        {assignment}
    {after}
"""
    for value in (None, False, object()):
        _check_observations(source, args=(value,))


@pytest.mark.parametrize("source", [
    "def entry(x):\n    return target()\ndef target(x):\n    pass\n",
    "def entry(x):\n    target = x\n    target()\ndef target():\n    pass\n",
    "def entry():\n    if False:\n        target = None\n    target()\ndef target():\n    pass\n",
    "def entry():\n    if False:\n        ValueError = None\n    raise ValueError\n",
])
def test_binding_or_call_rules_outside_model_are_unsupported(source):
    names = [node.name for node in ast.parse(source).body]
    candidate = ProgramCertificate(source_digest(source), tuple((name, FunctionSummary()) for name in names))
    assert check_source(source, candidate).status == "unsupported"


@pytest.mark.parametrize("budget", [0, -1, True, 1.5, "1"])
def test_invalid_inference_and_check_budgets_cannot_accept(budget):
    source = "def entry():\n    pass\n"
    certificate = infer_candidate(source)
    assert check_source(source, certificate, max_iterations=budget).status == "unknown"
    with pytest.raises(InferenceUnknown):
        infer_candidate(source, max_iterations=budget)


def test_malformed_direct_certificate_does_not_bypass_parse_checks():
    source = "def entry():\n    pass\n"
    certificate = ProgramCertificate(source_digest(source), (("entry", FunctionSummary(normal_exit=1)),))
    assert check_source(source, certificate).status == "rejected"


@pytest.mark.parametrize("source", [
    "def entry():\n    __debug__ = 1\n",
    "def entry(__debug__):\n    pass\n",
])
def test_ast_parse_success_does_not_certify_a_compile_invalid_program(source):
    from pythonstan.analysis.pointer.ai.certified.checker import UnsupportedSource

    ast.parse(source)  # Parsing alone deliberately misses these compiler checks.
    with pytest.raises(SyntaxError):
        compile(source, "<source>", "exec")
    candidate = ProgramCertificate(source_digest(source), (("entry", FunctionSummary()),))
    assert check_source(source, candidate).status == "unsupported"
    with pytest.raises(UnsupportedSource):
        infer_candidate(source)


def test_unpaired_unicode_surrogate_fails_closed():
    from pythonstan.analysis.pointer.ai.certified.checker import UnsupportedSource

    assert check_source("\ud800", {}).status in {"unsupported", "rejected", "unknown"}
    with pytest.raises(UnsupportedSource):
        infer_candidate("\ud800")


def test_builtin_namespace_function_rebinding_is_outside_model():
    source = "def __builtins__():\n    pass\ndef entry():\n    raise ValueError\n"
    candidate = ProgramCertificate(source_digest(source), (
        ("__builtins__", FunctionSummary(normal_exit=True)),
        ("entry", FunctionSummary(frozenset({"raise:ValueError"}), exceptions=frozenset({"ValueError"}))),
    ))
    assert check_source(source, candidate).status == "unsupported"
