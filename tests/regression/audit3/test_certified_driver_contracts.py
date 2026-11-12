"""Offline source-driver boundaries preserve proof and proposal separation."""

from __future__ import annotations

import ast
import json
from collections.abc import Mapping
from dataclasses import dataclass
from os import PathLike
from pathlib import Path
from typing import override

import pytest

from pythonstan.analysis.analysis import AnalysisConfig
from pythonstan.analysis.contracts.json_data import JsonObject, JsonValue, validate_json
from pythonstan.analysis.pointer.ai.certified.driver import (
    SourceAnalysisConfig,
    SourceAnalysisReport,
    SourceCertificateDriver,
    analyze_source,
)
from pythonstan.analysis.pointer.ai.certified.llm import ProposalConfig
from pythonstan.analysis.verification.closed_effects.checker import (
    ProgramCertificate,
    certificate_to_dict,
    infer_candidate,
)
from pythonstan.ir import IRModule, IRScope

SOURCE = "def leaf():\n    pass\ndef entry():\n    leaf()\n"


@dataclass(frozen=True)
class _CandidateRecord:
    # Successful custom providers historically need no unused reason field.
    candidate: ProgramCertificate
    status: str = "external-candidate"
    total_tokens: None = None
    attempts: int = 0


@dataclass(frozen=True)
class _FailureRecord:
    candidate: None = None
    status: str = "external-unavailable"
    reason: str = "offline"
    input_tokens: object = None


class _OfflineProposer:
    def __init__(self, result: object) -> None:
        self.result = result
        self.config = ProposalConfig(max_calls=0)
        self.calls = 0
        self.received: list[Mapping[str, JsonValue]] = []

    def propose(self, source: str, *, analysis_config: Mapping[str, JsonValue]) -> object:
        assert source == SOURCE
        self.calls += 1
        self.received.append(analysis_config)
        return self.result


def test_custom_candidate_without_reason_keeps_status_and_null_metrics() -> None:
    proposer = _OfflineProposer(_CandidateRecord(infer_candidate(SOURCE)))
    report = analyze_source(SOURCE, use_llm=True, proposer=proposer, allow_fallback=False)
    assert report.certified
    assert report.proposal_status == "external-candidate"
    assert report.candidate_check_status == "accepted"
    assert report.baseline_inference_status == "not_attempted"
    payload = report.to_dict()
    assert payload["proposal_metrics"] == {"total_tokens": None, "attempts": 0}
    effective = payload["effective_config"]
    assert isinstance(effective, dict)
    llm_config = effective["llm"]
    assert isinstance(llm_config, dict)
    assert llm_config["max_calls"] == 0
    assert proposer.calls == 1


@pytest.mark.parametrize("fallback", [False, True])
def test_external_failure_never_becomes_a_proof(fallback: bool) -> None:
    report = analyze_source(
        SOURCE,
        use_llm=True,
        proposer=_OfflineProposer(_FailureRecord(input_tokens={"nested": [0, False, None]})),
        allow_fallback=fallback,
    )
    assert report.proposal_status == "external-unavailable"
    assert report.diagnostics == ("proposal:offline",)
    assert report.certified is fallback
    assert report.fallback_used is fallback
    assert report.baseline_check_status == ("accepted" if fallback else "not_attempted")


@pytest.mark.parametrize("metric", [object(), float("nan"), float("inf")])
def test_invalid_provider_metrics_fail_at_the_json_boundary(metric: object) -> None:
    with pytest.raises((TypeError, ValueError)):
        analyze_source(
            SOURCE, use_llm=True, proposer=_OfflineProposer(_FailureRecord(input_tokens=metric))
        )


def test_configuration_export_is_owned_and_complete() -> None:
    config = SourceAnalysisConfig.from_dict({"checker_max_iterations": 3, "llm": {"max_calls": 0}})
    first = config.to_dict()
    llm_config = first["llm"]
    assert isinstance(llm_config, dict)
    llm_config["max_calls"] = 77
    assert config.llm.max_calls == 0
    assert SourceAnalysisConfig.from_dict(config.to_dict()) == config
    assert set(config.to_dict()) == {
        "inference_max_iterations",
        "checker_max_iterations",
        "max_source_bytes",
        "max_candidate_bytes",
        "llm",
    }


