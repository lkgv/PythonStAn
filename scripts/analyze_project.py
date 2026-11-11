#!/usr/bin/env python3
"""Analyze complete declared source trees in an isolated production solver process."""
import argparse
import hashlib
from io import StringIO
import json
import math
import os
from pathlib import Path
import signal
import stat
import subprocess
import sys
import tempfile
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
WORKER_CLEANUP_GRACE_SECONDS = 1.0
PROJECT_JSON_MARKER = "PROJECT_JSON:"
WORKER_JSON_CHUNK_CHARACTERS = 65536
WORKER_TRANSPORT_PROTOCOL = "project_json_line_stream_v2"
WORKER_TERMINAL_CLOCK_VERSION = 2


def _write_project_json(result, stream, *, chunk_chars=WORKER_JSON_CHUNK_CHARACTERS):
    """Emit one default-JSON receipt without retaining its whole encoded body.

    Writes and the coalescing buffer are bounded. Stock iterencode can still
    allocate one whole escaped string leaf; this is not a total encoder/RSS
    bound. Counters/hash exclude the marker and final newline. Return only
    after the newline and stdout flush succeed.
    """
    if type(chunk_chars) is not int or not 0 < chunk_chars <= WORKER_JSON_CHUNK_CHARACTERS:
        raise ValueError("chunk_chars must be an integer in [1, 65536]")
    payload_bytes = 0
    digest = hashlib.sha256()
    max_write = max_fragment = 0

    def write_all(text):
        nonlocal max_write
        for offset in range(0, len(text), chunk_chars):
            piece = text[offset:offset + chunk_chars]
            while piece:
                max_write = max(max_write, len(piece))
                written = stream.write(piece)
                if type(written) is not int or not 0 < written <= len(piece):
                    raise OSError("worker JSON stream returned an invalid write count")
                piece = piece[written:]

    def write_payload(text):
        nonlocal payload_bytes
        write_all(text)
        # ensure_ascii=True is the stock default, so characters equal bytes.
        digest.update(text.encode("ascii"))
        payload_bytes += len(text)

    write_all(PROJECT_JSON_MARKER)
    pending = []
    pending_chars = 0
    for fragment in json.JSONEncoder().iterencode(result):
        max_fragment = max(max_fragment, len(fragment))
        for offset in range(0, len(fragment), chunk_chars):
            piece = fragment[offset:offset + chunk_chars]
            if pending_chars + len(piece) > chunk_chars:
                write_payload("".join(pending))
                pending.clear()
                pending_chars = 0
            pending.append(piece)
            pending_chars += len(piece)
    if pending:
        write_payload("".join(pending))
    write_all("\n")
    stream.flush()
    return {"payload_bytes": payload_bytes, "payload_sha256": digest.hexdigest(),
            "max_write_characters": max_write, "max_encoder_fragment_characters": max_fragment}


def _bounded_worker_supported():
    # Windows communicate uses reader threads; closing a pipe from another
    # thread is not a verified bounded operation. Refuse before creating it.
    return os.name == "posix"


def _close_worker_pipes(process):
    for name in ("stdin", "stdout", "stderr"):
        stream = getattr(process, name, None)
        if stream is not None:
            try:
                stream.close()
            except OSError:
                pass


def _terminate_worker(process, *, process_group):
    """Kill our worker group, then spend at most one grace period draining it.

    This is an owned POSIX process group, not a general process-forest sandbox:
    a descendant that creates a new session can escape it. No unbounded Popen
    context-manager wait is used, including when a descendant retains a pipe.
    """
    started = time.monotonic()
    deadline = started + WORKER_CLEANUP_GRACE_SECONDS
    errors = []
    try:
        if process_group:
            os.killpg(process.pid, signal.SIGKILL)
        else:
            process.kill()
    except ProcessLookupError:
        pass  # The owned group/child may have exited between observation and kill.
    except OSError as exc:
        errors.append(type(exc).__name__ + ": " + str(exc))
        try:
            process.kill()
        except ProcessLookupError:
            pass
        except OSError as fallback:
            errors.append(type(fallback).__name__ + ": " + str(fallback))
    pipes_drained = False
    try:
        process.communicate(timeout=max(0.001, deadline - time.monotonic()))
        pipes_drained = True
    except (subprocess.TimeoutExpired, OSError, ValueError) as exc:
        errors.append(type(exc).__name__ + ": " + str(exc))
    finally:
        _close_worker_pipes(process)
    return {"scope": "owned_posix_process_group" if process_group else "direct_child_only",
            "process_group_supported": process_group,
            "escaping_descendants_covered": False,
            "cleanup_grace_seconds": WORKER_CLEANUP_GRACE_SECONDS,
            "cleanup_wall_seconds": time.monotonic() - started,
            "child_reaped": process.poll() is not None,
            "child_returncode": process.returncode,
            "pipes_drained": pipes_drained, "pipes_closed": True,
            "errors": errors}


