"""Source-bound verification with explicit, validated resource configuration.

Legacy PTA facts never become proof assumptions. Resource exhaustion is unknown,
not a successful empty summary. Proposal limits are shared by a driver instance.
"""

from __future__ import annotations

import hashlib
import json
import math
import tokenize
from collections.abc import Mapping
from dataclasses import dataclass, field
from os import PathLike
from pathlib import Path, PurePath
from typing import Literal, Protocol, TypeGuard, override

from pythonstan.analysis.analysis import AnalysisConfig, AnalysisDriver
from pythonstan.analysis.contracts.json_data import JsonObject, JsonValue
from pythonstan.analysis.verification.closed_effects.checker import (
    InferenceUnknown,
    ProgramCertificate,
    UnsupportedSource,
    certificate_to_dict,
    check_source,
    infer_candidate,
    parse_candidate,
)
from pythonstan.ir import IRModule, IRScope

from .llm import ProposalConfig

__all__ = [
    "CandidateProposer",
    "SourceAnalysisConfig",
    "SourceAnalysisReport",
    "SourceCertificateDriver",
    "analyze_source",
    "read_source",
]

# Historical reports contain tuples for these two sequence fields. JSON payloads
# stay recursive JSON data; this export alias preserves the Python API as well.
type ReportData = dict[str, JsonValue | tuple[str, ...]]
type SourceMode = Literal["baseline", "candidate", "llm"]


class CandidateProposer(Protocol):
    """External proposal operation; its result is untrusted until decoded."""

    def propose(self, source: str, *, analysis_config: Mapping[str, JsonValue]) -> object: ...


def _options(value: object, message: str, key_error: str) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError(message)
    result: dict[str, object] = {}
    for key, item in value.items():
        if not isinstance(key, str):
            raise ValueError(key_error)
        result[key] = item
    return result


def _integer(value: object, name: str, *, minimum: int = 1) -> int:
    if type(value) is not int or value < minimum:
        adjective = "positive" if minimum else "nonnegative"
        raise ValueError(name + " must be a " + adjective + " integer")
    return value


def _number(value: object, name: str) -> int | float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(name + " must be finite")
    if not math.isfinite(value) or value < 0:
        raise ValueError(name + " must be finite and nonnegative")
    if name != "retry_backoff_seconds" and value == 0:
        raise ValueError(name + " must be positive")
    return value


def _proposal_config(value: object) -> ProposalConfig:
    values = _options(value, "llm options must be an object", "unknown LLM option")
    defaults = ProposalConfig()
    if set(values) - set(_proposal_config_data(defaults)):
        raise ValueError("unknown LLM option")
    model = values.get("model", defaults.model)
    if model != defaults.model or not isinstance(model, str):
        raise ValueError("the configured model must be gpt-6-luna")
    # Decode in ProposalConfig's validation order, preserving which error is
    # reported when a caller supplies several invalid fields together.
    max_calls = _integer(values.get("max_calls", defaults.max_calls), "max_calls", minimum=0)
    max_retries = _integer(
        values.get("max_retries", defaults.max_retries), "max_retries", minimum=0
    )
    max_cache_entries = _integer(
        values.get("max_cache_entries", defaults.max_cache_entries), "max_cache_entries", minimum=0
    )
    max_source_bytes = _integer(
        values.get("max_source_bytes", defaults.max_source_bytes), "max_source_bytes"
    )
    max_request_bytes = _integer(
        values.get("max_request_bytes", defaults.max_request_bytes), "max_request_bytes"
    )
    max_response_bytes = _integer(
        values.get("max_response_bytes", defaults.max_response_bytes), "max_response_bytes"
    )
    max_output_tokens = _integer(
        values.get("max_output_tokens", defaults.max_output_tokens), "max_output_tokens"
    )
    timeout_seconds = _number(
        values.get("timeout_seconds", defaults.timeout_seconds), "timeout_seconds"
    )
    max_total_seconds = _number(
        values.get("max_total_seconds", defaults.max_total_seconds), "max_total_seconds"
    )
    retry_backoff_seconds = _number(
        values.get("retry_backoff_seconds", defaults.retry_backoff_seconds), "retry_backoff_seconds"
    )
    return ProposalConfig(
        model=model,
        max_calls=max_calls,
        max_retries=max_retries,
        max_source_bytes=max_source_bytes,
        max_request_bytes=max_request_bytes,
        max_response_bytes=max_response_bytes,
        max_output_tokens=max_output_tokens,
        timeout_seconds=timeout_seconds,
        max_total_seconds=max_total_seconds,
        retry_backoff_seconds=retry_backoff_seconds,
        max_cache_entries=max_cache_entries,
    )