def test_report_preserves_tuple_fields_and_arbitrary_json_payload_shape() -> None:
    report = SourceAnalysisReport(
        "unknown",
        "0" * 64,
        "external",
        ("diagnostic",),
        proposal_metrics_json="[null, 0]",
        effective_config_json="null",
    )
    data = report.to_dict()
    assert data["diagnostics"] == ("diagnostic",)
    assert isinstance(data["assumptions"], tuple)
    assert data["proposal_metrics"] == [None, 0]
    assert data["effective_config"] is None
    decoded: object = json.loads(json.dumps(data))
    round_trip = validate_json(decoded)
    assert isinstance(round_trip, dict)
    assert round_trip["diagnostics"] == ["diagnostic"]


def test_arbitrary_candidate_still_uses_checker_rejection_and_fallback() -> None:
    report = analyze_source(SOURCE, candidate=object(), allow_fallback=False)
    assert report.status == "rejected"
    assert report.diagnostics == (
        "candidate:rejected",
        "malformed certificate: certificate must be an object",
    )
    assert report.to_dict()["residual"] == {
        "kind": "unknown_behavior",
        "may_call_any": True,
        "may_modify_heap": True,
        "may_raise": True,
        "may_suspend": True,
    }


class _SourcePath(PathLike[str]):
    def __init__(self, path: Path) -> None:
        self.path = path
        self.calls = 0

    @override
    def __fspath__(self) -> str:
        self.calls += 1
        return str(self.path)


def test_real_framework_accepts_text_pathlike_and_ignores_transformed_ir(tmp_path: Path) -> None:
    path = tmp_path / "program.py"
    path.write_text(SOURCE)
    source_path = _SourcePath(path)
    config = AnalysisConfig(
        "checked",
        "SourceCertificateDriver",
        options={"type": "certified analysis", "source_path": source_path},
    )
    driver = SourceCertificateDriver(config)
    scope = IRModule("program", ast.parse("pass"), filename=str(path))
    report = driver.analyze(scope, {"untrusted_previous_facts": object()})
    assert report.certified
    assert report.source_sha256 == analyze_source(SOURCE).source_sha256
    assert source_path.calls == 1


class _FilenameScope(IRScope):
    def __init__(self, path: Path) -> None:
        super().__init__("custom")
        self.path = path
        self.reads = 0

    def get_filename(self) -> str:
        self.reads += 1
        return str(self.path)


class _DynamicFilenameScope(IRScope):
    def __init__(self, path: Path) -> None:
        super().__init__("dynamic")
        self.path = path
        self.lookups = 0
        self.calls = 0

    def _filename(self) -> str:
        self.calls += 1
        return str(self.path)

    def __getattr__(self, name: str) -> object:
        if name == "get_filename":
            self.lookups += 1
            return self._filename
        raise AttributeError(name)


@pytest.mark.parametrize("dynamic", [False, True])
def test_custom_scope_filename_service_is_preserved(tmp_path: Path, dynamic: bool) -> None:
    path = tmp_path / "custom.py"
    path.write_text(SOURCE)
    scope = _DynamicFilenameScope(path) if dynamic else _FilenameScope(path)
    driver = SourceCertificateDriver(
        AnalysisConfig("checked", "SourceCertificateDriver", options={"type": "certified analysis"})
    )
    report = driver.analyze(scope, {})
    assert report.certified
    assert report.source_sha256 == analyze_source(SOURCE).source_sha256
    if isinstance(scope, _DynamicFilenameScope):
        assert scope.lookups == scope.calls == 1
    else:
        assert scope.reads == 1


def test_candidate_input_budget_remains_distinct_from_checker_budget() -> None:
    candidate = infer_candidate(SOURCE)
    report = analyze_source(
        SOURCE,
        candidate=candidate,
        allow_fallback=False,
        analysis_config={"max_candidate_bytes": 1},
    )
    assert report.status == "unknown"
    assert report.candidate_check_status == "unknown"
    assert report.baseline_check_status == "not_attempted"
    assert report.diagnostics == ("candidate:input_budget_exhausted",)


