"""Compatibility and observation boundaries, using preserved historical bytes."""
from dataclasses import asdict
import hashlib
import json
from pathlib import Path

import pytest

from benchmark.round8_corpus import collect_results
from benchmark.round8_corpus.registry import projects
from pythonstan.analysis.project import ProjectConfig
from scripts.analyze_project import _read_progress


ROOT = Path(__file__).resolve().parents[2]


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2) + "\n")


@pytest.fixture
def historical_langserve(tmp_path):
    # This copies actual Round8 evidence unchanged, rather than deriving a
    # pretend old report from today's dataclass and accidentally testing itself.
    original = ROOT / "docs/certified_analysis/evidence/round8_projects"
    summary = json.loads((original / "summary.json").read_text())
    rows = [row for row in summary["rows"] if row["project_id"] == "langserve"]
    assert {row["mode"] for row in rows} == {"baseline", "current"}
    for row in rows:
        source = original / row["path"]
        assert collect_results.digest(source) == row["sha256"]
        raw = json.loads(source.read_text())
        assert "frontend_lifetime_policy" not in raw["configuration"]
        if "effective_config" in raw["report"]:
            assert "frontend_lifetime_policy" not in raw["report"]["effective_config"]
        (tmp_path / row["path"]).write_bytes(source.read_bytes())
    # Restrict the copied summary to an explicitly declared one-project cohort;
    # never mutate the preserved 14-project evidence.
    summary.update(rows=rows, projects=1, trials=2, project_ids=["langserve"])
    write_json(tmp_path / "summary.json", summary)
    records = {record["id"]: record for record in projects() if record["id"] == "langserve"}
    return tmp_path, records


def mutate_historical(directory, mutation):
    summary = json.loads((directory / "summary.json").read_text())
    row = next(row for row in summary["rows"] if row["mode"] == "current")
    path = directory / row["path"]
    raw = json.loads(path.read_text())
    mutation(raw)
    write_json(path, raw)
    row["sha256"] = collect_results.digest(path)
    write_json(directory / "summary.json", summary)


def test_actual_historical_reports_keep_their_old_schema_binding(historical_langserve):
    directory, records = historical_langserve
    result = collect_results.static_results(directory, records)
    assert result["projects_attempted"] == 1 and result["trials"] == 2
    assert {row["mode"] for row in result["rows"]} == {"baseline", "current"}
    assert all(row["whole_program_soundness_proved"] is False for row in result["rows"])


@pytest.mark.parametrize("policy", ["auto", "retain", "module_closure"])
def test_explicit_lifetime_input_cannot_be_erased_to_accept_old_digest(historical_langserve, policy):
    directory, records = historical_langserve
    mutate_historical(directory, lambda raw: raw["configuration"].update(frontend_lifetime_policy=policy))
    with pytest.raises(ValueError, match="configuration binding"):
        collect_results.static_results(directory, records)


def test_old_digest_does_not_accept_mismatching_effective_config(historical_langserve):
    directory, records = historical_langserve
    def mutate(raw):
        raw["report"].setdefault("effective_config", asdict(ProjectConfig.from_dict(raw["configuration"])))
        raw["report"]["effective_config"]["frontend_lifetime_policy"] = "retain"
    mutate_historical(directory, mutate)
    with pytest.raises(ValueError, match="configuration binding"):
        collect_results.static_results(directory, records)