def _proposal_config_data(config: ProposalConfig) -> JsonObject:
    return {
        "model": config.model,
        "max_calls": config.max_calls,
        "max_retries": config.max_retries,
        "max_source_bytes": config.max_source_bytes,
        "max_request_bytes": config.max_request_bytes,
        "max_response_bytes": config.max_response_bytes,
        "max_output_tokens": config.max_output_tokens,
        "timeout_seconds": config.timeout_seconds,
        "max_total_seconds": config.max_total_seconds,
        "retry_backoff_seconds": config.retry_backoff_seconds,
        "max_cache_entries": config.max_cache_entries,
    }


@dataclass(frozen=True)
class SourceAnalysisConfig:
    inference_max_iterations: int = 1000
    checker_max_iterations: int = 1000
    max_source_bytes: int = 1_000_000
    max_candidate_bytes: int = 1_000_000
    llm: ProposalConfig = field(default_factory=ProposalConfig)

    def __post_init__(self) -> None:
        for name, value in (
            ("inference_max_iterations", self.inference_max_iterations),
            ("checker_max_iterations", self.checker_max_iterations),
            ("max_source_bytes", self.max_source_bytes),
            ("max_candidate_bytes", self.max_candidate_bytes),
        ):
            _integer(value, name)
        if not isinstance(self.llm, ProposalConfig):
            raise ValueError("llm must be a ProposalConfig")

    @classmethod
    def from_dict(cls, options: object = None) -> SourceAnalysisConfig:
        if options is None:
            return cls()
        values = _options(
            options, "analysis_config must be an object", "unknown certified analysis option"
        )
        defaults = cls()
        if set(values) - set(defaults.to_dict()):
            raise ValueError("unknown certified analysis option")
        return cls(
            inference_max_iterations=_integer(
                values.get("inference_max_iterations", defaults.inference_max_iterations),
                "inference_max_iterations",
            ),
            checker_max_iterations=_integer(
                values.get("checker_max_iterations", defaults.checker_max_iterations),
                "checker_max_iterations",
            ),
            max_source_bytes=_integer(
                values.get("max_source_bytes", defaults.max_source_bytes), "max_source_bytes"
            ),
            max_candidate_bytes=_integer(
                values.get("max_candidate_bytes", defaults.max_candidate_bytes),
                "max_candidate_bytes",
            ),
            llm=_proposal_config(values["llm"]) if "llm" in values else defaults.llm,
        )

    def to_dict(self) -> JsonObject:
        return {
            "inference_max_iterations": self.inference_max_iterations,
            "checker_max_iterations": self.checker_max_iterations,
            "max_source_bytes": self.max_source_bytes,
            "max_candidate_bytes": self.max_candidate_bytes,
            "llm": _proposal_config_data(self.llm),
        }


def _is_finite_json(value: object) -> TypeGuard[JsonValue]:
    # Iteration preserves the JSON parser's nesting limits rather than adding a
    # smaller recursive-codec limit to historically valid provider payloads.
    pending: list[tuple[object, bool]] = [(value, False)]
    active: set[int] = set()
    while pending:
        item, exiting = pending.pop()
        if exiting:
            active.remove(id(item))
        elif item is None or isinstance(item, (str, bool, int)):
            continue
        elif isinstance(item, float):
            if not math.isfinite(item):
                return False
        elif isinstance(item, (dict, list)):
            if id(item) in active:
                return False
            active.add(id(item))
            pending.append((item, True))
            if isinstance(item, dict):
                for key, child in item.items():
                    if not isinstance(key, str):
                        return False
                    pending.append((child, False))
            else:
                pending.extend((child, False) for child in item)
        else:
            return False
    return True


