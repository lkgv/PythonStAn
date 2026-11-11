"""Finite relational contracts: independent CPython witnesses are test-only."""
from copy import deepcopy
from dataclasses import replace
import itertools

import pytest

from pythonstan.analysis.pointer.ai.certified import finite_entry as family
from pythonstan.analysis.pointer.ai.certified.closed_heap_effects import Budget

SOURCE = '''
class Registry:
    def __init__(self):
        self.routes = {}
    def register(self, name, callback):
        self.routes[name] = callback
    def dispatch(self, name):
        return self.routes[name]()
def original():
    events.append("original")
    return "original"
def override():
    events.append("override")
    return "override"
events = []
def request(use_override, fail, /, *, route):
    registry = Registry()
    registry.register(route, original)
    if use_override:
        registry.register(route, override)
    try:
        if fail:
            raise ValueError("bad request")
        result = registry.dispatch(route)
    except ValueError:
        result = "recovered"
    finally:
        events.append("cleanup")
    return result
'''
SPEC = family.FiniteEntrySpec("request", ((False, True), (False, True)), (("route", ("/a", "/b")),), ("events",))


def claims(result):
    return {"schema_version": 1,
            **{key: result[key] for key in ("semantics_version", "model_semantics_version", "source_sha256", "entry_spec_sha256")},
            "rows": {row["variant_id"]: row["observations"] for row in result["rows"]}}


def test_eight_entry_rows_retain_correlated_dispatch_and_cleanup():
    result = family.analyze_finite_entry(SOURCE, SPEC)
    assert result["status"] == "finite_model_completed", result
    assert result["declared_variants"] == result["covered_variants"] == 8
    assert result["verified_variants"] == 0
    for row, (use_override, fail, route) in zip(result["rows"], itertools.product((False, True), (False, True), ("/a", "/b"))):
        # Every concrete witness has a fresh namespace, matching the explicit
        # family contract. This does not execute source in production.
        namespace = {}
        exec(SOURCE, namespace)
        returned = namespace["request"](use_override, fail, route=route)
        assert row["observations"] == {"return": returned, "globals": {"events": namespace["events"]}}
        if fail:
            assert row["observations"]["globals"]["events"] == ["cleanup"]
        else:
            selected = "override" if use_override else "original"
            assert row["observations"] == {"return": selected, "globals": {"events": [selected, "cleanup"]}}
    checked = family.check_finite_entry(SOURCE, SPEC, claims(result))
    assert checked["status"] == "conditional_family_verified"
    assert checked["verified_variants"] == 8
    assert checked["residual"] and not checked["kcfa_results_certified"]
    assert not checked["full_python_soundness_proved"]


def test_candidate_cannot_merge_input_correlations_or_omit_an_input_row():
    result = family.analyze_finite_entry(SOURCE, SPEC)
    candidate = claims(result)
    keys = list(candidate["rows"])
    candidate["rows"][keys[0]], candidate["rows"][keys[2]] = candidate["rows"][keys[2]], candidate["rows"][keys[0]]
    assert family.check_finite_entry(SOURCE, SPEC, candidate)["status"] == "rejected"
    candidate = claims(result)
    candidate["rows"].pop(keys[-1])
    assert family.check_finite_entry(SOURCE, SPEC, candidate)["status"] == "rejected"


@pytest.mark.parametrize("key", ["source_sha256", "entry_spec_sha256", "semantics_version", "model_semantics_version"])
def test_candidate_cannot_change_binding(key):
    source = "def entry(flag):\n    return flag\n"
    spec = family.FiniteEntrySpec("entry", ((False, True),))
    result = family.analyze_finite_entry(source, spec)
    candidate = claims(result)
    candidate[key] = "forged"
    assert family.check_finite_entry(source, spec, candidate)["status"] == "rejected"


def test_domain_extension_requires_new_coverage():
    source = "def entry(flag):\n    return flag\n"
    first = family.FiniteEntrySpec("entry", ((False,),))
    second = replace(first, positional_domains=((False, True),))
    candidate = claims(family.analyze_finite_entry(source, first))
    result = family.check_finite_entry(source, second, candidate)
    assert result["status"] == "rejected" and result["verified_variants"] == 0


