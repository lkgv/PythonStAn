"""Independent invariants for contracts, snapshots and frozen SPEC-01 gold."""

import ast
from dataclasses import FrozenInstanceError
from hashlib import sha256
import json
from pathlib import Path

import pytest

from pythonstan.analysis.contracts import (
    EngineSnapshot, EntryScenario, GuaranteeLedger, Outcome, PropertyProof,
    SemanticIdentity, SourceModule, freeze_snapshot, value_snapshot,
)


def identity():
    return SemanticIdentity("source", "ir", "model", "policy", "python-core-v1")


def test_snapshot_is_deep_independent_and_serializable():
    values = {"example": {"result": {"constants": ["after"]}}}
    result = EngineSnapshot("ai_primary", "converged", "closed_under_profile", values=values)
    values["example"]["result"]["constants"].append("before")
    assert result["values"]["example"]["result"]["constants"] == ("after",)
    with pytest.raises(TypeError):
        result.values["example"]["result"]["constants"] = ()
    exported = result.to_dict()
    exported["values"]["example"]["result"]["constants"].append("changed")
    assert result.values["example"]["result"]["constants"] == ("after",)
    assert json.loads(json.dumps(result.to_dict()))["engine"] == "ai_primary"


def test_live_objects_and_nonfinite_json_cannot_escape():
    with pytest.raises(TypeError):
        freeze_snapshot({"solver": object()})
    with pytest.raises(ValueError):
        freeze_snapshot({"value": float("nan")})
    with pytest.raises(TypeError):
        freeze_snapshot({1: "bad key"})


def test_full_value_distinguishes_scalar_none_bottom_and_address_projection():
    string = value_snapshot(constants=("after",))
    assert string["objects"] == ()
    assert string["constants"] == ("after",)
    assert not string["bottom"]
    none = value_snapshot(constants=(None,))
    assert none["tagged_constants"] == ({"type": "none", "value": None},)
    assert not none["bottom"]
    assert value_snapshot()["bottom"]
    assert not value_snapshot(top=True)["bottom"]
    mixed = value_snapshot(constants=(True, 1))
    assert [x["type"] for x in mixed["tagged_constants"]] == ["bool", "int"]
    container = value_snapshot(types=("dict",), objects=("dict:1",), slots={"x": string})
    assert container["slots"]["x"]["constants"] == ("after",)
    assert value_snapshot(types=("list",), items=())["items"] == ()


def test_function_returns_and_module_exits_do_not_conflate_no_normal_return():
    result = EngineSnapshot("ai_primary", "converged", "closed_under_profile",
                            returns={"example.f": value_snapshot()},
                            module_exits={"example": {"normal": False}},
                            outcomes={"example.f": [{"kind": "raise", "type": "ValueError"}]})
    assert result["returns"]["example.f"]["bottom"]
    assert result["module_exits"]["example"]["normal"] is False
    assert result["outcomes"]["example.f"][0]["kind"] == "raise"


def test_outcome_payload_is_explicit():
    with pytest.raises(ValueError):
        Outcome("return", {})
    explicit_none = Outcome("return", {}, value=None, value_present=True)
    assert explicit_none.value_present
    assert Outcome("normal", {}).value_present is False
    with pytest.raises(ValueError):
        Outcome("break", {}, value=1, value_present=True)


def test_convergence_is_not_property_verification():
    assert GuaranteeLedger("converged", "closed_under_profile", "python-core-v1").proof_status == "not_requested"
    unknown = PropertyProof("q2", "unknown", identity(), "input", open_obligations=("external",))
    verified = PropertyProof("q1", "verified", identity(), "input", evidence=("source-check",))
    ledger = GuaranteeLedger("budget_exhausted", "open", "python-core-v1", (verified, unknown))
    assert ledger.proof_status == "unknown"
    assert ledger.solver_status == "budget_exhausted"
    assert GuaranteeLedger("converged", "closed_under_profile", "python-core-v1", (verified,)).proof_status == "verified"
    with pytest.raises(ValueError):
        PropertyProof("q", "verified", identity(), "input", open_obligations=("pending",), evidence=("partial",))


def test_source_digest_and_entry_scenario_input_identity():
    source = SourceModule("example", "memory.py", "x = 1\n")
    assert source.digest == sha256(b"x = 1\n").hexdigest()
    with pytest.raises(ValueError):
        SourceModule("example", "memory.py", "x = 1\n", "wrong")
    inputs = [{"top": True}]
    scenario = EntryScenario("example", "f", inputs, coverage="explicit_abstract_inputs")
    inputs[0]["top"] = False
    assert scenario.arguments[0]["top"] is True
    assert scenario.scenario_id != EntryScenario("example", "g", inputs).scenario_id


def test_frozen_gold_is_independent_and_retains_mandatory_obligations():
    root = Path(__file__).resolve().parents[3]
    gold = json.loads((root / "docs/refactor/query-gold.json").read_text())
    assert gold["status"] == "frozen"
    assert gold["authorship"].startswith("SPEC-01 independently authored")
    cases = gold["mandatory_supported_fixtures"]
    ids = [case["id"] for case in cases]
    assert len(ids) == len(set(ids))
    mandatory_audits = {a for case in cases for a in case["audit_items"]}
    assert {f"A{n:02}" for n in range(1, 14) if n != 6} <= mandatory_audits
    open_audits = {a for case in gold["required_open_behavior_fixtures"] for a in case["audit_items"]}
    assert "A06" in open_audits
    for case in cases:
        assert case["kind"] in ("source", "component")
        if "source" in case:
            ast.parse(case["source"], filename=case.get("filename", case["id"] + ".py"))
            compile(case["source"], case.get("filename", case["id"] + ".py"), "exec")
    assert gold["llm_benefit_family"]["table_sizes"] == [2, 8, 24]
    assert "proposer_work" in gold["llm_benefit_family"]["cost_metrics"]