def _load_json(text: str) -> JsonValue:
    value: object = json.loads(text)
    if not _is_finite_json(value):
        raise ValueError("report payload must contain finite JSON data")
    return value


@dataclass(frozen=True)
class SourceAnalysisReport:
    status: str
    source_sha256: str
    proposal_status: str
    diagnostics: tuple[str, ...]
    fallback_used: bool = False
    certificate_json: str | None = None
    candidate_check_status: str = "not_attempted"
    baseline_inference_status: str = "not_attempted"
    baseline_check_status: str = "not_attempted"
    proposal_metrics_json: str = "{}"
    effective_config_json: str = "{}"
    scope: str = "closed-call-effect-fragment"
    assumptions: tuple[str, ...] = (
        "closed module with stable function and builtin bindings",
        "unbounded resources; resource exhaustion is not modeled",
        "no tracing, concurrent interference, or finalizer effects",
    )

    @property
    def certified(self) -> bool:
        return self.status == "accepted" and self.certificate_json is not None

    def to_dict(self) -> ReportData:
        return {
            "status": self.status,
            "source_sha256": self.source_sha256,
            "proposal_status": self.proposal_status,
            "diagnostics": self.diagnostics,
            "fallback_used": self.fallback_used,
            "candidate_check_status": self.candidate_check_status,
            "baseline_inference_status": self.baseline_inference_status,
            "baseline_check_status": self.baseline_check_status,
            "scope": self.scope,
            "assumptions": self.assumptions,
            "certified": self.certified,
            "claim": "finite call/raise event and exit coverage within the declared semantic fragment",
            "certificate": _load_json(self.certificate_json) if self.certificate_json else None,
            "proposal_metrics": _load_json(self.proposal_metrics_json),
            "effective_config": _load_json(self.effective_config_json),
            "residual": None
            if self.certified
            else {
                "kind": "unknown_behavior",
                "may_call_any": True,
                "may_modify_heap": True,
                "may_raise": True,
                "may_suspend": True,
            },
        }


@dataclass(frozen=True)
class _Proposal:
    candidate: object
    status: str
    reason: str
    metrics_json: str


def _proposal_attribute(value: object, name: str) -> object:
    result: object = getattr(value, name)
    return result


def _decode_proposal(value: object) -> _Proposal:
    """Decode the extensible external record without trusting its fields."""
    candidate = _proposal_attribute(value, "candidate")
    status = _proposal_attribute(value, "status")
    metrics: dict[str, object] = {}
    for name in (
        "source_sha256",
        "config_sha256",
        "request_sha256",
        "from_cache",
        "attempts",
        "input_tokens",
        "output_tokens",
        "total_tokens",
        "cached_input_tokens",
        "elapsed_seconds",
        "http_status",
        "transport_detail",
    ):
        if hasattr(value, name):
            item: object = getattr(value, name)
            metrics[name] = item
    serialized = json.dumps(metrics, sort_keys=True)
    _load_json(serialized)
    reason = _proposal_attribute(value, "reason") if candidate is None else ""
    if not isinstance(status, str) or not isinstance(reason, str):
        raise TypeError("proposal status and reason must be strings")
    return _Proposal(candidate, status, reason, serialized)


@dataclass
class _ReportState:
    proposal_status: str = "baseline"
    fallback_used: bool = False
    candidate_check_status: str = "not_attempted"
    baseline_inference_status: str = "not_attempted"
    baseline_check_status: str = "not_attempted"
    proposal_metrics_json: str = "{}"
    effective_config_json: str = "{}"

    def report(
        self,
        status: str,
        digest: str,
        diagnostics: list[str],
        certificate: ProgramCertificate | None = None,
    ) -> SourceAnalysisReport:
        payload = (
            json.dumps(certificate_to_dict(certificate), sort_keys=True) if certificate else None
        )
        return SourceAnalysisReport(
            status=status,
            source_sha256=digest,
            proposal_status=self.proposal_status,
            diagnostics=tuple(diagnostics),
            fallback_used=self.fallback_used,
            certificate_json=payload,
            candidate_check_status=self.candidate_check_status,
            baseline_inference_status=self.baseline_inference_status,
            baseline_check_status=self.baseline_check_status,
            proposal_metrics_json=self.proposal_metrics_json,
            effective_config_json=self.effective_config_json,
        )


