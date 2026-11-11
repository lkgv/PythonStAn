"""Small, non-executing incident receipts and bounded independent trace tails.

Atomic replacement provides visibility, not power-loss durability. A receipt
describes last observed evidence; it is never a snapshot or a proof of liveness.
"""
import hashlib
import json
import math
import os
from pathlib import Path
import stat
import time
import uuid

from .contract import InspectorError


INCIDENT_PROTOCOL = "pythonstan_incident_v2"
TRACE_PROTOCOL = "pythonstan_runtime_trace_jsonl_v2"
MAX_RECEIPT_BYTES = 64 * 1024
MAX_TRACE_BYTES = 256 * 1024
MAX_TRACE_BATCH_BYTES = 64 * 1024
MAX_TRACE_BATCH_EVENTS = 128
CHECKPOINT_SECONDS = 1.0
CHECKPOINT_WORK_ITEMS = 1024


def json_bytes(value):
    return json.dumps(value, ensure_ascii=True, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("ascii")


def bounded_tree(value, *, max_nodes=8192, max_depth=20, max_string=4096):
    """Validate only an owned primitive tree, without invoking foreign code."""
    pending = [(value, 0)]
    nodes = 0
    while pending:
        item, depth = pending.pop()
        nodes += 1
        if nodes > max_nodes or depth > max_depth:
            raise InspectorError("incident JSON node/depth limit exceeded")
        kind = type(item)
        if kind is dict:
            for key, child in item.items():
                if type(key) is not str or len(key) > 128:
                    raise InspectorError("invalid incident JSON key")
                pending.append((child, depth + 1))
        elif kind in (list, tuple):
            pending.extend((child, depth + 1) for child in item)
        elif kind is str:
            if len(item) > max_string:
                raise InspectorError("incident string limit exceeded")
            item.encode("utf-8", errors="strict")
        elif kind is int:
            if not -(2 ** 63) <= item < 2 ** 63:
                raise InspectorError("incident integer limit exceeded")
        elif kind is float:
            if not math.isfinite(item):
                raise InspectorError("incident nonfinite number")
        elif item is not None and kind is not bool:
            raise InspectorError("incident requires primitive JSON data")


def atomic_bytes(path, payload):
    path = Path(path)
    temporary = path.with_name(path.name + ".tmp-" + uuid.uuid4().hex)
    try:
        with temporary.open("xb") as stream:
            if stream.write(payload) != len(payload):
                raise OSError("short atomic incident write")
            stream.flush()
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def read_fixed(path, limit):
    """Read a fixed regular file without following symlinks or waiting on FIFO."""
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    descriptor = os.open(path, flags)
    with os.fdopen(descriptor, "rb") as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode):
            raise InspectorError("artifact is not a regular file")
        if before.st_size > limit:
            raise InspectorError("artifact exceeds read byte limit")
        payload = stream.read(limit + 1)
        after = os.fstat(stream.fileno())
        if (len(payload) > limit or before.st_size != len(payload)
                or (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
                != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)):
            raise InspectorError("artifact changed during bounded read")
    return payload


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise InspectorError("duplicate incident JSON key")
        result[key] = value
    return result


def decode_object(payload):
    result = json.loads(payload.decode("utf-8", errors="strict"),
                        object_pairs_hook=_unique_object,
                        parse_constant=lambda _: (_ for _ in ()).throw(InspectorError("nonfinite JSON")))
    if type(result) is not dict:
        raise InspectorError("incident JSON must be an object")
    bounded_tree(result)
    return result


def read_receipt(path, run_id, request_sha256):
    payload = read_fixed(path, MAX_RECEIPT_BYTES)
    receipt = decode_object(payload)
    validate_receipt(receipt, run_id, request_sha256)
    return receipt


def validate_receipt(receipt, run_id, request_sha256):
    if (receipt.get("protocol") != INCIDENT_PROTOCOL or receipt.get("run_id") != run_id
            or receipt.get("request_sha256") != request_sha256):
        raise InspectorError("incident schema or run/request binding mismatch")
    if receipt.get("actor") not in {"worker", "supervisor"}:
        raise InspectorError("invalid incident actor")
    for field in ("identity", "process", "native", "capture", "transport", "check",
                  "phase", "counters", "observation", "artifacts"):
        if type(receipt.get(field)) is not dict:
            raise InspectorError("invalid incident structured field")
    if type(receipt["capture"].get("status")) is not str:
        raise InspectorError("invalid incident capture outcome")
    if any(type(value) is not dict for value in receipt["artifacts"].values()):
        raise InspectorError("invalid incident artifact descriptor")
    for descriptor in receipt["artifacts"].values():
        checksum = descriptor.get("sha256")
        if checksum is not None and (type(checksum) is not str or len(checksum) != 64
                or any(character not in "0123456789abcdef" for character in checksum)):
            raise InspectorError("invalid incident artifact hash")
        if "bytes" in descriptor and (type(descriptor["bytes"]) is not int or descriptor["bytes"] < 0):
            raise InspectorError("invalid incident artifact byte count")


