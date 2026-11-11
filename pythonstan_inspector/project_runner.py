"""Explicit, isolated capture of a native whole-project analysis.

Opening a diagnostic bundle never invokes this module. The worker consumes the
unchanged ProjectConfig and the native production driver, including its second
exported-entry solve and shared iteration budget. Wall and address-space limits
include capture, report encoding, and export. Termination may leave no bundle.
"""
from contextlib import redirect_stdout
from dataclasses import asdict
import ast
import hashlib
from io import BytesIO, StringIO
import json
import math
import os
from pathlib import Path
import platform
import stat
import sys
import time
import tokenize
from types import ModuleType
import uuid

from .contract import CaptureRequest, InspectorError
from .project_process import run_worker_process, worker_limits, WorkerTimeout, UnsupportedWorkerPlatform
from .project_progress import _read_progress
from .incident import (IncidentRecorder, INCIDENT_PROTOCOL, TRACE_PROTOCOL,
    MAX_RECEIPT_BYTES, MAX_TRACE_BYTES, MAX_TRACE_BATCH_BYTES, MAX_TRACE_BATCH_EVENTS,
    CHECKPOINT_SECONDS, CHECKPOINT_WORK_ITEMS, atomic_bytes, bounded_tree,
    decode_object, read_fixed, read_receipt, read_trace, write_receipt, validate_receipt)


_RESULT_MARKER = "PROJECT_INSPECTOR_JSON:"
_RESULT_STREAM_PROTOCOL = "project_inspector_json_line_v2"
_LEGACY_RESULT_PROTOCOL = "legacy_unversioned_inspector_receipt"
MAX_BOOTSTRAP_BYTES = 1024 * 1024
DEFAULT_REPORT_BYTES = 8 * 1024 * 1024
MAX_REPORT_BYTES = 64 * 1024 * 1024
REPORT_CHUNK_BYTES = 65536
_ROUTINE_PHASES = frozenset(("module_parse_started", "module_frontend_started",
    "module_frontend_completed", "module_closure_started", "module_closure_completed"))
MAX_SELECTOR_SOURCE_BYTES = 1024 * 1024


def _validate_observation(mode, selector):
    if type(mode) is not str or mode not in {"capture", "off", "counters", "targeted"}:
        raise InspectorError("observation_mode must be capture, off, counters or targeted")
    if mode != "targeted":
        if selector is not None:
            raise InspectorError("trace_selector requires targeted observation_mode")
        return None
    if type(selector) is not dict or set(selector) - {"file", "line", "column", "model"}:
        raise InspectorError("targeted selector supports file, line, optional column and model only")
    if (type(selector.get("file")) is not str or not selector["file"] or len(selector["file"]) > 512
            or type(selector.get("line")) is not int or not 1 <= selector["line"] <= 2 ** 31
            or selector.get("model", "keyword_collector") != "keyword_collector"
            or ("column" in selector and (type(selector["column"]) is not int
                or not 0 <= selector["column"] < 2 ** 31))):
        raise InspectorError("invalid targeted source/model selector; context filtering is unsupported")
    return {**selector, "model": "keyword_collector"}


def _normalize_trace_selector(selector, configuration):
    """Bind one source/model selection without importing or running source.

    This prehash and bounded AST location check is not consumed-source proof.
    The native inventory and read hashes provide the later independent status.
    """
    if selector is None:
        return None, None
    try:
        if type(configuration.get("project_root")) is not str:
            raise InspectorError("targeted selection requires a project_root")
        root = Path(configuration["project_root"]).resolve(strict=True)
        requested = Path(selector["file"])
        path = (requested if requested.is_absolute() else root / requested).resolve(strict=True)
        if not root.is_dir() or not path.is_relative_to(root) or path.suffix != ".py":
            raise InspectorError("targeted file must be Python source inside the declared project_root")
        normalized = str(path)
        if len(normalized) > 512:
            raise InspectorError("normalized targeted file exceeds 512 characters")
        normalized.encode("utf-8", errors="strict")
        raw = read_fixed(path, MAX_SELECTOR_SOURCE_BYTES)
        encoding, _ = tokenize.detect_encoding(BytesIO(raw).readline)
        tree = ast.parse(raw.decode(encoding), filename=normalized)
        count = visits = 0
        for node in ast.walk(tree):
            visits += 1
            if visits > 100000:
                raise InspectorError("targeted source AST verification node limit exceeded")
            if (isinstance(node, ast.Call) and node.lineno == selector["line"]
                    and ("column" not in selector or node.col_offset == selector["column"])):
                count += 1
        if not count:
            raise InspectorError("targeted position is not a verified source Call location")
        return {**selector, "file": normalized}, {"file": normalized,
            "source_sha256": hashlib.sha256(raw).hexdigest(), "source_bytes": len(raw),
            "source_verification": "prehashed_only_not_worker_consumed", "position_validation": "bounded_AST_Call_location",
            "matching_source_calls": count, "column_units": "UTF8_byte_offset",
            "native_source_admission": "not_yet_observed", "granularity": "source_line_model_without_context"}
    except InspectorError:
        raise
    except (OSError, ValueError, UnicodeError, SyntaxError, LookupError) as exc:
        raise InspectorError("targeted source/location validation failed: " + type(exc).__name__) from None


def _stream_report(path, report):
    """Atomically stream canonical report bytes with incremental content hash.

    The native report remains resident. JSONEncoder may allocate a whole string
    leaf; bounded writes do not claim an O(chunk) total-memory encoder.
    """
    digest = hashlib.sha256()
    size = 0
    largest_fragment = 0
    temporary = path.with_name(path.name + ".tmp-" + uuid.uuid4().hex)
    try:
        with temporary.open("xb") as stream:
            encoder = json.JSONEncoder(ensure_ascii=True, sort_keys=True,
                                      separators=(",", ":"), allow_nan=False)
            for fragment in encoder.iterencode(report):
                largest_fragment = max(largest_fragment, len(fragment))
                for offset in range(0, len(fragment), REPORT_CHUNK_BYTES):
                    chunk = fragment[offset:offset + REPORT_CHUNK_BYTES].encode("ascii")
                    if stream.write(chunk) != len(chunk):
                        raise OSError("short native report write")
                    digest.update(chunk)
                    size += len(chunk)
            stream.flush()
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
    return {"path": path.name, "bytes": size, "sha256": digest.hexdigest(),
            "max_write_bytes": REPORT_CHUNK_BYTES, "max_encoder_fragment_characters": largest_fragment,
            "memory_contract": "resident_native_report_and_unbounded_encoder_string_leaf"}