def analyze_source(
    source: str,
    *,
    candidate: object = None,
    use_llm: bool = False,
    proposer: CandidateProposer | None = None,
    analysis_config: object = None,
    allow_fallback: bool = True,
) -> SourceAnalysisReport:
    """Untrusted proposal -> independent check -> optional source inference.

    Configuration controls computation, not just the prompt/cache identity.
    Fallback statuses and proposal provenance remain separate in every result.
    """
    if type(use_llm) is not bool or type(allow_fallback) is not bool:
        raise ValueError("use_llm and allow_fallback must be Boolean")
    if candidate is not None and use_llm:
        raise ValueError("choose a supplied candidate or LLM proposal")
    config = SourceAnalysisConfig.from_dict(analysis_config)
    if not isinstance(source, str):
        raise ValueError("source must be text")
    try:
        encoded = source.encode("utf-8")
    except UnicodeError as exc:
        raise ValueError("source must be valid UTF-8") from exc
    if len(encoded) > config.max_source_bytes:
        raise ValueError("source exceeds configured input budget")
    digest = hashlib.sha256(encoded).hexdigest()
    diagnostics: list[str] = []
    state = _ReportState(effective_config_json=json.dumps(config.to_dict(), sort_keys=True))

    if use_llm:
        if proposer is None:
            from .llm import OpenAICandidateProposer

            proposer = OpenAICandidateProposer(config=config.llm)
        effective = config.to_dict()
        actual_config: object = getattr(proposer, "config", None)
        if isinstance(actual_config, ProposalConfig):
            exported_config = _proposal_attribute(proposer, "config")
            if not isinstance(exported_config, ProposalConfig):
                raise TypeError("proposer config must remain a ProposalConfig")
            effective["llm"] = _proposal_config_data(exported_config)
            state.effective_config_json = json.dumps(effective, sort_keys=True)
        proposal = _decode_proposal(proposer.propose(source, analysis_config=effective))
        candidate = proposal.candidate
        state.proposal_status = proposal.status
        state.proposal_metrics_json = proposal.metrics_json
        if candidate is None:
            diagnostics.append("proposal:" + proposal.reason)
            if not allow_fallback:
                return state.report("unknown", digest, diagnostics)
            state.fallback_used = True
    elif candidate is not None:
        state.proposal_status = "supplied"

    if candidate is not None:
        try:
            candidate_data = (
                certificate_to_dict(candidate)
                if isinstance(candidate, ProgramCertificate)
                else candidate
            )
            candidate_size = len(json.dumps(candidate_data, ensure_ascii=True).encode("utf-8"))
        except (TypeError, ValueError, AttributeError, RecursionError):
            # Let the semantic checker produce its original malformed-input rejection.
            candidate_size = 0
        if candidate_size > config.max_candidate_bytes:
            state.candidate_check_status = "unknown"
            diagnostics.append("candidate:input_budget_exhausted")
            if not allow_fallback:
                return state.report("unknown", digest, diagnostics)
            state.fallback_used = True
            candidate = None
    if candidate is not None:
        checked = check_source(source, candidate, max_iterations=config.checker_max_iterations)
        state.candidate_check_status = checked.status
        if checked.certified:
            return state.report("accepted", digest, diagnostics, checked.certificate)
        diagnostics.append("candidate:" + checked.status)
        diagnostics.extend(str(reason) for reason in checked.diagnostics)
        if not allow_fallback:
            return state.report(checked.status, digest, diagnostics)
        state.fallback_used = True

    try:
        inferred = infer_candidate(source, max_iterations=config.inference_max_iterations)
        state.baseline_inference_status = "inferred"
    except UnsupportedSource as exc:
        diagnostics.append(str(exc))
        state.baseline_inference_status = "unsupported"
        return state.report("unsupported", digest, diagnostics)
    except InferenceUnknown as exc:
        diagnostics.append(str(exc))
        state.baseline_inference_status = "unknown"
        return state.report("unknown", digest, diagnostics)
    checked = check_source(source, inferred, max_iterations=config.checker_max_iterations)
    state.baseline_check_status = checked.status
    diagnostics.extend(str(reason) for reason in checked.diagnostics)
    return state.report(
        checked.status, digest, diagnostics, checked.certificate if checked.certified else None
    )