def write_receipt(path, receipt):
    encode_started = time.perf_counter()
    bounded_tree(receipt)
    payload = json_bytes(receipt)
    if len(payload) > MAX_RECEIPT_BYTES:
        raise InspectorError("incident receipt byte limit exceeded")
    encode_seconds = time.perf_counter() - encode_started
    write_started = time.perf_counter()
    atomic_bytes(path, payload)
    write_seconds = time.perf_counter() - write_started
    return {"path": Path(path).name, "bytes": len(payload),
            "sha256": hashlib.sha256(payload).hexdigest(), "visibility": "atomic_replace_not_power_loss_durable",
            "validation_encode_seconds": encode_seconds,
            "write_replace_seconds": write_seconds}


def read_trace(path, run_id, request_sha256):
    """Recover only the strict UTF-8, LF-committed, bound continuous prefix."""
    events = []
    first_rejection = None
    stream_id = None
    previous = None
    payload = read_fixed(path, MAX_TRACE_BYTES)
    # Only literal LF is a delimiter; bare CR and Unicode separators are data.
    lines = payload.split(b"\n")
    for number, body in enumerate(lines, 1):
        if number == len(lines) and not body:
            break
        line = body + (b"\n" if number < len(lines) else b"")
        try:
            if not line.endswith(b"\n"):
                raise InspectorError("unterminated_frame")
            event = decode_object(line)
            if (event.get("trace_protocol") != TRACE_PROTOCOL
                    or event.get("run_id") != run_id or event.get("request_sha256") != request_sha256):
                raise InspectorError("unbound_trace_frame")
            sequence = event.get("sequence")
            if type(sequence) is not int or sequence < 1:
                raise InspectorError("invalid_trace_sequence")
            if sequence != (previous + 1 if previous is not None else 1):
                raise InspectorError("noncontinuous_trace_prefix")
            if type(event.get("stream_id")) is not str or (stream_id is not None and event["stream_id"] != stream_id):
                raise InspectorError("changed_trace_stream")
            stream_id = event["stream_id"]
            previous = sequence
            events.append(event)
        except (InspectorError, ValueError, UnicodeError, RecursionError) as exc:
            first_rejection = {"line": number, "reason": type(exc).__name__}
            break
    return {"protocol": TRACE_PROTOCOL, "events": events, "stream_id": stream_id,
            "first_sequence": events[0]["sequence"] if events else None,
            "last_sequence": previous, "first_rejection": first_rejection,
            "recovery_policy": "continuous_last_complete_record_prefix",
            "history_claim": "observed_only_no_replay_or_snapshot_identity"}


