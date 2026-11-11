import pytest

from benchmark.round8_protocol.metrics import macro_average, project_evidence, score_properties


def claim(identity, expected, property="local_parameter_flow", project="p"):
    return {"id": identity, "project_id": project, "property": property,
            "expected": expected, "adjudicated": True}


def test_selective_success_cannot_hide_unmeasured_positive_or_negative():
    gold = [claim("found", True), claim("not_run_positive", True), claim("not_run_negative", False)]
    result = score_properties(gold, [{"id": "found", "status": "resolved", "value": True}])
    assert result["conditional_precision"] == result["conditional_recall"] == 1
    assert result["positive_obligation_recovery"] == 0.5
    assert result["assessment_coverage"] == pytest.approx(1 / 3)
    assert result["unmeasured_positive"] == result["unmeasured_negative"] == 1
    assert result["vulnerability_precision"] is None


@pytest.mark.parametrize("status", ["unknown", "unsupported", "not_run", "timeout", "error", "incomplete"])
def test_every_unmeasured_state_retains_denominator(status):
    result = score_properties([claim("p", True), claim("n", False)],
        [{"id": "p", "status": status}, {"id": "n", "status": status}])
    assert result["declared_tasks"] == 2
    assert result["unmeasured_positive"] == result["unmeasured_negative"] == 1
    assert result["positive_obligation_recovery"] == 0
    assert result["conditional_precision"] is result["conditional_recall"] is None


def test_ast_path_is_not_a_solver_path():
    with pytest.raises(ValueError, match="AST path"):
        score_properties([claim("edge", True, "solver_call_path")],
            [{"id": "edge", "status": "resolved", "value": True,
              "evidence_origin": "ast", "solver_completed": True}])


def test_partial_solver_cannot_validate_positive_or_negative_path():
    for value in (False, True):
        with pytest.raises(ValueError, match="partial solver"):
            score_properties([claim("edge", value, "solver_call_path")],
                [{"id": "edge", "status": "resolved", "value": value,
                  "evidence_origin": "solver", "solver_completed": False}])


def test_scanner_inventory_does_not_create_vulnerability_labels():
    result = score_properties([claim("sink", True, "sink_inventory")],
        [{"id": "sink", "status": "resolved", "value": True}])
    assert result["tp"] == 1
    assert result["unit"] == "manually_adjudicated_bounded_property"
    assert result["vulnerability_precision"] is None


@pytest.mark.parametrize("extra", [
    {"expected": 1}, {"adjudicated": False}, {"property": "vulnerability"},
])
def test_unadjudicated_or_retyped_gold_is_rejected(extra):
    row = claim("a", True)
    row.update(extra)
    with pytest.raises(ValueError):
        score_properties([row], [])


def test_duplicate_prediction_or_gold_cannot_improve_counts():
    with pytest.raises(ValueError):
        score_properties([claim("a", True), claim("a", False)], [])
    row = {"id": "a", "status": "resolved", "value": True}
    with pytest.raises(ValueError):
        score_properties([claim("a", True)], [row, row])


def test_project_download_and_inventory_are_not_solver_completion():
    result = project_evidence([{"project_id": "a", "category": "web", "acquired": True,
        "source_verified": True, "static_attempted": False,
        "static_report": {"status": "not_run", "coverage": {"discovered_python_files": 1000}}}])
    assert result["unique_declared_projects"] == result["acquired"] == result["inventory_measured"] == 1
    assert result["frontend_complete"] == result["solver_complete"] == result["static_attempted"] == 0


def test_failed_project_and_partial_frontend_remain_in_category_denominator():
    rows = [{"project_id": "a", "category": "web", "static_attempted": True,
             "static_report": {"status": "timeout", "coverage": {"discovered_python_files": 5,
                 "parsed_modules": 5, "lowered_modules": 4}}},
            {"project_id": "b", "category": "web", "static_report": {"status": "not_run"}}]
    report = project_evidence(rows)
    assert report["categories"]["web"]["declared_projects"] == 2
    assert report["frontend_complete"] == report["solver_complete"] == 0


def test_configuration_repetitions_do_not_multiply_projects():
    row = {"project_id": "a", "category": "infra", "static_attempted": True,
           "static_report": {"status": "incomplete"}}
    report = project_evidence([row, row, row])
    assert report["unique_declared_projects"] == 1 and report["trials"] == 3
    assert report["static_attempted"] == 1 and report["solver_complete"] == 0


def test_macro_average_keeps_missing_projects_visible():
    result = macro_average([{"project_id": "a", "recall": 1}], "recall", declared_projects=["a", "b"])
    assert result["available_project_macro"] == 1
    assert result["complete_project_macro"] is None
    assert result["measured_projects"] == 1 and result["unmeasured_projects"] == ["b"]


def test_macro_rejects_configuration_rows_masquerading_as_projects():
    with pytest.raises(ValueError):
        macro_average([{"project_id": "a", "m": 1}, {"project_id": "a", "m": 0}],
                      "m", declared_projects=["a"])


def full_trial():
    return {"project_id": "p", "category": "web", "source_verified": True,
        "static_attempted": True, "static_report": {"status": "completed_with_residuals",
        "implementation_stable": True, "source_snapshot_stable": True,
        "solver_completed": True, "unresolved_entries": [], "frontend_errors": [],
        "coverage": {"discovered_python_files": 2, "parsed_modules": 2, "lowered_modules": 2,
                     "translated_modules": 2, "unprocessed_files": 0}}}


def test_solver_fixpoint_with_unresolved_entry_is_not_complete_project():
    row = full_trial()
    assert project_evidence([row])["solver_complete"] == 1
    row["static_report"]["unresolved_entries"] = ["p.missing_entry"]
    assert project_evidence([row])["solver_complete"] == 0
    row["static_report"].pop("unresolved_entries")
    assert project_evidence([row])["solver_complete"] == 0


def test_only_processed_subset_cannot_report_complete_project():
    row = full_trial()
    row["static_report"]["coverage"]["translated_modules"] = 1
    row["static_report"]["coverage"]["unprocessed_files"] = 1
    assert project_evidence([row])["solver_complete"] == 0


def test_stable_modified_sources_cannot_inherit_pinned_project_completion():
    row = full_trial()
    row["source_verified"] = False
    report = project_evidence([row])
    assert report["source_verified"] == report["frontend_complete"] == report["solver_complete"] == 0