def read_source(path: str | PathLike[str], max_bytes: int = 1_000_000) -> str:
    if type(max_bytes) is not int or max_bytes <= 0:
        raise ValueError("source byte budget must be positive")
    path = Path(path)
    if path.stat().st_size > max_bytes:
        raise ValueError("source exceeds configured input budget")
    with tokenize.open(path) as stream:
        source = stream.read(max_bytes + 1)
    if len(source.encode("utf-8")) > max_bytes:
        raise ValueError("source exceeds configured input budget")
    return source


def _path(value: object, message: str) -> str | PurePath:
    if isinstance(value, (str, PurePath)):
        return value
    if isinstance(value, PathLike):
        raw_path: object = value.__fspath__()
        if isinstance(raw_path, str):
            return raw_path
    raise ValueError(message)


def _source_mode(value: object) -> SourceMode:
    if value not in {"baseline", "candidate", "llm"}:
        raise ValueError("mode must be baseline, candidate, or llm")
    if value == "baseline":
        return "baseline"
    if value == "candidate":
        return "candidate"
    return "llm"


def _scope_path(scope: IRScope) -> str | PurePath:
    """Read the existing optional filename service at its actual boundary."""
    if isinstance(scope, IRModule):
        result: object = scope.get_filename()
    else:
        # Plugin scopes may implement this finite service directly or through
        # __getattr__. Keep the original single lookup and call observations.
        service: Literal["get_filename"] = "get_filename"
        getter: object = getattr(scope, service)
        if not callable(getter):
            raise TypeError(f"{type(getter).__name__!r} object is not callable")
        result = getter()
    return _path(result, "source_path must be a path")


class SourceCertificateDriver(AnalysisDriver):
    """One pass instance shares proposal call/cache budgets across scopes."""

    def __init__(self, analysis_config: AnalysisConfig) -> None:
        super().__init__(analysis_config)
        options = self.config.options
        known = {
            "type",
            "source_path",
            "mode",
            "candidate_path",
            "allow_fallback",
            "analysis_config",
        }
        if set(options) - known:
            raise ValueError("unknown source certificate driver option")
        self.mode: SourceMode = _source_mode(options.get("mode", "baseline"))
        fallback = options.get("allow_fallback", True)
        if type(fallback) is not bool:
            raise ValueError("allow_fallback must be Boolean")
        self.allow_fallback: bool = fallback
        candidate_path = options.get("candidate_path")
        if self.mode == "candidate" and not candidate_path:
            raise ValueError("candidate mode requires candidate_path")
        if self.mode != "candidate" and candidate_path:
            raise ValueError("candidate_path requires candidate mode")
        self.settings: SourceAnalysisConfig = SourceAnalysisConfig.from_dict(
            options.get("analysis_config")
        )
        self.proposer: CandidateProposer | None = None
        if self.mode == "llm":
            from .llm import OpenAICandidateProposer

            self.proposer = OpenAICandidateProposer(config=self.settings.llm)
        self.results = None

    @override
    def analyze(self, scope: IRScope, prev_results: dict[str, object]) -> SourceAnalysisReport:
        options = self.config.options
        selected_path = options.get("source_path")
        if not selected_path:
            source_path = _scope_path(scope)
        else:
            source_path = _path(selected_path, "source_path must be a path")
        candidate: ProgramCertificate | None = None
        if self.mode == "candidate":
            path = Path(_path(options["candidate_path"], "candidate_path must be a path"))
            if path.stat().st_size > self.settings.max_candidate_bytes:
                raise ValueError("candidate exceeds input budget")
            raw: object = json.loads(path.read_text(encoding="utf-8"))
            candidate = parse_candidate(raw)
        result = analyze_source(
            read_source(source_path, self.settings.max_source_bytes),
            candidate=candidate,
            use_llm=self.mode == "llm",
            proposer=self.proposer,
            analysis_config=self.settings.to_dict(),
            allow_fallback=self.allow_fallback,
        )
        self.results = result
        return result
