"""Project consumed-source admission, independently checked from finite AST.

Positive controls use the actual project worker, adapter and storage reader.
Negative controls are explicitly controlled metadata mutations of those sealed
bundles; they do not claim naturally changed source or failed native reads.
"""
import hashlib

import pytest

from pythonstan_inspector.checker import check
from pythonstan_inspector.collector_checker import CHANNELS, GENERATION
from pythonstan_inspector.contract import CaptureRequest, QueryRequest, SealedSnapshot
from pythonstan_inspector.project_runner import run_project
from pythonstan_inspector.storage import load_bundle, write_bundle


SOURCE = "def take(**kw):\n    return next(iter(kw.keys()))\nresult = take(x=1)\n"
SOURCE_SHA = hashlib.sha256(SOURCE.encode()).hexdigest()
VIEWS = ("node_points", "ctx_points", "pfg_edges", "call_edges", "kwargs_model")


def _request(claim):
    return QueryRequest("check", {"checker": claim, "source_text": SOURCE}, claim=claim)


@pytest.fixture(scope="module", params=[None, "0-cfa"], ids=["project_default_context", "explicit_0cfa"])
def project_capture(request, tmp_path_factory):
    base = tmp_path_factory.mktemp("project_collector_source")
    project = base / "project"
    project.mkdir()
    source = project / "app.py"
    source.write_text(SOURCE)
    configuration = {"project_root": str(project), "entry_file": "app.py",
                     "auto_framework_entries": False, "max_iterations": 5000}
    if request.param is not None:
        configuration["context_policy"] = request.param
    response = run_project(configuration, base / "incident", timeout_seconds=15,
        request=CaptureRequest(views=VIEWS), observation_mode="targeted",
        trace_selector={"file": "app.py", "line": 3, "column": 9, "model": "keyword_collector"})
    assert response["status"] == "worker_finished", response
    assert response["capture_state"]["status"] == "sealed", response["capture_state"]
    return load_bundle(response["bundle"]), source, response


def test_real_targeted_project_source_is_consumed_bound_for_both_named_claims(project_capture):
    bundle, source, response = project_capture
    run = bundle.data["run"]
    assert type(run["inputs"]) is dict
    assert run["source_validation"] == "pre_post_verified_with_consumed_hashes"
    assert run["configuration"]["builtin_bootstrap_policy"] == "queued"
    binding = run["selected_source_binding"]
    assert type(binding["version"]) is int and binding["version"] == 1
    assert binding["semantic_version"] == "native_pre_consumed_post_source_v1"
    assert binding["scope"] == "declared_targeted_source_file"
    assert binding["rows"] == [{"path": str(source), "sha256_before": SOURCE_SHA,
        "consumed_sha256": SOURCE_SHA, "sha256_after": SOURCE_SHA, "source_stable": True}]
    assert run["observation"]["selector_source"]["native_source_admission"] == "consumed"
    assert response["worker_incident_receipt"]["observation"]["selector_source"]["consumed_source_binding"] == "matched_initial_selection"
    for claim, expected in ((GENERATION, 4), (CHANNELS, 3)):
        result = check(bundle, _request(claim))
        assert result["answer"] == "passed", result
        assert result["claim"] == result["requested_claim"] == claim
        assert result["obligations_expected"] == result["obligations_examined"] == expected
        assert result["obligations_unexamined"] == 0
        assert result["source_binding_scope"] == "verified_targeted_file_not_entire_project_source_spec"


@pytest.mark.parametrize("variant,expected", [
    ("binding_omitted", "unsupported"),
    ("unknown_version", "unsupported"),
    ("boolean_version", "unsupported"),
    ("unknown_semantics", "unsupported"),
    ("whole_project_scope", "unsupported"),
    ("wrong_consumed", "indeterminate"),
    ("unverified", "indeterminate"),
    ("source_changed", "indeterminate"),
    ("unstable_selected_file", "indeterminate"),
    ("not_consumed", "indeterminate"),
    ("unmatched_selection", "indeterminate"),
    ("wrong_catalog_algorithm", "indeterminate"),
    ("wrong_selector_file", "indeterminate"),
])
def test_controlled_project_binding_gap_cannot_pass(project_capture, tmp_path, variant, expected):
    bundle, _, _ = project_capture
    data = bundle.data
    run = data["run"]
    binding = run["selected_source_binding"]
    if variant == "binding_omitted":
        del run["selected_source_binding"]
    elif variant == "unknown_version":
        binding["version"] = 2
    elif variant == "boolean_version":
        binding["version"] = True
    elif variant == "unknown_semantics":
        binding["semantic_version"] = "unknown_future_source_binding"
    elif variant == "whole_project_scope":
        binding["scope"] = "whole_project"
    elif variant == "wrong_consumed":
        binding["rows"][0]["consumed_sha256"] = "0" * 64
    elif variant == "unverified":
        run["source_validation"] = "pending"
    elif variant == "source_changed":
        run["source_validation"] = "changed_during_analysis"
    elif variant == "unstable_selected_file":
        binding["rows"][0]["source_stable"] = False
    elif variant == "not_consumed":
        run["observation"]["selector_source"]["native_source_admission"] = "inventoried_not_consumed"
    elif variant == "unmatched_selection":
        run["observation"]["selector_source"]["consumed_source_binding"] = "unverified"
    elif variant == "wrong_catalog_algorithm":
        run["inputs"]["identity_algorithm"] = "unknown_future_catalog"
    else:
        run["observation"]["selector"]["file"] += ".other"
    controlled = load_bundle(write_bundle(SealedSnapshot.seal(data), tmp_path / "controlled_binding"))
    for claim in (GENERATION, CHANNELS):
        result = check(controlled, _request(claim))
        assert result["answer"] == expected, result
        assert result["obligations_expected"] > 0
        assert result["obligations_examined"] == 0


def test_real_untargeted_project_compact_catalog_is_not_a_per_file_source_proof(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    (project / "app.py").write_text(SOURCE)
    response = run_project({"project_root": str(project), "entry_file": "app.py",
        "auto_framework_entries": False, "max_iterations": 5000}, tmp_path / "incident",
        timeout_seconds=15, request=CaptureRequest(views=VIEWS), observation_mode="capture")
    assert response["capture_state"]["status"] == "sealed", response
    bundle = load_bundle(response["bundle"])
    assert type(bundle.data["run"]["inputs"]) is dict
    assert "selected_source_binding" not in bundle.data["run"]
    for claim in (GENERATION, CHANNELS):
        result = check(bundle, _request(claim))
        assert result["answer"] == "unsupported", result
        assert result["obligations_expected"] > 0
