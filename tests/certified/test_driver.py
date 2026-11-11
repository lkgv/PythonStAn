"""Integration tests: rejected candidates never become legacy AI summaries."""
import ast
import json
from pathlib import Path
import subprocess
import sys

from pythonstan.analysis import AnalysisConfig
from pythonstan.analysis.pointer.ai.certified import certificate_to_dict, infer_candidate
from pythonstan.analysis.pointer.ai.certified.driver import analyze_source
from pythonstan.ir import IRModule
from pythonstan.world.analysis_manager import AnalysisManager


SOURCE = "def leaf():\n    pass\n\ndef entry():\n    leaf()\n"


def test_baseline_is_checked_and_offline():
    report = analyze_source(SOURCE)
    assert report.certified
    assert report.proposal_status == "baseline"
    assert report.to_dict()["residual"] is None


def test_rejected_stale_certificate_can_fall_back_without_applying_it():
    candidate = infer_candidate(SOURCE)
    changed = SOURCE + "\ndef additional():\n    entry()\n"
    refused = analyze_source(changed, candidate=candidate, allow_fallback=False)
    assert not refused.certified
    assert refused.status == "rejected"
    recovered = analyze_source(changed, candidate=candidate)
    assert recovered.certified
    assert recovered.fallback_used
    assert recovered.proposal_status == "supplied"
    assert recovered.candidate_check_status == "rejected"
    assert recovered.baseline_check_status == "accepted"
    assert recovered.source_sha256 != certificate_to_dict(candidate)["source_sha256"]


def test_unsupported_dynamic_code_is_not_reported_effect_free():
    report = analyze_source("def entry(x):\n    return x.dynamic()\n")
    assert report.status == "unsupported"
    assert not report.certified
    assert report.to_dict()["residual"]["may_modify_heap"]


def test_report_serialization_does_not_share_mutable_certificate():
    report = analyze_source(SOURCE)
    serialized = report.to_dict()
    serialized["certificate"].clear()
    assert report.to_dict()["certificate"]


def test_framework_driver_uses_original_file_not_modified_ir(tmp_path):
    path = tmp_path / "program.py"
    path.write_text(SOURCE)
    # Deliberately hand the pass a different transformed AST.
    scope = IRModule("program", ast.parse("pass"), filename=str(path))
    config = AnalysisConfig("checked", "SourceCertificateDriver", options={
        "type": "certified analysis",
    })
    driver = AnalysisManager().gen_analyzer(config)
    report = driver.analyze(scope, {"legacy": object()})
    assert report.certified
    assert report.source_sha256 == analyze_source(SOURCE).source_sha256
    assert driver.results is report


def test_cli_reports_unsupported_with_nonzero_exit(tmp_path):
    root = Path(__file__).resolve().parents[2]
    path = tmp_path / "dynamic.py"
    path.write_text("def entry(x):\n    return x.dynamic()\n")
    run = subprocess.run([sys.executable, str(root / "scripts/do_certified.py"), str(path)],
                         capture_output=True, text=True, timeout=20)
    assert run.returncode == 2
    payload = json.loads(run.stdout)
    assert payload["status"] == "unsupported"
    assert not payload["certified"]


def test_pipeline_pass_end_to_end(tmp_path):
    from pythonstan.world.pipeline import Pipeline
    path = tmp_path / "example.py"
    path.write_text(SOURCE)
    pipeline = Pipeline(config={
        "filename": str(path), "project_path": str(tmp_path), "library_paths": [],
        "lazy_ir_construction": True,
        "analysis": [{"name": "checked", "id": "SourceCertificateDriver",
                      "description": "original-source certificate check",
                      "prev_analysis": [], "options": {"type": "certified analysis"}}],
    })
    pipeline.run()
    assert pipeline.analysis_manager.get_results("checked").certified


def test_inference_budget_is_exercised_not_just_hashed():
    report = analyze_source(SOURCE, analysis_config={"inference_max_iterations": 1})
    assert report.status == "unknown" and not report.certified
    assert report.baseline_inference_status == "unknown"
    assert report.to_dict()["residual"]["may_call_any"]
    assert analyze_source(SOURCE, analysis_config={"inference_max_iterations": 10}).certified


