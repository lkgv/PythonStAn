import copy

import pytest

from benchmark.round8_protocol.metrics import aggregate_security_tasks
from pythonstan.analysis.security.tasks import evaluate_tasks


def task(identity="positive", expected=True, project="p"):
    return {"id": identity, "project_id": project, "source": "m.py", "source_sha256": "frozen",
            "qualname": "m.run", "line": 4, "adjudicated": True, "expected": expected,
            "assertion": {"kind": "resolved_sink", "symbol": "subprocess.run"}}


def report():
    return {"inventory": [{"source": "m.py", "status": "parsed", "sha256": "frozen"}],
            "functions": [{"source": "m.py", "qualified_name": "m.run"}],
            "findings": [{"source": "m.py", "function": "m.run", "line": 4,
                          "resolution": "source_import_binding", "resolved_symbol": "subprocess.run"}]}


@pytest.mark.parametrize("mutation", ["hash", "parse", "function"])
def test_source_or_function_binding_failure_is_unmeasured_not_false_negative(mutation):
    observed = report()
    if mutation == "hash":
        observed["inventory"][0]["sha256"] = "changed"
    elif mutation == "parse":
        observed["inventory"][0]["status"] = "parse_error"
    else:
        observed["functions"][0]["qualified_name"] = "m.different"
    result = evaluate_tasks([task()], observed)
    assert result["declared"] == result["unmeasured"] == 1
    assert result["measured"] == result["false_property_negatives"] == 0
    assert result["property_recall_among_measured"] is None


def test_exact_site_binding_prevents_neighboring_operation_satisfying_task():
    observed = report()
    observed["findings"][0]["line"] = 5
    result = evaluate_tasks([task()], observed)
    assert result["false_property_negatives"] == 1
    assert result["true_property_positives"] == 0


def test_literal_true_policy_does_not_accept_integer_one():
    gold = task()
    gold["assertion"] = {"kind": "literal_policy", "keyword": "shell", "value": True}
    observed = report()
    observed["findings"][0]["policy_evidence"] = [{"name": "shell", "status": "literal", "value": 1}]
    result = evaluate_tasks([gold], observed)
    assert result["false_property_negatives"] == 1


def test_adapter_retains_missing_projects_and_all_positive_denominator():
    gold = [task(), task("missing_positive", True, "q"), task("missing_negative", False, "q")]
    predicted = [{"id": "positive", "status": "measured", "property_prediction": True}]
    result = aggregate_security_tasks(predicted, gold, {
        "p": {"status": "inventory_completed_with_residuals", "category": "web"},
        "q": {"status": "timeout", "category": "web"},
        "no_gold": {"status": "inventory_completed_with_residuals", "category": "infra"}})
    assert result["declared_projects"] == 3 and result["projects_with_gold"] == 2
    assert result["projects_without_gold"] == ["no_gold"]
    assert result["conditional_recall"] == result["conditional_precision"] == 1
    assert result["positive_obligation_recovery"] == 0.5
    assert result["unmeasured_positive"] == result["unmeasured_negative"] == 1
    assert result["assessment_coverage"] == pytest.approx(1 / 3)
    assert result["macros"]["conditional_recall"]["complete_project_macro"] is None
    assert result["macros"]["positive_obligation_recovery"]["complete_project_macro"] == 0.5
    assert result["categories"]["infra"]["projects_with_gold"] == 0
    assert result["categories"]["infra"]["macros"]["assessment_coverage"]["available_project_macro"] is None


def test_adapter_uses_independent_gold_instead_of_prediction_expected_field():
    prediction = {"id": "positive", "status": "measured", "property_prediction": False,
                  "expected_property": False, "matches": True}
    result = aggregate_security_tasks([prediction], [task()], {"p": "completed"})
    assert result["fn"] == 1 and result["tn"] == 0
    assert result["vulnerability_precision"] is result["vulnerability_recall"] is None


def test_adapter_rejects_duplicate_config_predictions_and_unreviewed_gold():
    prediction = {"id": "positive", "status": "measured", "property_prediction": True}
    with pytest.raises(ValueError):
        aggregate_security_tasks([prediction, copy.deepcopy(prediction)], [task()], {"p": "completed"})
    gold = task()
    gold["adjudicated"] = False
    with pytest.raises(ValueError):
        aggregate_security_tasks([prediction], [gold], {"p": "completed"})


def test_adapter_retains_unmeasured_negative_without_manufacturing_true_negative():
    result = aggregate_security_tasks([{"id": "negative", "status": "unmeasured"}],
        [task("negative", False)], {"p": "error"})
    assert result["unmeasured_negative"] == 1 and result["tn"] == 0
    assert result["negative_obligation_exclusion"] == 0
    assert result["assessment_coverage"] == 0
    assert result["conditional_precision"] is result["conditional_recall"] is None