class IncidentRecorder:
    """One analysis thread owns phase/counter receipt state and cold writes."""

    def __init__(self, directory, run_id, request_sha256, adapter_sha256,
                 configuration, mode):
        self.path = Path(directory) / "incident.json"
        self.started = time.monotonic()
        self.last_checkpoint = -CHECKPOINT_SECONDS
        self.last_attempt = -CHECKPOINT_SECONDS
        self.last_checkpoint_work = 0
        self.write_failures = 0
        self.persistence = {"version": 1,
            "scope": "completed operations before this receipt encoding; current attempt start is explicit",
            "clock": "worker_perf_counter_durations_monotonic_elapsed",
            "attempts_started": 0, "attempts_completed": 0, "successful_writes": 0,
            "forced_attempts": 0, "routine_attempts": 0,
            "failed_attempts": 0, "cadence_skips": 0, "successful_bytes": 0,
            "validation_encode_seconds": 0.0, "write_replace_seconds": 0.0,
            "failed_attempt_seconds": 0.0,
            "last_attempt_worker_elapsed_seconds": None,
            "last_successful_write_worker_elapsed_seconds": None}
        self.data = {"protocol": INCIDENT_PROTOCOL, "actor": "worker",
            "run_id": run_id, "request_sha256": request_sha256,
            "identity": {"adapter_sha256": adapter_sha256, "adapter_verification": "start_only",
                "implementation_sha256": None, "implementation_verification": "unverified",
                "configuration_sha256": None, "configuration_verification": "unverified",
                "source_verification": "unverified", "source_binding": "not_yet_observed"},
            "process": {"status": "running", "completion": "not_observed"},
            "native": {"status": "not_observed", "stop_reason": None},
            "capture": {"status": "not_requested" if mode in ("off", "counters") else "not_observed"},
            "transport": {"status": "not_observed"}, "check": {"status": "not_run"},
            "phase": {"last_entered": "worker_bootstrap", "last_completed": None,
                "action": "bounded bootstrap and native import", "last_progress_sequence": None},
            "counters": {"status": "unavailable", "reason": "solver_not_yet_constructed"},
            "observation": {"mode": mode, "requested_bootstrap_policy": configuration.get("builtin_bootstrap_policy", "queued"),
                "actual_bootstrap_behavior": "not_observed", "heartbeat": "cooperative_cold_boundaries",
                "heartbeat_preemption": False, "counter_sampling": "not_yet_observed"},
            "health": {"receipt_write_failures": 0, "trace_write_failures": 0},
            "artifacts": {"snapshot": {"status": "missing", "reason": "not_captured"},
                "native_report": {"status": "missing", "reason": "not_exported"},
                "runtime_trace": {"status": "missing", "reason": "not_recorded"}},
            "evidence_kind": "diagnostic_last_observation_not_semantic_snapshot"}
        self.data["receipt_persistence"] = self.persistence

    def phase(self, event):
        phase = event.get("phase")
        if type(phase) is not str:
            return
        current = self.data["phase"]
        current["last_progress_sequence"] = event.get("sequence")
        if phase.endswith(("_completed", "_ready", "_finished")):
            current["last_completed"] = phase
            current["action"] = None
        else:
            current["last_entered"] = phase
            current["action"] = phase[:256]
        for key in ("module", "frontend_phase"):
            if type(event.get(key)) is str:
                current[key] = event[key][:512]
        identity = self.data["identity"]
        for source, destination in (("implementation_sha256_before", "implementation_sha256"),
                                    ("configuration_sha256", "configuration_sha256")):
            if type(event.get(source)) is str and len(event[source]) == 64:
                identity[destination] = event[source]
                identity["configuration_verification" if destination.startswith("configuration")
                         else "implementation_verification"] = "start_only"
        row = event.get("module_record")
        if type(row) is dict and "consumed_sha256" in row:
            identity["source_binding"] = "consumed_hashes_observed_not_finally_verified"

    def counters(self, counters):
        bounded_tree(counters, max_nodes=2048, max_depth=12)
        self.data["counters"] = counters

    def checkpoint_due(self):
        """Routine cadence uses attempts, including failed I/O, not successes."""
        return time.monotonic() - self.last_attempt >= CHECKPOINT_SECONDS

    def checkpoint(self, *, force=False):
        now = time.monotonic()
        if not force and now - self.last_attempt < CHECKPOINT_SECONDS:
            self.persistence["cadence_skips"] += 1
            return False
        self.last_attempt = now
        self.persistence["attempts_started"] += 1
        self.persistence["forced_attempts" if force else "routine_attempts"] += 1
        self.persistence["last_attempt_worker_elapsed_seconds"] = now - self.started
        self.data["worker_elapsed_seconds"] = now - self.started
        self.data["health"]["receipt_write_failures"] = self.write_failures
        started = time.perf_counter()
        try:
            descriptor = write_receipt(self.path, self.data)
        except (OSError, InspectorError, UnicodeError, ValueError):
            self.write_failures += 1
            self.persistence["attempts_completed"] += 1
            self.persistence["failed_attempts"] += 1
            self.persistence["failed_attempt_seconds"] += time.perf_counter() - started
            return False
        self.persistence["attempts_completed"] += 1
        self.persistence["successful_writes"] += 1
        self.persistence["successful_bytes"] += descriptor["bytes"]
        self.persistence["validation_encode_seconds"] += descriptor["validation_encode_seconds"]
        self.persistence["write_replace_seconds"] += descriptor["write_replace_seconds"]
        self.last_checkpoint = time.monotonic()
        self.persistence["last_successful_write_worker_elapsed_seconds"] = self.last_checkpoint - self.started
        return True