def test_default_does_not_invoke_optional_proposer(monkeypatch: pytest.MonkeyPatch) -> None:
    def reject_proposal(source: str, *, analysis_config: JsonObject) -> object:
        pytest.fail("offline default invoked proposer")

    monkeypatch.setattr(
        "pythonstan.analysis.pointer.ai.certified.llm.OpenAICandidateProposer.propose",
        reject_proposal,
    )
    assert analyze_source(SOURCE).certified


def test_serializable_metric_containers_keep_legacy_json_projection() -> None:
    nested: object = 0
    for _ in range(150):
        nested = [nested]
    metric = {1: (None, False, nested)}
    report = analyze_source(
        SOURCE,
        use_llm=True,
        proposer=_OfflineProposer(_FailureRecord(input_tokens=metric)),
        allow_fallback=False,
    )
    metrics = report.to_dict()["proposal_metrics"]
    assert isinstance(metrics, dict)
    projected: object = json.loads(json.dumps(metric))
    assert metrics["input_tokens"] == projected


@pytest.mark.parametrize(
    ("llm_options", "message"),
    [
        ({"model": "wrong-model", "max_calls": -1}, "the configured model must be gpt-6-luna"),
        (
            {"max_cache_entries": -1, "max_source_bytes": 0},
            "max_cache_entries must be a nonnegative integer",
        ),
        (
            {"timeout_seconds": -1, "max_total_seconds": "invalid"},
            "timeout_seconds must be finite and nonnegative",
        ),
    ],
)
def test_configuration_preserves_first_error_order(
    llm_options: dict[str, object], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        SourceAnalysisConfig.from_dict({"llm": llm_options})


@pytest.mark.parametrize("stale", [False, True])
def test_framework_candidate_file_is_checked_against_original_source(
    tmp_path: Path, stale: bool
) -> None:
    source_path = tmp_path / "source.py"
    actual_source = SOURCE + ("# stale digest\n" if stale else "")
    source_path.write_text(actual_source)
    candidate_path = tmp_path / "candidate.json"
    candidate_path.write_text(json.dumps(certificate_to_dict(infer_candidate(SOURCE))))
    driver = SourceCertificateDriver(
        AnalysisConfig(
            "checked",
            "SourceCertificateDriver",
            options={
                "type": "certified analysis",
                "mode": "candidate",
                "source_path": source_path,
                "candidate_path": candidate_path,
                "allow_fallback": False,
            },
        )
    )
    report = driver.analyze(IRModule("source", ast.parse("pass"), filename=str(source_path)), {})
    assert report.certified is not stale
    assert report.candidate_check_status == ("rejected" if stale else "accepted")
    assert report.baseline_inference_status == "not_attempted"
    assert report.proposal_status == "supplied"


class _PropertyProposal:
    def __init__(self) -> None:
        self.candidate = infer_candidate(SOURCE)
        self.status = "dynamic-record"
        self.metric_reads = 0

    @property
    def attempts(self) -> int:
        self.metric_reads += 1
        return self.metric_reads


class _PropertyProposer:
    def __init__(self) -> None:
        self.config_reads = 0
        self.result = _PropertyProposal()

    @property
    def config(self) -> ProposalConfig:
        self.config_reads += 1
        return ProposalConfig(max_calls=self.config_reads)

    def propose(self, source: str, *, analysis_config: Mapping[str, JsonValue]) -> object:
        llm_config = analysis_config["llm"]
        assert isinstance(llm_config, dict)
        assert llm_config["max_calls"] == 2
        return self.result


def test_custom_property_observation_counts_and_selected_values_are_preserved() -> None:
    proposer = _PropertyProposer()
    report = analyze_source(SOURCE, use_llm=True, proposer=proposer)
    assert report.certified
    assert proposer.config_reads == 2
    assert proposer.result.metric_reads == 2
    assert report.to_dict()["proposal_metrics"] == {"attempts": 2}
