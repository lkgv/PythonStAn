import copy
from dataclasses import asdict
import hashlib
import json
from pathlib import Path

import pytest

from benchmark.round8_corpus import collect_results
from benchmark.round8_security import run_challenges
from pythonstan.analysis.project import ProjectConfig


CORE = "a" * 64
SOURCE = "def f(x):\n    return sink(x)\n\ndef g(x):\n    return 'other'\n"


def sha(content):
    return hashlib.sha256(content).hexdigest()


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n")


@pytest.fixture
def cohort(tmp_path):
    cache = tmp_path / "cache"
    source = cache / "p/pkg/a.py"
    source.parent.mkdir(parents=True)
    source.write_text(SOURCE)
    record = {"id": "p", "cohort": "web", "project_root": "p", "python_files": 1,
              "source_roots": ["pkg"], "import_roots": ["."], "entry_file": "pkg/a.py",
              "entry_points": ["pkg.a.f"],
              "source_files": [{"path": "pkg/a.py", "sha256": sha(source.read_bytes())}]}
    return {"cache": cache, "record": record, "records": {"p": record}, "evidence": tmp_path / "evidence"}


def anchor(cohort, qualname="pkg.a.f", line=2, expression="sink(x)"):
    return {"source": "pkg/a.py", "sha256": cohort["record"]["source_files"][0]["sha256"],
            "qualname": qualname, "line": line, "expression": expression}


def case(cohort):
    return {"id": "p_complex", "project_id": "p", "type": "trust_and_flow",
            "security_question": "Does an externally trusted path reach this operation?",
            "status": "unproved", "vulnerability_label": None,
            "required_properties": ["independently prove endpoint trust and argument transfer"],
            "relations": ["f and g require an independently justified relation"],
            "blockers": ["source anchors do not prove call or trust relations"],
            "anchors": [anchor(cohort), anchor(cohort, "pkg.a.g", 5, "'other'")]}


def write_trials(cohort, modes=("baseline", "current")):
    directory = cohort["evidence"] / "round8_projects"
    rows = []
    for mode in modes:
        config = {"project_root": str(cohort["cache"] / "p"), "entry_file": "pkg/a.py",
                  "source_roots": ["pkg"], "import_roots": ["."], "entry_points": ["pkg.a.f"],
                  "context_policy": "1-cfa", "index_sensitive": True,
                  "enable_ai_intrinsics": mode == "current", "synthetic_method_contexts": False,
                  "max_modules": 10000, "max_source_bytes": 200 * 1024 * 1024,
                  "max_iterations": 50000, "analysis_stage": "solver"}
        effective = asdict(ProjectConfig.from_dict(config))
        inventory = [{**cohort["record"]["source_files"][0], "ownership": "first_party",
                      "parsed": True, "lowered": True, "translated": True}]
        report = {"status": "completed_with_residuals", "solver_completed": True,
                  "first_party_translation_complete": True, "frontend_errors": [],
                  "unresolved_entries": [], "implementation_stable": True,
                  "implementation_sha256_before": CORE, "implementation_sha256_after": CORE,
                  "source_snapshot_stable": True, "modules": inventory, "inventory": inventory,
                  "effective_config": effective,
                  "configuration_sha256": sha(json.dumps(effective, sort_keys=True, separators=(",", ":")).encode()),
                  "coverage": {"discovered_python_files": 1, "whole_declared_first_party_source_lowered": True,
                               "whole_declared_first_party_module_constraints_translated": True,
                               "unprocessed_files": 0}, "call_edges": [], "statistics": {}}
        path = directory / ("p_" + mode + ".json")
        write_json(path, {"project_id": "p", "mode": mode, "configuration": config, "report": report})
        rows.append({"project_id": "p", "mode": mode, "path": path.name, "sha256": sha(path.read_bytes()),
                     "source_tree_after_verified": True, "status": report["status"]})
    write_json(directory / "summary.json", {"rows": rows, "implementation_stable": True,
        "implementation_sha256_before": CORE, "implementation_sha256_after": CORE,
        "projects": 1, "trials": len(rows), "project_ids": ["p"],
        "integrity": {"p": {"source_integrity_verified": True}}})
    return directory


def mutate_trial(directory, mutation, mode="current", rebind=True):
    path = directory / ("p_" + mode + ".json")
    bound = json.loads(path.read_text())
    mutation(bound)
    write_json(path, bound)
    if rebind:
        summary_path = directory / "summary.json"
        summary = json.loads(summary_path.read_text())
        for row in summary["rows"]:
            if row["path"] == path.name:
                row["sha256"] = sha(path.read_bytes())
        write_json(summary_path, summary)


def challenge_setup(cohort, monkeypatch, challenges=None):
    manifests = cohort["evidence"] / "challenge_manifests"
    monkeypatch.setattr(run_challenges, "HERE", manifests)
    monkeypatch.setattr(run_challenges, "projects", lambda: list(cohort["records"].values()))
    for name in ("web", "ops", "ai"):
        write_json(manifests / (name + "_challenges.json"),
                   {"challenges": (challenges if challenges is not None else [case(cohort)]) if name == "web" else []})
    write_trials(cohort)
    return lambda: run_challenges.run(cohort["cache"], cohort["evidence"], cohort["evidence"] / "verified.json")


