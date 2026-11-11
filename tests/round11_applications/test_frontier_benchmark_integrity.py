"""Freeze integrity and independent controlled language observations."""
import inspect
import json
from pathlib import Path
import shutil

import pytest

from benchmark.round11_applications.run import ROOT, digest, manifest, score


@pytest.mark.parametrize("case", manifest()["cases"], ids=lambda c: c["id"])
def test_hand_authored_gold_matches_only_trusted_synthetic_execution(case):
    namespace = {}
    source = (ROOT / case["source"]).read_text()
    try:
        exec(compile(source, "<trusted-round11-synthetic-frontier>", "exec"), namespace)
        assert namespace["events"] == case["expected_events"]
        assert namespace["result"] == case["expected_result"]
    finally:
        # Closing an unstarted synthetic coroutine performs no body effects.
        # Upstream framework source is never imported/executed by this test.
        for value in namespace.values():
            if inspect.iscoroutine(value):
                value.close()


def test_internal_hash_callback_edges_cannot_discharge_native_dispatch():
    case = manifest()["cases"][0]
    observed = {("Dependency.__hash__", "hash_effect"), ("Dependency.__eq__", "equality_effect"),
                ("<module>", "replacement_effect")}
    result = score(case, observed, True)
    assert result["missing"] == [["select_override", "Dependency.__eq__"], ["select_override", "Dependency.__hash__"]]
    assert result["call_obligations_met"] is False


def test_unfinished_predictions_retain_all_obligations_as_unmeasured():
    case = manifest()["cases"][0]
    result = score(case, case["required_edges"], False)
    assert result["required_unmeasured"] == 5 and result["negative_unmeasured"] == 1
    assert result["recovered"] is result["missing"] is result["negative_violations"] is None
    assert not result["call_obligations_met"]


def test_call_recovery_never_becomes_an_effect_or_entry_proof():
    case = manifest()["cases"][0]
    result = score(case, map(tuple, case["required_edges"]), True)
    assert result["call_obligations_met"]
    assert result["effect_or_entry_contract_proved"] is False


@pytest.mark.parametrize("mutation", ["source", "gold", "duplicate", "traversal"])
def test_changed_frozen_fixture_or_denominator_is_rejected(tmp_path, mutation):
    root = tmp_path / "frontiers"
    shutil.copytree(ROOT, root, ignore=shutil.ignore_patterns("__pycache__"))
    path = root / "manifest.json"
    frozen = root / "gold_freeze.json"
    data = json.loads(path.read_text())
    if mutation == "source":
        with (root / data["cases"][0]["source"]).open("a") as stream:
            stream.write("\n# mutation\n")
    elif mutation == "gold":
        data["cases"][0]["required_edges"] = []
        path.write_text(json.dumps(data))
    else:
        if mutation == "duplicate":
            data["cases"][1]["id"] = data["cases"][0]["id"]
        else:
            data["cases"][0]["source"] = "../outside.py"
        path.write_text(json.dumps(data))
        freeze = json.loads(frozen.read_text())
        freeze["manifest_sha256"] = digest(path)
        frozen.write_text(json.dumps(freeze))
    with pytest.raises(ValueError):
        manifest(root)
