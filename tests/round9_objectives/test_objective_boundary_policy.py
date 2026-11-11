import copy
import hashlib
import json

import pytest

from pythonstan.analysis.security.boundary_policy import (
    infer_boundary_policy, check_boundary_policy, UnsupportedPolicyModel,
    REQUIRED_CONTRACTS,
)
from benchmark.round9_objectives.run import score_subgoals


WRAPPER = '''from helper import load as pl_load
def entry(path, *, weights_only=None):
    if absent(path):
        raise FileNotFoundError(path)
    return pl_load(path, weights_only=weights_only)
'''
HELPER = '''import torch
def load(path, weights_only=None):
    if raw_object(path):
        return torch.load(path, weights_only=weights_only)
    if remote(path):
        if weights_only is None:
            weights_only = False
        return torch.hub.load_state_dict_from_url(path, weights_only=weights_only)
    with open(path, 'rb') as f:
        return torch.load(f, weights_only=weights_only)
'''


def request(**overrides):
    return {"entry": "wrapper.entry", "policy": "weights_only",
        "forwarders": {"pl_load": "helper.load"},
        "boundaries": ["torch.load", "torch.hub.load_state_dict_from_url"],
        "boundary_imports": {"torch": "torch"},
        "entry_values": [False, True, None, "omitted"],
        "contracts": sorted(REQUIRED_CONTRACTS), **overrides}


def test_conditional_cross_procedure_default_rewrite_and_explicit_override():
    sources = {"wrapper": WRAPPER, "helper": HELPER}
    proposal = infer_boundary_policy(sources, request())
    result = check_boundary_policy(sources, request(), proposal)
    assert result["status"] == "conditional_model_verified"
    assert result["end_to_end_challenges_discharged"] == 0
    assert not result["native_safety_proved"]
    values = {(r["input"], r["boundary"], r["value"]) for r in proposal["observations"]}
    assert ("none", "torch.hub.load_state_dict_from_url", "false") in values
    assert ("none", "torch.load", "none") in values
    assert not any(i == "true" and v != "true" for i, _, v in values)
    assert ("omitted", "torch.hub.load_state_dict_from_url", "false") in values


def test_checker_rejects_tampered_all_paths_policy_claim():
    sources = {"wrapper": WRAPPER, "helper": HELPER}
    proposal = infer_boundary_policy(sources, request())
    proposal["observations"] = [r for r in proposal["observations"] if not (r["input"] == "none" and r["value"] == "false")]
    assert check_boundary_policy(sources, request(), proposal)["status"] == "rejected"


def test_default_propagation_is_not_confused_with_explicit_none():
    source = "import torch\ndef load(path, *, weights_only=True):\n return torch.load(path, weights_only=weights_only)\n"
    r = request(entry="helper.load", forwarders={})
    proposal = infer_boundary_policy({"helper": source}, r)
    values = {(v["input"],v["value"]) for v in proposal["observations"]}
    assert ("omitted", "true") in values
    assert ("none", "none") in values


@pytest.mark.parametrize("body", [
    "return torch.load(path, **{'weights_only': weights_only})",
    "while x: weights_only = False\n return torch.load(path, weights_only=weights_only)",
    "try:\n  return torch.load(path, weights_only=weights_only)\n except Exception:\n  return None",
    "weights_only = (False if path else True)\n return torch.load(path, weights_only=weights_only)",
    "if weights_only == None:\n  weights_only=False\n return torch.load(path, weights_only=weights_only)",
])
def test_unsupported_semantics_are_not_upgraded_to_checked_facts(body):
    sources = {"helper": "import torch\ndef load(path, weights_only=None):\n " + body + "\n"}
    with pytest.raises(UnsupportedPolicyModel):
        infer_boundary_policy(sources, request(entry="helper.load", forwarders={}))


