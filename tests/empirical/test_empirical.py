"""Evaluate actual engines while keeping benchmark success distinct from proof."""
import copy
import subprocess

import pytest

from benchmark.empirical.runner import ROOT, compare_edges, read_manifest, read_source, run_suite, run_worker


CASES = read_manifest()["cases"]


def case(name):
    return next(c for c in CASES if c["id"] == name)


def test_frozen_sources_and_original_framework_bodies_are_verified():
    assert len(CASES) == 7
    assert sum(c["cohort"] == "framework_slice" for c in CASES) == 3
    assert sum(len(c["required_edges"]) for c in CASES) == 26
    assert sum(len(c["forbidden_edges"]) for c in CASES) == 5
    for item in CASES:
        assert read_source(item)
        assert item["review_notes"]
    damaged = copy.deepcopy(CASES[0])
    damaged["sha256"] = "0" * 64
    with pytest.raises(ValueError, match="hash mismatch"):
        read_source(damaged)


def test_no_edges_or_unreviewed_extra_targets_cannot_count_as_success():
    item = case("flask_dispatch_registry")
    empty = compare_edges(item, [])
    assert not empty["all_reviewed_call_obligations_met"]
    assert empty["recovered_count"] == 0
    assert len(empty["missing_required"]) == 6
    broad = compare_edges(item, item["required_edges"] + item["forbidden_edges"])
    assert broad["recovered_count"] == 6
    assert not broad["all_reviewed_call_obligations_met"]
    assert broad["violated_negative"]
    # No seeded entrypoint / empty oracle must not become vacuous success.
    assert not compare_edges(case("starlette_original_module"),[])["all_reviewed_call_obligations_met"]


def test_actual_pipeline_reports_calls_and_configuration_for_both_modes():
    item = case("configured_reflective_dispatch")
    for enabled in (False, True):
        result = run_worker(item,"static",ai_enabled=enabled)
        assert result["status"] == "completed", result
        assert result["analysis_executed"]
        assert result["statistics"]["iterations"] > 0
        assert result["config"]["enable_ai_intrinsics"] is enabled
        assert result["call_obligations"]["recovered_count"] == 2
        assert not result["call_obligations"]["violated_negative"]
        assert result["effect_obligations_discharged_by_kcfa"] == 0
        assert not result["soundness_proved"]


def test_runtime_effect_checks_are_independent_bounded_observations():
    for item in CASES:
        if item["cohort"] == "unmodeled_import_control":
            continue
        result = run_worker(item,"runtime")
        assert result["status"] == "completed", result
        assert result["exception_matches"], (item["id"],result)
        assert all(effect["matches"] for effect in result["effect_observations"]), result
        assert result["observation_kind"] == "one_controlled_execution_lower_bound_only"
        assert not result["soundness_evidence"]


def test_certified_cleanup_checks_exact_prefix_and_exception_summary():
    result = run_worker(case("exception_cleanup"),"certified")
    assert result["status"] == "accepted", result
    assert result["reviewed_effect_summary_matches"]
    assert "final harness invocation removed" in result["projection"]
    assert result["summaries"]["subject"]["events"] == ["call:cleanup","call:work","raise:ValueError"]


def test_iteration_exhaustion_retains_denominator_and_is_not_useful():
    report = run_suite(case_ids=["configured_reflective_dispatch"],max_iterations=1,include_runtime=False)
    for mode in ("baseline","current"):
        result = report["rows"][0][mode]
        assert result["status"] == "incomplete", result
        assert not result["useful_reviewed_call_result"]
        assert result["statistics"]["iteration_limit_reached"] == 1
        assert report["totals"][mode]["required_call_obligations"] == 2
    assert report["whole_app_cases_analyzed"] == 0


def test_timeout_is_explicit_and_never_credits_obligations(monkeypatch):
    def timeout(*args,**kwargs):
        raise subprocess.TimeoutExpired(args[0],kwargs["timeout"])
    monkeypatch.setattr(subprocess,"run",timeout)
    report = run_suite(case_ids=["configured_reflective_dispatch"],include_runtime=False)
    for mode in ("baseline","current"):
        totals = report["totals"][mode]
        assert totals["statuses"] == {"timeout":1}
        assert totals["required_call_obligations"] == 2
        assert totals["required_calls_in_failed_rows"] == 2
        assert totals["recovered_required_calls"] == 0
        assert totals["useful_reviewed_call_cases"] == 0


def test_unprocessed_import_control_has_no_vacuous_usefulness():
    result = run_worker(case("starlette_original_module"),"static",ai_enabled=False)
    assert result["status"] == "completed", result
    assert set(result["unprocessed_imports"]) == {"sys","typing","typing_extensions","starlette._utils","starlette.concurrency"}
    assert result["call_obligations"]["required_count"] == 0
    assert result["call_obligations"]["reviewed_obligation_recovery"] is None
    assert not result["useful_reviewed_call_result"]


def test_unexpected_runtime_result_fails_harness_validation(monkeypatch):
    def fake_worker(item,kind,**options):
        if kind == "runtime":
            return {"status":"completed","exception_matches":False,"effect_observations":[]}
        return {"status":"unsupported"}
    monkeypatch.setattr("benchmark.empirical.runner.run_worker",fake_worker)
    result = run_suite(case_ids=["configured_reflective_dispatch"])
    assert not result["harness_validation"]["passed"]
    assert result["harness_validation"]["runtime_mismatch_cases"] == ["configured_reflective_dispatch"]


def test_unknown_case_cannot_silently_run_an_empty_suite():
    with pytest.raises(ValueError,match="Unknown empirical case"):
        run_suite(case_ids=["typo"])


def test_separate_holdout_sources_and_gold_preserve_development_denominator():
    holdouts = read_manifest(ROOT/"holdouts/manifest.json")["cases"]
    assert len(holdouts) == 4
    assert sum(c["cohort"] == "derived_metamorphic_holdout" for c in holdouts) == 3
    assert sum(c["cohort"] == "new_upstream_method_holdout" for c in holdouts) == 1
    assert sum(len(c["required_edges"]) for c in holdouts) == 28
    assert sum(len(c["forbidden_edges"]) for c in holdouts) == 5
    for item in holdouts:
        assert read_source(item)
    assert len(read_manifest()["cases"]) == 7
    assert sum(len(c["required_edges"]) for c in read_manifest()["cases"]) == 26


def test_actual_holdout_method_pairs_and_runtime_effect_oracle():
    report = run_suite(manifest_path=ROOT/"holdouts/manifest.json")
    assert report["harness_validation"]["passed"], report["harness_validation"]
    assert report["harness_validation"]["runtime_cases"] == 4
    for mode in ("baseline","current"):
        assert report["totals"][mode]["recovered_required_calls"] == 28
        assert report["totals"][mode]["violated_negatives"] == 0
        assert report["totals"][mode]["useful_reviewed_call_cases"] == 4
    assert all(row["certified"]["status"] == "unsupported" for row in report["rows"])
