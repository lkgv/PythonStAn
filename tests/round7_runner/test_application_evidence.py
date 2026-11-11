"""Application inventory, entry and unmeasured denominators are separate gates."""
from pathlib import Path
import json

import pytest

from benchmark.round7_apps.runner import configuration, score, verify_sources, ROOT
from scripts.analyze_project import run_isolated


CASE = {"required_edges": [["app.main", "app.callback"]],
        "forbidden_edges": [["app.main", "app.never"]],
        "required_entry_points": ["app.main"], "first_party_module_count": 2}


def complete_report():
    return {"solver_completed": True, "first_party_translation_complete": True,
            "implementation_stable": True, "source_snapshot_stable": True,
            "entry_points": ["app.main", "app.callback"],
            "coverage": {"first_party_discovered": 2, "unprocessed_files": 0},
            "call_edges": []}


def test_entry_recognition_does_not_satisfy_call_requirement():
    report = score(CASE, complete_report())
    assert report["recovered_pairs"] == 0
    assert report["missing_required"] == [("app.main", "app.callback")]
    assert not report["entry_discovery_is_dispatch_proof"]


@pytest.mark.parametrize("change", [
    {"solver_completed": False}, {"implementation_stable": False},
    {"source_snapshot_stable": False}, {"first_party_translation_complete": False},
    {"coverage": {"first_party_discovered": 1}},
    {"coverage": {"first_party_discovered": 2, "unprocessed_files": 1}},
    {"frontend_errors": [{"reason": "unprocessed module"}]},
])
def test_incomplete_projects_keep_both_denominators(change):
    report = complete_report()
    report.update(change, call_edges=[["app.main", "app.callback"]])
    checked = score(CASE, report)
    assert checked["recovered_pairs"] == 0
    assert checked["unmeasured_required_pairs"] == checked["unmeasured_negative_pairs"] == 1
    assert checked["violated_negatives"] == checked["missing_required"] == []
    assert checked["partial_recovered_pairs_diagnostic"] == 1


def test_dependency_variant_keeps_five_first_party_modules():
    folder = ROOT / "nonweb"
    case = json.loads((folder / "manifest.json").read_text())["cases"][1]
    config = configuration(folder, case, False)
    assert not set(config["source_roots"]) & set(config["dependency_source_roots"])
    assert len(config["source_roots"]) == 2
    assert verify_sources(folder, case) > 10


def test_whole_application_source_remains_byte_identical():
    folder = ROOT / "web"
    case = json.loads((folder / "manifest.json").read_text())["cases"][0]
    assert verify_sources(folder, case) == 22
    assert configuration(folder, case, True)["external_frameworks"]["flask"] == "3.1.2"


def test_static_worker_never_executes_target_source(tmp_path):
    marker = tmp_path / "must_not_exist"
    (tmp_path / "main.py").write_text(f"open({str(marker)!r}, 'w').write('executed')\n")
    result = run_isolated({"project_root": str(tmp_path), "entry_file": "main.py"}, timeout=20)
    assert result["analysis_executed"]
    assert result["target_source_executed"] is False
    assert not marker.exists()


@pytest.mark.parametrize("timeout,memory", [(0, 256), (float('nan'), 256), (True, 256), (30, True), (30, 64)])
def test_invalid_process_budgets_are_rejected(timeout, memory):
    with pytest.raises(ValueError):
        run_isolated({}, timeout=timeout, memory_mb=memory)
