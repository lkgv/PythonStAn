"""Project catalogs are comparison evidence, not per-file source specimens."""
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

from pythonstan_inspector.checker import MODULE_COPY_SPEC, check
from pythonstan_inspector.contract import InspectorError, QueryLimits, QueryRequest, SealedSnapshot, canonical_bytes
from pythonstan_inspector.core import diff_query, query
from pythonstan_inspector.storage import load_bundle, write_bundle

_spec = importlib.util.spec_from_file_location("project_identity_fixtures", Path(__file__).with_name("fixtures.py"))
_fixtures = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_fixtures)


def _catalog():
    return {"identity_algorithm": "sha256_ascii_canonical_json_rows_v1",
            "inventory_sha256": "a" * 64, "inventory_count": 1,
            "consumed_sha256": "b" * 64, "consumed_count": 1,
            "full_identity_rows": "companion native_project_report.json modules"}


def _source_request(source="x = 1\ny = x\n"):
    return QueryRequest("check", {"checker": MODULE_COPY_SPEC, "source_text": source}, claim=MODULE_COPY_SPEC)


@pytest.fixture
def actual_project_observation(tmp_path):
    from pythonstan.analysis.project import ProjectConfig
    from pythonstan_inspector.project_runner import run_project

    project = tmp_path / "project"
    project.mkdir()
    source = "x = 1\ny = x\n"
    (project / "app.py").write_text(source)
    config = ProjectConfig(str(project), "app.py", source_roots=(".",),
                           auto_framework_entries=False, entry_points=(), max_iterations=5000)
    run = run_project(config, tmp_path / "captures", timeout_seconds=20)
    assert run["status"] == "worker_finished", run
    assert run["capture_state"]["status"] == "sealed", run["capture_state"]
    return run, source


def test_actual_project_bundle_catalog_refuses_narrow_per_file_source_claim(actual_project_observation):
    run, source = actual_project_observation
    bundle = load_bundle(run["bundle"])
    assert bundle.data["run"]["inputs"]["inventory_count"] == 1
    result = query(bundle, _source_request(source))
    assert result["check_status"] == result["answer"] == "unsupported"
    assert result["requested_claim"] == result["claim"] == MODULE_COPY_SPEC
    assert result["evidence"] == []
    assert "not_catalog" in result["diagnostics"][0]
    same = diff_query(bundle, bundle, QueryRequest("diff", {"view": "node_points"}))
    assert same["comparability"]["comparable"] is True
    assert same["answer"] == "structural_diff"


def test_supported_project_catalog_allows_conditional_self_comparison():
    data = _fixtures.artifact()
    data["run"]["inputs"] = _catalog()
    data["run"]["source_validation"] = "pre_post_verified_with_consumed_hashes"
    bundle = _fixtures.bundle(data)
    result = diff_query(bundle, bundle, QueryRequest("diff"))
    assert result["comparability"]["comparable"] is True
    assert result["answer"] == "structural_diff"
    assert result["evidence"] == []
    assert check(bundle, _source_request())["check_status"] == "unsupported"


@pytest.mark.parametrize("mutation", [
    lambda value: value.update(identity_algorithm="future_source_catalog_v2"),
    lambda value: value.update(inventory_sha256="A" * 64),
    lambda value: value.update(consumed_sha256="broken"),
    lambda value: value.update(inventory_count=-1),
    lambda value: value.update(consumed_count=2),
    lambda value: value.update(consumed_count=True),
    lambda value: value.update(inventory_count=2**63),
])
def test_poisoned_or_future_catalog_cannot_authorize_even_identical_diff(mutation):
    data = _fixtures.artifact()
    catalog = _catalog()
    mutation(catalog)
    data["run"]["inputs"] = catalog
    bundle = _fixtures.bundle(data)
    result = diff_query(bundle, bundle, QueryRequest("diff"))
    assert result["comparability"]["comparable"] is False
    assert result["answer"] == "incomparable"
    assert result["comparability"]["invalid_identity_evidence"]["left"]
    source = check(bundle, _source_request())
    assert source["requested_claim"] == MODULE_COPY_SPEC
    assert source["check_status"] == "unsupported"


