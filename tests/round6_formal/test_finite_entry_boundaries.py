"""Independent finite-domain coverage and untrusted-candidate checks."""
import copy

import pytest

from pythonstan.analysis.pointer.ai.certified.finite_entry import (
    FamilyBudget, FiniteEntrySpec, analyze_finite_entry, check_finite_entry,
)


SOURCE = '''events = []
def entry(flag):
    events.append(flag)
    return list(events)
'''
SPEC = FiniteEntrySpec("entry", positional_domains=((True, False),))


def claim(report):
    return {key: report[key] for key in (
        "schema_version", "semantics_version", "model_semantics_version",
        "source_sha256", "entry_spec_sha256",
    )} | {"rows": {row["variant_id"]: row["observations"] for row in report["rows"]}}


def test_declared_domain_relation_preserves_input_correlation_and_fresh_heaps():
    report = analyze_finite_entry(SOURCE, SPEC)
    assert report["status"] == "finite_model_completed", report
    assert report["declared_variants"] == report["covered_variants"] == 2
    assert [row["observations"] for row in report["rows"]] == [
        {"return": [True], "globals": {}}, {"return": [False], "globals": {}},
    ]
    checked = check_finite_entry(SOURCE, SPEC, claim(report))
    assert checked["status"] == "conditional_family_verified"
    assert checked["verified_variants"] == 2
    assert checked["full_python_soundness_proved"] is False
    assert checked["kcfa_results_certified"] is False


@pytest.mark.parametrize("change", ["drop_row", "change_bool_to_int", "wrong_source", "wrong_spec"])
def test_candidate_cannot_weaken_domain_or_type_bound_claim(change):
    report = analyze_finite_entry(SOURCE, SPEC)
    candidate = copy.deepcopy(claim(report))
    key = next(iter(candidate["rows"]))
    if change == "drop_row":
        del candidate["rows"][key]
    elif change == "change_bool_to_int":
        candidate["rows"][key]["return"] = [1]
    elif change == "wrong_source":
        candidate["source_sha256"] = "0" * 64
    else:
        candidate["entry_spec_sha256"] = "0" * 64
    checked = check_finite_entry(SOURCE, SPEC, candidate)
    assert checked["status"] == "rejected", checked
    assert checked["verified_variants"] == 0


def test_mixed_completed_and_unsupported_rows_cannot_verify_family():
    source = '''def entry(flag):
    if flag:
        return 1
    return eval("1")
'''
    report = analyze_finite_entry(source, SPEC)
    assert report["declared_variants"] == 2
    assert report["covered_variants"] == 1
    checked = check_finite_entry(source, SPEC, claim(report))
    assert checked["status"] != "conditional_family_verified"
    assert checked["verified_variants"] == 0


def test_variant_budget_preserves_uncovered_family_status():
    report = analyze_finite_entry(SOURCE, SPEC, budget=FamilyBudget(max_variants=1))
    assert report["status"] == "unknown"
    assert report["covered_variants"] == report["verified_variants"] == 0
    assert report["rows"] == []


def test_candidate_key_subclass_cannot_execute_equality_hook():
    report = analyze_finite_entry(SOURCE, SPEC)
    candidate = claim(report)
    calls = []

    class Key(str):
        __hash__ = str.__hash__

        def __eq__(self, other):
            calls.append(other)
            return str.__eq__(self, other)

    candidate[Key("source_sha256")] = candidate.pop("source_sha256")
    checked = check_finite_entry(SOURCE, SPEC, candidate)
    assert checked["status"] == "rejected"
    assert calls == []


def test_non_source_builtin_cannot_be_promoted_to_named_source_entry():
    spec = FiniteEntrySpec("len", positional_domains=(((1, 2),),))
    report = analyze_finite_entry("", spec)
    assert report["status"] != "finite_model_completed"
    assert report["verified_variants"] == 0
