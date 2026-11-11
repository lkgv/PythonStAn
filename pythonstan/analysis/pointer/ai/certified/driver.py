"""Source-bound verification with explicit, validated resource configuration.

Legacy PTA facts never become proof assumptions. Resource exhaustion is unknown,
not a successful empty summary. Proposal limits are shared by a driver instance.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields
import hashlib
import json
from pathlib import Path
import tokenize
from typing import Any, Mapping

from pythonstan.analysis import AnalysisConfig, AnalysisDriver
from .llm import ProposalConfig


@dataclass(frozen=True)
class SourceAnalysisConfig:
    inference_max_iterations: int = 1000
    checker_max_iterations: int = 1000
    max_source_bytes: int = 1_000_000
    max_candidate_bytes: int = 1_000_000
    llm: ProposalConfig = field(default_factory=ProposalConfig)

    def __post_init__(self):
        for name in ("inference_max_iterations", "checker_max_iterations",
                     "max_source_bytes", "max_candidate_bytes"):
            value = getattr(self, name)
            if type(value) is not int or value <= 0:
                raise ValueError(name + " must be a positive integer")
        if not isinstance(self.llm, ProposalConfig):
            raise ValueError("llm must be a ProposalConfig")

    @classmethod
    def from_dict(cls, options: Mapping[str, Any] | None = None):
        if options is None:
            return cls()
        if not isinstance(options, Mapping):
            raise ValueError("analysis_config must be an object")
        values = dict(options)
        unknown = set(values) - {f.name for f in fields(cls)}
        if unknown:
            raise ValueError("unknown certified analysis option")
        if "llm" in values:
            if not isinstance(values["llm"], Mapping):
                raise ValueError("llm options must be an object")
            try:
                values["llm"] = ProposalConfig(**values["llm"])
            except TypeError as exc:
                raise ValueError("unknown LLM option") from exc
        return cls(**values)


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

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        for name in ("certificate_json", "proposal_metrics_json", "effective_config_json"):
            del result[name]
        result["certified"] = self.certified
        result["claim"] = "finite call/raise event and exit coverage within the declared semantic fragment"
        result["certificate"] = json.loads(self.certificate_json) if self.certificate_json else None
        result["proposal_metrics"] = json.loads(self.proposal_metrics_json)
        result["effective_config"] = json.loads(self.effective_config_json)
        result["residual"] = None if self.certified else {
            "kind": "unknown_behavior", "may_call_any": True,
            "may_modify_heap": True, "may_raise": True, "may_suspend": True,
        }
        return result


def analyze_source(
    source: str, *, candidate: Any = None, use_llm: bool = False,
    proposer: Any = None, analysis_config: Mapping[str, Any] | None = None,
    allow_fallback: bool = True,
) -> SourceAnalysisReport:
    """Untrusted proposal -> independent check -> optional source inference.

    Configuration controls computation, not just the prompt/cache identity.
    Fallback statuses and proposal provenance remain separate in every result.
    """
    from . import (UnsupportedSource, InferenceUnknown, ProgramCertificate, certificate_to_dict,
                   check_source, infer_candidate)

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
    state = dict(proposal_status="baseline", fallback_used=False,
                 candidate_check_status="not_attempted",
                 baseline_inference_status="not_attempted",
                 baseline_check_status="not_attempted",
                 proposal_metrics_json="{}",
                 effective_config_json=json.dumps(asdict(config), sort_keys=True))

    def report(status, certificate=None):
        payload = json.dumps(certificate_to_dict(certificate), sort_keys=True) if certificate else None
        return SourceAnalysisReport(status, digest, diagnostics=tuple(diagnostics),
                                    certificate_json=payload, **state)

    if use_llm:
        if proposer is None:
            from .llm import OpenAICandidateProposer
            proposer = OpenAICandidateProposer(config=config.llm)
        # A caller-supplied proposer owns its transport and budget. Record its
        # actual config as well, rather than imply it used our default limits.
        effective = asdict(config)
        if isinstance(getattr(proposer, "config", None), ProposalConfig):
            effective["llm"] = asdict(proposer.config)
            state["effective_config_json"] = json.dumps(effective, sort_keys=True)
        proposal = proposer.propose(source, analysis_config=effective)
        candidate = proposal.candidate
        state["proposal_status"] = proposal.status
        metric_names = ("source_sha256", "config_sha256", "request_sha256", "from_cache",
                        "attempts", "input_tokens", "output_tokens", "total_tokens",
                        "cached_input_tokens", "elapsed_seconds", "http_status", "transport_detail")
        metrics = {name: getattr(proposal, name) for name in metric_names if hasattr(proposal, name)}
        state["proposal_metrics_json"] = json.dumps(metrics, sort_keys=True)
        if candidate is None:
            diagnostics.append("proposal:" + proposal.reason)
            if not allow_fallback:
                return report("unknown")
            state["fallback_used"] = True
    elif candidate is not None:
        state["proposal_status"] = "supplied"

    if candidate is not None:
        try:
            candidate_data = certificate_to_dict(candidate) if isinstance(candidate, ProgramCertificate) else candidate
            candidate_size = len(json.dumps(candidate_data, ensure_ascii=True).encode("utf-8"))
        except (TypeError, ValueError, AttributeError, RecursionError):
            # Let the semantic checker produce a rejection for malformed input.
            candidate_size = 0
        if candidate_size > config.max_candidate_bytes:
            state["candidate_check_status"] = "unknown"
            diagnostics.append("candidate:input_budget_exhausted")
            if not allow_fallback:
                return report("unknown")
            state["fallback_used"] = True
            candidate = None
    if candidate is not None:
        checked = check_source(source, candidate, max_iterations=config.checker_max_iterations)
        state["candidate_check_status"] = checked.status
        if checked.certified:
            return report("accepted", checked.certificate)
        diagnostics.append("candidate:" + checked.status)
        diagnostics.extend(str(reason) for reason in checked.diagnostics)
        if not allow_fallback:
            return report(checked.status)
        state["fallback_used"] = True

    try:
        inferred = infer_candidate(source, max_iterations=config.inference_max_iterations)
        state["baseline_inference_status"] = "inferred"
    except UnsupportedSource as exc:
        diagnostics.append(str(exc))
        state["baseline_inference_status"] = "unsupported"
        return report("unsupported")
    except InferenceUnknown as exc:
        diagnostics.append(str(exc))
        state["baseline_inference_status"] = "unknown"
        return report("unknown")
    checked = check_source(source, inferred, max_iterations=config.checker_max_iterations)
    state["baseline_check_status"] = checked.status
    diagnostics.extend(str(reason) for reason in checked.diagnostics)
    return report(checked.status, checked.certificate if checked.certified else None)


def read_source(path: str | Path, max_bytes: int = 1_000_000) -> str:
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


class SourceCertificateDriver(AnalysisDriver):
    """One pass instance shares proposal call/cache budgets across scopes."""

    def __init__(self, analysis_config: AnalysisConfig):
        super().__init__(analysis_config)
        options = dict(self.config.options)
        known = {"type", "source_path", "mode", "candidate_path", "allow_fallback", "analysis_config"}
        if set(options) - known:
            raise ValueError("unknown source certificate driver option")
        self.mode = options.get("mode", "baseline")
        if self.mode not in {"baseline", "candidate", "llm"}:
            raise ValueError("mode must be baseline, candidate, or llm")
        self.allow_fallback = options.get("allow_fallback", True)
        if type(self.allow_fallback) is not bool:
            raise ValueError("allow_fallback must be Boolean")
        if self.mode == "candidate" and not options.get("candidate_path"):
            raise ValueError("candidate mode requires candidate_path")
        if self.mode != "candidate" and options.get("candidate_path"):
            raise ValueError("candidate_path requires candidate mode")
        self.settings = SourceAnalysisConfig.from_dict(options.get("analysis_config"))
        self.proposer = None
        if self.mode == "llm":
            from .llm import OpenAICandidateProposer
            self.proposer = OpenAICandidateProposer(config=self.settings.llm)
        self.results: SourceAnalysisReport | None = None

    def analyze(self, scope: Any, prev_results: dict[str, Any]) -> SourceAnalysisReport:
        options = self.config.options
        source_path = options.get("source_path") or scope.get_filename()
        candidate = None
        if self.mode == "candidate":
            from . import parse_candidate
            path = Path(options["candidate_path"])
            if path.stat().st_size > self.settings.max_candidate_bytes:
                raise ValueError("candidate exceeds input budget")
            candidate = parse_candidate(json.loads(path.read_text(encoding="utf-8")))
        self.results = analyze_source(
            read_source(source_path, self.settings.max_source_bytes),
            candidate=candidate, use_llm=self.mode == "llm", proposer=self.proposer,
            analysis_config=asdict(self.settings), allow_fallback=self.allow_fallback,
        )
        return self.results
