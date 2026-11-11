import copy
import json

import pytest

from benchmark.round11_protocol import metrics
from tests.round11_empirical.test_round11_bindings_and_censoring import candidate, validated
from tests.round11_empirical.test_round11_queue_and_policy_metrics import policy_study


def arms(tmp_path):
    fair_path = tmp_path / "primary_fair"; fair_path.mkdir()
    record, protocol = candidate(fair_path)
    fair = validated(fair_path, record, protocol)
    legacy_path = tmp_path / "explicit_legacy"
    record, protocol = policy_study(legacy_path, "legacy")
    legacy = validated(legacy_path, record, protocol)
    amendment = {"study_id": protocol["study_id"], "amendment_id": "test-reuse", "project_ids": ["demo"]}
    return legacy, fair, amendment


def test_primary_fair_reuse_keeps_real_omitted_input_and_original_raw_binding(tmp_path):
    legacy, fair, amendment = arms(tmp_path)
    original = copy.deepcopy(fair)
    result = metrics.compare_reused_fair(legacy, fair, amendment=amendment)
    assert fair == original
    assert "work_scheduling_policy" not in fair["rows"][0]["configuration"]
    assert result["pairs"][0]["primary_fair_raw_sha256"] == fair["rows"][0]["raw_sha256"]
    assert result["new_unique_legacy_trials"] == result["primary_fair_trials_reused"] == 1
    assert result["reused_trials_are_additional_independent_samples"] is False
    assert result["pairs"][0]["causal_runtime_ratio"] is None


@pytest.mark.parametrize("mutation", [
    lambda legacy, fair: fair.update(engine_core_sha256="other core"),
    lambda legacy, fair: fair.update(engine_script_sha256="other CLI"),
    lambda legacy, fair: fair.update(parallel_workers=1),
    lambda legacy, fair: fair.update(scheduling_policy_override="fair"),
    lambda legacy, fair: fair["rows"][0]["configuration"].update(work_scheduling_policy="fair"),
    lambda legacy, fair: fair["rows"][0]["configuration"].update(index_sensitive=False),
    lambda legacy, fair: fair["rows"][0].update(source_files_sha256="other source"),
    lambda legacy, fair: fair["rows"][0].update(configured_scheduling_policy="legacy"),
    lambda legacy, fair: legacy["rows"][0]["configuration"].pop("work_scheduling_policy"),
    lambda legacy, fair: fair["rows"].clear(),
    lambda legacy, fair: fair["rows"].append(copy.deepcopy(fair["rows"][0])),
])
def test_amendment_rejects_relabeling_selection_or_other_input_changes(tmp_path, mutation):
    legacy, fair, amendment = arms(tmp_path)
    mutation(legacy, fair)
    with pytest.raises(ValueError):
        metrics.compare_reused_fair(legacy, fair, amendment=amendment)


def test_cannot_reduce_frozen_amendment_project_denominator(tmp_path):
    legacy, fair, amendment = arms(tmp_path)
    amendment["project_ids"].append("unmeasured")
    with pytest.raises(ValueError, match="entire declared cohort"):
        metrics.compare_reused_fair(legacy, fair, amendment=amendment)


def test_frozen_companion_amendment_hash_and_protocol_driver_bindings():
    directory = metrics.HERE
    path = directory / "reused_fair_amendment.json"
    declared = json.loads((directory / "reused_fair_amendment_sha256.json").read_text())["sha256"]
    value = json.loads(path.read_text())
    assert metrics.file_hash(path) == declared
    assert value["frozen_protocol_sha256"] == metrics.file_hash(directory / "protocol.json")
    assert value["frozen_driver_sha256"] == metrics.file_hash(directory / "run_pair.py")
    assert len(value["project_ids"]) == 14 and value["total_unique_trials_if_complete"] == 54
    assert value["declared_before_explicit_legacy_phase"] is True
