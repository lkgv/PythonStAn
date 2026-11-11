"""Exit-sensitive source obligations, independent of candidate proposals."""

import ast
import sys

import pytest

from pythonstan.analysis.pointer.ai.certified import (
    UnsupportedSource, certificate_to_dict, check_source, infer_candidate,
)


def checked(source, name="f"):
    certificate = infer_candidate(source)
    result = check_source(source, certificate)
    assert result.certified, result.diagnostics
    return result.certificate.summary_map()[name]


def program(body):
    return ("def mark():\n    pass\ndef other():\n    pass\n"
            "def fail():\n    raise ValueError()\ndef f(flag):\n" +
            "\n".join("    " + line for line in body.splitlines()) + "\n")


@pytest.mark.parametrize("body,events,normal,exceptions", [
    ("try:\n    fail()\nfinally:\n    mark()",
     {"call:fail", "raise:ValueError", "call:mark"}, False, {"ValueError"}),
    ("try:\n    fail()\nfinally:\n    return None",
     {"call:fail", "raise:ValueError"}, True, set()),
    ("try:\n    return None\nfinally:\n    raise TypeError()",
     {"raise:TypeError"}, False, {"TypeError"}),
    ("try:\n    fail()\nfinally:\n    raise TypeError()",
     {"call:fail", "raise:ValueError", "raise:TypeError"}, False, {"TypeError"}),
    ("try:\n    fail()\nexcept Exception:\n    mark()\nelse:\n    other()",
     {"call:fail", "raise:ValueError", "call:mark"}, True, set()),
    ("try:\n    mark()\nexcept Exception:\n    other()\nelse:\n    fail()",
     {"call:mark", "call:fail", "raise:ValueError"}, False, {"ValueError"}),
    ("try:\n    fail()\nexcept Exception:\n    raise TypeError()\nexcept TypeError:\n    mark()",
     {"call:fail", "raise:ValueError", "raise:TypeError"}, False, {"TypeError"}),
    ("try:\n    raise UnboundLocalError()\nexcept NameError:\n    mark()\nexcept Exception:\n    other()",
     {"raise:UnboundLocalError", "call:mark"}, True, set()),
    ("try:\n    raise SystemExit()\nexcept Exception:\n    mark()",
     {"raise:SystemExit"}, False, {"SystemExit"}),
    ("try:\n    raise KeyboardInterrupt()\nexcept:\n    mark()",
     {"raise:KeyboardInterrupt", "call:mark"}, True, set()),
    ("try:\n    fail()\nexcept (TypeError, ValueError):\n    mark()",
     {"call:fail", "raise:ValueError", "call:mark"}, True, set()),
    ("try:\n    fail()\nexcept ():\n    mark()",
     {"call:fail", "raise:ValueError"}, False, {"ValueError"}),
    ("try:\n    while True:\n        mark()\nfinally:\n    other()",
     {"call:mark"}, False, set()),
    ("try:\n    fail()\nfinally:\n    mark()\n    while True:\n        pass",
     {"call:fail", "raise:ValueError", "call:mark"}, False, set()),
    ("while True:\n    try:\n        return None\n    finally:\n        break\nmark()",
     {"call:mark"}, True, set()),
    ("while True:\n    try:\n        return None\n    finally:\n        continue\nmark()",
     set(), False, set()),
    ("while True:\n    try:\n        break\n    finally:\n        raise TypeError()\nmark()",
     {"raise:TypeError"}, False, {"TypeError"}),
    ("while True:\n    try:\n        continue\n    finally:\n        return None\nmark()",
     set(), True, set()),
    ("try:\n    try:\n        fail()\n    finally:\n        return None\nfinally:\n    mark()",
     {"call:fail", "raise:ValueError", "call:mark"}, True, set()),
    ("try:\n    try:\n        fail()\n    finally:\n        raise TypeError()\nexcept TypeError:\n    mark()",
     {"call:fail", "raise:ValueError", "raise:TypeError", "call:mark"}, True, set()),
])
def test_pending_exit_composition(body, events, normal, exceptions):
    summary = checked(program(body))
    assert summary.events == events
    assert summary.normal_exit is normal
    assert summary.exceptions == exceptions


