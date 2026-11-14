"""One resolved request owns explicit preparation, execution and publication."""

from __future__ import annotations

import time
import uuid
from collections.abc import Mapping
from types import TracebackType
from typing import TYPE_CHECKING, Self

from pythonstan.analysis.contracts.json_data import (
    FrozenJsonValue,
    JsonObject,
    JsonValue,
    require_array,
    require_int,
    require_object,
    validate_json_object,
)
from pythonstan.analysis.contracts.resources import ResourceStop, RunLedger, WorkReservation
from pythonstan.analysis.contracts.wire import thaw_snapshot
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

    def __init__(
        self, request: ResolvedRequest, *, resource_ledger: RunLedger | None = None
    ) -> None:
        if not isinstance(request, ResolvedRequest):
            raise ConfigurationError(
                "AnalysisSession requires a ResolvedRequest from resolve_request"
            )
        self.request = request
        resources = request.config.resources
        provider = request.config.llm.provider_options
        self.resource_ledger: RunLedger = (
            RunLedger(
                resources.max_work,
                resources.max_seconds,
                resources.max_memory_mb,
                started_at=request.started_at,
                max_api_calls=provider.max_calls,
                max_api_tokens=provider.max_total_tokens,
            )
            if resource_ledger is None
            else resource_ledger
        )
        self._terminal_permit: WorkReservation | None = None
        self._last_partial: Mapping[str, JsonValue | FrozenJsonValue] | None = None
        self._resolution_billed: bool = False
        self._policy_digest_value: str | None = None
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

    def _configuration(self, category: str) -> JsonObject:
        with self.resource_ledger.structural_meter(category) as meter:
            return self.config.to_dict(visit=meter.visit)

    def _policy_digest(self) -> str:
        if self._policy_digest_value is None:
            with self.resource_ledger.structural_meter("configuration_digest") as meter:
                self._policy_digest_value = self.config.digest_for(visit=meter.visit)
        return self._policy_digest_value

    def prepare(self) -> PreparedRun:
        if self.closed:
            raise RuntimeError("AnalysisSession is closed")
        if self._prepared is None:
            with self.resource_ledger.phase("setup"):
                if not self._resolution_billed:
                    self.resource_ledger.charge_work(1, "configuration_resolution")
                    self._configuration("configuration_resolution")
                    self._resolution_billed = True
                self._prepared = prepare_request(
                    self.request,
                    source_override=self._source_override,
                    resource_ledger=self.resource_ledger,
                )
        return self._prepared

    @property
    def snapshot(self) -> SourceSnapshot:
        if self._prepared is None:
            raise RuntimeError("Call prepare or execute before reading the source snapshot")
        return self._prepared.snapshot

    @classmethod
    def from_snapshot(
        cls,
        request: ResolvedRequest,
        snapshot: SourceSnapshot,
        *,
        resource_ledger: RunLedger | None = None,
    ) -> AnalysisSession:
        """Retain frozen bytes while creating a fresh AST and state on execution."""
        validation_started = time.monotonic()
        session = cls(request, resource_ledger=resource_ledger)
        with session.resource_ledger.phase("snapshot_reuse"):
            session.resource_ledger.charge_work(
                1 + len(snapshot.modules), "snapshot_reuse_validation"
            )
            validate_snapshot_request(request, snapshot, resource_ledger=session.resource_ledger)
        validation_seconds = time.monotonic() - validation_started
        session._source_override = snapshot
        session._reuse_validation_seconds = validation_seconds
        return session

    def _observer(self, prepared: PreparedRun) -> ObservationSink | None:
        config = self.config
        policy_digest = self._policy_digest()
        if not config.observation.enabled:
            return None
        from pythonstan.analysis.contracts.observation import ObservationSink

        return ObservationSink(
            run_id=self.run_id,
            source_digest=prepared.snapshot.source_digest,
            model_digest="pythonstan-builtins-v1",
            policy_digest=policy_digest,
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
        options = self._configuration("abstract_configuration_copy")
        native_phase: JsonObject | None = None
        native_work = 0
        oracle = None
        if config.engine == "native_then_ai":
            from pythonstan.analysis.composition.native_snapshot import NativeSnapshotOracle
            from pythonstan.compat.legacy_runner import run_legacy

            native_config = validate_json_object(execution_config)
            native_config["engine"] = "native"
            native_phase = run_legacy(
                prepared.execution_snapshot, native_config, ledger=self.resource_ledger
            )
            self._last_partial = native_phase
            if self.resource_ledger.stop_reason is not None:
                return native_phase, native_phase
            if native_phase.get("method_policy_validation") == "rejected":
                return native_phase, native_phase
            stats = require_object(native_phase.get("stats", {}))
            native_work = require_int(
                stats.get("total_work", stats.get("global_work_used", stats.get("native_work", 0)))
            )
            native_phase.update(
                source_digest=prepared.snapshot.source_digest,
                profile=config.semantic_profile,
                model_digest="pythonstan-builtins-v1",
                policy_digest=self._policy_digest(),
            )
            oracle = NativeSnapshotOracle(
                native_phase,
                source_digest=prepared.snapshot.source_digest,
                profile=config.semantic_profile,
                policy_digest=self._policy_digest(),
                model_digest="pythonstan-builtins-v1",
                max_work=self.resource_ledger.remaining_work,
            )
        driver = InterproceduralDriver(
            prepared.snapshot.module_map,
            options=options,
            oracle=oracle,
            observer=sink,
            program=prepared.program,
            resource_ledger=self.resource_ledger,
        )
        if oracle is not None:
            driver.install_oracle(oracle, incremental=oracle, phase_record=oracle.phase_record)
        provider: GuardedDispatchProvider | None = None
        provider_failure: str | None = None
        if config.llm.enabled and config.llm.level2:
            try:
                from pythonstan.llm.providers import make_summary_provider

                provider = make_summary_provider(
                    prepared.snapshot,
                    options,
                    remaining_work=self.resource_ledger.remaining_work,
                    resource_ledger=self.resource_ledger,
                )
            except ResourceStop:
                raise
            except MemoryError:
                raise
            except Exception as error:
                provider_failure = type(error).__name__
        configure_driver(
            driver,
            plan=prepared.plan,
            source_digest=prepared.snapshot.source_digest,
            config=options,
            summary_provider=provider,
        )
        with self.resource_ledger.phase("core"):
            snapshot = driver.run(prepared.execution_snapshot.scenarios)
        self._last_partial = snapshot
        with (
            self.resource_ledger.phase("bridge"),
            self.resource_ledger.structural_meter("engine_result_copy") as meter,
        ):
            result = require_object(thaw_snapshot(snapshot, visit=meter.visit))
        stats = require_object(result.setdefault("stats", {}))
        stats["native_prepass_work"] = native_work
        stats["total_work"] = self.resource_ledger.work.used
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
        self._terminal_permit = self.resource_ledger.ensure_terminal_permit(self._terminal_permit)
        try:
            with self.resource_ledger.work_scope(
                self.config.resources.max_work, "analysis_session"
            ):
                return self._execute()
        except ResourceStop as stopped:
            return self._terminal_result(stopped.reason)
        except (MemoryError, KeyboardInterrupt) as failure:
            reason = "max_memory_mb" if isinstance(failure, MemoryError) else "cancelled"
            self.resource_ledger.stop(reason)
            return self._terminal_result(reason)

    def _terminal_result(self, reason: str) -> AnalysisResult:
        status = "budget_exhausted" if reason.startswith(("max_", "stage_")) else "failed"
        return AnalysisResult.from_terminal(
            self._last_partial,
            self.queries,
            self.output,
            resource_ledger=self.resource_ledger,
            terminal_permit=self._terminal_permit,
            reason=reason,
            status=status,
            engine=self.config.engine,
        )

    def _execute(self) -> AnalysisResult:
        prepared = self.prepare()
        config = self.config
        execution_config = self._configuration("execution_configuration_copy")
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

            result = run_legacy(
                prepared.execution_snapshot, execution_config, ledger=self.resource_ledger
            )
        else:
            result, native_phase = self._run_abstract(prepared, execution_config, sink)
        self._last_partial = result
        if self.resource_ledger.stop_reason is not None:
            return self._terminal_result(self.resource_ledger.stop_reason)
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
            with self.resource_ledger.structural_meter("region_plan_result_copy") as meter:
                result["plan"] = prepared.plan.to_dict(visit=meter.visit)
        if config.llm.enabled and config.llm.level1:
            from pythonstan.llm.strategy import RefinementController

            self.resource_ledger.charge_json(result["stats"], "l1_baseline_stats_copy")
            self.resource_ledger.charge_json(
                result.get("phase_trace", []), "l1_baseline_trace_copy"
            )
            baseline_stats = dict(require_object(result["stats"]))
            baseline_trace = list(require_array(result.get("phase_trace", [])))
            result, feedback = RefinementController().run(
                target=self.target,
                source_snapshot=prepared.snapshot,
                config=self._configuration("l1_configuration_copy"),
                queries=self.queries,
                output=self.output,
                baseline=result,
                resource_ledger=self.resource_ledger,
            )
            self.resource_ledger.charge_json(feedback, "l1_feedback_install")
            self.resource_ledger.charge_json(baseline_trace, "l1_feedback_trace_copy")
            attach_l1_feedback(
                result,
                feedback=feedback,
                requested_config=self._configuration("l1_requested_configuration_copy"),
                baseline_stats=baseline_stats,
                baseline_trace=baseline_trace,
                total_seconds=time.monotonic() - self.started,
            )
        self._last_partial = result
        with self.resource_ledger.phase("export"):
            self.resource_ledger.charge_work(1, "session_export_metadata")
            level1 = require_object(result.get("llm", {})).get("level1")
            if isinstance(level1, dict):
                level1["total_work"] = self.resource_ledger.work.used
                level1["reanalysis_work"] = self.resource_ledger.work.used - require_int(
                    level1["baseline_work"]
                )
                level1["total_work_scope"] = "before_checked_publication_freeze"
                level1["reanalysis_work_scope"] = (
                    "All post-baseline control, validation, rerun, paid read and feedback work before checked publication freeze"
                )
            result["resource_ledger"] = self.resource_ledger.snapshot()
            result["resource_ledger_scope"] = "before_checked_publication_freeze"
            stats = require_object(result.setdefault("stats", {}))
            stats["total_work"] = self.resource_ledger.work.used
            stats["total_work_scope"] = "before_checked_publication_freeze"
            return AnalysisResult(
                result,
                self.queries,
                self.output,
                resource_ledger=self.resource_ledger,
                terminal_permit=self._terminal_permit,
            )

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