def test_typed_input_identity_and_typed_claim_values():
    source = "def entry(value):\n    return value\n"
    spec = family.FiniteEntrySpec("entry", ((False, 0, 0.0, -0.0, True, 1),))
    result = family.analyze_finite_entry(source, spec)
    assert result["status"] == "finite_model_completed"
    assert len({row["variant_id"] for row in result["rows"]}) == 6
    candidate = claims(result)
    candidate["rows"][result["rows"][0]["variant_id"]]["return"] = 0
    assert family.check_finite_entry(source, spec, candidate)["status"] == "rejected"
    candidate = claims(family.analyze_finite_entry(source, spec))
    candidate["rows"][result["rows"][3]["variant_id"]]["return"] = 0.0
    assert family.check_finite_entry(source, spec, candidate)["status"] == "rejected"


def test_unsupported_branch_prevents_whole_family_verification():
    source = "def entry(flag):\n    if flag:\n        return eval('1')\n    return 0\n"
    spec = family.FiniteEntrySpec("entry", ((False, True),))
    result = family.analyze_finite_entry(source, spec)
    assert result["status"] == "residual" and result["covered_variants"] == 1
    checked = family.check_finite_entry(source, spec, claims(result))
    assert checked["status"] == "residual" and checked["verified_variants"] == 0


def test_escaping_exception_is_not_a_complete_normal_return_contract():
    source = "def entry(flag):\n    if flag:\n        raise ValueError\n    return 0\n"
    spec = family.FiniteEntrySpec("entry", ((False, True),))
    result = family.analyze_finite_entry(source, spec)
    assert result["status"] == "residual"
    assert [row["status"] for row in result["rows"]] == ["closed_model_completed", "model_exception"]