@pytest.mark.parametrize("body,normal,exceptions", [
    ("try:\n    x = fail()\nfinally:\n    return x", False, {"UnboundLocalError"}),
    ("try:\n    x = fail()\nexcept ValueError:\n    return x", False, {"UnboundLocalError"}),
    ("try:\n    x = None\n    fail()\nfinally:\n    return x", True, set()),
    ("try:\n    fail()\nexcept ValueError as err:\n    return err", True, set()),
    ("try:\n    fail()\nexcept ValueError as err:\n    pass\nreturn err", False, {"UnboundLocalError"}),
    ("err = None\ntry:\n    fail()\nexcept ValueError as err:\n    pass\nreturn err", False, {"UnboundLocalError"}),
    ("try:\n    try:\n        fail()\n    except ValueError as err:\n        return None\nfinally:\n    return err", False, {"UnboundLocalError"}),
    ("try:\n    fail()\nexcept ValueError as err:\n    try:\n        return None\n    finally:\n        return err", True, set()),
    ("try:\n    if flag is None:\n        x = None\n    fail()\nexcept ValueError:\n    return x", True, {"UnboundLocalError"}),
    ("try:\n    if flag is None:\n        x = None\n        return None\n    raise ValueError()\nfinally:\n    return x", True, {"UnboundLocalError"}),
    ("while True:\n    try:\n        fail()\n    except ValueError as err:\n        break\nreturn err", False, {"UnboundLocalError"}),
    ("try:\n    fail()\nexcept ValueError as err:\n    raise TypeError()\nfinally:\n    return err", False, {"UnboundLocalError"}),
])
def test_exit_binding_states_and_implicit_handler_target_deletion(body, normal, exceptions):
    summary = checked(program(body))
    assert summary.normal_exit is normal
    assert summary.exceptions == exceptions


@pytest.mark.parametrize("body", [
    "try:\n    pass\nexcept flag:\n    pass",
    "try:\n    pass\nexcept (Exception, flag):\n    pass",
    "try:\n    pass\nexcept ((Exception,),):\n    pass",
    "try:\n    pass\nexcept mark():\n    pass",
    "try:\n    pass\nexcept Exception as Exception:\n    pass",
    "try:\n    pass\nfinally:\n    raise",
    "try:\n    fail()\nexcept Exception:\n    raise",
    "try:\n    pass\nexcept* Exception:\n    pass",
    "try:\n    return None\nfinally:\n    flag.attr",
    "try:\n    pass\nexcept Exception:\n    raise ValueError() from None",
])
def test_uncovered_exception_protocols_are_rejected_even_if_unreachable(body):
    with pytest.raises(UnsupportedSource):
        infer_candidate(program(body))


def test_handler_target_is_a_local_throughout_function():
    source = "def err():\n    pass\ndef f():\n    err()\n    try:\n        pass\n    except Exception as err:\n        pass\n"
    with pytest.raises(UnsupportedSource):
        infer_candidate(source)


def test_caught_raise_is_still_a_prefix_obligation():
    source = program("try:\n    fail()\nexcept Exception:\n    pass")
    candidate = certificate_to_dict(infer_candidate(source))
    candidate["summaries"]["f"]["events"].remove("raise:ValueError")
    result = check_source(source, candidate)
    assert result.status == "rejected"
    assert any("missing prefix" in message for message in result.diagnostics)


def test_cleanup_on_recursive_return_cannot_be_omitted_by_false_no_exit_claim():
    source = "def mark():\n    pass\ndef f(flag):\n    try:\n        if flag is None:\n            return None\n        f(flag)\n    finally:\n        mark()\n"
    candidate = certificate_to_dict(infer_candidate(source))
    candidate["summaries"]["f"]["normal_exit"] = False
    candidate["summaries"]["f"]["events"].remove("call:mark")
    assert check_source(source, candidate).status == "rejected"


def test_recursive_divergence_never_fabricates_cleanup_execution():
    source = "def mark():\n    pass\ndef f():\n    try:\n        f()\n    finally:\n        mark()\n"
    summary = checked(source)
    assert summary.events == {"call:f"}
    assert not summary.normal_exit
    assert not summary.exceptions


def test_candidate_cannot_remove_finally_exception_exit():
    source = program("try:\n    return None\nfinally:\n    raise TypeError()")
    candidate = certificate_to_dict(infer_candidate(source))
    candidate["summaries"]["f"]["exceptions"] = []
    assert check_source(source, candidate).status == "rejected"


def test_candidate_v1_profile_is_not_silently_reinterpreted():
    source = program("try:\n    fail()\nfinally:\n    mark()")
    candidate = certificate_to_dict(infer_candidate(source))
    candidate["semantics_version"] = "closed-call-effects-v1"
    assert check_source(source, candidate).status == "rejected"


