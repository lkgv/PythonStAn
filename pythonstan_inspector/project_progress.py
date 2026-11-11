"""Stdlib-only decoder for bound native project progress frames.

Kept byte-for-byte at function level with the frozen native CLI decoder; parity
and golden-frame tests guard this deliberate package-local duplication.
"""
import json
import math
import os
import stat
from pathlib import Path


def _read_progress(path, run_id, request_digest):
    """Recover newline-committed, bound progress after termination.

    These are phase/source observations, never a partial call graph or proof.
    V2 admits only a continuous valid prefix. Unversioned historical files keep
    their weaker skip-invalid recovery contract, explicitly labelled below.
    Unknown or mixed protocols cannot choose either contract by fallback.
    """
    modules = {}
    last = None
    accepted = rejected = 0
    protocol = None
    first_rejection = None
    legacy_protocol = "legacy_unversioned_progress_jsonl"
    current_protocol = "project_progress_jsonl_v2"
    inventory_complete = False
    source_binding = None
    solver_statistics = None
    solver_statistics_phase = None
    last_analysis_error = None
    last_frontend_lifetime = None
    phase_timing_observations = {}
    last_worker_elapsed = -1.0
    timing_observations_rejected = 0
    serialized_json_bytes = None
    identity_fields = ("path", "module", "ownership", "sha256", "bytes", "module_name_syntax")
    try:
        # Read bytes so a bare CR or incomplete UTF-8 tail is not normalized
        # into a committed line by text-mode universal-newline handling.
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
        stream = os.fdopen(descriptor, "rb")
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            stream.close()
            raise OSError("progress artifact is not regular")
    except OSError:
        return {"available": False, "inventory_complete": False, "events": 0,
                "progress_protocol": None, "frame_recovery_policy": "unavailable",
                "first_rejection": None}
    total_progress_bytes = 0
    with stream:
        for line_number, line in enumerate(iter(lambda: stream.readline(8 * 1024 * 1024 + 1), b""), 1):
            protocol_error = False
            try:
                total_progress_bytes += len(line)
                if len(line) > 8 * 1024 * 1024 or total_progress_bytes > 64 * 1024 * 1024:
                    protocol_error = True
                    raise ValueError("progress_frame_or_stream_byte_limit")
                if not line.endswith(b"\n"):
                    raise ValueError("unterminated_frame")
                # JSON's bytes input auto-detects UTF-16/32. The wire contract
                # is UTF-8, so decode explicitly before parsing the body.
                event = json.loads(line.decode("utf-8"))
                if type(event) is not dict:
                    raise ValueError("non_object_frame")
                declared_protocol = event.get("progress_protocol", legacy_protocol)
                if "progress_protocol" in event and declared_protocol != current_protocol:
                    protocol_error = True
                    raise ValueError("unsupported_progress_protocol")
                if protocol is not None and declared_protocol != protocol:
                    protocol_error = True
                    raise ValueError("mixed_progress_protocol")
                if protocol is None:
                    protocol = declared_protocol
                if event.get("run_id") != run_id or event.get("request_sha256") != request_digest:
                    raise ValueError("unbound_checkpoint")
                if (type(event.get("sequence")) is not int
                        or event["sequence"] != accepted + 1):
                    raise ValueError("out_of_order_checkpoint")
                binding = (event.get("implementation_sha256_before"), event.get("configuration_sha256"))
                if any(type(value) is not str or len(value) != 64 for value in binding):
                    raise ValueError("missing implementation/configuration binding")
                if source_binding is not None and binding != source_binding:
                    raise ValueError("changed checkpoint binding")
                fresh_inventory = None
                if event.get("phase") == "inventory_completed":
                    rows = event.get("inventory")
                    if inventory_complete or type(rows) is not list:
                        raise ValueError("replaced or malformed inventory")
                    fresh_inventory = {}
                    for row in rows:
                        if (type(row) is not dict or type(row.get("path")) is not str
                                or row["path"] in fresh_inventory):
                            raise ValueError("invalid inventory identity")
                        fresh_inventory[row["path"]] = dict(row)
                updates = event.get("module_updates", [])
                if type(updates) is not list:
                    raise ValueError("malformed module updates")
                updates = [*updates, *([event["module_record"]] if "module_record" in event else [])]
                frame_modules = modules if fresh_inventory is None else fresh_inventory
                for row in updates:
                    if type(row) is not dict or type(row.get("path")) is not str:
                        raise ValueError("malformed module record")
                    original = frame_modules.get(row["path"])
                    if original is None or any(key in row and row[key] != original.get(key) for key in identity_fields):
                        raise ValueError("changed module source identity")
            except (ValueError, TypeError) as exc:
                rejected += 1
                if first_rejection is None:
                    reason = ("invalid_utf8" if isinstance(exc, UnicodeDecodeError)
                        else "malformed_json" if isinstance(exc, json.JSONDecodeError) else str(exc))
                    first_rejection = {"line": line_number, "reason": reason}
                # A v2 prefix never resumes past a bad frame. Before a
                # protocol is identified, recovery cannot assume legacy.
                if protocol_error or protocol != legacy_protocol:
                    break
                continue
            accepted += 1
            source_binding = binding
            elapsed = event.get("worker_elapsed_seconds")
            if elapsed is not None:
                if (type(elapsed) in (int, float) and math.isfinite(elapsed)
                        and elapsed >= 0 and elapsed >= last_worker_elapsed
                        and type(event.get("phase")) is str):
                    last_worker_elapsed = elapsed
                    phase_timing_observations[event["phase"]] = {
                        "sequence": event["sequence"], "worker_elapsed_seconds": elapsed}
                else:
                    timing_observations_rejected += 1
            size = event.get("worker_json_bytes")
            if event.get("phase") == "worker_json_ready" and type(size) is int and size >= 0:
                serialized_json_bytes = size
            if type(event.get("statistics")) is dict:
                solver_statistics = event["statistics"]
                solver_statistics_phase = event.get("phase")
            if type(event.get("analysis_error")) is str:
                last_analysis_error = {"diagnostic": event["analysis_error"], "phase": event.get("failure_phase")}
            if type(event.get("frontend_lifetime")) is dict:
                last_frontend_lifetime = {"origin_phase": event.get("phase"), "observation": event["frontend_lifetime"]}
            if fresh_inventory is not None:
                modules = fresh_inventory
                inventory_complete = True
            for row in updates:
                modules[row["path"]].update(row)
            last = event
    inventory = list(modules.values())
    return {"available": last is not None, "events": accepted, "rejected_or_torn_frames": rejected,
            "progress_protocol": protocol,
            "frame_recovery_policy": ("legacy_bound_frame_recovery" if protocol == legacy_protocol
                else "continuous_committed_prefix" if protocol == current_protocol else "unidentified"),
            "first_rejection": first_rejection,
            "inventory_complete": inventory_complete,
            "last_phase": last.get("phase") if last else None,
            "last_module": last.get("module", last.get("module_record", {}).get("module")) if last else None,
            "last_frontend_phase": last.get("frontend_phase") if last else None,
            "implementation_sha256_before": last.get("implementation_sha256_before") if last else None,
            "configuration_sha256": last.get("configuration_sha256") if last else None,
            "source_snapshot_stability_after_termination": "unverified",
            "last_observed_statistics": solver_statistics,
            "last_observed_statistics_phase": solver_statistics_phase,
            "last_observed_analysis_error": last_analysis_error,
            "last_observed_frontend_lifetime": last_frontend_lifetime,
            "phase_timing_observations": phase_timing_observations,
            "timing_observations_rejected": timing_observations_rejected,
            "serialized_json_bytes": serialized_json_bytes,
            "inventory": inventory,
            "coverage_observed": {"discovered_python_files": len(inventory),
                **{name + "_modules": sum(bool(row.get(name)) for row in inventory)
                   for name in ("parsed", "lowered", "translated", "solver_constraints_scheduled")},
                "first_party_discovered": sum(row.get("ownership") == "first_party" for row in inventory),
                "dependency_source_discovered": sum(row.get("ownership") == "dependency_source" for row in inventory)},
            "call_projection_completed": False, "semantic_complete": False}