def test_variant_budget_checked_before_any_model_run(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("model must not run for an oversized product")
    monkeypatch.setattr(family.heap_model, "analyze_closed_source", forbidden)
    spec = family.FiniteEntrySpec("entry", (tuple(range(100)), tuple(range(100))))
    result = family.analyze_finite_entry("def entry(a, b):\n    return 1\n", spec, budget=family.FamilyBudget(max_variants=20))
    assert result["status"] == "unknown" and result["covered_variants"] == 0 and not result["rows"]
    assert result["declared_variants"] == 10_000


def test_each_budget_exhausted_branch_retains_other_rows_without_verifying():
    source = "def entry(flag):\n    if flag:\n        while True:\n            pass\n    return 1\n"
    spec = family.FiniteEntrySpec("entry", ((False, True),))
    budget = family.FamilyBudget(per_variant=Budget(max_steps=40))
    result = family.analyze_finite_entry(source, spec, budget=budget)
    assert result["status"] == "residual" and result["covered_variants"] == 1
    assert result["rows"][1]["status"] == "unknown"
    assert family.check_finite_entry(source, spec, claims(result), budget=budget)["verified_variants"] == 0


@pytest.mark.parametrize("value", [[], {}, object(), float("inf"), float("nan")])
def test_opaque_or_mutable_inputs_are_rejected(value):
    result = family.analyze_finite_entry("def entry(x):\n    return 1\n", family.FiniteEntrySpec("entry", ((value,),)))
    assert result["status"] == "unsupported" and not result["rows"]


@pytest.mark.parametrize("domains", [((),), ((1, 1),), ([1, 2],)])
def test_empty_duplicate_or_mutable_domains_rejected(domains):
    result = family.analyze_finite_entry("def entry(x):\n    return x\n", family.FiniteEntrySpec("entry", domains))
    assert result["status"] == "unsupported"


def test_reserved_return_sink_does_not_overwrite_source_binding():
    source = "_finite_entry_return = 'kept'\ndef entry():\n    return _finite_entry_return\n"
    result = family.analyze_finite_entry(source, family.FiniteEntrySpec("entry", observed_globals=("_finite_entry_return",)))
    assert result["status"] == "finite_model_completed"
    assert result["rows"][0]["observations"] == {"return": "kept", "globals": {"_finite_entry_return": "kept"}}


def test_inputs_cannot_inject_source_through_strings():
    text = "'); raise ValueError(); #"
    source = "def entry(value):\n    return value\n"
    result = family.analyze_finite_entry(source, family.FiniteEntrySpec("entry", ((text,),)))
    assert result["rows"][0]["observations"]["return"] == text


def test_production_does_not_execute_target_source(monkeypatch):
    import builtins
    def forbidden(*args, **kwargs):
        raise AssertionError("target exec/eval forbidden")
    monkeypatch.setattr(builtins, "exec", forbidden)
    monkeypatch.setattr(builtins, "eval", forbidden)
    result = family.analyze_finite_entry("def entry(x):\n    return x\n", family.FiniteEntrySpec("entry", ((True, False),)))
    assert result["status"] == "finite_model_completed"


def test_source_change_during_family_analysis_prevents_verification(monkeypatch):
    tokens = iter(("before", "after"))
    monkeypatch.setattr(family, "_implementation_digest", lambda: next(tokens))
    result = family.analyze_finite_entry("def entry():\n    return 1\n", family.FiniteEntrySpec("entry"))
    assert result["status"] == "unknown" and not result["implementation_stable"]


def test_literal_and_candidate_budgets_are_not_certificates():
    source = "def entry(x):\n    return x\n"
    spec = family.FiniteEntrySpec("entry", (("long",),))
    result = family.analyze_finite_entry(source, spec, budget=family.FamilyBudget(max_string_chars=2))
    assert result["status"] == "unknown"
    normal = family.analyze_finite_entry(source, spec)
    checked = family.check_finite_entry(source, spec, claims(normal), budget=family.FamilyBudget(max_candidate_bytes=10))
    assert checked["status"] == "unknown" and checked["verified_variants"] == 0


def test_candidate_hostile_key_cannot_run_equality_hooks():
    calls = []
    class Hostile(str):
        __hash__ = str.__hash__
        def __eq__(self, other):
            calls.append(other)
            return super().__eq__(other)
    source = "def entry():\n    return 1\n"
    spec = family.FiniteEntrySpec("entry")
    candidate = claims(family.analyze_finite_entry(source, spec))
    candidate[Hostile("source_sha256")] = candidate.pop("source_sha256")
    calls.clear()
    assert family.check_finite_entry(source, spec, candidate)["status"] == "rejected"
    assert not calls


def test_entry_requires_a_source_declaration_not_an_implicit_builtin():
    result = family.analyze_finite_entry("", family.FiniteEntrySpec("len", (((1, 2),),)))
    assert result["status"] == "unsupported"


def test_distinct_tuple_literal_identity_cannot_silently_assume_no_constant_pooling():
    source = "def entry(a, b):\n    return a is b\n"
    namespace = {}
    exec(source + "\nresult = entry((1, 2), (1, 2))\n", namespace)
    assert namespace["result"] is True
    result = family.analyze_finite_entry(source, family.FiniteEntrySpec("entry", (((1, 2),), ((1, 2),))))
    # The closed heap model cannot infer whether Python pools immutable literal
    # allocations. It must reject that observation or derive the true result.
    assert (result["status"] != "finite_model_completed"
            or result["rows"][0]["observations"]["return"] is True)


def test_finite_entry_cli_runs_declared_default_relation(tmp_path):
    import json
    from pathlib import Path
    import subprocess
    import sys
    root = Path(__file__).resolve().parents[2]
    output = tmp_path / "family.json"
    run = subprocess.run([sys.executable, "scripts/check_finite_entry.py", "--output", str(output)], cwd=root, capture_output=True, text=True, timeout=20)
    payload = json.loads(output.read_text())
    assert run.returncode == 0, run.stderr + run.stdout
    assert payload["status"] == "conditional_family_verified"
    assert payload["verified_variants"] == 8


def test_cli_product_budget_reports_unknown_and_nonzero_exit(tmp_path):
    import json
    from pathlib import Path
    import subprocess
    import sys
    root = Path(__file__).resolve().parents[2]
    run = subprocess.run([sys.executable, "scripts/check_finite_entry.py", "--derive-only", "--max-variants", "1"], cwd=root, capture_output=True, text=True, timeout=20)
    payload = json.loads(run.stdout)
    assert run.returncode == 2 and payload["status"] == "unknown"
    assert payload["declared_variants"] == 8
