"""Independent false-certificate probes for the closed operational checker.

These trusted, hand-authored witnesses are not executed by the checker. Expected
values specify Python behavior; refusing an unsupported operation is acceptable,
but claiming a different result is not. Positive witnesses separately prevent an
always-unsupported implementation from satisfying the complete suite.
"""
import hashlib

import pytest

from pythonstan.analysis.pointer.ai.certified.closed_heap_effects import (
    Budget, SEMANTICS_VERSION, analyze_closed_source, check_closed_effects,
)


def proposal(source, values):
    return {"source_sha256": hashlib.sha256(source.encode()).hexdigest(),
            "semantics_version": SEMANTICS_VERSION, "globals": values}


@pytest.mark.parametrize("source,correct,wrong", [
    ('''d = {"a": 1}
view = d.keys()
result = list(view) == list(view)
''', True, False),
    ('''result = isinstance(range(2), tuple)
''', False, True),
    ('''class A:
    pass
result = isinstance(A.__dict__, dict)
''', False, True),
    ('''x = 1
class A:
    x = 2
    class B:
        y = x
result = A.B.y
''', 1, 2),
    ('''def reraiser():
    raise
try:
    raise ValueError("original")
except ValueError:
    try:
        reraiser()
    except RuntimeError:
        result = "wrong"
    except ValueError:
        result = "right"
''', "right", "wrong"),
    ('''try:
    try:
        raise ValueError("original")
    finally:
        raise
except RuntimeError:
    result = "wrong"
except ValueError:
    result = "right"
''', "right", "wrong"),
    ('''class A:
    pass
a = A()
result = a.__dict__ is a.__dict__
''', True, False),
    ('''class SetterOnly:
    def __set__(self, obj, value):
        pass
class A:
    x = SetterOnly()
a = A()
a.__dict__["x"] = 42
result = isinstance(a.x, SetterOnly)
''', False, True),
    ('''result = isinstance(len, type)
''', False, True),
])
def test_modeled_counterexamples_never_verify_the_wrong_python_result(source, correct, wrong):
    derived = analyze_closed_source(source, ["result"])
    if derived["status"] == "closed_model_completed":
        assert type(derived["observations"]["result"]) is type(correct)
        assert derived["observations"]["result"] == correct
    checked = check_closed_effects(source, proposal(source, {"result": wrong}))
    assert checked["status"] != "conditional_verified"
    assert checked["conditional_effects_verified"] == 0


def test_closed_closure_identity_and_heap_alias_copy_are_preserved():
    source = '''def factory(value):
    def callback():
        return value
    return callback
first = factory("first")
second = factory("second")
original = {"callback": first}
alias = original
copied = dict(original)
copied["callback"] = second
result = [alias["callback"](), copied["callback"]()]
'''
    checked = check_closed_effects(source, proposal(source, {"result": ["first", "second"]}))
    assert checked["status"] == "conditional_verified"
    assert not checked["full_python_soundness_proved"]
    assert not checked["kcfa_results_certified"]
    assert not checked["runtime_observations_used"]
    assert checked["residual"]


def test_finally_overrides_return_after_observable_prefix_effects():
    source = '''events = []
def subject():
    try:
        events.append("body")
        return 1
    finally:
        events.append("finally")
        return 2
result = subject()
'''
    checked = check_closed_effects(source, proposal(source, {"events": ["body", "finally"], "result": 2}))
    assert checked["status"] == "conditional_verified"


def test_source_binding_and_candidate_values_do_not_change_transfer():
    source = "result = 42\n"
    valid = proposal(source, {"result": 42})
    assert check_closed_effects(source, valid)["status"] == "conditional_verified"
    assert check_closed_effects("result = 43\n", valid)["status"] == "rejected"
    assert check_closed_effects(source, proposal(source, {"result": 43}))["status"] == "rejected"


@pytest.mark.parametrize("source,incorrect", [
    ("result = [True]\n", [1]),
    ('result = {"flag": True}\n', {"flag": 1}),
    ("result = (1, 2)\n", [1, 2]),
])
def test_literal_projection_preserves_nested_scalar_and_container_types(source, incorrect):
    checked = check_closed_effects(source, proposal(source, {"result": incorrect}))
    assert checked["status"] != "conditional_verified"
    assert checked["conditional_effects_verified"] == 0


def test_unsupported_guard_cannot_be_treated_as_a_false_branch():
    source = '''if external_flag():
    result = 1
else:
    result = 2
'''
    checked = check_closed_effects(source, proposal(source, {"result": 2}))
    assert checked["status"] != "conditional_verified"
    assert checked["conditional_effects_verified"] == 0


def test_unreachable_unsupported_branch_is_closed_source_only():
    source = '''if False:
    result = external_flag()
else:
    result = 2
'''
    checked = check_closed_effects(source, proposal(source, {"result": 2}))
    assert checked["status"] == "conditional_verified"
    assert any("no later external calls" in item for item in checked["contract"])


@pytest.mark.parametrize("source,budget", [
    ("while True:\n    pass\nresult = 0\n", Budget(max_steps=20)),
    ("def recurse():\n    return recurse()\nresult = recurse()\n", Budget(max_call_depth=3)),
    ("values = [[], [], []]\nresult = 0\n", Budget(max_objects=2)),
])
def test_budget_exhaustion_never_turns_prefix_information_into_a_certificate(source, budget):
    checked = check_closed_effects(source, proposal(source, {"result": 0}), budget=budget)
    assert checked["status"] == "unknown"
    assert checked["conditional_effects_verified"] == 0
    assert not checked["normal_module_completion"]


def test_reachable_suspension_requires_a_continuation_model():
    source = '''def generator():
    yield 1
result = next(generator())
'''
    checked = check_closed_effects(source, proposal(source, {"result": 1}))
    assert checked["status"] == "unsupported"
    assert checked["conditional_effects_verified"] == 0