def test_checker_budget_is_exercised_independently():
    large = "def entry():\n" + "    pass\n" * 1100
    candidate = infer_candidate(large)
    report = analyze_source(large, candidate=candidate, allow_fallback=False,
                            analysis_config={"checker_max_iterations": 1})
    assert report.status == "unknown" and report.candidate_check_status == "unknown"
    assert report.proposal_status == "supplied"
    assert analyze_source(large, candidate=candidate,
                          analysis_config={"checker_max_iterations": 10}).certified


def test_certified_configuration_rejects_typos_and_non_boolean_controls():
    import pytest
    from pythonstan.analysis.pointer.ai.certified.driver import SourceAnalysisConfig
    for options in ({"checker_max_iteration": 10}, {"inference_max_iterations": True},
                    {"checker_max_iterations": 0}, {"llm": {"api_key": "not-allowed"}},
                    {"llm": {"max_calls": -1}}, {"llm": {"model": "different-model"}}):
        with pytest.raises(ValueError):
            SourceAnalysisConfig.from_dict(options)
    with pytest.raises(ValueError):
        analyze_source(SOURCE, allow_fallback="false")
    with pytest.raises(ValueError):
        analyze_source(SOURCE, analysis_config={"max_source_bytes": 1})


def test_driver_shares_llm_budget_and_retains_proposal_metrics(tmp_path, monkeypatch):
    from pythonstan.analysis.pointer.ai.certified import llm
    from pythonstan.analysis.pointer.ai.certified.driver import SourceCertificateDriver
    from dataclasses import asdict
    calls = []
    class Proposer:
        def __init__(self, config):
            self.config = config
            calls.append(self)
        def propose(self, source, *, analysis_config):
            assert analysis_config["llm"]["max_calls"] == 0
            return llm.ProposalResult("unknown", reason="call_budget_exhausted", attempts=0,
                                       total_tokens=0, from_cache=False)
    monkeypatch.setattr(llm, "OpenAICandidateProposer", Proposer)
    path = tmp_path / "example.py"
    path.write_text(SOURCE)
    scope = IRModule("example", ast.parse(SOURCE), filename=str(path))
    driver = SourceCertificateDriver(AnalysisConfig("cert", "SourceCertificateDriver", options={
        "type": "certified analysis", "mode": "llm",
        "analysis_config": {"llm": {"max_calls": 0}},
    }))
    first, second = driver.analyze(scope, {}), driver.analyze(scope, {})
    assert len(calls) == 1
    assert first.certified and second.certified and second.fallback_used
    assert second.proposal_status == "unknown"
    assert second.baseline_check_status == "accepted"
    payload = second.to_dict()
    assert payload["proposal_metrics"]["total_tokens"] == 0
    assert payload["effective_config"]["llm"]["max_calls"] == 0


def test_cli_budget_option_yields_unknown(tmp_path):
    root = Path(__file__).resolve().parents[2]
    path = tmp_path / "program.py"
    path.write_text(SOURCE)
    run = subprocess.run([sys.executable, str(root / "scripts/do_certified.py"), str(path),
                          "--inference-max-iterations", "1"], capture_output=True, text=True, timeout=20)
    assert run.returncode == 2
    assert json.loads(run.stdout)["baseline_inference_status"] == "unknown"


def test_direct_candidate_byte_limit_cannot_be_bypassed():
    candidate = infer_candidate(SOURCE)
    refused = analyze_source(SOURCE, candidate=candidate, allow_fallback=False,
                             analysis_config={"max_candidate_bytes": 1})
    assert refused.status == "unknown" and not refused.certified
    recovered = analyze_source(SOURCE, candidate=candidate,
                               analysis_config={"max_candidate_bytes": 1})
    assert recovered.certified and recovered.fallback_used
    assert recovered.candidate_check_status == "unknown"
    assert recovered.baseline_check_status == "accepted"