@pytest.mark.parametrize("inputs", [None, "legacy-source", 1, [None], ["row"], [3],
    [{"path": "subject.py"}],
    [{"path": None, "sha256_before": "a" * 64, "sha256_after": "a" * 64}],
    [{"path": "subject.py", "sha256_before": [], "sha256_after": "a" * 64}],
])
def test_malformed_legacy_identity_is_typed_unsupported_without_weaker_claim(inputs):
    data = _fixtures.artifact()
    data["run"]["inputs"] = inputs
    result = check(_fixtures.bundle(data), _source_request())
    assert result["answer"] == result["check_status"] == "unsupported"
    assert result["requested_claim"] == result["claim"] == MODULE_COPY_SPEC
    assert result["evidence"] == []


@pytest.mark.parametrize("inputs", [[None], ["row"], "unsupported_encoding"])
def test_malformed_legacy_identity_also_blocks_self_diff(inputs):
    data = _fixtures.artifact()
    data["run"]["inputs"] = inputs
    bundle = _fixtures.bundle(data)
    result = diff_query(bundle, bundle, QueryRequest("diff"))
    assert result["answer"] == "incomparable"
    assert result["comparability"]["comparable"] is False


def test_non_utf8_python_source_string_is_explicitly_unsupported_and_serializable():
    source = "x = '\ud800'\ny = x\n"
    result = query(_fixtures.bundle(), _source_request(source))
    assert result["answer"] == result["check_status"] == "unsupported"
    assert result["requested_claim"] == MODULE_COPY_SPEC
    assert result["diagnostics"] == ["source_text_not_valid_utf8"]
    description = result["parameters"]["source_text"]
    assert description["representation"] == "python_unicode_escape_v1"
    assert description["text"].encode("ascii").decode("unicode_escape") == source
    assert canonical_bytes(result)


def test_unknown_view_semantics_never_prove_narrow_source_registration():
    source = "x = 1\ny = x\n"
    checksum = hashlib.sha256(source.encode()).hexdigest()
    data = _fixtures.artifact()
    data["run"].update(source_validation="pre_post_verified",
        inputs=[{"path": "subject.py", "sha256_before": checksum, "sha256_after": checksum}])
    data["snapshot"]["views"]["pfg_edges"]["semantic_version"] = "future_storage_v2"
    result = check(_fixtures.bundle(data), _source_request(source))
    assert result["answer"] == "unsupported"
    assert result["requested_claim"] == MODULE_COPY_SPEC


def test_unknown_snapshot_major_is_refused_before_any_identity_claim(tmp_path):
    data = _fixtures.artifact()
    data["schema"]["major"] = 2
    with pytest.raises(InspectorError, match="major"):
        write_bundle(SealedSnapshot.seal(data), tmp_path / "unknown")


def test_checker_compact_coverage_preserves_full_claim_and_independent_byte_bound():
    data = _fixtures.artifact()
    data["snapshot"]["entities"][2]["attributes"]["native_summary"] = False
    data["snapshot"]["views"]["node_points"]["coverage"]["optional_description"] = "x" * 100000
    from pythonstan_inspector.checker import COPY_CLOSURE
    request = QueryRequest("check", {"checker": COPY_CLOSURE}, claim=COPY_CLOSURE,
                           limits=QueryLimits(max_bytes=65536))
    result = check(_fixtures.bundle(data), request)
    assert result["check_status"] == "passed"
    assert result["requested_claim"] == result["claim"] == COPY_CLOSURE
    assert result["coverage"]["node_points"]["captured_owner_count"] == 2
    assert "owners" not in result["coverage"]["node_points"]
    # Independent encoding verifies the whole result, not implementation counters.
    assert len(json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()) <= 65536
    data["snapshot"]["views"]["node_points"]["coverage"]["owners"]["8"]["fields"]["facts"] = "partial"
    assert check(_fixtures.bundle(data), request)["check_status"] == "indeterminate"


