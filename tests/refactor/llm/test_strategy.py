from copy import deepcopy
import pytest
from pythonstan import Source, ValueQuery, analyze
from pythonstan.config import resolve_config, ConfigurationError
from pythonstan.inputs import snapshot_target
from pythonstan.llm.strategy import PolicyProposal, RefinementController

SOURCE = """def identity(payload):
    return payload
def box(payload):
    return {'value':payload}
def dispatch(key,payload):
    table={'read':identity,'write':box}
    return table[key](payload)
result=dispatch(key,'after')
"""


def configuration():
    return {"engine": "ai_primary", "abstract": {"enable_value_partition": False},
            "inputs": {"bindings": {"example.key": {"type": "str", "top": True}}},
            "llm": {"enabled": True, "level1": True}}


def test_l1_actual_query_precision_and_full_cost_record():
    target = Source(SOURCE, module="example")
    q = ValueQuery("example", "result")
    before_cfg = configuration(); before_cfg["llm"]["enabled"] = False
    before = analyze(target, config=before_cfg, queries=(q,))
    after = analyze(target, config=configuration(), queries=(q, "stats"))
    pairs = lambda result: {(row["key"], row["handler"]) for row in result.query(q).value["relations"]}
    assert len(pairs(before)) == 4
    assert pairs(after) == {("'read'", "example.identity"), ("'write'", "example.box")}
    assert before.coverage_status == after.coverage_status == "closed_under_profile"
    assert after.proof_status == "not_requested"
    meta = after.raw_snapshot()["llm"]["level1"]
    assert meta["proposals"][0]["status"] == "accepted"
    assert meta["baseline_work"] > 0 and meta["reanalysis_work"] > 0
    assert after.raw_snapshot()["stats"]["total_work"] == meta["total_work"]
    assert meta["total_work"] <= configuration().get("resources", {}).get("max_work", 100000)
    assert any("l1_rerun" in phase for phase in after.phase_trace)
    assert meta["proposals"][0]["policy_digest"] == after.raw_snapshot()["policy_digest"]


@pytest.mark.parametrize("change", [
    {"strategy": "delete_entry"}, {"verified": True},
    {"source_digest": "stale"}, {"resources": {"max_work": 0}},
    {"parameters": {"enable_value_partition": "yes"}},
    {"region": {"module": "absent", "kind": "function"}},
])
def test_untrusted_policy_rejections(change):
    target = Source(SOURCE, module="example")
    config = resolve_config(target, configuration()).to_dict()
    snapshot = snapshot_target(target, config)
    proposal = {"query": "relations", "source_digest": snapshot.source_digest,
                "region": {"module": "*", "kind": "block"}, "strategy": "key_partitions",
                "parameters": {"enable_value_partition": True}, "resources": {}}
    proposal.update(change)
    with pytest.raises((ConfigurationError, ValueError, TypeError)):
        RefinementController().validate(proposal, snapshot, config)


def test_adversarial_provider_rejected_and_core_still_usable():
    config = configuration(); config["llm"]["provider"] = "adversarial"
    q = ValueQuery("example", "result")
    result = analyze(Source(SOURCE, module="example"), config=config, queries=(q,))
    assert result.solver_status == "converged"
    assert len(result.query(q).value["relations"]) == 4
    assert result.raw_snapshot()["llm"]["level1"]["proposals"][0]["status"] == "rejected"
    assert result.proof_status == "not_requested"
