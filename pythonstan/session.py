"""One resolved request owns explicit preparation, execution and publication."""

from __future__ import annotations

import time
import uuid
from types import TracebackType
from typing import TYPE_CHECKING, Self

from pythonstan.analysis.contracts.json_data import (
    JsonObject,
    require_array,
    require_int,
    require_object,
    validate_json_object,
)
from pythonstan.config import ResolvedConfig
from pythonstan.config.errors import ConfigurationError
from pythonstan.inputs import SourceSnapshot
from pythonstan.inputs.targets import Target
from pythonstan.request import ResolvedRequest
from pythonstan.results import AnalysisResult
from pythonstan.results.output import OutputSpec
from pythonstan.results.queries import QuerySpec
from pythonstan.session_preparation import PreparedRun, prepare_request, validate_snapshot_request
from pythonstan.session_results import (
    attach_checked_properties,
    attach_l1_feedback,
    attach_request_metadata,
)

if TYPE_CHECKING:
    from pythonstan.analysis.composition.guarded_dispatch import GuardedDispatchProvider
    from pythonstan.analysis.contracts.observation import ObservationSink


class AnalysisSession:
    request: ResolvedRequest
    started: float
    run_id: str
    closed: bool
    _prepared: PreparedRun | None
    _source_override: SourceSnapshot | None
    _reuse_validation_seconds: float

    def __init__(self, request: ResolvedRequest) -> None:
        if not isinstance(request, ResolvedRequest):
            raise ConfigurationError(
                "AnalysisSession requires a ResolvedRequest from resolve_request"
            )
        self.request = request
        self.started = request.started_at
        self.run_id = uuid.uuid4().hex
        self.closed = False
        self._prepared = None
        self._source_override = None
        self._reuse_validation_seconds = 0.0

    @property
    def config(self) -> ResolvedConfig:
        return self.request.config

    @property
    def target(self) -> Target:
        return self.request.target

    @property
    def queries(self) -> tuple[QuerySpec, ...]:
        return self.request.queries

    @property
    def output(self) -> OutputSpec:
        return self.request.output

    def prepare(self) -> PreparedRun:
        if self.closed:
            raise RuntimeError("AnalysisSession is closed")
        if self._prepared is None:
            self._prepared = prepare_request(self.request, source_override=self._source_override)
        return self._prepared

    @property
    def snapshot(self) -> SourceSnapshot:
        if self._prepared is None:
            raise RuntimeError("Call prepare or execute before reading the source snapshot")
        return self._prepared.snapshot

    @classmethod
    def from_snapshot(cls, request: ResolvedRequest, snapshot: SourceSnapshot) -> AnalysisSession:
        """Retain frozen bytes while creating a fresh AST and state on execution."""
        validation_started = time.monotonic()
        validate_snapshot_request(request, snapshot)
        validation_seconds = time.monotonic() - validation_started
        session = cls(request)
        session._source_override = snapshot
        session._reuse_validation_seconds = validation_seconds
        return session

    def _observer(self, prepared: PreparedRun) -> ObservationSink | None:
        config = self.config
        if not config.observation.enabled:
            return None
        from pythonstan.analysis.contracts.observation import ObservationSink

        return ObservationSink(
            run_id=self.run_id,
            source_digest=prepared.snapshot.source_digest,
            model_digest="pythonstan-builtins-v1",
            policy_digest=config.digest,
            profile=config.semantic_profile,
            sources=prepared.snapshot.module_map,
            max_events=config.observation.max_events,
        )

    def _run_abstract(
        self,
        prepared: PreparedRun,
        execution_config: JsonObject,
        sink: ObservationSink | None,
    ) -> tuple[JsonObject, JsonObject | None]:
        from pythonstan.analysis.composition.hybrid import configure_driver
        from pythonstan.analysis.engines.abstract.driver import InterproceduralDriver

        config = self.config
        options = config.to_dict()
        native_phase: JsonObject | None = None
        native_work = 0
        oracle = None
        if config.engine == "native_then_ai":
            from pythonstan.analysis.composition.native_snapshot import NativeSnapshotOracle
            from pythonstan.compat.legacy_runner import run_legacy

            native_config = validate_json_object(execution_config)
            native_config["engine"] = "native"
            native_phase = validate_json_object(
                run_legacy(prepared.execution_snapshot, native_config)
            )
            stats = require_object(native_phase.get("stats", {}))
            native_work = require_int(
                stats.get("total_work", stats.get("global_work_used", stats.get("native_work", 0)))
            )
            native_phase.update(
                source_digest=prepared.snapshot.source_digest,
                profile=config.semantic_profile,
                model_digest="pythonstan-builtins-v1",
                policy_digest=config.digest,
            )
            oracle = NativeSnapshotOracle(
                native_phase,
                source_digest=prepared.snapshot.source_digest,
                profile=config.semantic_profile,
                policy_digest=config.digest,
                model_digest="pythonstan-builtins-v1",
                max_work=max(0, config.resources.max_work - native_work),
            )
        driver = InterproceduralDriver(
            prepared.snapshot.module_map,
            options=options,
            oracle=oracle,
            observer=sink,
            program=prepared.program,
        )
        if oracle is not None:
            driver.install_oracle(oracle, incremental=oracle, phase_record=oracle.phase_record)
        driver.max_work = max(0, config.resources.max_work - native_work)
        provider: GuardedDispatchProvider | None = None
        provider_failure: str | None = None
        if config.llm.enabled and config.llm.level2:
            try:
                from pythonstan.llm.providers import make_summary_provider

                provider = make_summary_provider(
                    prepared.snapshot, options, remaining_work=driver.max_work
                )
            except Exception as error:
                provider_failure = type(error).__name__
        configure_driver(
            driver,
            plan=prepared.plan,
            source_digest=prepared.snapshot.source_digest,
            config=options,
            summary_provider=provider,
        )
        seconds = config.resources.max_seconds
        remaining = seconds - (time.monotonic() - self.started) if seconds else None
        if remaining is not None and remaining <= 0:
            driver.max_work = 0
        elif remaining is not None:
            reserve = min(1.0, max(0.05, remaining * 0.25))
            driver.max_seconds = max(0.001, remaining - reserve)
        snapshot = driver.run(prepared.execution_snapshot.scenarios)
        result = validate_json_object(snapshot.to_dict())
        stats = require_object(result.setdefault("stats", {}))
        stats["native_prepass_work"] = native_work
        stats["total_work"] = native_work + driver.work
        if native_phase is not None:
            result["phase_trace"] = require_array(
                native_phase.get("phase_trace", [])
            ) + require_array(result.get("phase_trace", []))
            stats["native_prepass"] = native_phase.get("stats", {})
        if provider is not None:
            attach_checked_properties(
                result,
                records=[validate_json_object(record) for record in provider.proof_records],
                queries=self.queries,
                source_digest=prepared.snapshot.source_digest,
                statistics=validate_json_object(provider.statistics),
            )
        if provider_failure is not None:
            require_array(result.setdefault("diagnostics", [])).append(
                {"kind": "llm_provider_failure", "message": provider_failure}
            )
        return result, native_phase

    def execute(self) -> AnalysisResult:
        prepared = self.prepare()
        config = self.config
        execution_config = config.to_dict()
        elapsed = time.monotonic() - self.started
        seconds = config.resources.max_seconds
        exhausted = bool(seconds and elapsed >= seconds)
        if seconds:
            require_object(execution_config["resources"])["max_seconds"] = max(
                0.001, seconds - elapsed
            )
        sink = self._observer(prepared)
        native_phase = None
        result: JsonObject
        engine_started = time.monotonic()
        if exhausted:
            result = {
                "solver_status": "budget_exhausted",
                "coverage_status": "open",
                "values": {},
                "calls": [],
                "returns": {},
                "effects": {},
                "outcomes": {},
                "phase_trace": [
                    {
                        "phase": "pre_execution",
                        "seconds": elapsed,
                        "timing_scope": "inclusive_request_elapsed_including_idle",
                        "status": "budget_exhausted",
                    }
                ],
                "obligations": [{"kind": "time_budget", "stage": "pre_execution"}],
            }
        elif config.engine == "native":
            from pythonstan.compat.legacy_runner import run_legacy

            result = validate_json_object(run_legacy(prepared.execution_snapshot, execution_config))
        else:
            result, native_phase = self._run_abstract(prepared, execution_config, sink)
        engine_seconds = 0.0 if exhausted else time.monotonic() - engine_started
        attach_request_metadata(
            result,
            request=self.request,
            snapshot=prepared.snapshot,
            execution_snapshot=prepared.execution_snapshot,
            run_id=self.run_id,
            native_phase=native_phase,
            preparation_seconds=prepared.preparation_seconds,
            snapshot_reuse_validation_seconds=self._reuse_validation_seconds,
            engine_seconds=engine_seconds,
            total_seconds=time.monotonic() - self.started,
        )
        if sink is not None:
            events = validate_json_object(sink.snapshot())
            if config.engine == "native":
                events.update(
                    observation_status="unsupported", reason="legacy_native_producer_not_attached"
                )
            elif config.engine == "native_then_ai":
                events.update(
                    phase_coverage={"native": "not_attached", "abstract": "recorded"},
                    observation_status="partial",
                )
            result["events"] = events
            require_object(result["stats"])["observation"] = validate_json_object(sink.stats)
        if prepared.plan is not None:
            result["plan"] = validate_json_object(prepared.plan.to_dict())
        if config.llm.enabled and config.llm.level1:
            from pythonstan.llm.strategy import RefinementController

            baseline_stats = dict(require_object(result["stats"]))
            baseline_trace = list(require_array(result.get("phase_trace", [])))
            result, feedback = RefinementController().run(
                target=self.target,
                source_snapshot=prepared.snapshot,
                config=config.to_dict(),
                queries=self.queries,
                output=self.output,
                baseline=result,
            )
            attach_l1_feedback(
                result,
                feedback=feedback,
                requested_config=config.to_dict(),
                baseline_stats=baseline_stats,
                baseline_trace=baseline_trace,
                total_seconds=time.monotonic() - self.started,
            )
        return AnalysisResult(result, self.queries, self.output)

    def close(self) -> None:
        self.closed = True

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()
