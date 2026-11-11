"""Independent positive and adversarial tests of L2 source/guard/application."""
import ast
from dataclasses import replace
import json
from types import SimpleNamespace

import pytest

from pythonstan.analysis.contracts import EntryScenario, SourceModule, value_snapshot
from pythonstan.analysis.verification.guarded_dispatch import (
    CandidateError, FreshArena, GuardedEntryContract, PointCoverageProof,
    VerificationBudget, apply_verified_summary, candidate_to_dict,
    check_source_candidate, guard_from_entry_contract, guard_from_source_call,
    identity_for_source, parse_candidate, untrusted_snapshot_evidence)
from pythonstan.llm.providers import OfflineDispatchProposer, make_summary_provider


SOURCE = """def ident(payload):
    return payload
def box(payload):
    return {'value': payload}
def dispatch(table, key, payload):
    return table[key](payload)
table = {'read': ident, 'write': box}
a = dispatch(table, 'read', 'after')
b = dispatch(table, 'write', 'before')
"""


def checked(source=SOURCE, **proposer_options):
    proposal = OfflineDispatchProposer(**proposer_options).propose(source)
    assert proposal.status == "candidate", proposal
    result = check_source_candidate(source,proposal.candidate)
    assert result.certified, result
    return result.certificate


def application(certificate, evidence, **changes):
    args = dict(identity=certificate.source_facts.identity,point=evidence.point,
                table_digest=evidence.table_digest,keys=evidence.keys,
                payload=value_snapshot(constants=("before",)),
                existing_objects=("table-runtime","ident-runtime","box-runtime","dispatch-runtime"))
    args.update(changes)
    return apply_verified_summary(certificate,evidence,**args)


def test_identity_and_fresh_payload_are_relational_not_event_vectors():
    certificate = checked()
    read = guard_from_source_call(certificate,"a")
    payload = value_snapshot(constants=("after",))
    result = application(certificate,read,payload=payload)
    assert result.proof_status == "verified"
    assert result.outcomes[0]["value"] == payload
    assert not result.allocations
    write = guard_from_source_call(certificate,"b")
    arena = FreshArena("test-session")
    first = application(certificate,write,arena=arena)
    second = application(certificate,write,arena=arena)
    left,right = first.allocations[0],second.allocations[0]
    assert left["object"] != right["object"]
    assert left["slots"]["value"] == value_snapshot(constants=("before",))
    assert first.relations[0]["slot_equals"] == "input_payload"
    assert first.outcomes[0]["existing_heap"] == "unchanged"


def test_fresh_instantiation_requires_frame_bound_and_avoids_or_rejects_capture():
    certificate = checked()
    evidence = guard_from_entry_contract(certificate,GuardedEntryContract(
        "entry:dispatch",certificate.source_facts.table_digest,("write",)))
    handler = next(handler for handler in certificate.source_facts.handlers if handler.name == "box")
    collision = f"fixed:fresh_box:{handler.allocation}:{evidence.point}:0"
    payload = value_snapshot(objects=(collision,))
    assert application(certificate,evidence,payload=payload,existing_objects=None).proof_status == "unknown"
    result = application(certificate,evidence,payload=payload,existing_objects=(collision,),arena=FreshArena("fixed"))
    assert result.proof_status == "verified"
    assert result.outcomes[0]["value"]["objects"][0] != collision
    assert result.allocations[0]["slots"]["value"]["objects"] == (collision,)
    class CapturingAllocator:
        def allocate(self,*args):
            return collision
    rejected = application(certificate,evidence,payload=payload,existing_objects=(collision,),arena=CapturingAllocator())
    assert rejected.proof_status == "unknown" and rejected.residual


@pytest.mark.parametrize("mutation", ["wrong_handler","missing_handler","wrong_fresh","stale_ir","self_verified"])
def test_adversarial_proposals_never_gain_certificate(mutation):
    proposal = OfflineDispatchProposer(adversarial=mutation).propose(SOURCE)
    result = check_source_candidate(SOURCE,proposal.candidate)
    assert not result.certified
    assert result.status == "rejected"