def test_finite_execution_oracle_for_nested_cleanup_and_handlers():
    # This oracle executes authored pure fixtures, never candidate text. Trace
    # hooks are a testing observer, not a feature admitted by the source model.
    bodies = [
        "try:\n    if flag is None:\n        fail()\n    else:\n        mark()\nexcept Exception as err:\n    other()\nelse:\n    mark()\nfinally:\n    other()",
        "try:\n    try:\n        fail()\n    except Exception as err:\n        if flag is None:\n            return err\n        raise TypeError()\nfinally:\n    return err",
        "while True:\n    try:\n        fail()\n    finally:\n        if flag is None:\n            break\n        return None\nmark()",
    ]
    for body in bodies:
        source = program(body)
        summary = checked(source)
        filename = "<certificate-exit-oracle>"
        namespace = {}
        exec(compile(ast.parse(source), filename, "exec"), namespace)
        for flag in (None, True, False, object()):
            observed = set()
            def trace(frame, event, arg):
                if frame.f_code.co_filename == filename:
                    if event == "call" and frame.f_code.co_name != "f":
                        observed.add("call:" + frame.f_code.co_name)
                    elif event == "exception":
                        observed.add("raise:" + type(arg[1]).__name__)
                return trace
            previous = sys.gettrace()
            try:
                sys.settrace(trace)
                try:
                    namespace["f"](flag)
                except BaseException as exception:
                    assert type(exception).__name__ in summary.exceptions
                else:
                    assert summary.normal_exit
            finally:
                sys.settrace(previous)
            assert observed <= summary.events


def test_caught_exception_creates_unforgeable_normal_exit_obligation():
    source = program("try:\n    fail()\nexcept Exception:\n    return None")
    candidate = certificate_to_dict(infer_candidate(source))
    candidate["summaries"]["f"]["normal_exit"] = False
    assert check_source(source, candidate).status == "rejected"


def test_nested_handler_using_same_target_does_not_restore_old_binding():
    source = program("try:\n    fail()\nexcept ValueError as err:\n    try:\n        raise TypeError()\n    except TypeError as err:\n        pass\n    return err")
    summary = checked(source)
    assert not summary.normal_exit
    assert summary.exceptions == {"UnboundLocalError"}


def test_exception_hierarchy_table_matches_exact_runtime_builtins():
    # The trusted source matcher is static; this independently checks every
    # modeled pair against the host builtin hierarchy without user classes.
    import builtins
    from pythonstan.analysis.pointer.ai.certified.checker import (
        EXCEPTION_TYPES, _exception_matches,
    )
    for raised in EXCEPTION_TYPES:
        for caught in EXCEPTION_TYPES:
            handler = ast.ExceptHandler(type=ast.Name(id=caught), name=None, body=[])
            assert _exception_matches(raised, handler) is issubclass(
                getattr(builtins, raised), getattr(builtins, caught))


def test_cleanup_transfer_is_monotone_over_increasing_recursive_summary_vectors():
    import random
    from pythonstan.analysis.pointer.ai.certified.checker import (
        FunctionSummary, _apply, _load_program,
    )
    source = ("def g():\n    pass\ndef f(flag):\n    try:\n"
              "        g()\n        x = None\n        if flag is None:\n"
              "            return None\n        f(flag)\n"
              "    except (ValueError, TypeError) as err:\n"
              "        g()\n    finally:\n"
              "        try:\n            return x\n"
              "        except UnboundLocalError:\n            g()\n")
    parsed = _load_program(source)
    rng = random.Random(321)
    events = ("call:f", "call:g", "raise:ValueError", "raise:TypeError")
    exceptions = ("ValueError", "TypeError", "UnboundLocalError")
    for _ in range(100):
        lower, upper = {}, {}
        for name in parsed.functions:
            low_events = frozenset(event for event in events if rng.choice((False, True)))
            high_events = low_events | frozenset(event for event in events if rng.choice((False, True)))
            low_exceptions = frozenset(kind for kind in exceptions if rng.choice((False, True)))
            high_exceptions = low_exceptions | frozenset(kind for kind in exceptions if rng.choice((False, True)))
            low_normal = rng.choice((False, True))
            lower[name] = FunctionSummary(low_events, low_normal, low_exceptions)
            upper[name] = FunctionSummary(high_events, low_normal or rng.choice((False, True)), high_exceptions)
        low_result, _ = _apply(parsed, lower, 100000)
        high_result, _ = _apply(parsed, upper, 100000)
        assert all(low_result[name].included_in(high_result[name]) for name in lower)
