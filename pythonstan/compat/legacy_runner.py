"""Run the World/bitmask based engines in an isolated disposable process."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import time
from collections.abc import Iterator, Mapping
from typing import TYPE_CHECKING, BinaryIO

from pythonstan.analysis.contracts.cost_model import COST_MODEL_DIGEST, COST_MODEL_VERSION
from pythonstan.analysis.contracts.identity import SourceModule
from pythonstan.analysis.contracts.json_data import (
    JsonObject,
    JsonValue,
    require_array,
    require_int,
    require_number,
    require_object,
    thaw_json,
    validate_json,
    validate_json_object,
)
from pythonstan.config.model import EffectiveConfig
from pythonstan.inputs import SourceSnapshot

if TYPE_CHECKING:
    from pythonstan.analysis.contracts.resources import RunLedger, WorkReservation


def _latest_frame(output: bytes | str | None) -> JsonObject | None:
    """Select the newest valid progress/result frame from the worker wire."""
    if isinstance(output, bytes):
        output = output.decode("utf-8", errors="replace")
    latest: JsonObject | None = None
    for line in (output or "").splitlines():
        try:
            decoded: object = json.loads(line)
            frame = validate_json(decoded)
        except (ValueError, TypeError):
            continue
        if not isinstance(frame, dict):
            continue
        result = frame.get("result")
        kind = frame.get("kind")
        if kind in ("progress", "result") and isinstance(result, dict):
            latest = result
    return latest


def _source_module_wire(source: SourceModule) -> JsonObject:
    """Publish every current SourceModule identity field without dynamic asdict."""
    return {
        "module": source.module,
        "filename": source.filename,
        "source": source.source,
        "digest": source.digest,
        "is_package": source.is_package,
        "original_bytes_digest": source.original_bytes_digest,
        "encoding": source.encoding,
        "import_root_digest": source.import_root_digest,
    }


def _object_field(document: JsonObject, key: str) -> JsonObject:
    """Keep existing fields strict while materializing an absent mutable object."""
    return require_object(document.setdefault(key, {}))


def _array_field(document: JsonObject, key: str) -> list[JsonValue]:
    return require_array(document.setdefault(key, []))


def _run_legacy_unshared(
    snapshot: SourceSnapshot,
    config: EffectiveConfig | Mapping[str, JsonValue],
) -> JsonObject:
    """Run one disposable Native worker and retain honest timeout reservations."""
    started = time.monotonic()
    options = (
        config.to_dict() if isinstance(config, EffectiveConfig) else validate_json_object(config)
    )
    scenarios: list[JsonValue] = [
        {
            "root_module": scenario.root_module,
            "entry_qualname": scenario.entry_qualname,
            "arguments": [thaw_json(argument) for argument in scenario.arguments],
            "scenario_id": scenario.scenario_id,
        }
        for scenario in snapshot.scenarios
    ]
    payload: JsonObject = {
        "modules": [_source_module_wire(module) for module in snapshot.modules],
        "scenarios": scenarios,
        "config": options,
    }
    resources = require_object(options["resources"])
    seconds = require_number(resources["max_seconds"])
    max_work = require_int(resources["max_work"])
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "pythonstan.compat.native_worker"],
            input=json.dumps(payload),
            text=True,
            capture_output=True,
            timeout=seconds if seconds > 0 else None,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        partial = _latest_frame(exc.stdout)
        if partial is not None:
            partial.update(solver_status="budget_exhausted", coverage_status="open")
            _array_field(partial, "obligations").append(
                {
                    "kind": "time_budget",
                    "reason": "Legacy wall deadline; completed scenario snapshots retained, current scenario incomplete",
                }
            )
            stats = _object_field(partial, "stats")
            stats["process_wall_seconds"] = time.monotonic() - started
            observed = require_int(stats.get("global_work_used", 0))
            stats.update(
                global_work_observed=observed,
                unobserved_work_reservation=max(0, max_work - observed),
                total_work=max_work,
                work_count_status="conservative_reservation",
            )
            _array_field(partial, "phase_trace").append(
                {"phase": "legacy_process", "status": "time_budget", "partial_export": True}
            )
            return partial
        return {
            "engine": options["engine"],
            "solver_status": "budget_exhausted",
            "coverage_status": "open",
            "values": {},
            "calls": [],
            "returns": {},
            "effects": {},
            "outcomes": {},
            "obligations": [
                {
                    "kind": "time_budget",
                    "reason": "Legacy process exceeded global wall time; no partial state exported",
                }
            ],
            "diagnostics": [],
            "stats": {
                "wall_seconds": time.monotonic() - started,
                "global_work_observed": 0,
                "unobserved_work_reservation": max_work,
                "total_work": max_work,
                "work_count_status": "conservative_reservation",
            },
            "phase_trace": [{"phase": "legacy_process", "status": "time_budget"}],
        }
    if proc.returncode:
        raise RuntimeError("Legacy process failed: " + proc.stderr[-4000:])
    result = _latest_frame(proc.stdout)
    if result is None:
        raise RuntimeError("Legacy process emitted invalid JSON: " + proc.stdout[-1000:])
    if "error" in result:
        raise RuntimeError(result["error"])
    _object_field(result, "stats")["process_wall_seconds"] = time.monotonic() - started
    return result


_MAX_WORKER_FRAME_BYTES = 32 * 1024 * 1024


def _worker_frames(stream: BinaryIO, reading: WorkReservation) -> Iterator[JsonObject | str]:
    """Read only prepaid chunks; cleanup capacity was held before dispatch."""
    end = stream.seek(0, 2)
    stream.seek(0)
    while stream.tell() < end:
        if reading.observed + 2 > reading.amount:
            yield "native_wire_work_budget"
            return
        reading.observe(reading.observed + 1)
        chunks: list[bytes] = []
        size = 0
        while stream.tell() < end:
            if size >= _MAX_WORKER_FRAME_BYTES:
                yield "native_wire_capacity_exceeded"
                return
            if reading.observed + 1 > reading.amount:
                yield "native_wire_work_budget"
                return
            reading.observe(reading.observed + 1)
            chunk = stream.readline(min(1024, _MAX_WORKER_FRAME_BYTES - size))
            size += len(chunk)
            if size > _MAX_WORKER_FRAME_BYTES:
                yield "native_wire_capacity_exceeded"
                return
            chunks.append(chunk)
            if not chunk or chunk.endswith(b"\n"):
                break
        try:
            decoded: object = json.loads(b"".join(chunks))
            frame = validate_json_object(decoded)
        except (TypeError, ValueError, UnicodeError):
            yield "native_wire_protocol_failure"
            return
        if frame.get("kind") not in {"progress", "result"} or not isinstance(
            frame.get("result"), dict
        ):
            yield "native_wire_protocol_failure"
            return
        yield frame


def _reported_work(document: JsonObject) -> int | None:
    stats = document.get("stats")
    if not isinstance(stats, dict):
        return None
    value = stats.get("global_work_used")
    return value if type(value) is int and value >= 0 else None


def _result_shape(document: JsonObject) -> bool:
    """Validate mutable result ports before accepting a completed prefix."""
    return (
        all(
            key not in document or isinstance(document[key], dict)
            for key in ("stats", "values", "returns", "effects", "outcomes")
        )
        and all(
            key not in document or isinstance(document[key], list)
            for key in ("calls", "obligations", "diagnostics", "phase_trace")
        )
        and all(
            key not in document or isinstance(document[key], str)
            for key in ("engine", "solver_status", "coverage_status")
        )
    )


def _shared_failure(
    options: JsonObject,
    ledger: RunLedger,
    reason: str,
    *,
    partial: JsonObject | None,
    permit: WorkReservation | None,
    reading: WorkReservation | None,
) -> JsonObject:
    if reading is not None:
        reading.settle(reading.observed)
    ledger.stop(reason)
    reason = ledger.stop_reason or reason
    result: JsonObject = (
        partial
        if partial is not None
        else {
            "engine": options.get("engine", "native"),
            "values": {},
            "calls": [],
            "returns": {},
            "effects": {},
            "outcomes": {},
            "obligations": [],
            "diagnostics": [],
            "stats": {},
            "phase_trace": [],
        }
    )
    result.update(
        solver_status="budget_exhausted"
        if reason
        in {
            "max_work",
            "max_seconds",
            "stage_max_seconds",
            "stage_max_work",
            "max_memory_mb",
            "native_wire_capacity_exceeded",
            "native_wire_work_budget",
        }
        else "failed",
        coverage_status="open",
    )
    _array_field(result, "obligations").append({"kind": "native_incomplete", "reason": reason})
    stats = _object_field(result, "stats")
    if permit is not None:
        stats.update(
            global_work_observed=permit.observed,
            unobserved_work_reservation=permit.held,
            work_count_status="unknown_reservation"
            if permit.status == "unknown"
            else permit.status,
        )
    stats["resource_ledger"] = ledger.snapshot()
    _array_field(result, "phase_trace").append({"phase": "legacy_process", "status": reason})
    return result


def _run_legacy_shared(
    snapshot: SourceSnapshot, options: JsonObject, ledger: RunLedger
) -> JsonObject:
    from pythonstan.analysis.contracts.resources import ResourceStop

    permit: WorkReservation | None = None
    reading: WorkReservation | None = None
    partial: JsonObject | None = None
    dispatched = False
    try:
        with ledger.phase("native_setup"):
            ledger.checkpoint(force=True)
            payload: JsonObject = {
                "modules": [_source_module_wire(source) for source in snapshot.modules],
                "scenarios": [
                    {
                        "root_module": scenario.root_module,
                        "entry_qualname": scenario.entry_qualname,
                        "arguments": [thaw_json(value) for value in scenario.arguments],
                        "scenario_id": scenario.scenario_id,
                    }
                    for scenario in snapshot.scenarios
                ],
                "config": options,
                "cost_model": {"version": COST_MODEL_VERSION, "digest": COST_MODEL_DIGEST},
            }
            ledger.charge_json(payload, "native_setup")
            resources = require_object(options["resources"])
            available = ledger.remaining_work
            reading = ledger.reserve_work(max(1, available // 8), category="native_wire")
            quota = min(require_int(resources["max_work"]), ledger.remaining_work)
            if quota <= 0:
                raise ResourceStop("max_work")
            seconds = require_number(resources["max_seconds"])
            remaining = ledger.remaining_seconds
            if remaining is not None:
                seconds = min(seconds, remaining) if seconds > 0 else remaining
                if seconds <= 0:
                    raise ResourceStop("max_seconds")
            resources["max_work"] = quota
            resources["max_seconds"] = seconds
            memory = ledger.remaining_memory_bytes
            if memory is not None:
                if memory <= 0:
                    raise ResourceStop("max_memory_mb")
                child_cap = memory / (1024 * 1024)
                configured = require_number(resources.get("max_memory_mb", 0))
                resources["max_memory_mb"] = min(configured, child_cap) if configured else child_cap
            encoded = json.dumps(payload, allow_nan=False)
            permit = ledger.reserve_work(quota, category="native_worker")
        with (
            ledger.phase("native_worker"),
            tempfile.TemporaryFile() as output,
            tempfile.TemporaryFile() as error,
        ):
            ledger.checkpoint(force=True)
            timeout = ledger.remaining_seconds
            if timeout is not None and timeout <= 0:
                raise ResourceStop("max_seconds")
            if seconds > 0:
                timeout = min(timeout, seconds) if timeout is not None else seconds
            reason: str | None = None
            return_code: int | None = None
            try:
                dispatched = True
                process = subprocess.run(
                    [sys.executable, "-m", "pythonstan.compat.native_worker"],
                    input=encoded,
                    text=True,
                    stdout=output,
                    stderr=error,
                    timeout=timeout,
                    check=False,
                )
                return_code = process.returncode
            except subprocess.TimeoutExpired:
                reason = "max_seconds"
                ledger.stop(reason)
            terminal: JsonObject | None = None
            for frame in _worker_frames(output, reading):
                if isinstance(frame, str):
                    reason = reason or frame
                    break
                if terminal is not None:
                    reason = reason or "native_terminal_protocol_failure"
                    break
                if frame.get("cost_model") != {
                    "version": COST_MODEL_VERSION,
                    "digest": COST_MODEL_DIGEST,
                }:
                    reason = reason or "native_cost_model_mismatch"
                    break
                candidate = require_object(frame["result"])
                if not _result_shape(candidate):
                    reason = reason or "native_result_protocol_failure"
                    break
                if "error" in candidate:
                    reason = reason or "native_worker_failure"
                    break
                observed = _reported_work(candidate)
                if observed is None:
                    reason = reason or "native_work_usage_unavailable"
                    break
                try:
                    permit.observe(observed)
                except ValueError:
                    reason = reason or "native_work_protocol_failure"
                    break
                peak = frame.get("peak_resident_bytes")
                if type(peak) is int and peak >= 0:
                    try:
                        ledger.record_child_memory(peak)
                    except ResourceStop as stopped:
                        reason = reason or stopped.reason
                    finally:
                        ledger.release_child_memory()
                elif ledger.memory_cap is not None:
                    reason = reason or "native_memory_observation_unavailable"
                if reason is not None:
                    ledger.stop(reason)
                partial = candidate
                if frame["kind"] == "result":
                    terminal = candidate
            if reason is None and return_code != 0:
                reason = "native_process_failure"
            if reason is None and terminal is None:
                reason = "native_terminal_unavailable"
            if (
                reason in {None, "max_memory_mb", "native_memory_observation_unavailable"}
                and return_code == 0
                and terminal is not None
            ):
                total = _reported_work(terminal)
                if total is None:
                    reason = "native_work_usage_unavailable"
                else:
                    try:
                        permit.settle(total)
                    except ValueError:
                        reason = "native_work_protocol_failure"
            if reason is not None:
                if permit.status != "settled":
                    permit.unknown(reason)
                return _shared_failure(
                    options, ledger, reason, partial=partial, permit=permit, reading=reading
                )
            assert terminal is not None
            reading.settle(reading.observed)
            ledger.checkpoint(force=True)
            _object_field(terminal, "stats")["resource_ledger"] = ledger.snapshot()
            return terminal
    except ResourceStop as stopped:
        if permit is not None and permit.status != "settled":
            permit.unknown(stopped.reason) if dispatched else permit.settle(0)
        return _shared_failure(
            options, ledger, stopped.reason, partial=partial, permit=permit, reading=reading
        )
    except (KeyboardInterrupt, MemoryError, OSError, ValueError, TypeError) as failure:
        reason = (
            "cancelled"
            if isinstance(failure, KeyboardInterrupt)
            else "max_memory_mb"
            if isinstance(failure, MemoryError)
            else "native_process_failure"
        )
        if permit is not None and permit.status != "settled":
            permit.unknown(reason) if dispatched else permit.settle(0)
        return _shared_failure(
            options, ledger, reason, partial=partial, permit=permit, reading=reading
        )
    finally:
        if reading is not None:
            reading.settle(reading.observed)
        ledger.release_child_memory()


def run_legacy(
    snapshot: SourceSnapshot,
    config: EffectiveConfig | Mapping[str, JsonValue],
    *,
    ledger: RunLedger | None = None,
) -> JsonObject:
    """Run a disposable Native worker under the actual request grant when supplied."""
    if ledger is None:
        return _run_legacy_unshared(snapshot, config)
    options = (
        config.to_dict() if isinstance(config, EffectiveConfig) else validate_json_object(config)
    )
    return _run_legacy_shared(snapshot, options, ledger)
