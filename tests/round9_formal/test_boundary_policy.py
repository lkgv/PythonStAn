"""Adversarial source/candidate tests independent of the policy inferencer."""
import copy
import hashlib
import json

import pytest

from pythonstan.analysis.security.boundary_policy import (
    MODEL, REQUIRED_CONTRACTS, check_boundary_policy, infer_boundary_policy,
)


GOOD = '''
import torch
def entry(path, weights_only=None):
    if weights_only is None:
        weights_only = False
    return torch.load(path, weights_only=weights_only)
'''


def request(**changes):
    return {"entry": "app.entry", "policy": "weights_only", "forwarders": {},
            "boundaries": ["torch.load"], "boundary_imports": {"torch": "torch"},
            "entry_values": [False, True, None, "omitted"],
            "contracts": sorted(REQUIRED_CONTRACTS), **changes}


def empty_candidate(sources, spec):
    # Deliberately constructed without running abstract inference.
    return {"model": MODEL,
            "source_sha256": {m: hashlib.sha256(s.encode()).hexdigest() for m, s in sources.items()},
            "request_sha256": hashlib.sha256(json.dumps(spec, sort_keys=True).encode()).hexdigest(),
            "observations": []}


def test_checker_rejects_empty_relation_for_a_real_modeled_boundary():
    sources, spec = {"app": GOOD}, request()
    result = check_boundary_policy(sources, spec, empty_candidate(sources, spec))
    assert result["status"] == "rejected"
    assert result["missing"]


def test_policy_rewrite_relation_is_explicitly_conditional_and_source_bound():
    sources, spec = {"app": GOOD}, request()
    candidate = infer_boundary_policy(sources, spec)
    observed = {(r["input"], r["value"]) for r in candidate["observations"]}
    assert observed == {("false", "false"), ("true", "true"),
                        ("none", "false"), ("omitted", "false")}
    result = check_boundary_policy(sources, spec, candidate)
    assert result["status"] == "conditional_model_verified"
    assert result["whole_program_soundness_proved"] is False
    assert result["native_safety_proved"] is False
    assert result["end_to_end_challenges_discharged"] == 0
    changed = {"app": GOOD.replace("weights_only = False", "weights_only = True")}
    assert check_boundary_policy(changed, spec, candidate)["status"] == "rejected"
    narrowed = copy.deepcopy(candidate)
    narrowed["observations"] = [r for r in narrowed["observations"] if r["value"] == "true"]
    assert check_boundary_policy(sources, spec, narrowed)["status"] == "rejected"


@pytest.mark.parametrize("source", [
    GOOD.replace("import torch", "import torch\ntorch = replacement"),
    GOOD.replace("import torch", "import torch\ntorch.load = replacement"),
    GOOD.replace("entry(path,", "entry(torch,"),
    GOOD.replace("weights_only is None", "weights_only == None"),
    GOOD.replace("weights_only is None", "weights_only"),
    GOOD.replace("return torch.load(path, weights_only=weights_only)",
                 "return torch.load(path, **options)"),
    GOOD.replace("weights_only = False", "weights_only = not weights_only"),
    GOOD.replace("return torch.load(path, weights_only=weights_only)",
                 "for item in items:\n        torch.load(path, weights_only=weights_only)"),
    GOOD.replace("return torch.load(path, weights_only=weights_only)",
                 "setattr(torch, 'load', replacement)\n    return torch.load(path, weights_only=weights_only)"),
])
def test_unsupported_binding_control_and_expansion_cannot_be_verified(source):
    sources, spec = {"app": source}, request()
    result = check_boundary_policy(sources, spec, empty_candidate(sources, spec))
    assert result["status"] in {"unsupported", "rejected"}


def test_suppressing_context_manager_cannot_erase_a_later_normal_path_boundary():
    source = '''
import torch
class Suppress:
    def __enter__(self): return self
    def __exit__(self, *args): return True
def entry(path, weights_only=None):
    with Suppress():
        raise ValueError()
    return torch.load(path, weights_only=weights_only)
'''
    # In Python __exit__ may suppress the exception and the final boundary is
    # reached. Stable names and no frame mutation do not rule this out. A model
    # that does not represent this must reject it, not verify an empty relation.
    sources, spec = {"app": source}, request()
    result = check_boundary_policy(sources, spec, empty_candidate(sources, spec))
    assert result["status"] in {"unsupported", "rejected"}


def test_noboundary_is_not_a_verified_no_effect_claim():
    sources, spec = {"app": "def entry(path, weights_only=None):\n    return None\n"}, request()
    result = check_boundary_policy(sources, spec, empty_candidate(sources, spec))
    assert result["status"] != "conditional_model_verified"


def test_original_body_contract_is_not_optional_for_a_decorated_entry():
    sources = {"app": GOOD.replace("def entry", "@wrap\ndef entry")}
    spec = request(contracts=sorted(REQUIRED_CONTRACTS - {"original_function_body_entry"}))
    assert check_boundary_policy(sources, spec, empty_candidate(sources, spec))["status"] == "unsupported"


def test_forwarded_argument_binding_must_match_the_supplied_body():
    sources = {
        "app": "from support import forward as pl_load\n"
               "def entry(path, weights_only=None):\n"
               "    return pl_load(weights_only=weights_only)\n",
        "support": "import torch\n"
                   "def forward(path, weights_only=None):\n"
                   "    return torch.load(path, weights_only=weights_only)\n",
    }
    spec = request(forwarders={"pl_load": "support.forward"})
    # The real Python call lacks required path; do not silently manufacture a
    # successful forwarded frame simply because the tracked scalar is supplied.
    assert check_boundary_policy(sources, spec, empty_candidate(sources, spec))["status"] == "unsupported"


def test_malformed_untrusted_candidate_shape_fails_closed_without_raising():
    sources, spec = {"app": GOOD}, request()
    result = check_boundary_policy(sources, spec, [])
    assert result["status"] in {"unsupported", "rejected"}


def test_syntax_invalid_source_cannot_crash_or_issue_a_conditional_certificate():
    sources, spec = {"app": "def entry(:\n    pass\n"}, request()
    result = check_boundary_policy(sources, spec, empty_candidate(sources, spec))
    assert result["status"] in {"unsupported", "rejected"}