def _bootstrap_bytes(value):
    """Bound a primitive request before encoding or child-side JSON decode."""
    pending = [(value, 0)]
    visited = 0
    text_characters = 0
    while pending:
        item, depth = pending.pop()
        visited += 1
        if visited > 65536 or depth > 32:
            raise InspectorError("project bootstrap node or depth limit exceeded")
        kind = type(item)
        if kind is dict:
            if len(item) > 65536:
                raise InspectorError("project bootstrap node limit exceeded")
            for key, child in item.items():
                if type(key) is not str:
                    raise InspectorError("project bootstrap requires string dictionary keys")
                text_characters += len(key)
                pending.append((child, depth + 1))
        elif kind in (tuple, list):
            if len(item) > 65536:
                raise InspectorError("project bootstrap node limit exceeded")
            pending.extend((child, depth + 1) for child in item)
        elif kind is str:
            text_characters += len(item)
        elif kind is int:
            if not -(2 ** 63) <= item < 2 ** 63:
                raise InspectorError("project bootstrap integer exceeds signed64 range")
        elif kind is float:
            if not math.isfinite(item):
                raise InspectorError("project bootstrap floats must be finite")
        elif item is not None and kind is not bool:
            raise InspectorError("project bootstrap must be a primitive JSON tree")
        if text_characters > MAX_BOOTSTRAP_BYTES:
            raise InspectorError("project bootstrap payload exceeds 1 MiB")
    encoded = _json_bytes(value)
    if len(encoded) > MAX_BOOTSTRAP_BYTES:
        raise InspectorError("project bootstrap payload exceeds 1 MiB")
    return encoded