def test_anchor_rejects_correct_node_under_wrong_function(cohort):
    with pytest.raises(ValueError, match="AST/function/line"):
        run_challenges.verify_anchor(anchor(cohort, qualname="pkg.a.g"), cohort["record"], cohort["cache"])


def test_anchor_rejects_wrong_source_hash(cohort):
    wrong = anchor(cohort)
    wrong["sha256"] = "0" * 64
    with pytest.raises(ValueError, match="source hash"):
        run_challenges.verify_anchor(wrong, cohort["record"], cohort["cache"])


def test_completed_solver_and_exact_anchors_do_not_promote_trust_flow_or_vulnerability(cohort, monkeypatch):
    run = challenge_setup(cohort, monkeypatch)
    result = run()
    assert result["source_grounded_challenges"] == result["unproved_challenges"] == 1
    assert result["proved_challenges"] == result["proof_obligation_completion"] == 0
    row = result["rows"][0]
    assert row["solver_completed"] is True and row["status"] == "unproved"
    assert row["proof_obligations_discharged"] is False and row["vulnerability_label"] is None
    assert result["vulnerability_precision"] is result["vulnerability_recall"] is None


def test_missing_project_obligation_cannot_shrink_denominator(cohort, monkeypatch):
    cohort["records"]["q"] = {**cohort["record"], "id": "q"}
    run = challenge_setup(cohort, monkeypatch)
    with pytest.raises(ValueError, match="one advanced obligation"):
        run()


def test_duplicate_challenge_cannot_create_an_extra_obligation(cohort, monkeypatch):
    gold = case(cohort)
    run = challenge_setup(cohort, monkeypatch, [gold, copy.deepcopy(gold)])
    with pytest.raises(ValueError, match="unique"):
        run()


def test_duplicate_anchor_cannot_masquerade_as_two_source_sites(cohort, monkeypatch):
    gold = case(cohort)
    gold["anchors"][1] = copy.deepcopy(gold["anchors"][0])
    run = challenge_setup(cohort, monkeypatch, [gold])
    with pytest.raises(ValueError):
        run()


def test_challenge_rejects_report_changed_after_frozen_summary(cohort, monkeypatch):
    run = challenge_setup(cohort, monkeypatch)
    mutate_trial(cohort["evidence"] / "round8_projects", lambda b: b["report"].update(solver_completed=False), rebind=False)
    with pytest.raises(ValueError):
        run()


def test_challenge_rejects_a_different_project_report_in_expected_filename(cohort, monkeypatch):
    run = challenge_setup(cohort, monkeypatch)
    mutate_trial(cohort["evidence"] / "round8_projects", lambda b: b.update(project_id="q"))
    with pytest.raises(ValueError):
        run()


def test_partial_frontend_is_not_whole_project_completion(cohort):
    directory = write_trials(cohort)
    mutate_trial(directory, lambda b: b["report"]["coverage"].update(unprocessed_files=1))
    result = collect_results.static_results(directory, cohort["records"])
    assert result["whole_declared_source_completed_trials"] == 1


def test_frozen_matrix_rejects_missing_configuration_trial(cohort):
    directory = write_trials(cohort, modes=("baseline",))
    with pytest.raises(ValueError):
        collect_results.static_results(directory, cohort["records"])


def test_frozen_matrix_rejects_unregistered_mode(cohort):
    directory = write_trials(cohort)
    mutate_trial(directory, lambda b: b.update(mode="unregistered"))
    with pytest.raises(ValueError):
        collect_results.static_results(directory, cohort["records"])


def test_same_inventory_count_does_not_establish_source_hash_binding(cohort):
    directory = write_trials(cohort)
    def mutation(bound):
        bound["report"]["inventory"][0]["sha256"] = "b" * 64
        bound["report"]["modules"][0]["sha256"] = "b" * 64
    mutate_trial(directory, mutation)
    with pytest.raises(ValueError):
        collect_results.static_results(directory, cohort["records"])


def test_worker_core_hash_must_match_paired_cohort_hash(cohort):
    directory = write_trials(cohort)
    mutate_trial(directory, lambda b: b["report"].update(implementation_sha256_before="b" * 64,
                                                       implementation_sha256_after="b" * 64))
    with pytest.raises(ValueError):
        collect_results.static_results(directory, cohort["records"])


def test_failed_pinned_source_after_check_prevents_whole_completion(cohort):
    directory = write_trials(cohort)
    path = directory / "summary.json"
    summary = json.loads(path.read_text())
    summary["rows"][1]["source_tree_after_verified"] = False
    write_json(path, summary)
    result = collect_results.static_results(directory, cohort["records"])
    assert result["whole_declared_source_completed_trials"] == 1


def test_summary_stability_flag_cannot_override_different_core_hashes(cohort):
    directory = write_trials(cohort)
    path = directory / "summary.json"
    summary = json.loads(path.read_text())
    summary["implementation_sha256_after"] = "b" * 64
    write_json(path, summary)
    with pytest.raises(ValueError):
        collect_results.static_results(directory, cohort["records"])
