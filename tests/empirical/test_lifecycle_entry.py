"""Declared lifecycle entries align static and runtime benchmark assumptions.

Source bytes and reviewed gold stay frozen. Runtime traces and required target
names have no role in producing the static analysis source.
"""
import ast
import copy
import hashlib

import pytest

from benchmark.empirical import runner
from tests.analysis.legacy_ai_support import isolated_world


SOURCE = "def callback(): return 1\nasync def job(): return callback()\npending = job()\n"


def test_static_adapter_is_identity_without_an_await_global_declaration():
    source, assumption = runner.static_entry_source({}, SOURCE)
    assert source == SOURCE
    assert assumption["status"] == "not_declared"
    assert assumption["operation"] is None
    assert runner.digest(source.encode()) == runner.digest(SOURCE.encode())


@pytest.mark.parametrize("invalid", ["", "queue()", "pending.attr", "pending; callback()", "await", False, 1, [], {}])
def test_lifecycle_symbol_is_validated_as_an_identifier(invalid):
    with pytest.raises(ValueError, match="single non-keyword Python identifier"):
        runner.static_entry_source({"await_global": invalid}, SOURCE)


def test_declared_adapter_preserves_source_ast_and_ignores_gold_and_runtime_targets():
    case = {"await_global": "pending"}
    expected, assumption = runner.static_entry_source(case, SOURCE)
    poisoned = {**case, "required_edges": [["invented", "not_a_source_target"]],
        "runtime_lower_bound": {"edges": [["runtime_only", "also_not_a_source_target"]]},
        "effects": [{"runtime_global": "do_not_seed_this"}]}
    assert runner.static_entry_source(poisoned, SOURCE) == (expected, assumption)
    assert expected.startswith(SOURCE)
    before, after = ast.parse(SOURCE), ast.parse(expected)
    assert [ast.dump(node) for node in after.body[:len(before.body)]] == [ast.dump(node) for node in before.body]
    resume = after.body[-1].body[0].value
    assert isinstance(resume, ast.Call)
    assert isinstance(resume.func, ast.Attribute) and resume.func.attr == "send"
    assert isinstance(resume.func.value, ast.Name) and resume.func.value.id == "pending"
    assert len(resume.args) == 1 and isinstance(resume.args[0], ast.Constant) and resume.args[0].value is None
    assert assumption["full_asyncio_run_equivalence_proved"] is False
    assert assumption["effect_obligations_proved"] is False


@pytest.mark.parametrize("declared", [False, True])
def test_real_static_pipeline_resumes_only_the_declared_lifecycle_entry(tmp_path, monkeypatch, declared):
    path = tmp_path / "subject.py"
    path.write_text(SOURCE)
    original = path.read_bytes()
    case = {"id": "isolated_lifecycle_fixture", "source": "subject.py",
        "sha256": hashlib.sha256(original).hexdigest(), "provenance": [],
        "required_edges": [["job", "callback"]], "forbidden_edges": [], "effects": []}
    if declared:
        case["await_global"] = "pending"
    monkeypatch.setattr(runner, "ROOT", tmp_path)
    with isolated_world():
        result = runner.static_worker(case, ai_enabled=False)
    assert result["status"] == "completed"
    assert (["job", "callback"] in [list(edge) for edge in result["call_edges"]]) is declared
    assert result["original_source_sha256"] == case["sha256"]
    assert (result["effective_analysis_source_sha256"] != case["sha256"]) is declared
    assert result["original_source_unchanged_after_analysis"] is True
    assert path.read_bytes() == original
    assert result["effect_obligations_discharged_by_kcfa"] == 0
    assert result["soundness_proved"] is False


HOLDOUTS = [case for case in runner.read_manifest(runner.ROOT / "holdouts/manifest.json")["cases"]
            if case.get("await_global")]


@pytest.mark.parametrize("case", HOLDOUTS, ids=lambda case: case["id"])
def test_frozen_starlette_holdouts_record_explicit_entry_without_modifying_gold(case):
    source_path = runner.ROOT / case["source"]
    manifest_path = runner.ROOT / "holdouts/manifest.json"
    source_before, manifest_before = source_path.read_bytes(), manifest_path.read_bytes()
    gold_before = copy.deepcopy((case["required_edges"], case["forbidden_edges"]))
    result = runner.run_worker(case, "static", ai_enabled=True)
    assert result["status"] == "completed", result
    assert result["call_obligations"]["recovered_count"] == len(case["required_edges"])
    assert not result["call_obligations"]["violated_negative"]
    assert result["original_source_sha256"] == case["sha256"]
    assert result["effective_analysis_source_sha256"] != case["sha256"]
    assert result["lifecycle_entry_assumption"]["symbol"] == case["await_global"]
    assert result["lifecycle_entry_assumption"]["status"] == "declared_first_resume_may_entry"
    assert result["effect_obligations_discharged_by_kcfa"] == 0
    assert source_path.read_bytes() == source_before
    assert manifest_path.read_bytes() == manifest_before
    assert (case["required_edges"], case["forbidden_edges"]) == gold_before