def _json_bytes(value):
    # Native reports can contain Python strings with unpaired surrogates. ASCII
    # escaping preserves those strings without claiming a UTF-8 description.
    return json.dumps(value, ensure_ascii=True, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("ascii")


def _content_digest(value):
    return hashlib.sha256(_json_bytes(value)).hexdigest()


def _rows_digest(rows, fields):
    digest = hashlib.sha256()
    for row in rows:
        digest.update(_json_bytes({field: row.get(field) for field in fields}))
        digest.update(b"\n")
    return digest.hexdigest()


def _atomic_bytes(path, payload):
    temporary = path.with_name(path.name + ".tmp-" + uuid.uuid4().hex)
    try:
        with temporary.open("xb") as stream:
            stream.write(payload)
            stream.flush()
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _adapter_digest():
    digest = hashlib.sha256()
    root = Path(__file__).resolve().parent
    for path in sorted(root.glob("*.py")):
        digest.update(path.name.encode("utf-8") + b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _diagnostic_error(exc):
    # Avoid invoking arbitrary exception formatting from a diagnostic sink.
    reason = exc.args[0] if exc.args and type(exc.args[0]) is str else type(exc).__name__
    reason = reason[:512]
    try:
        reason.encode("utf-8")
    except UnicodeError:
        reason = type(exc).__name__
    return {"exception": type(exc).__name__, "reason": reason}


def _project_metadata(report, run_id, report_sha256, adapter_before, adapter_after, trace_health=None):
    rows = report.get("modules", [])
    consumed = [row for row in rows if "consumed_sha256" in row]
    source_stable = report.get("source_snapshot_stable") is True and all(
        row.get("consumed_sha256") == row.get("sha256") for row in consumed)
    configuration = report["effective_config"]
    native_implementation_stable = report.get("implementation_stable") is True
    entry_descriptor = {"requested_sha256": _content_digest(configuration.get("entry_points", [])),
        "resolved_sha256": _content_digest(report.get("resolved_entry_points", [])),
        "resolved_count": len(report.get("resolved_entry_points", [])),
        "activation_contract_sha256": _content_digest(report.get("exported_entry_activation", [])),
        "source_scope_assumptions_sha256": _content_digest(report.get("source_scope_entry_assumptions", [])),
        "interpretation": "native conditional external roots; no extra inspector roots"}
    metadata = {
        "run_id": run_id,
        "implementation_sha256": report["implementation_sha256_before"],
        "implementation_sha256_after": report.get("implementation_sha256_after"),
        "implementation_stable": native_implementation_stable and adapter_before == adapter_after,
        "adapter_implementation_sha256": adapter_before,
        "adapter_implementation_sha256_after": adapter_after,
        "adapter_implementation_stable": adapter_before == adapter_after,
        "code_identity_policy": "native_engine_inventory_and_inspector_content_inventory",
        "inputs": {
            "identity_algorithm": "sha256_ascii_canonical_json_rows_v1",
            "inventory_sha256": _rows_digest(rows, ("path", "module", "ownership", "sha256", "bytes")),
            "inventory_count": len(rows),
            "consumed_sha256": _rows_digest(consumed, ("path", "consumed_sha256", "consumed_bytes")),
            "consumed_count": len(consumed),
            "full_identity_rows": "companion native_project_report.json modules",
        },
        "configuration": configuration,
        "configuration_sha256": report["configuration_sha256"],
        "entry_points": entry_descriptor,
        "dependency_policy": {
            "dependency_source_roots": configuration.get("dependency_source_roots", []),
            "dependency_paths": configuration.get("dependency_paths", []),
            "boundaries_sha256": _content_digest(report.get("dependency_boundaries", [])),
            "boundary_count": len(report.get("dependency_boundaries", [])),
            "interpretation": report.get("boundary_policy"),
        },
        "models": {
            "native_builtin_implementation_sha256": report["implementation_sha256_before"],
            "framework_discovery_sha256": _content_digest(report.get("framework_entry_discovery", {})),
            "boundary_contract_applications_sha256": _content_digest(report.get("boundary_contract_applications", [])),
            "declared_external_frameworks": configuration.get("external_frameworks", []),
        },
        "cache": "new_world",
        "source_validation": "pre_post_verified_with_consumed_hashes" if source_stable else "changed_during_analysis",
        "source_validation_limit": "consumed reads plus native pre/post verification; transient changes outside reads remain unobserved",
        "native_project_status": report.get("status"),
        "native_project_solver_completed": report.get("solver_completed", False),
        "native_project_source_coverage": report.get("coverage", {}),
        "whole_program_soundness_proved": False,
        "project_report": {"path": "native_project_report.json", "base": "incident_directory",
            "sha256": report_sha256, "body_policy": "native report before observer health fields"},
        "environment": {"python": sys.version, "platform": platform.platform()},
        "phases": {"native_project_seconds": report.get("wall_seconds")},
    }
    if trace_health is not None:
        metadata["trace_health"] = trace_health
    return metadata


class _ProjectDiagnostic:
    """Single-thread native bridge; hot observers write only bounded memory."""

    def __init__(self, incident, run_id, request, trace=False, *, request_sha256=None,
                 configuration=None, observation_mode="capture", trace_selector=None,
                 trace_source_binding=None):
        self.incident = Path(incident)
        self.run_id = run_id
        self.request = request
        self.report_path = self.incident / "native_project_report.json"
        self.bundle_path = self.incident / "bundle"
        self.report_sha256 = None
        self.report_written = False
        self.finished = False
        self.capture_state = {"status": "not_observed"}
        self.metrics = {}
        self.observation_costs = {"version": 1,
            "scope": "completed matching operations before current receipt encoding",
            "clock": "worker_perf_counter_cumulative_wall_durations",
            "overlap": "phase observer includes cold checkpoint; cold includes counter, trace and receipt; do not sum nested clocks",
            "phase_observer_calls": 0, "phase_observer_seconds": 0.0,
            "cold_checkpoint_calls": 0, "cold_checkpoint_seconds": 0.0,
            "cold_checkpoint_attempts": 0, "cold_checkpoint_failures": 0,
            "routine_checkpoint_skips": 0,
            "counter_sampling_calls": 0, "counter_sampling_seconds": 0.0,
            "trace_flush_calls": 0, "trace_flush_seconds": 0.0,
            "progress_frames_encoded": 0, "progress_encoding_seconds": 0.0,
            "progress_write_attempts_completed": 0, "progress_write_flush_seconds": 0.0,
            "progress_frames_written": 0, "progress_bytes_written": 0,
            "progress_write_failures": 0}
        self.metrics["observation_costs"] = self.observation_costs
        self.adapter_before = _adapter_digest()
        self.mode = observation_mode
        self.selector = trace_selector
        self.configuration = configuration or {}
        self.solver = None
        self._last_trace_sequence = 0
        self._trace_bytes = 0
        self._trace_stopped = False
        self._hot_steps = 0
        self._last_cold_work = 0
        self._last_cold_attempt = -CHECKPOINT_SECONDS
        self._requested_trace = trace
        self.receipt = IncidentRecorder(self.incident, run_id,
            request_sha256 or "direct_diagnostic_unbound_request", self.adapter_before,
            self.configuration, observation_mode)
        self.receipt.data["observation_costs"] = self.observation_costs
        self.metrics["receipt_persistence"] = self.receipt.persistence
        self.receipt.data["observation"]["phase_receipt_policy"] = "routine_attempt_cadence_critical_forced_v1"
        self.receipt.data["observation"]["phase_evidence_sources"] = "every committed progress frame; independently sampled small receipt"
        if trace_selector is not None:
            self.receipt.data["observation"]["selector"] = dict(trace_selector)
            self.receipt.data["observation"]["selector_source"] = trace_source_binding or {"source_verification": "unavailable_direct_diagnostic"}
        self.recorder = None
        if trace or observation_mode == "targeted":
            from .trace import TraceRecorder
            self.recorder = TraceRecorder()
            self.recorder.record("run_start", stage="project_analysis")
        self.receipt.checkpoint(force=True)

    def solver_ready(self, solver):
        self.solver = solver
        eager = getattr(solver.config, "builtin_bootstrap_policy", "queued") == "eager_inert"
        if self.mode in {"capture", "targeted"}:
            solver.enable_inspector_boundaries()
        if self.mode != "off":
            solver.enable_inspector_diagnostics("targeted" if self.mode == "targeted" else "counters",
                self.run_id, selector=self.selector)
        if self.recorder is not None and self._requested_trace:
            solver.set_work_step_observer(self.observe_step)
        if self.mode != "off" and not (self.mode == "counters" and eager):
            solver.set_inspector_cold_checkpoint(self.cold_checkpoint,
                work_interval=CHECKPOINT_WORK_ITEMS, seconds=CHECKPOINT_SECONDS)
        capabilities = self.receipt.data["observation"]
        capabilities["counter_sampling"] = "disabled" if self.mode == "off" else "cooperative_native_scalar_getter"
        if self.mode == "counters" and eager:
            capabilities["heartbeat"] = "phase_boundaries_only_eager_same_policy"
            capabilities["queued_step_heartbeat"] = "unsupported_eager_admission_contract"
        capabilities["trace_granularity"] = "source_model_no_context_filter" if self.mode == "targeted" else "selected_steps" if self._requested_trace else "not_recorded"
        self.cold_checkpoint(solver, force=True)

    def observe_phase(self, event):
        started = time.perf_counter()
        try:
            self.receipt.phase(event)
            # The worker commits its bound LF progress frame first, or marks
            # the stopped progress prefix as an explicit observation failure.
            # Unknown/new phases stay critical, rather than being throttled by
            # an open-ended prefix match. The receipt remains an older cut.
            self.cold_checkpoint(self.solver, force=event.get("phase") not in _ROUTINE_PHASES)
        finally:
            self._add_cost("phase_observer_calls", 1)
            self._add_cost("phase_observer_seconds", time.perf_counter() - started)

    def _add_cost(self, name, amount):
        # Fixed keys only. Metrics must not make diagnostic failure semantic.
        try:
            self.observation_costs[name] += amount
        except Exception:
            self.receipt.data["health"]["observation_cost_metrics_failed"] = True

    def observe_step(self, event):
        # Called in the atomic work path: no filesystem, clock or fact traversal.
        self._hot_steps += 1
        if self.recorder is not None and self._requested_trace:
            self.recorder.observe_step(event)

    def _sample_counters(self, solver):
        if solver is None or self.mode == "off":
            return
        started = time.perf_counter()
        try:
            counters = solver.get_inspector_counters()
            self.receipt.counters(counters)
            self.receipt.data["observation"]["actual_bootstrap_behavior"] = counters.get("actual_bootstrap", {}).get("observed_behavior", "unavailable_see_counter_coverage")
        except Exception:
            self.receipt.data["counters"] = {"status": "unavailable", "reason": "native_scalar_getter_unavailable_or_invalid"}
        finally:
            self._add_cost("counter_sampling_calls", 1)
            self._add_cost("counter_sampling_seconds", time.perf_counter() - started)

    def _persist_trace_batch(self):
        if self.recorder is None or self._trace_stopped:
            return
        batch = self.recorder.cold_batch(self._last_trace_sequence,
            max_events=MAX_TRACE_BATCH_EVENTS, max_bytes=MAX_TRACE_BATCH_BYTES)
        health = self.receipt.data["health"]
        health["trace"] = batch["health"]
        if batch["gap_before_batch"]:
            health["trace_prefix_stop_reason"] = "ring_loss_before_cold_checkpoint"
            self._trace_stopped = True
            return
        encoded = []
        size = 0
        for event in batch["events"]:
            frame = _json_bytes({**event, "trace_protocol": TRACE_PROTOCOL,
                "run_id": self.run_id, "request_sha256": self.receipt.data["request_sha256"]}) + b"\n"
            if size + len(frame) > MAX_TRACE_BATCH_BYTES:
                break
            encoded.append(frame)
            size += len(frame)
        if not encoded:
            return
        if self._trace_bytes + size > MAX_TRACE_BYTES:
            health["trace_prefix_stop_reason"] = "persisted_trace_byte_budget_exhausted"
            self._trace_stopped = True
            return
        try:
            with (self.incident / "runtime_trace.jsonl").open("ab") as stream:
                for frame in encoded:
                    if stream.write(frame) != len(frame):
                        raise OSError("short trace frame write")
                stream.flush()
            self._trace_bytes += size
            self._last_trace_sequence = json.loads(encoded[-1])["sequence"]
            self.receipt.data["artifacts"]["runtime_trace"] = {"status": "last_complete_record",
                "path": "runtime_trace.jsonl", "bytes": self._trace_bytes,
                "last_sequence": self._last_trace_sequence, "history": "bounded_prefix_not_replay"}
        except (OSError, ValueError):
            health["trace_write_failures"] += 1
            self._trace_stopped = True
            health["trace_prefix_stop_reason"] = "write_or_flush_error"

    def cold_checkpoint(self, solver=None, *, force=True):
        """Native calls this on the owning thread outside atomic work.

        Synchronous filesystem latency is observation cost under the external
        wall deadline, not a hard per-write latency guarantee.
        """
        # Native cold dispatch already applies the fixed work/time cadence and
        # therefore forces its sample. Routine phase callbacks explicitly pass
        # force=False and gate the entire observation before sampling/encoding.
        started = time.perf_counter()
        try:
            now = time.monotonic()
            if (not force and now - max(self._last_cold_attempt, self.receipt.last_attempt)
                    < CHECKPOINT_SECONDS):
                self._add_cost("routine_checkpoint_skips", 1)
                self.receipt.persistence["cadence_skips"] += 1
                return False
            # Admission covers the entire cold observation, including failures
            # before the eventual receipt writer (sampling, drain or trace).
            self._last_cold_attempt = now
            self._add_cost("cold_checkpoint_attempts", 1)
            self._sample_counters(solver)
            if self.mode == "targeted" and solver is not None:
                batch = solver.drain_inspector_events()
                self.recorder.record_native_batch(batch)
                self.receipt.data["health"]["native_targeted"] = batch.get("health", {})
            trace_started = time.perf_counter()
            self._persist_trace_batch()
            if self.recorder is not None:
                self._add_cost("trace_flush_calls", 1)
                self._add_cost("trace_flush_seconds", time.perf_counter() - trace_started)
            self._last_cold_work = self._hot_steps
            return self.receipt.checkpoint(force=force)
        except Exception:
            # No observation failure may turn an otherwise native step into an
            # analysis failure. Interrupts and process exits still propagate.
            self.receipt.data["health"]["cold_checkpoint_failed"] = True
            self._add_cost("cold_checkpoint_failures", 1)
            return False
        finally:
            self._add_cost("cold_checkpoint_calls", 1)
            self._add_cost("cold_checkpoint_seconds", time.perf_counter() - started)

    def project_finished(self, solver, report):
        self.finished = True
        state = self.receipt.data
        state["native"] = {"status": report.get("status"), "solver_completed": report.get("solver_completed", False),
            "stop_reason": report.get("analysis_failure_phase") or report.get("analysis_error") or report.get("status")}
        # Never copy arbitrary native error values into the bounded receipt.
        if type(state["native"]["stop_reason"]) is str:
            state["native"]["stop_reason"] = state["native"]["stop_reason"][:512]
        else:
            state["native"]["stop_reason"] = report.get("status")
        identity = state["identity"]
        identity["implementation_verification"] = "verified_same" if report.get("implementation_stable") is True else "changed" if report.get("implementation_stable") is False and report.get("implementation_sha256_after") else "unverified"
        identity["source_verification"] = "verified_same" if report.get("source_snapshot_stable") is True else "changed" if report.get("source_snapshot_stable") is False else "unverified"
        identity["source_binding"] = "consumed_and_finally_verified" if (report.get("source_snapshot_stable") is True and any("consumed_sha256" in row for row in report.get("modules", []))) else "final_native_verification_without_consumed_binding" if report.get("source_snapshot_stable") is True else identity["source_binding"]
        if self.selector is not None:
            selection = state["observation"]["selector_source"]
            selected_path = Path(self.selector["file"])
            rows = [row for row in report.get("modules", [])
                if str((Path(self.configuration.get("project_root", ".")) / row.get("path", "")).resolve()) == str(selected_path)]
            selection["native_source_admission"] = "not_in_native_inventory" if not rows else "consumed" if "consumed_sha256" in rows[0] else "inventoried_not_consumed"
            selection["consumed_source_binding"] = ("matched_initial_selection" if rows and rows[0].get("consumed_sha256") == selection.get("source_sha256")
                else "changed_from_initial_selection" if rows and "consumed_sha256" in rows[0] else "unverified")
            state["observation"]["targeted_window_end"] = {"reason": "native_project_finished_callback",
                "native_status": report.get("status"), "semantic_claim": "observed_selected_operations_only"}
        if "source_snapshot_stable" in report and "implementation_stable" in report:
            state["phase"]["last_completed"] = "final_native_verification_completed"
        self.cold_checkpoint(solver, force=True)
        if self.recorder is not None:
            self.recorder.record("run_stop", stage="project_analysis", native_status=report.get("status"))
            self.cold_checkpoint(solver, force=True)
        try:
            if self.mode in {"off", "counters"}:
                self.capture_state = {"status": "not_requested", "reason": "observation_mode_" + self.mode}
            elif solver is None:
                self.capture_state = {"status": "unsupported", "reason": "native project did not construct a solver"}
            elif (report.get("status") not in {"completed_with_residuals", "incomplete", "partial_source_analysis"}
                    or report.get("source_snapshot_stable") is not True
                    or report.get("implementation_stable") is not True):
                self.capture_state = {"status": "refused",
                    "reason": "native project failed or its final source/code evidence is unstable",
                    "native_status": report.get("status")}
            else:
                from .adapter import capture
                from .storage import write_bundle
                adapter_after = _adapter_digest()
                if adapter_after != self.adapter_before:
                    self.capture_state = {"status": "refused", "reason": "tool_changed_during_analysis"}
                    raise InspectorError("tool_changed_during_analysis")
                identity["adapter_verification"] = "verified_same"
                metadata = _project_metadata(report, self.run_id, None,
                    self.adapter_before, adapter_after, self.recorder.health if self.recorder else None)
                # This immutable snapshot binds existing run/input/config data.
                # A future companion hash belongs only to the outer incident.
                metadata["project_report"] = {"path": "native_project_report.json", "base": "incident_directory",
                    "binding": "separate_incident_receipt_after_snapshot", "sha256": None}
                metadata["observation"] = dict(state["observation"])
                if self.selector is not None:
                    selected = state["observation"]["selector_source"]
                    matched_rows = [row for row in report.get("modules", [])
                        if str((Path(self.configuration["project_root"]) / row.get("path", "")).resolve())
                        == self.selector["file"]]
                    # A small selected-file receipt supplements the compact
                    # whole-project catalog. Final native verification checked
                    # every source_stable flag; no new read or producer recipe
                    # supplies this independent source binding.
                    metadata["selected_source_binding"] = {
                        "version": 1, "semantic_version": "native_pre_consumed_post_source_v1",
                        "scope": "declared_targeted_source_file", "rows": [{
                            "path": self.selector["file"],
                            "sha256_before": selected.get("source_sha256"),
                            "consumed_sha256": row.get("consumed_sha256"),
                            "sha256_after": row.get("sha256") if row.get("source_stable") is True else None,
                            "source_stable": row.get("source_stable") is True}
                            for row in matched_rows]}
                state["phase"].update(last_entered="inspector_capture_started", action="capture verified native boundary within CaptureRequest budgets")
                self.receipt.checkpoint(force=True)
                started_capture = time.perf_counter()
                snapshot = capture(solver, self.request, metadata)
                self.metrics["capture_seconds"] = time.perf_counter() - started_capture
                started_export = time.perf_counter()
                write_bundle(snapshot, self.bundle_path, self.recorder.events if self.recorder else None)
                self.metrics["bundle_export_seconds"] = time.perf_counter() - started_export
                self.capture_state = {"status": "sealed", "snapshot_ref": asdict(snapshot.ref),
                                      "snapshot_sha256": snapshot.sha256}
                state["artifacts"]["snapshot"] = {"status": "sealed", "path": "bundle",
                    "snapshot_sha256": snapshot.sha256, "snapshot_ref": asdict(snapshot.ref)}
                state["phase"].update(last_completed="inspector_capture_completed", action=None)
        except InspectorError as exc:
            if self.capture_state.get("reason") != "tool_changed_during_analysis":
                self.capture_state = {"status": "refused", **_diagnostic_error(exc)}
        except Exception as exc:
            self.capture_state = {"status": "failed", **_diagnostic_error(exc)}
        state["capture"] = self.capture_state
        if self.capture_state["status"] != "sealed":
            state["artifacts"]["snapshot"] = {"status": "missing", "reason": self.capture_state.get("reason", self.capture_state["status"])}
        self.cold_checkpoint(solver, force=True)
        # Full native report export is deliberately after the small receipt
        # and optional safe snapshot. Export failure cannot erase those records.
        started = time.perf_counter()
        state["phase"].update(last_entered="native_report_export_started", action="stream native report to separate atomic artifact")
        self.receipt.checkpoint(force=True)
        try:
            report_body = {key: value for key, value in report.items() if key != "inspector"}
            descriptor = _stream_report(self.report_path, report_body)
            self.report_sha256 = descriptor["sha256"]
            self.report_written = True
            self.metrics["native_report_export_seconds"] = time.perf_counter() - started
            self.metrics["native_report_bytes"] = descriptor["bytes"]
            state["artifacts"]["native_report"] = {"status": "exported", **descriptor}
            state["phase"].update(last_completed="native_report_export_completed", action=None)
        except Exception as exc:
            state["artifacts"]["native_report"] = {"status": "missing", "reason": "report_export_failed", "error": _diagnostic_error(exc)}
            state["health"]["report_export_error"] = _diagnostic_error(exc)
        finally:
            self.cold_checkpoint(solver, force=True)


def _read_bound_artifacts(incident, run_id, *, request_digest=None, report_mode="inline",
                          max_report_bytes=DEFAULT_REPORT_BYTES, details=None):
    """Read only fixed files in this newly-created incident directory."""
    report_path = incident / "native_project_report.json"
    report = None
    report_sha = None
    diagnostics = []
    details = details if details is not None else {}
    receipt = None
    try:
        if request_digest is None:
            receipt = decode_object(read_fixed(incident / "incident.json", MAX_RECEIPT_BYTES))
            validate_receipt(receipt, run_id, receipt.get("request_sha256"))
        else:
            receipt = read_receipt(incident / "incident.json", run_id, request_digest)
        details["worker_receipt"] = receipt
    except (OSError, InspectorError, ValueError, UnicodeError, RecursionError):
        details["worker_receipt"] = None
        details["incident_loading"] = "missing_or_invalid_bound_receipt"
    expected_report = (receipt or {}).get("artifacts", {}).get("native_report", {})
    try:
        descriptor = os.open(report_path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
        with os.fdopen(descriptor, "rb") as stream:
            info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode):
            raise InspectorError("native report is not regular")
        reference = {"path": str(report_path), "bytes": info.st_size,
            "sha256": expected_report.get("sha256"),
            "hash_verification": "worker_export_receipt_only_not_parent_content_read" if expected_report.get("sha256") else "unavailable"}
        if (expected_report.get("bytes") is not None and expected_report["bytes"] != info.st_size
                and (report_mode == "reference" or info.st_size > max_report_bytes)):
            raise InspectorError("native report size changed since worker export")
        details["native_report_reference"] = reference
        if report_mode == "reference" or info.st_size > max_report_bytes:
            details["native_report_loading"] = {"status": "reference_only",
                "reason": "requested_reference" if report_mode == "reference" else "inline_byte_budget_exceeded",
                "inline_byte_limit": max_report_bytes}
            report_sha = expected_report.get("sha256")
        else:
            payload = read_fixed(report_path, max_report_bytes)
            report = json.loads(payload.decode("utf-8", errors="strict"))
            if type(report) is not dict:
                raise ValueError("native project report is not an object")
            report_sha = hashlib.sha256(payload).hexdigest()
            if expected_report.get("sha256") is not None and expected_report["sha256"] != report_sha:
                diagnostics.append("native_report_content_does_not_match_incident_binding")
                report = None
            details["native_report_loading"] = {"status": "inline" if report is not None else "refused_content_mismatch",
                "bytes": len(payload), "inline_byte_limit": max_report_bytes}
            reference["sha256"] = report_sha
            reference["hash_verification"] = "parent_bounded_content_read"
    except (OSError, InspectorError, ValueError, UnicodeError, RecursionError):
        report = None
        details["native_report_loading"] = {"status": "unavailable", "reason": "missing_or_invalid_report",
            "inline_byte_limit": max_report_bytes}
    bundle_path = incident / "bundle"
    sealed = None
    try:
        from .storage import load_bundle
        bundle = load_bundle(bundle_path)
        if bundle.snapshot.ref.run_id == run_id:
            sealed = {"path": str(bundle_path), "snapshot_ref": asdict(bundle.snapshot.ref),
                      "snapshot_sha256": bundle.snapshot.sha256}
            expected_report_sha = bundle.data["run"].get("project_report", {}).get("sha256")
            if expected_report_sha is not None and expected_report_sha != report_sha:
                diagnostics.append("native_report_content_does_not_match_sealed_binding")
                report = None
            if expected_report.get("sha256") is not None and expected_report.get("sha256") != report_sha:
                diagnostics.append("native_report_content_does_not_match_sealed_binding")
                report = None
    except (InspectorError, OSError):
        pass
    if request_digest is not None and receipt is None:
        separately_bound = False
        if sealed is not None:
            # Historical snapshots bind the companion hash themselves. New
            # snapshots intentionally require the independent incident receipt.
            separately_bound = type(expected_report_sha) is str and expected_report_sha == report_sha
        if report is not None and not separately_bound:
            diagnostics.append("native_report_missing_independent_run_binding")
            report = None
    return report, report_sha, sealed, diagnostics


def run_project(config, output, *, timeout_seconds=60, memory_mb=1536, trace=False, request=None,
                observation_mode="capture", trace_selector=None,
                report_mode="inline", max_report_bytes=DEFAULT_REPORT_BYTES):
    """Run an unchanged native ProjectConfig under whole-process limits."""
    started = time.monotonic()
    if type(timeout_seconds) not in (int, float) or not math.isfinite(timeout_seconds) or not 0 < timeout_seconds <= 3600:
        raise InspectorError("timeout_seconds must be finite and in (0, 3600]")
    if type(memory_mb) is not int or memory_mb < 128:
        raise InspectorError("memory_mb must be an integer >=128")
    if type(trace) is not bool:
        raise InspectorError("trace must be Boolean")
    trace_selector = _validate_observation(observation_mode, trace_selector)
    if trace and observation_mode in {"off", "counters"}:
        raise InspectorError("trace=True conflicts with off/counters observation_mode")
    if type(report_mode) is not str or report_mode not in {"inline", "reference"}:
        raise InspectorError("report_mode must be inline or reference")
    if type(max_report_bytes) is not int or not 1 <= max_report_bytes <= MAX_REPORT_BYTES:
        raise InspectorError("max_report_bytes must be a positive integer <=64 MiB")
    request = request or CaptureRequest()
    if type(request) is not CaptureRequest:
        raise InspectorError("request must be CaptureRequest")
    if type(config) is dict:
        # The CLI/corpus primitive request must not import or initialize native
        # analysis outside the bounded worker. Native validation happens there.
        configuration = json.loads(_bootstrap_bytes(config))
    else:
        # Convenience for callers that already constructed a native config.
        native_module = sys.modules.get("pythonstan.analysis.project.analysis")
        native_config = vars(native_module).get("ProjectConfig") if type(native_module) is ModuleType else None
        if native_config is None or type(config) is not native_config:
            raise InspectorError("config must be ProjectConfig or its dictionary")
        configuration = json.loads(_bootstrap_bytes(asdict(config)))
    trace_selector, trace_source_binding = _normalize_trace_selector(trace_selector, configuration)
    request_digest = _content_digest(configuration)
    run_id = uuid.uuid4().hex
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    incident = output / run_id
    incident.mkdir()
    progress_path = incident / "progress.jsonl"
    payload = {"configuration": configuration, "capture_request": asdict(request),
        "memory_mb": memory_mb, "trace": trace, "incident": str(incident),
        "run_id": run_id, "request_sha256": request_digest,
        "observation_mode": observation_mode, "trace_selector": trace_selector,
        "trace_source_binding": trace_source_binding}
    bootstrap = _bootstrap_bytes(payload)
    response = None
    response_protocol = None
    process_status = "worker_error"
    returncode = None
    diagnostic = None
    termination = None
    transport_health = {}
    lifecycle_limits = worker_limits()
    receive_started = time.monotonic()
    try:
        completed = run_worker_process([sys.executable, "-m", "pythonstan_inspector.project_runner", "--worker"],
            input=bootstrap.decode("ascii"),
            cwd=Path(__file__).resolve().parents[1], timeout=timeout_seconds)
        returncode = completed.returncode
        transport_health = getattr(completed, "transport_health", {})
        record = None
        # Only LF terminates a protocol line. Unicode line separators inside
        # a valid UTF-8 JSON string are payload, not framing characters.
        for line in StringIO(completed.stdout, newline="\n"):
            if line.startswith(_RESULT_MARKER):
                record = line
        if record is not None:
            try:
                # The last relevant record is authoritative, including when
                # torn or unbound. Never fall back to an earlier good receipt.
                if not record.endswith("\n"):
                    raise ValueError("unterminated final receipt")
                # The process layer uses lossless surrogateescape decoding.
                # Invalid UTF-8 must not be replaced into a valid JSON body.
                encoded_record = record.encode("utf-8")
                if len(encoded_record) > MAX_RECEIPT_BYTES:
                    raise ValueError("terminal reference receipt exceeds 64 KiB")
                candidate = decode_object(record[len(_RESULT_MARKER):-1].encode("utf-8"))
                if (type(candidate) is not dict or candidate.get("run_id") != run_id
                        or candidate.get("request_sha256") != request_digest):
                    raise ValueError("unbound final receipt")
                if (("capture_state" in candidate and (type(candidate["capture_state"]) is not dict
                        or type(candidate["capture_state"].get("status")) is not str))
                        or ("metrics" in candidate and type(candidate["metrics"]) is not dict)
                        or ("worker_analysis_error" in candidate and candidate["worker_analysis_error"] is not None
                            and type(candidate["worker_analysis_error"]) is not dict)
                        or ("memory_limit_enforced" in candidate and type(candidate["memory_limit_enforced"]) is not bool)):
                    raise ValueError("malformed terminal reference receipt fields")
                declared_protocol = candidate.get("transport_protocol", _LEGACY_RESULT_PROTOCOL)
                if "transport_protocol" in candidate and declared_protocol != _RESULT_STREAM_PROTOCOL:
                    raise ValueError("unsupported final receipt protocol")
                response = candidate
                response_protocol = declared_protocol
            except (ValueError, TypeError, RecursionError, OverflowError) as exc:
                diagnostic = "Invalid worker receipt: " + type(exc).__name__ + ": " + str(exc)
        if transport_health.get("stdout_overflow"):
            response = None
            diagnostic = "Worker stdout byte limit exceeded; all terminal receipts refused"
        if response is not None and returncode == 0 and not response.get("worker_analysis_error"):
            process_status = "worker_finished"
        else:
            diagnostic = ((diagnostic + "\n") if diagnostic else "") + completed.stderr[-3000:] + completed.stdout[-1000:]
    except WorkerTimeout as exc:
        process_status = "timeout"
        termination = exc.termination
        transport_health = exc.transport_health
    except UnsupportedWorkerPlatform as exc:
        diagnostic = str(exc)
    except (OSError, ValueError, UnicodeError) as exc:
        diagnostic = "worker_start_or_transport_error: " + _diagnostic_error(exc)["reason"]
    receive_seconds = time.monotonic() - receive_started
    artifact_read_started = time.monotonic()
    progress = _read_progress(progress_path, run_id, request_digest)
    details = {}
    report, report_sha, sealed, artifact_diagnostics = _read_bound_artifacts(incident, run_id,
        request_digest=request_digest, report_mode=report_mode,
        max_report_bytes=max_report_bytes, details=details)
    # Read-only compatibility for the former inline stdout producer. New
    # workers never emit it. The entire wire was already capped at 1 MiB;
    # keep a separately labelled primitive/tree budget and the caller's cap.
    if (report is None and response is not None and sealed is None
            and "native_report_fallback" in response and report_mode == "inline"):
        fallback = response["native_report_fallback"]
        try:
            encoded_fallback = _bootstrap_bytes(fallback)
            if type(fallback) is not dict or len(encoded_fallback) > max_report_bytes:
                raise InspectorError("legacy inline fallback exceeds budget")
            report = fallback
            details["native_report_loading"] = {"status": "legacy_bounded_stdout_inline",
                "producer_contract": response_protocol, "bytes": len(encoded_fallback)}
        except (InspectorError, ValueError, UnicodeError):
            artifact_diagnostics.append("legacy_inline_report_fallback_refused")
    capture_state = (response or {}).get("capture_state", {"status": "unavailable",
        "reason": "worker did not return a capture outcome"})
    if sealed is not None and capture_state.get("status") != "sealed":
        capture_state = {"status": "sealed", "recovered_after_worker_termination": True,
                         "snapshot_ref": sealed["snapshot_ref"]}
    if capture_state.get("status") == "sealed" and sealed is None:
        capture_state = {"status": "failed", "reason": "worker claimed a bundle that did not validate"}
    worker_receipt = details.get("worker_receipt")
    # A valid independently committed receipt remains evidence even if the
    # terminal transport failed; its observation is not process completion.
    if worker_receipt is not None and sealed is None and response is None:
        worker_capture = worker_receipt.get("capture", {})
        if worker_capture.get("status") != "sealed":
            capture_state = worker_capture
    runtime_trace = None
    try:
        runtime_trace = read_trace(incident / "runtime_trace.jsonl", run_id, request_digest)
    except (OSError, InspectorError, ValueError, UnicodeError, RecursionError):
        pass
    artifact_read_seconds = time.monotonic() - artifact_read_started
    external = {"protocol": INCIDENT_PROTOCOL, "actor": "supervisor", "run_id": run_id,
        "request_sha256": request_digest, "process": {"status": process_status, "returncode": returncode,
            "liveness_observation": "child_reaped" if (termination or {}).get("child_reaped") else "exited" if returncode is not None else "unknown",
            **({"termination": termination} if termination is not None else {})},
        "identity": (worker_receipt or {}).get("identity", {"source_verification": "unverified",
            "implementation_verification": "unverified", "configuration_verification": "unverified"}),
        "native": (worker_receipt or {}).get("native", {"status": "unobserved", "stop_reason": "worker_silent"}),
        "capture": capture_state, "transport": {"status": "accepted" if response is not None else "unavailable_or_refused",
            "health": transport_health}, "check": {"status": "not_run"},
        "phase": (worker_receipt or {}).get("phase", {"last_entered": None, "last_completed": None}),
        "counters": (worker_receipt or {}).get("counters", {"status": "unavailable"}),
        "observation": (worker_receipt or {}).get("observation", {"mode": observation_mode}),
        "artifacts": {"worker_receipt": {"status": "last_complete_record" if worker_receipt else "missing_or_invalid"},
            "snapshot": {"status": "sealed" if sealed else "missing", "reason": None if sealed else capture_state.get("reason", capture_state.get("status"))},
            "native_report": details.get("native_report_loading", {"status": "unavailable"}),
            "runtime_trace": {"status": "last_complete_record_prefix" if runtime_trace else "missing"}},
        "supervisor_elapsed_seconds": time.monotonic() - started,
        "evidence_kind": "external_process_result_and_last_verified_diagnostic_evidence"}
    external_path = incident / "supervisor_incident.json"
    external_write_error = None
    try:
        write_receipt(external_path, external)
    except (OSError, InspectorError, ValueError, UnicodeError):
        external_write_error = "supervisor_receipt_write_failed"
    return {"status": process_status, "run_id": run_id, "incident": str(incident),
        "native_report": report,
        "native_report_reference": details.get("native_report_reference"),
        "native_report_loading": details.get("native_report_loading"),
        "incident_receipt": external, "worker_incident_receipt": worker_receipt,
        "incident_receipt_path": str(external_path) if external_write_error is None else None,
        "incident_receipt_write_error": external_write_error, "runtime_trace": runtime_trace,
        "native_report_origin": ("sealed_callback_before_worker_error" if sealed and process_status != "worker_finished"
            else "worker_exception_report" if (response or {}).get("worker_analysis_error")
            else "native_final_callback" if report is not None else "unavailable"),
        "native_report_path": str(incident / "native_project_report.json") if report_sha else None,
        "native_report_sha256": report_sha, "bundle": sealed["path"] if sealed else None,
        "last_sealed_snapshot": sealed, "capture_state": capture_state,
        "metrics": {**(response or {}).get("metrics", {}),
            "parent_receive_seconds_including_cleanup": receive_seconds,
            "parent_artifact_read_parse_seconds": artifact_read_seconds}, "partial_progress": progress,
        "artifact_diagnostics": artifact_diagnostics,
        "returncode": returncode, "diagnostic": diagnostic,
        "worker_analysis_error": (response or {}).get("worker_analysis_error"),
        "supervisor_wall_seconds": time.monotonic() - started,
        "resource_limits": {"timeout_seconds": timeout_seconds, "memory_mb": memory_mb,
            **lifecycle_limits,
            **({"worker_termination": termination} if termination is not None else {}),
            "scope": "whole_worker_including_capture_and_export",
            "worker_transport_protocol": _RESULT_STREAM_PROTOCOL,
            "worker_response_protocol": response_protocol,
            "bootstrap_payload_bytes_limit": MAX_BOOTSTRAP_BYTES,
            "incident_receipt_bytes_limit": MAX_RECEIPT_BYTES,
            "runtime_trace_bytes_limit": MAX_TRACE_BYTES,
            "native_report_inline_bytes_limit": max_report_bytes,
            "native_report_inline_policy": report_mode,
            "targeted_selector_source_bytes_limit": MAX_SELECTOR_SOURCE_BYTES,
            "address_space_limit_activation": "after_bounded_bootstrap_decode_before_engine_import",
            "address_space_limit_enforced": (response or {}).get("memory_limit_enforced")}}


def _worker():
    worker_started = time.monotonic()
    encoded_payload = sys.stdin.buffer.read(MAX_BOOTSTRAP_BYTES + 1)
    if len(encoded_payload) > MAX_BOOTSTRAP_BYTES:
        raise InspectorError("project bootstrap payload exceeds 1 MiB")
    payload = json.loads(encoded_payload)
    enforced = False
    try:
        import resource
        bound = payload["memory_mb"] * 1024 ** 2
        resource.setrlimit(resource.RLIMIT_AS, (bound, bound))
        enforced = True
    except ImportError:
        pass
    incident = Path(payload["incident"])
    progress_path = incident / "progress.jsonl"
    last_event = None
    progress_failed = False
    request_fields = dict(payload["capture_request"])
    request_fields["views"] = tuple(request_fields["views"])
    bridge = _ProjectDiagnostic(incident, payload["run_id"], CaptureRequest(**request_fields), payload["trace"],
        request_sha256=payload["request_sha256"], configuration=payload["configuration"],
        observation_mode=payload.get("observation_mode", "capture"),
        trace_selector=payload.get("trace_selector"),
        trace_source_binding=payload.get("trace_source_binding"))

    def checkpoint(event):
        nonlocal last_event, progress_failed
        row = {**event, "progress_protocol": "project_progress_jsonl_v2",
               "run_id": payload["run_id"], "request_sha256": payload["request_sha256"],
               "worker_elapsed_seconds": time.monotonic() - worker_started}
        if not progress_failed:
            encode_started = time.perf_counter()
            write_started = None
            try:
                frame = _json_bytes(row) + b"\n"
                bridge._add_cost("progress_frames_encoded", 1)
                bridge._add_cost("progress_encoding_seconds", time.perf_counter() - encode_started)
                write_started = time.perf_counter()
                with progress_path.open("ab") as stream:
                    if stream.write(frame) != len(frame):
                        raise OSError("short progress frame write")
                    stream.flush()
                bridge._add_cost("progress_frames_written", 1)
                bridge._add_cost("progress_bytes_written", len(frame))
                last_event = row
            except (OSError, ValueError, UnicodeError):
                progress_failed = True
                bridge._add_cost("progress_write_failures", 1)
                bridge.receipt.data["health"]["progress_write_error"] = "progress_prefix_stopped"
            finally:
                if write_started is not None:
                    bridge._add_cost("progress_write_attempts_completed", 1)
                    bridge._add_cost("progress_write_flush_seconds", time.perf_counter() - write_started)
        bridge.observe_phase(row)
    worker_analysis_error = None
    try:
        from pythonstan.analysis.project import ProjectConfig, analyze_project
        # Existing frontend prints are retained only in the worker; target source
        # is never imported or executed by this capture path.
        with redirect_stdout(sys.stderr):
            report = analyze_project(ProjectConfig.from_dict(payload["configuration"]),
                                     progress=checkpoint,
                                     diagnostic=None if bridge.mode == "off" else bridge)
    except Exception as exc:
        worker_analysis_error = _diagnostic_error(exc)
        report = {"status": "analysis_error", "analysis_error": _diagnostic_error(exc),
                  "solver_completed": False, "whole_program_soundness_proved": False}
    if not bridge.finished:
        bridge.project_finished(None, report)
    result = {"transport_protocol": _RESULT_STREAM_PROTOCOL,
        "run_id": payload["run_id"], "request_sha256": payload["request_sha256"],
        "capture_state": bridge.capture_state, "metrics": bridge.metrics,
        "memory_limit_enforced": enforced, "worker_analysis_error": worker_analysis_error,
        "incident_receipt": {"protocol": INCIDENT_PROTOCOL, "path": "incident.json"},
        "native_report_reference": bridge.receipt.data["artifacts"]["native_report"]}
    print(_RESULT_MARKER + _json_bytes(result).decode("ascii"), flush=True)
    sys.stderr.flush()
    if last_event is not None:
        checkpoint({"schema_version": 1, "sequence": last_event["sequence"] + 1,
            "phase": "inspector_worker_finished", "implementation_sha256_before": last_event["implementation_sha256_before"],
            "configuration_sha256": last_event["configuration_sha256"]})
    bridge.receipt.data["process"] = {"status": "worker_output_flushed", "completion": "owned_outputs_flushed_before_exit"}
    bridge.receipt.data["transport"] = {"status": "stdout_reference_receipt_flushed"}
    bridge.cold_checkpoint(bridge.solver, force=True)
    # Only this private process bypasses teardown; native reports, bundles,
    # stdout/stderr, and the terminal progress frame must all be flushed first.
    # Terminal flush/checkpoint errors propagate rather than reach this exit;
    # caught analysis/observer failures retain their separate response statuses.
    os._exit(0)


if __name__ == "__main__":
    if sys.argv[1:] != ["--worker"]:
        raise SystemExit("project_runner is an internal trusted worker; use capture-project")
    _worker()