def test_coarse_top_can_be_legal_but_does_not_prove_return_relation():
    certificate = checked(coarse=True)
    evidence = guard_from_source_call(certificate,"b")
    result = application(certificate,evidence)
    assert result.coverage_status == "closed_under_profile"
    assert result.outcomes[0]["value"]["top"]
    assert result.proof_status == "unknown"
    assert application(certificate,evidence,requested_property="key_handler").proof_status == "verified"
    assert application(certificate,evidence,requested_property=None).proof_status == "not_requested"


@pytest.mark.parametrize("extra", [
    "\ndef hidden(payload):\n    return payload\n",
    "\ntable['write'] = ident\n",
    "\ndef mutate(payload):\n    payload['x'] = 1\n    return payload\n",
    "\nimport os\n",
])
def test_missing_callee_source_and_alias_mutations_invalidate_candidate(extra):
    candidate = OfflineDispatchProposer().propose(SOURCE).candidate
    result = check_source_candidate(SOURCE+extra,candidate)
    assert not result.certified
    assert result.status in ("rejected","unsupported")


@pytest.mark.parametrize("replacement", [
    "return dispatch(table,key,payload)",
    "while True:\n        return table[key](payload)",
    "return getattr(table,key)(payload)",
    "table[key] = box\n    return table[key](payload)",
])
def test_recursion_loops_and_hidden_protocols_are_explicitly_unsupported(replacement):
    source = SOURCE.replace("return table[key](payload)",replacement)
    proposal = OfflineDispatchProposer().propose(source)
    if proposal.candidate is not None:
        result = check_source_candidate(source,proposal.candidate)
        assert result.status == "unsupported"
        assert not result.certified


def test_whole_source_initialization_order_is_checked():
    invalid = "table={'read':ident}\n" + SOURCE.replace("table = {'read': ident, 'write': box}\n","")
    proposal = OfflineDispatchProposer().propose(invalid)
    assert check_source_candidate(invalid,proposal.candidate).status == "unsupported"


def test_partial_worklist_bottom_and_forged_or_replaced_evidence_cannot_prove_guard():
    certificate = checked()
    valid = guard_from_source_call(certificate,"a")
    partial = untrusted_snapshot_evidence(valid.identity,valid.point,valid.table_digest,("read",),pending_work=1)
    assert application(certificate,partial,payload=value_snapshot(constants=("after",))).proof_status == "unknown"
    fabricated = PointCoverageProof("source_module_initialization",valid.profile,valid.point,
        valid.identity,valid.input_bound_digest,valid.table_digest,("read",),valid.payload_digest,True)
    assert application(certificate,fabricated,payload=value_snapshot(constants=("after",))).proof_status == "unknown"
    replaced = replace(valid,keys=("read","write"))
    assert application(certificate,replaced,payload=value_snapshot(constants=("after",))).proof_status == "unknown"
    bottom = untrusted_snapshot_evidence(valid.identity,valid.point,valid.table_digest,())
    assert application(certificate,bottom).proof_status == "unknown"
    assert application(certificate,valid,keys=(),payload=value_snapshot(constants=("after",))).proof_status == "unknown"


def test_later_input_versions_and_forged_certificate_are_rechecked():
    certificate = checked()
    evidence = guard_from_source_call(certificate,"a")
    assert application(certificate,evidence,keys=("read","write"),payload=value_snapshot(constants=("after",))).proof_status == "unknown"
    assert application(certificate,evidence,identity=replace(evidence.identity,state_version="new")).proof_status == "unknown"
    assert application(certificate,evidence,table_digest="mutated-table").proof_status == "unknown"
    assert application(certificate,evidence,payload=value_snapshot(constants=("changed",))).proof_status == "unknown"
    other = replace(certificate,candidate=replace(certificate.candidate,dispatch="fake"))
    assert not other.issued_by_checker
    assert application(other,evidence).proof_status == "unknown"


def test_guard_complement_is_covered_without_claiming_unknown_protocols_closed():
    certificate = checked()
    contract = GuardedEntryContract("entry:dispatch",certificate.source_facts.table_digest,("read","missing"))
    evidence = guard_from_entry_contract(certificate,contract)
    result = application(certificate,evidence,payload=value_snapshot(objects=("payload-object",)))
    assert [outcome["kind"] for outcome in result.outcomes] == ["return","raise"]
    assert result.outcomes[1]["exception"] == "KeyError"
    assert result.proof_status == "verified"
    unknown = application(certificate,evidence,unknown_key=True)
    assert unknown.coverage_status == "open" and unknown.proof_status == "unknown"
    assert unknown.residual[0]["may_modify_heap"] and unknown.residual[0]["may_call_any"]
    # A complete two-key entry bound cannot be replaced by a read-only partial
    # state, even when the candidate is valid for read on its own.
    assert application(certificate,evidence,keys=("read",)).proof_status == "unknown"


