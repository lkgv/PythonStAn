"""Independent round-six witnesses for false closed-source certificates.

Only this trusted regression harness executes its fixed witness snippets. The
production checker must derive results itself, or refuse unsupported semantics.
"""
import hashlib

import pytest

from pythonstan.analysis.pointer.ai.certified.closed_heap_effects import (
    SEMANTICS_VERSION, analyze_closed_source, check_closed_effects,
)


def candidate(source, result):
    return {"source_sha256": hashlib.sha256(source.encode()).hexdigest(),
            "semantics_version": SEMANTICS_VERSION, "globals": {"result": result}}


CASES = [
    pytest.param('''class _:
    __x = 1
try:
    result = _.xxx
except AttributeError:
    result = "missing"
'''.replace("xxx", "___x"), "missing", 1,
                 id="all_underscore_class_name_does_not_mangle"),
    pytest.param('''def matcher():
    try:
        raise
    except ValueError:
        return ValueError
    except RuntimeError:
        return TypeError
try:
    try:
        raise ValueError()
    except matcher():
        result = "caught"
except ValueError:
    result = "missed"
''', "caught", "missed", id="handler_selector_observes_pending_exception"),
    pytest.param('''x = 99
try:
    answer = [x for y in [0] for x in [x]]
    result = "wrong"
except UnboundLocalError:
    result = "right"
''', "right", "wrong", id="later_comprehension_binding_is_local"),
    pytest.param('''y = True
try:
    answer = [x for x in [1] if y for y in [True]]
    result = "wrong"
except UnboundLocalError:
    result = "right"
''', "right", "wrong", id="filter_cannot_read_outer_later_target"),
    pytest.param('''x = 99
try:
    answer = {x: y for y in [0] for x in [x]}
    result = "wrong"
except UnboundLocalError:
    result = "right"
''', "right", "wrong", id="dictionary_comprehension_binding_is_local"),
    pytest.param('''try:
    try:
        raise ValueError()
    except (ValueError, 1):
        result = "accepted"
except TypeError:
    result = "type_error"
''', "type_error", "accepted", id="handler_tuple_validates_later_invalid_member"),
    pytest.param('''try:
    try:
        raise ValueError()
    except (ValueError, (TypeError,)):
        result = "accepted"
except TypeError:
    result = "type_error"
''', "type_error", "accepted", id="handler_tuple_does_not_admit_nested_tuple"),
]


@pytest.mark.parametrize("source, expected, wrong", CASES)
def test_no_false_conditional_certificate(source, expected, wrong):
    oracle = {}
    exec(source, oracle)
    assert oracle["result"] == expected
    derived = analyze_closed_source(source, ["result"])
    if derived["status"] == "closed_model_completed":
        assert derived["observations"]["result"] == expected
    forged = check_closed_effects(source, candidate(source, wrong))
    assert forged["status"] != "conditional_verified"
    assert forged["conditional_effects_verified"] == 0


@pytest.mark.parametrize("source, expected", [
    ('''x = [1, 2]
result = [x for x in x]
''', [1, 2]),
    ('''x = [1, 2]
class C:
    x = [3, 4]
    values = [x for x in x]
result = C.values
''', [3, 4]),
    ('''result = [[x for x in [y]] for y in [1, 2]]
''', [[1], [2]]),
    ('''try:
    raise ValueError()
except (TypeError, ValueError):
    result = "caught"
''', "caught"),
])
def test_supported_comprehension_and_handler_control_cases(source, expected):
    oracle = {}
    exec(source, oracle)
    assert oracle["result"] == expected
    result = check_closed_effects(source, candidate(source, expected))
    assert result["status"] == "conditional_verified", result


@pytest.mark.parametrize("field", ["source_sha256", "semantics_version"])
def test_metadata_requires_plain_strings_without_host_equality_hooks(field):
    source = "result = 1\n"
    accesses = []

    class EqualityTrap:
        def __eq__(self, other):
            accesses.append(other)
            return True

    proposal = candidate(source, 1)
    proposal[field] = EqualityTrap()
    result = check_closed_effects(source, proposal)
    assert result["status"] == "rejected"
    assert result["conditional_effects_verified"] == 0
    assert accesses == []


def test_candidate_keys_reject_string_subclasses_before_comparison():
    source = "result = 1\n"
    accesses = []

    class StringTrap(str):
        __hash__ = str.__hash__

        def __eq__(self, other):
            accesses.append(other)
            return str.__eq__(self, other)

    proposal = candidate(source, 1)
    value = proposal.pop("source_sha256")
    proposal[StringTrap("source_sha256")] = value
    result = check_closed_effects(source, proposal)
    assert result["status"] == "rejected"
    assert result["conditional_effects_verified"] == 0
    assert accesses == []


@pytest.mark.parametrize("source, wrong", [
    ("result = 0.0\n", -0.0),
    ("result = {'value': [-0.0]}\n", {"value": [0.0]}),
])
def test_exact_literal_projection_preserves_signed_zero(source, wrong):
    result = check_closed_effects(source, candidate(source, wrong))
    assert result["status"] == "rejected"
    assert result["conditional_effects_verified"] == 0
