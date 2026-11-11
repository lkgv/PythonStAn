"""Explicit policy roundtrips and complete source-state schedule witnesses."""
import itertools

import pytest

from benchmark.round11_solver.fair_experiment import CASES, analyze_source
from pythonstan.analysis.pointer.kcfa.config import Config


@pytest.mark.parametrize("policy", ["legacy", "fair"])
def test_policy_roundtrips_with_explicit_contract(policy):
    config = Config(work_scheduling_policy=policy)
    assert Config.from_dict(config.to_dict()) == config
    assert config.to_dict()["work_scheduling_policy"] == policy


@pytest.mark.parametrize("invalid", [None, True, False, 0, 1, "", "FIFO", "round_robin", [], {}])
def test_policy_rejects_invalid_values(invalid):
    with pytest.raises(ValueError, match="work_scheduling_policy"):
        Config.from_dict({"work_scheduling_policy":invalid})


@pytest.mark.parametrize("case_id", CASES)
@pytest.mark.parametrize("depth,index,intrinsics", itertools.product(range(3), (False, True), (False, True)))
def test_completed_source_and_field_payload_equal_between_policies(tmp_path, case_id, depth, index, intrinsics):
    arms = {policy:analyze_source(tmp_path, CASES[case_id], policy=policy,
                context_depth=depth, index_sensitive=index, intrinsics=intrinsics)
            for policy in ("legacy", "fair")}
    assert all(arm["statistics"]["converged"] == 1
               and arm["statistics"]["pending_work"] == 0
               and arm["statistics"]["iteration_limit_reached"] == 0 for arm in arms.values())
    assert arms["legacy"]["signature"] == arms["fair"]["signature"]
    assert arms["legacy"]["edges"] == arms["fair"]["edges"]
    for policy, arm in arms.items():
        assert arm["solver_configuration"]["enable_synthetic_method_contexts"] is False
        assert arm["statistics"]["work_scheduling_policy"] == policy
        assert not arm["signature"]["semantic_status"]["whole_program_soundness_proved"]
    if case_id == "dynamic_bound_method":
        assert ("subject.dispatch", "subject.Service.handle") in arms["fair"]["edges"]
        assert all(edge[1] != "subject.unrelated" for edge in arms["fair"]["edges"])
    if case_id == "mapping_late_growth" and intrinsics:
        assert ("subject.invoke", "subject.early") in arms["fair"]["edges"]
        assert ("subject.invoke", "subject.later") in arms["fair"]["edges"]
        assert ("subject.invoke", "subject.unrelated") not in arms["fair"]["edges"]


@pytest.mark.parametrize("policy", ["legacy", "fair"])
def test_same_tiny_budget_cannot_claim_complete_source(policy, tmp_path):
    result = analyze_source(tmp_path, CASES["mapping_late_growth"], policy=policy, max_iterations=1)
    assert result["statistics"]["iterations"] == 1
    assert result["statistics"]["converged"] == 0
    assert result["statistics"]["iteration_limit_reached"] == 1
    assert result["statistics"]["pending_work"] > 0
    assert not result["signature"]["semantic_status"]["certificate_eligible"]