def test_unreachable_later_source_call_and_unrelated_point_do_not_gain_guard_evidence():
    source = SOURCE.replace("'read', 'after'","'missing', 'after'")
    certificate = checked(source)
    with pytest.raises(ValueError):
        guard_from_source_call(certificate,"b")
    with pytest.raises(ValueError):
        guard_from_entry_contract(certificate,GuardedEntryContract("unrelated:point",certificate.source_facts.table_digest,("read",)))


def test_checker_and_application_exhaustion_never_accept_partial_vector():
    candidate = OfflineDispatchProposer().propose(SOURCE).candidate
    for work in (0,1,30,70):
        result = check_source_candidate(SOURCE,candidate,budget=VerificationBudget(max_work=work))
        assert result.status == "unknown" and result.certificate is None
        assert result.checked_work == work
    certificate = checked()
    evidence = guard_from_source_call(certificate,"b")
    result = application(certificate,evidence,max_work=0)
    assert result.proof_status == "unknown" and result.residual
    assert result.solver_status is None


def test_json_dsl_has_no_authority_fields_and_no_execution():
    candidate = OfflineDispatchProposer().propose(SOURCE).candidate
    duplicate = json.dumps(candidate).replace('"profile": "guarded-dispatch-v1"','"profile": "guarded-dispatch-v1", "profile": "guarded-dispatch-v1"',1)
    with pytest.raises((ValueError,CandidateError)):
        parse_candidate(duplicate)
    candidate["guard"]["execute"] = "__import__('os').system('false')"
    assert check_source_candidate(SOURCE,candidate).status == "rejected"


def test_empty_string_key_is_a_normal_finite_literal():
    source = SOURCE.replace("'read'","''")
    assert checked(source).issued_by_checker


def test_offline_provider_is_actually_consumed_by_independent_ai_without_pta():
    from pythonstan.analysis.engines.abstract.driver import InterproceduralDriver
    source = SourceModule("example","example.py",SOURCE)
    snapshot = SimpleNamespace(modules=(source,))
    config = {"engine":"ai_primary","resources":{"max_work":100000},
              "llm":{"enabled":True,"level2":True,"provider":"offline","max_proposals":2,"checker_budget":10000}}
    provider = make_summary_provider(snapshot,config)
    driver = InterproceduralDriver((source,),options=config)
    driver.summary_provider = provider
    result = driver.run((EntryScenario("example"),)).to_dict()
    assert result["solver_status"] == "converged"
    assert result["values"]["example"]["a"]["constants"] == ["after"]
    boxes = result["values"]["example"]["b"]["slots"]
    assert boxes[0]["entries"][0]["value"]["constants"] == ["before"]
    assert provider.statistics["applications"] == 2
    assert provider.statistics["charged_to_driver_work"] == provider.statistics["total_work"]
    assert all(record["status"] == "verified" for record in provider.proof_records)
    assert result["stats"]["pta_work"] == 0
    assert result["stats"]["checked_summary_applications"] == 2


def test_adversarial_provider_falls_back_to_core_and_never_records_verified():
    from pythonstan.analysis.engines.abstract.driver import InterproceduralDriver
    source = SourceModule("example","example.py",SOURCE)
    snapshot = SimpleNamespace(modules=(source,))
    config = {"engine":"ai_primary","resources":{"max_work":100000},
              "llm":{"enabled":True,"level2":True,"provider":"adversarial","max_proposals":2,"checker_budget":10000}}
    provider = make_summary_provider(snapshot,config)
    driver = InterproceduralDriver((source,),options=config)
    driver.summary_provider = provider
    result = driver.run((EntryScenario("example"),)).to_dict()
    assert result["values"]["example"]["a"]["constants"] == ["after"]
    assert provider.statistics["rejected"] == 1
    assert all(record["status"] != "verified" for record in provider.proof_records)