def test_explicit_retention_with_its_own_effective_digest_is_supported(historical_langserve):
    directory, records = historical_langserve
    def mutate(raw):
        raw["configuration"]["frontend_lifetime_policy"] = "retain"
        effective = asdict(ProjectConfig.from_dict(raw["configuration"]))
        raw["report"]["effective_config"] = effective
        raw["report"]["configuration_sha256"] = hashlib.sha256(
            json.dumps(effective, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    mutate_historical(directory, mutate)
    assert collect_results.static_results(directory, records)["trials"] == 2


BASE = {"run_id": "run", "request_sha256": "request", "implementation_sha256_before": "a" * 64,
        "configuration_sha256": "b" * 64}
SOURCE = {"path": "pkg/a.py", "sha256": "c" * 64, "ownership": "first_party",
          "module": "pkg.a", "parsed": True, "lowered": True}
OBSERVATION = {"requested_policy": "auto", "effective_policy": "module_closure",
               "module_closure_modules": 1, "retired_statement_cfgs": 3, "deferred_modules": []}


def observed_frames():
    return [dict(BASE, sequence=1, phase="inventory_completed", inventory=[dict(SOURCE)]),
            dict(BASE, sequence=2, phase="module_closure_completed", frontend_lifetime=dict(OBSERVATION))]


def read_frames(tmp_path, frames, tail=""):
    path = tmp_path / "progress.jsonl"
    path.write_text("".join(json.dumps(frame) + "\n" for frame in frames) + tail)
    return _read_progress(path, "run", "request")


def assert_retained_observation(result):
    assert result["last_observed_frontend_lifetime"] == {
        "origin_phase": "module_closure_completed", "observation": OBSERVATION}
    assert result["call_projection_completed"] is False and result["semantic_complete"] is False
    assert result["source_snapshot_stability_after_termination"] == "unverified"


def test_lifetime_observation_survives_later_phases_without_completion_promotion(tmp_path):
    result = read_frames(tmp_path, [*observed_frames(), dict(BASE, sequence=3, phase="solver_fixpoint_started")])
    assert result["events"] == 3 and result["last_phase"] == "solver_fixpoint_started"
    assert_retained_observation(result)


@pytest.mark.parametrize("changed", [
    {"run_id": "other"}, {"request_sha256": "other"},
    {"implementation_sha256_before": "d" * 64}, {"configuration_sha256": "e" * 64},
    {"sequence": True},
])
def test_mismatched_bound_event_cannot_replace_lifetime_observation(tmp_path, changed):
    fake = dict(BASE, sequence=3, phase="forged", frontend_lifetime={"retired_statement_cfgs": 999})
    fake.update(changed)
    result = read_frames(tmp_path, [*observed_frames(), fake, dict(BASE, sequence=3, phase="solver_fixpoint_started")])
    assert result["events"] == 3 and result["rejected_or_torn_frames"] == 1
    assert result["last_phase"] == "solver_fixpoint_started"
    assert_retained_observation(result)


@pytest.mark.parametrize("update_kind", ["module_record", "module_updates", "inventory"])
def test_changed_source_identity_cannot_replace_lifetime_observation(tmp_path, update_kind):
    fake = dict(BASE, sequence=3, phase="forged", frontend_lifetime={"retired_statement_cfgs": 999})
    source = {**SOURCE, "sha256": "f" * 64}
    if update_kind == "inventory":
        fake.update(phase="inventory_completed", inventory=[source])
    elif update_kind == "module_updates":
        fake[update_kind] = [source]
    else:
        fake[update_kind] = source
    result = read_frames(tmp_path, [*observed_frames(), fake, dict(BASE, sequence=3, phase="solver_fixpoint_started")])
    assert result["events"] == 3 and result["rejected_or_torn_frames"] == 1
    assert result["inventory"][0]["sha256"] == SOURCE["sha256"]
    assert_retained_observation(result)


def test_torn_final_frame_cannot_fabricate_lifetime_observation(tmp_path):
    result = read_frames(tmp_path, observed_frames(), '{"sequence": 3, "frontend_lifetime":')
    assert result["events"] == 2 and result["rejected_or_torn_frames"] == 1
    assert_retained_observation(result)


def test_non_object_lifetime_observation_does_not_replace_last_bound_object(tmp_path):
    result = read_frames(tmp_path, [*observed_frames(), dict(BASE, sequence=3, phase="solver_fixpoint_started",
                                                         frontend_lifetime=["not", "an", "object"])])
    assert result["events"] == 3
    assert_retained_observation(result)
