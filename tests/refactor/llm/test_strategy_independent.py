"""Independent adversarial L1 lifecycle and resource-plan witnesses."""
from copy import deepcopy

from pythonstan import Source, File, ValueQuery, analyze
from pythonstan.config import resolve_config
from pythonstan.inputs import snapshot_target
from pythonstan.llm.strategy import PolicyProposal, RefinementController


SOURCE = """def identity(payload):
    return payload
def box(payload):
    return {'value': payload}
def dispatch(key, payload):
    table = {'read': identity, 'write': box}
    return table[key](payload)
result = dispatch(key, 'after')
"""


def setup_run(target=None):
    target = target or Source(SOURCE, filename="example.py", module="example")
    query = ValueQuery("example", "result")
    options = {"engine": "ai_primary", "abstract": {"enable_value_partition": False},
               "inputs": {"bindings": {"example.key": {"type": "str", "top": True}}},
               "resources": {"max_work": 2000}, "llm": {"enabled": True, "level1": True}}
    config = resolve_config(target, options).to_dict()
    snapshot = snapshot_target(target, config)
    baseline_options = deepcopy(options)
    baseline_options["llm"]["enabled"] = False
    baseline = analyze(target, config=baseline_options, queries=(query,)).raw_snapshot()
    return target, query, config, snapshot, baseline


def proposal_for(snapshot, **changes):
    data = {"query": "relations", "source_digest": snapshot.source_digest,
            "region": {"module": "*", "qualname": "*", "kind": "block"},
            "strategy": "key_partitions", "parameters": {"enable_value_partition": True},
            "resources": {}}
    data.update(changes)
    return PolicyProposal.parse(data)


def run_controller(proposer, target, query, config, snapshot, baseline):
    return RefinementController(proposer=proposer).run(
        target=target, source_snapshot=snapshot, config=config,
        queries=(query,), output=None, baseline=baseline)


def test_provider_failure_retains_usable_baseline():
    context = setup_run()
    class BrokenProvider:
        def propose(self, *args):
            raise RuntimeError("offline provider failure")
    selected, feedback = run_controller(BrokenProvider(), *context)
    assert selected["values"] == context[-1]["values"]
    assert selected["source_digest"] == context[-1]["source_digest"]
    assert feedback["attempted"] == 0
    assert selected["proof_status"] == "not_requested"


def test_lower_proposal_work_cap_is_enforced_and_partial_not_adopted():
    context = setup_run()
    snapshot = context[-2]
    class OneWorkProvider:
        def propose(self, *args):
            return [proposal_for(snapshot, resources={"max_work": 1})]
    selected, feedback = run_controller(OneWorkProvider(), *context)
    assert feedback["proposals"][0]["status"] == "retained_baseline"
    assert 0 < feedback["reanalysis_work"] <= 1
    assert selected["values"] == context[-1]["values"]
    assert feedback["total_work"] <= context[2]["resources"]["max_work"]


def test_input_change_during_proposal_cannot_replace_frozen_source_result(tmp_path):
    path = tmp_path / "example.py"
    path.write_text(SOURCE)
    context = setup_run(File(path))
    snapshot = context[-2]
    class ConcurrentEditProvider:
        def propose(self, *args):
            path.write_text(SOURCE.replace("'after'", "'concurrent-edit'"))
            return [proposal_for(snapshot)]
    selected, feedback = run_controller(ConcurrentEditProvider(), *context)
    assert path.read_text() != SOURCE, "The concurrent edit must remain preserved"
    assert selected["source_digest"] == snapshot.source_digest
    assert selected["values"] == context[-1]["values"]
    assert feedback["proposals"][0]["status"] != "accepted"


def test_many_policy_proposals_obey_original_retry_limit():
    context = setup_run()
    snapshot = context[-2]
    class SwitchingProvider:
        def propose(self, *args):
            return [proposal_for(snapshot, strategy="call_context", parameters={"context_depth": depth})
                    for depth in range(1, 31)]
    selected, feedback = run_controller(SwitchingProvider(), *context)
    assert feedback["attempted"] <= min(context[2]["llm"]["max_proposals"],
                                        context[2]["resources"]["max_reanalyses"])
    assert len(feedback["proposals"]) == feedback["attempted"]
    assert selected["proof_status"] == "not_requested"


def test_started_failed_reanalysis_cannot_reuse_spent_work_for_free(monkeypatch):
    from pythonstan.session import AnalysisSession

    context = setup_run()
    snapshot = context[-2]
    actual_execute = AnalysisSession.execute
    consumed = []
    class ValidProvider:
        def propose(self, *args):
            return [proposal_for(snapshot),
                    proposal_for(snapshot, strategy="call_context", parameters={"context_depth": 3})]
    def fail_after_core_work(session):
        result = actual_execute(session)
        consumed.append(result.raw_snapshot()["stats"]["total_work"])
        raise ValueError("Injected result-publication failure after real core work")
    monkeypatch.setattr(AnalysisSession, "execute", fail_after_core_work)
    selected, feedback = run_controller(ValidProvider(), *context)
    assert consumed and sum(consumed) > 0
    assert selected["values"] == context[-1]["values"]
    assert feedback["total_work"] >= feedback["baseline_work"] + sum(consumed)
    assert feedback["total_work"] <= context[2]["resources"]["max_work"]