def test_failed_checker_keeps_concrete_witness_after_coverage_compaction():
    from pythonstan_inspector.checker import COPY_CLOSURE
    data = _fixtures.artifact()
    data["snapshot"]["entities"][2]["attributes"]["native_summary"] = False
    points = data["snapshot"]["views"]["node_points"]
    points["rows"] = [row for row in points["rows"] if row["owner"] != 8]
    points["coverage"]["optional_description"] = "x" * 100000
    result = check(_fixtures.bundle(data), QueryRequest("check", {"checker": COPY_CLOSURE},
        claim=COPY_CLOSURE, limits=QueryLimits(max_bytes=65536)))
    assert result["check_status"] == "failed"
    assert result["evidence"][0]["missing_consequence"]["owner"]["local_id"] == 8
    assert result["evidence"][0]["target_field_coverage"] == "complete"
    assert len(canonical_bytes(result)) <= 65536


def test_seven_query_helper_uses_real_small_project_bundle(actual_project_observation):
    from benchmark.round12_projects.inspect_bundles import LIMITS, OPERATIONS, query_bundle
    run, _ = actual_project_observation
    result = query_bundle(load_bundle(run["bundle"]))
    assert set(result["queries"]) == set(OPERATIONS)
    assert all(item["status"] == "returned" for item in result["queries"].values())
    assert result["queries"]["show"]["result"]["answer"] == "present_at_snapshot"
    assert result["queries"]["diff"]["result"]["comparability"]["comparable"] is True
    assert result["queries"]["check"]["result"]["requested_claim"] == "registered_normal_pfg_closure_v1"
    assert result["selected_point"]["owner"] is not None
    assert all(len(canonical_bytes(item["result"])) <= LIMITS.max_bytes
               for item in result["queries"].values())


def test_protocol_postprocessor_preserves_failed_checker_evidence():
    from benchmark.round12_projects.inspect_bundles import query_bundle
    data = _fixtures.artifact()
    data["snapshot"]["entities"][2]["attributes"]["native_summary"] = False
    points = data["snapshot"]["views"]["node_points"]
    points["rows"] = [row for row in points["rows"] if row["owner"] != 8]
    outcome = query_bundle(_fixtures.bundle(data))["queries"]["check"]
    assert outcome["status"] == "returned"
    assert outcome["result"]["check_status"] == "failed"
    assert outcome["result"]["evidence"][0]["missing_consequence"]["owner"]["local_id"] == 8


def test_missing_obs_trials_keep_entire_original_denominator(tmp_path):
    from benchmark.round12_projects.inspect_bundles import inspect_cohort
    from benchmark.round8_corpus.registry import projects
    records = projects()
    plan = {"schema_version": 1, "plan_id": "a" * 64,
        "identity": {"arm": "observed", "engine_sha256": "b" * 64,
                     "source_identity_sha256": {row["id"]: "c" * 64 for row in records}},
        "project_order": [row["id"] for row in records], "declared_projects": 14,
        "declared_runtime_python_files": 8482}
    (tmp_path / "plan.json").write_text(json.dumps(plan))
    report = inspect_cohort(tmp_path)
    assert report["declared_projects"] == len(report["rows"]) == 14
    assert report["project_outcomes"] == {"trial_missing": 14}
    assert report["query_outcomes"] == {"not_run": 14 * 7}
    assert report["whole_program_soundness_proved"] is False
    plan["project_order"].pop()
    (tmp_path / "plan.json").write_text(json.dumps(plan))
    with pytest.raises(InspectorError, match="cohort"):
        inspect_cohort(tmp_path)


def test_query_budget_refusal_is_not_a_passing_outcome():
    from benchmark.round12_projects.inspect_bundles import _outcome

    def refused():
        raise InspectorError("query budget admission refused before snapshot decoding")

    result = _outcome(refused)
    assert result["status"] == "refused"
    assert "result" not in result