def test_setup_is_charged_without_any_application_and_unproposed_modules_stay_unknown():
    from pythonstan.analysis.engines.abstract.driver import InterproceduralDriver
    source = SourceModule("example","example.py",SOURCE.split("a = dispatch")[0])
    other = SourceModule("other","other.py","x=1\n")
    snapshot = SimpleNamespace(modules=(source,other))
    config = {"engine":"ai_primary","resources":{"max_work":100000},
              "llm":{"enabled":True,"level2":True,"provider":"offline","max_proposals":1,"checker_budget":10000}}
    provider = make_summary_provider(snapshot,config)
    driver = InterproceduralDriver((source,other),options=config)
    driver.summary_provider = provider
    result = driver.run((EntryScenario("example"),)).to_dict()
    assert provider.statistics["applications"] == 0
    assert provider.statistics["charged_to_driver_work"] == provider.statistics["total_work"] > 0
    assert any(record["module"] == "other" and record["status"] == "unknown" for record in provider.proof_records)
    assert result["stats"]["work"] >= provider.statistics["total_work"]


def test_remaining_work_does_not_change_policy_identity_and_zero_budget_stays_unknown():
    source = SourceModule("example","example.py",SOURCE)
    snapshot = SimpleNamespace(modules=(source,))
    config = {"engine":"ai_primary","resources":{"max_work":100000},
              "llm":{"enabled":True,"level2":True,"provider":"offline","max_proposals":2,"checker_budget":10000}}
    provider = make_summary_provider(snapshot,config,remaining_work=0)
    from pythonstan.analysis.contracts import stable_digest
    assert provider.policy_digest == stable_digest(config)
    assert provider.statistics["total_work"] == 0
    assert all(record["status"] == "unknown" for record in provider.proof_records)


@pytest.mark.parametrize("mutation", ["model","ir","module_prefix"])
def test_runtime_model_or_ir_mutation_invalidates_application_without_using_partial_state_as_guard(mutation):
    from pythonstan.analysis.engines.abstract.driver import InterproceduralDriver
    source = SourceModule("example","example.py",SOURCE)
    snapshot = SimpleNamespace(modules=(source,))
    config = {"engine":"ai_primary","resources":{"max_work":100000},
              "llm":{"enabled":True,"level2":True,"provider":"offline","max_proposals":2,"checker_budget":10000}}
    provider = make_summary_provider(snapshot,config)
    driver = InterproceduralDriver((source,),options=config)
    driver.summary_provider = provider
    if mutation == "model":
        driver.model_digest = "changed-model"
    elif mutation == "ir":
        box = next(node for node in driver.trees["example"].body if isinstance(node,ast.FunctionDef) and node.name == "box")
        box.body[0].value = ast.Name(id="payload",ctx=ast.Load())
    else:
        driver.trees["example"].body.insert(0,ast.parse("unused_prefix=1").body[0])
    result = driver.run((EntryScenario("example"),)).to_dict()
    assert result["values"]["example"]["a"]["constants"] == ["after"]
    assert provider.statistics["applications"] == 0
    assert all(record["status"] != "verified" for record in provider.proof_records)


@pytest.mark.parametrize("replacement", [
    "def ident(payload: mark()):", "def ident(payload=mark()):",
    "@mark\ndef ident(payload):", "def ident(payload) -> mark():",
])
def test_eager_definition_effects_are_rejected_even_with_pure_return(replacement):
    source = SOURCE.replace("def ident(payload):",replacement)
    candidate = OfflineDispatchProposer().propose(source).candidate
    assert check_source_candidate(source,candidate).status == "unsupported"


def test_type_ignore_comments_use_the_same_full_ir_identity_as_frontend():
    source = SOURCE.replace("return payload","return payload  # type: ignore",1)
    certificate = checked(source)
    import hashlib
    tree = ast.parse(source,type_comments=True,feature_version=(3,12))
    assert certificate.source_facts.identity.ir_digest == hashlib.sha256(ast.dump(tree,include_attributes=False).encode()).hexdigest()


def test_ast_parse_only_invalid_binding_never_receives_certificate():
    source = SOURCE.replace("return payload","__debug__ = payload\n    return __debug__",1)
    candidate = OfflineDispatchProposer().propose(source).candidate
    assert check_source_candidate(source,candidate).status == "unsupported"
