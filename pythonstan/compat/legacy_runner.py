"""Run the World/bitmask based engines in an isolated disposable process."""

from __future__ import annotations

import json
import subprocess
import sys
import time
from collections.abc import Mapping

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


def run_legacy(
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