@pytest.mark.parametrize("source", [
    "import torch\ntorch.load = replacement\ndef load(path, weights_only=None):\n return torch.load(path, weights_only=weights_only)\n",
    "import torch\ndef load(path, weights_only=None):\n torch = replacement\n return torch.load(path, weights_only=weights_only)\n",
    "import replacement as torch\ndef load(path, weights_only=None):\n return torch.load(path, weights_only=weights_only)\n",
])
def test_callable_source_binding_is_not_owned_by_a_candidate(source):
    with pytest.raises(UnsupportedPolicyModel):
        infer_boundary_policy({"helper":source},request(entry="helper.load",forwarders={}))


def test_source_or_request_change_rejects_prior_candidate():
    sources = {"wrapper": WRAPPER, "helper": HELPER}
    proposal = infer_boundary_policy(sources, request())
    altered = {**sources,"helper": HELPER.replace("weights_only = False", "weights_only = True")}
    assert check_boundary_policy(altered, request(), proposal)["status"] == "rejected"
    r = request(entry_values=[True])
    assert check_boundary_policy(sources, r, proposal)["status"] == "rejected"


def test_raise_operand_is_evaluated_before_control_terminates():
    source = "import torch\ndef load(path, weights_only=None):\n raise ValueError(torch.load(path, weights_only=weights_only))\n"
    r = request(entry="helper.load",forwarders={})
    proposal = infer_boundary_policy({"helper":source},r)
    assert len(proposal["observations"]) == 4
    assert check_boundary_policy({"helper":source},r,proposal)["status"] == "conditional_model_verified"


@pytest.mark.parametrize("call", [
    "pl_load(weights_only=weights_only)",
    "pl_load(path, weights_only=weights_only, unknown=True)",
    "pl_load(path, weights_only, weights_only=weights_only)",
])
def test_invalid_python_argument_binding_is_not_a_forwarding_proof(call):
    source = "from helper import load as pl_load\ndef entry(path, weights_only=None):\n return " + call + "\n"
    with pytest.raises(UnsupportedPolicyModel):
        infer_boundary_policy({"wrapper":source,"helper":HELPER},request())


def test_no_contracts_cannot_establish_original_python_or_native_behavior():
    with pytest.raises(UnsupportedPolicyModel):
        infer_boundary_policy({"wrapper": WRAPPER, "helper": HELPER},request(contracts=[]))


def test_budget_exhaustion_never_accepts_a_partial_boundary_relation():
    sources = {"wrapper": WRAPPER, "helper": HELPER}
    with pytest.raises(UnsupportedPolicyModel, match="budget exhausted"):
        infer_boundary_policy(sources, request(max_steps=1))
    proposal = infer_boundary_policy(sources, request())
    limited = request(max_steps=1)
    proposal['request_sha256'] = hashlib.sha256(json.dumps(limited,sort_keys=True).encode()).hexdigest()
    checked = check_boundary_policy(sources, limited, proposal)
    assert checked["status"] == "unsupported"
    assert "budget exhausted" in checked["reason"]


def test_subgoal_scorer_rechecks_candidate_instead_of_trusting_stale_success():
    sources = {"wrapper": WRAPPER, "helper": HELPER}
    proposal = infer_boundary_policy(sources, request())
    goal = [{"id":"true_stays_true","input":"true","allowed_values":["true"]}]
    assert score_subgoals(sources, request(), proposal, goal)[0]["status"] == "conditional_model_verified"
    broken = copy.deepcopy(proposal)
    broken["observations"] = [r for r in broken["observations"] if r["input"] != "true"]
    assert score_subgoals(sources, request(), broken, goal)[0]["status"] == "unproved"


def test_empty_relation_is_not_noop_proof_success():
    source = 'import torch\ndef load(path, weights_only=None):\n return None\n'
    r = request(entry='helper.load', forwarders={})
    proposal = infer_boundary_policy({'helper':source},r)
    assert check_boundary_policy({'helper':source},r,proposal)['status'] == 'unmeasured'