def _request_digest(configuration):
    return hashlib.sha256(json.dumps(configuration, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


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

def run_isolated(configuration, *, timeout=120, memory_mb=1536):
    if type(timeout) not in (int, float) or not 0 < timeout <= 3600:
        raise ValueError("timeout must be in (0, 3600]")
    if type(memory_mb) is not int or memory_mb < 128:
        raise ValueError("memory_mb must be an integer >=128")
    started = time.monotonic()
    run_id = uuid.uuid4().hex
    request_digest = _request_digest(configuration)
    process_group = _bounded_worker_supported()
    limits = {"timeout_seconds": timeout, "memory_mb": memory_mb,
              "cleanup_grace_seconds": WORKER_CLEANUP_GRACE_SECONDS,
              "process_group_supported": process_group,
              "bounded_pipe_cleanup_supported": process_group,
              "platform_support": "supported_posix" if process_group else "unsupported_platform",
              "worker_exit_policy": "disposable_after_owned_output_flush",
              "worker_transport_protocol": WORKER_TRANSPORT_PROTOCOL,
              "worker_terminal_clock_version": WORKER_TERMINAL_CLOCK_VERSION,
              "worker_json_chunk_characters": WORKER_JSON_CHUNK_CHARACTERS,
              "worker_json_ready_meaning": "encoding_and_stdout_flush_completed",
              "timeout_scope": "worker communication deadline; bounded cleanup is additional"}
    if not process_group:
        return {"status": "worker_error", "solver_completed": False, "analysis_executed": False,
                "first_party_translation_complete": False, "call_edges": [],
                "whole_program_soundness_proved": False, "semantic_complete": False,
                "obligations_retained": True, "returncode": None, "resource_limits": limits,
                "diagnostic": "unsupported_platform: bounded worker lifecycle requires POSIX process groups"}
    with tempfile.TemporaryDirectory(prefix="pythonstan-project-progress-") as temporary:
        checkpoint = str(Path(temporary) / "events.jsonl")
        process = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "--worker"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            cwd=ROOT, start_new_session=process_group)
        try:
            stdout, stderr = process.communicate(
                input=json.dumps({"configuration": configuration, "memory_mb": memory_mb,
                                  "progress_path": checkpoint, "run_id": run_id,
                                  "request_sha256": request_digest}).encode("utf-8"),
                timeout=timeout)
            # Keep CR/LF and malformed UTF-8 observable, without changing the
            # text interface used by diagnostics and receipt parsing below.
            completed = subprocess.CompletedProcess(process.args, process.returncode,
                stdout.decode("utf-8", errors="surrogateescape"),
                stderr.decode("utf-8", errors="surrogateescape"))
        except subprocess.TimeoutExpired:
            termination = _terminate_worker(process, process_group=process_group)
            observed = _read_progress(checkpoint, run_id, request_digest)
            return {"status": "timeout", "solver_completed": False, "analysis_executed": None,
                    "first_party_translation_complete": False, "call_edges": [],
                    "whole_program_soundness_proved": False, "semantic_complete": False,
                    "obligations_retained": True, "wall_seconds": time.monotonic() - started,
                    "resource_limits": {**limits, "worker_termination": termination},
                    "partial_progress": observed}
        except BaseException:
            _terminate_worker(process, process_group=process_group)
            raise
        finally:
            _close_worker_pipes(process)
        observed = _read_progress(checkpoint, run_id, request_digest)
    records = [line for line in StringIO(completed.stdout, newline="\n")
               if line.startswith(PROJECT_JSON_MARKER)]
    def worker_error(diagnostic=None):
        tail = completed.stderr[-3000:] + completed.stdout[-1000:]
        return {"status": "worker_error", "solver_completed": False, "analysis_executed": None,
                "first_party_translation_complete": False, "call_edges": [],
                "whole_program_soundness_proved": False, "semantic_complete": False,
                "obligations_retained": True, "returncode": completed.returncode,
                "resource_limits": limits,
                "diagnostic": ((diagnostic + "\n") if diagnostic else "") + tail,
                "partial_progress": observed}
    # Never fall back to an earlier complete record if the last marker is torn.
    if not records or not records[-1].endswith("\n") or completed.returncode:
        return worker_error()
    try:
        records[-1].encode("utf-8")  # Reject invalid wire bytes, not replacement text.
        result = json.loads(records[-1][len(PROJECT_JSON_MARKER):-1])
        if type(result) is not dict:
            raise ValueError("worker JSON report must be an object")
    except (ValueError, TypeError) as exc:
        return worker_error("Invalid worker report: " + type(exc).__name__ + ": " + str(exc))
    result["supervisor_wall_seconds"] = time.monotonic() - started
    result["resource_limits"] = {**limits,
                                 "address_space_limit_enforced": result.pop("_memory_limit_enforced", False)}
    result["execution_progress"] = {key: value for key, value in observed.items() if key != "inventory"}
    return result


def worker():
    worker_started = time.monotonic()
    payload = json.load(sys.stdin)
    last_checkpoint = None
    def checkpoint(event):
        nonlocal last_checkpoint
        record = {**event, "progress_protocol": "project_progress_jsonl_v2",
                  "run_id": payload["run_id"], "request_sha256": payload["request_sha256"],
                  "worker_elapsed_seconds": time.monotonic() - worker_started}
        with open(payload["progress_path"], "a") as stream:
            stream.write(json.dumps(record) + "\n")
            stream.flush()
        last_checkpoint = record

    def terminal_checkpoint(phase, **details):
        if last_checkpoint is None:
            return
        frame = {key: last_checkpoint[key] for key in
                 ("implementation_sha256_before", "configuration_sha256")}
        checkpoint({**frame, "sequence": last_checkpoint["sequence"] + 1,
                    "phase": phase,
                    "worker_transport_protocol": WORKER_TRANSPORT_PROTOCOL,
                    "worker_terminal_clock_version": WORKER_TERMINAL_CLOCK_VERSION,
                    **details})
    enforced = False
    try:
        import resource
        bound = payload["memory_mb"] * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (bound, bound))
        enforced = True
    except ImportError:
        pass
    try:
        from pythonstan.analysis.project import ProjectConfig, analyze_project
        result = analyze_project(ProjectConfig.from_dict(payload["configuration"]), progress=checkpoint)
    except Exception as exc:
        result = {"status": "analysis_error", "diagnostic": type(exc).__name__ + ": " + str(exc),
                  "solver_completed": False, "first_party_translation_complete": False,
                  "call_edges": [], "analysis_executed": False,
                  "semantic_complete": False, "whole_program_soundness_proved": False,
                  "obligations_retained": True}
    result["_memory_limit_enforced"] = enforced
    from scripts.process_memory import worker_memory
    result.update(worker_memory())
    terminal_checkpoint("worker_serialization_started")
    try:
        emitted = _write_project_json(result, sys.stdout)
    except MemoryError:
        try:
            terminal_checkpoint("worker_serialization_resource_exhausted",
                                analysis_error="MemoryError during streaming worker JSON encoding or output",
                                failure_phase="worker_serialization_started",
                                worker_output_may_be_partial=True)
        except MemoryError:
            pass
        raise
    # Under terminal clock v2 this includes pipe writes and stdout flush;
    # unlike v1, this is not a pre-output encode-only timing boundary.
    terminal_checkpoint("worker_json_ready", worker_json_bytes=emitted["payload_bytes"],
                        worker_json_sha256=emitted["payload_sha256"],
                        worker_json_max_write_characters=emitted["max_write_characters"],
                        worker_json_max_encoder_fragment_characters=emitted["max_encoder_fragment_characters"],
                        worker_json_ready_meaning="encoding_and_stdout_flush_completed")
    sys.stderr.flush()
    terminal_checkpoint("worker_output_completed")
    # This private, one-shot process owns the analysis arena. All report and
    # progress writes above must succeed before bypassing interpreter teardown.
    # Exit here: returning first can release large worker-local graphs. Never
    # put this in finally, which would hide serialization or flush failures.
    os._exit(0)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--timeout", type=float, default=120)
    parser.add_argument("--memory-mb", type=int, default=1536)
    args = parser.parse_args()
    if args.worker:
        worker()
        return 0
    if args.config is None:
        parser.error("--config is required")
    try:
        config = json.loads(args.config.read_text())
        result = run_isolated(config, timeout=args.timeout, memory_mb=args.memory_mb)
    except (ValueError, OSError) as exc:
        parser.error(str(exc))
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, indent=2) + "\n")
        print(json.dumps({k: result.get(k) for k in
                          ("status", "solver_completed", "coverage", "entry_points", "semantic_complete")}, indent=2))
    else:
        print(json.dumps(result, indent=2))
    observational_complete = result.get("status") in {"inventory_completed", "frontend_completed"}
    return 0 if observational_complete or (result.get("solver_completed") and result.get("first_party_translation_complete")) else 2


if __name__ == "__main__":
    raise SystemExit(main())
