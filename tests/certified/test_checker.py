"""Adversarial checks of the source/candidate trust boundary."""

from dataclasses import FrozenInstanceError

import pytest

from pythonstan.analysis.pointer.ai.certified import (
    InferenceUnknown, UnsupportedSource, candidate_json_schema,
    certificate_to_dict, check_source, infer_candidate, parse_candidate,
)


def checked(source):
    candidate = infer_candidate(source)
    result = check_source(source, candidate)
    assert result.certified, result.diagnostics
    return result.certificate.summary_map()


def test_recursive_prefix_survives_absence_of_any_exit():
    source = "def f():\n    g()\ndef g():\n    f()\n"
    summaries = checked(source)
    for summary in summaries.values():
        assert summary.events == {"call:f", "call:g"}
        assert not summary.normal_exit
        assert not summary.exceptions


def test_effect_before_nonterminating_loop_is_retained():
    source = "def effect():\n    pass\ndef f():\n    effect()\n    while True:\n        pass\n"
    summary = checked(source)["f"]
    assert summary.events == {"call:effect"}
    assert not summary.normal_exit


def test_candidate_cannot_delete_recursive_prefix():
    source = "def f():\n    f()\n"
    wire = certificate_to_dict(infer_candidate(source))
    wire["summaries"]["f"]["events"] = []
    result = check_source(source, wire)
    assert result.status == "rejected"
    assert "missing prefix" in " ".join(result.diagnostics)


def test_candidate_cannot_delete_exception_exit():
    source = "def f():\n    raise ValueError('x')\n"
    wire = certificate_to_dict(infer_candidate(source))
    wire["summaries"]["f"]["exceptions"] = []
    assert check_source(source, wire).status == "rejected"


def test_candidate_cannot_invent_guard_to_ignore_source():
    source = "def f():\n    return 1\n"
    wire = certificate_to_dict(infer_candidate(source))
    wire["guard"] = False
    assert check_source(source, wire).status == "rejected"


def test_source_identity_and_exact_function_coverage():
    source = "def f():\n    pass\ndef g():\n    pass\n"
    wire = certificate_to_dict(infer_candidate(source))
    assert check_source(source + "\n", wire).status == "rejected"
    del wire["summaries"]["g"]
    assert check_source(source, wire).status == "rejected"


@pytest.mark.parametrize("body", [
    "return x + 1", "return x.attr", "return x[0]", "return unknown(x)",
    "yield x", "if x:\n        return x", "try:\n        pass\n    except x:\n        pass",
    "for y in x:\n        pass", "assert True", "import os", "return [x]",
])
def test_source_coverage_rejects_unmodeled_semantics(body):
    source = "def f(x):\n    " + body + "\n"
    with pytest.raises(UnsupportedSource):
        infer_candidate(source)


@pytest.mark.parametrize("source", [
    "def f():\n    pass\nf = 1\n",
    "@decorator\ndef f():\n    pass\n",
    "def f(x=effect()):\n    pass\n",
    "def f(x: effect()):\n    pass\n",
    "def f():\n    pass\ndef g(f):\n    f()\n",
    "def f():\n    f()\n    f = None\n",
    "def f(x):\n    return x\ndef g():\n    f()\n",
    "def ValueError():\n    pass\n",
    "def f(ValueError):\n    raise ValueError()\n",
    "def __builtins__():\n    pass\ndef f():\n    raise ValueError()\n",
    "def __debug__():\n    pass\n",
    "def f(__debug__):\n    pass\n",
    "def f():\n    __debug__ = None\n",
])
def test_definition_and_lookup_effects_cannot_be_hidden(source):
    with pytest.raises(UnsupportedSource):
        infer_candidate(source)


def test_unreachable_syntax_still_requires_source_coverage():
    source = "def f():\n    return 1\n    unknown()\n"
    with pytest.raises(UnsupportedSource):
        infer_candidate(source)


def test_unbound_locals_are_exceptional_not_implicit_top_values():
    source = "def f():\n    return x\n    x = None\n"
    summary = checked(source)["f"]
    assert not summary.normal_exit
    assert summary.exceptions == {"UnboundLocalError"}
    assert summary.events == {"raise:UnboundLocalError"}


def test_conditional_binding_covers_both_normal_and_unbound_paths():
    source = "def f(flag):\n    if flag is None:\n        x = 1\n    return x\n"
    summary = checked(source)["f"]
    assert summary.normal_exit
    assert summary.exceptions == {"UnboundLocalError"}


def test_argument_evaluation_order_keeps_only_reachable_calls():
    source = "def die():\n    raise ValueError()\ndef later():\n    pass\ndef target(x, y):\n    pass\ndef f():\n    target(die(), later())\n"
    summary = checked(source)["f"]
    assert summary.events == {"call:die", "raise:ValueError"}
    assert summary.exceptions == {"ValueError"}
    assert not summary.normal_exit


def test_short_circuit_does_not_evaluate_unbound_local():
    source = "def f():\n    if True or x is None:\n        return 1\n    x = None\n"
    summary = checked(source)["f"]
    assert summary.normal_exit
    assert not summary.exceptions


def test_chained_identity_comparison_short_circuits():
    source = "def f():\n    if None is True is x:\n        pass\n    return None\n    x = None\n"
    summary = checked(source)["f"]
    assert summary.normal_exit
    assert not summary.exceptions


def test_while_break_does_not_run_else():
    source = "def f():\n    while True:\n        break\n    else:\n        raise ValueError()\n    return None\n"
    summary = checked(source)["f"]
    assert summary.normal_exit
    assert not summary.exceptions


def test_while_continue_cannot_fall_through():
    source = "def f():\n    while True:\n        continue\n    return None\n"
    assert not checked(source)["f"].normal_exit


def test_normal_exit_cannot_be_forged_away_through_mutual_recursion():
    source = "def f(x):\n    if x is None:\n        return 1\n    return g(x)\ndef g(x):\n    return f(x)\n"
    wire = certificate_to_dict(infer_candidate(source))
    for summary in wire["summaries"].values():
        summary["normal_exit"] = False
    assert check_source(source, wire).status == "rejected"


def test_unknown_budget_never_means_proof():
    source = "def f():\n    pass\n"
    candidate = infer_candidate(source)
    with pytest.raises(InferenceUnknown):
        infer_candidate(source, max_iterations=0)
    result = check_source(source, candidate, max_iterations=0)
    assert result.status == "unknown"
    assert not result.certified
    assert result.certificate is None


def test_accepted_snapshot_does_not_alias_mutable_candidate():
    source = "def f():\n    pass\n"
    wire = certificate_to_dict(infer_candidate(source))
    result = check_source(source, wire)
    wire["summaries"]["f"]["normal_exit"] = False
    assert result.certificate.summary_map()["f"].normal_exit
    with pytest.raises(FrozenInstanceError):
        result.certificate.source_sha256 = "0" * 64


def test_strict_json_shape_and_boolean_types():
    source = "def f():\n    pass\n"
    wire = certificate_to_dict(infer_candidate(source))
    wire["summaries"]["f"]["normal_exit"] = 1
    with pytest.raises(ValueError):
        parse_candidate(wire)
    schema = candidate_json_schema(["f"])
    assert schema["properties"]["summaries"]["additionalProperties"] is False


def test_invalid_unicode_source_fails_closed():
    assert check_source("\ud800", {}).status == "unsupported"
    with pytest.raises(UnsupportedSource):
        infer_candidate("\ud800")
