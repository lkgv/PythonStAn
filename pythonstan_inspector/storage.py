"""Bounded, non-executing JSON bundle storage, independent of the analyzer.

The manifest is the commit record.  Snapshot integrity is mandatory; optional
trace damage preserves only an explicitly qualified complete record prefix.
Atomic installation promises file visibility, not power-loss durability.
"""
from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import stat
from typing import Any, Optional
import uuid

from .contract import InspectorError, REFERENCE_STATES, SCHEMA_MAJOR, SealedSnapshot, canonical_bytes


@dataclass(frozen=True)
class ReadLimits:
    max_snapshot_bytes: int = 64_000_000
    max_trace_bytes: int = 64_000_000
    max_manifest_bytes: int = 1_000_000
    max_depth: int = 64
    max_nodes: int = 2_000_000
    max_entities: int = 200_000
    max_rows: int = 1_000_000
    max_events: int = 100_000
    max_string_bytes: int = 1_000_000

    def __post_init__(self):
        for name in self.__dataclass_fields__:
            value = getattr(self, name)
            if type(value) is not int or value < 1:
                raise InspectorError("read limits must be positive integers")


@dataclass(frozen=True)
class Bundle:
    snapshot: SealedSnapshot
    _manifest: bytes
    _trace: bytes = b"[]"
    _trace_health: bytes = b'{"status":"not_recorded"}'

    @property
    def data(self):
        return self.snapshot.data

    @property
    def manifest(self):
        return json.loads(self._manifest)

    @property
    def trace(self):
        return json.loads(self._trace)

    @property
    def trace_health(self):
        return json.loads(self._trace_health)


def _fail(message):
    raise InspectorError(message)


def _integer(value, label, minimum=0):
    if type(value) is not int or not minimum <= value <= 2**63 - 1:
        _fail(label + " must be a bounded integer")


def _string(value, label, nonempty=False):
    if type(value) is not str or (nonempty and not value):
        _fail(label + " must be a string")


def _mapping(value, label):
    if type(value) is not dict:
        _fail(label + " must be an object")
    return value


def _array(value, label):
    if type(value) is not list:
        _fail(label + " must be an array")
    return value


def _reject_constant(value):
    _fail("nonfinite JSON number: " + value)


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            _fail("duplicate JSON key: " + key[:80])
        result[key] = value
    return result


def _tree_limits(value, limits):
    """Walk only decoded JSON, never a live engine or arbitrary object graph."""
    stack = [(value, 0)]
    count = 0
    while stack:
        item, depth = stack.pop()
        count += 1
        if count > limits.max_nodes or depth > limits.max_depth:
            _fail("JSON node/depth limit exceeded")
        if item is None or type(item) is bool:
            continue
        if type(item) is str:
            if len(item.encode("utf-8")) > limits.max_string_bytes:
                _fail("JSON string limit exceeded")
        elif type(item) is int:
            if not -(2**63) <= item <= 2**63 - 1:
                _fail("JSON integer outside supported range")
        elif type(item) is float:
            if not math.isfinite(item):
                _fail("nonfinite JSON number")
        elif type(item) is list:
            stack.extend((child, depth + 1) for child in item)
        elif type(item) is dict:
            for key, child in item.items():
                _string(key, "JSON key")
                stack.append((key, depth + 1))
                stack.append((child, depth + 1))
        else:
            _fail("non-JSON value in artifact")


def _decode(encoded, limits, label):
    try:
        result = json.loads(encoded, object_pairs_hook=_unique_object,
                            parse_constant=_reject_constant)
        _tree_limits(result, limits)
        return result
    except InspectorError:
        raise
    except (ValueError, UnicodeError, RecursionError, OverflowError) as error:
        raise InspectorError("invalid " + label + " JSON") from error


def _schema(value, label):
    descriptor = _mapping(value, label)
    _integer(descriptor.get("major"), label + ".major")
    _integer(descriptor.get("minor"), label + ".minor")
    if descriptor["major"] != SCHEMA_MAJOR:
        _fail("unsupported " + label + " major version")


def _scalar_mapping(value, label):
    for key, item in _mapping(value, label).items():
        if type(item) in (dict, list):
            _fail(label + "." + key + " must be a JSON scalar")


def _validate_snapshot(data, limits):
    root = _mapping(data, "snapshot artifact")
    _schema(root.get("schema"), "snapshot schema")
    _mapping(root.get("run"), "run")
    snap = _mapping(root.get("snapshot"), "snapshot")
    for key in ("run_id", "snapshot_id", "scope"):
        _string(snap.get(key), "snapshot." + key, True)
    boundary = _mapping(snap.get("boundary"), "snapshot.boundary")
    if type(boundary.get("coherent")) is not bool:
        _fail("boundary.coherent must be boolean")
    _string(boundary.get("stage"), "boundary.stage", True)
    entities = _array(snap.get("entities"), "entities")
    if len(entities) > limits.max_entities:
        _fail("entity limit exceeded")
    ids = set()
    for entity in entities:
        _mapping(entity, "entity")
        _integer(entity.get("id"), "entity.id")
        if entity["id"] in ids:
            _fail("duplicate snapshot-local entity ID")
        ids.add(entity["id"])
        _string(entity.get("kind"), "entity.kind", True)
        _string(entity.get("label"), "entity.label")
        _scalar_mapping(entity.get("origin", {}), "entity.origin")
        _scalar_mapping(entity.get("attributes", {}), "entity.attributes")
        _mapping(entity.get("links", {}), "entity.links")
    for entity in entities:
        for link in entity.get("links", {}).values():
            _mapping(link, "entity link")
            if link.get("status") not in REFERENCE_STATES:
                _fail("unknown entity reference state")
            ref = link.get("ref")
            if ref is not None:
                _integer(ref, "entity link ref")
            if link["status"] == "resolved":
                if ref not in ids:
                    _fail("resolved entity reference is absent")
            else:
                _string(link.get("reason"), "unresolved link reason", True)
    views = _mapping(snap.get("views"), "snapshot.views")
    total_rows = 0
    for name, view in views.items():
        _mapping(view, "view " + name)
        # Unknown optional views are retained but never interpreted here.
        if name not in ("node_points", "ctx_points", "pfg_edges", "call_edges"):
            if "version" in view:
                _integer(view["version"], "view.version", 1)
            continue
        _integer(view.get("version"), "view.version", 1)
        # Unknown semantics must remain inspectable but are not usable as v1.
        if view["version"] != 1:
            continue
        _string(view.get("fidelity"), "view.fidelity", True)
        for field in ("dimensions", "operations"):
            for item in _array(view.get(field), "view." + field):
                _string(item, "view." + field + " element", True)
        coverage = _mapping(view.get("coverage"), "view.coverage")
        _string(coverage.get("registry"), "coverage.registry", True)
        for owner, cov in _mapping(coverage.get("owners"), "coverage.owners").items():
            if not owner.isascii() or not owner.isdigit() or str(int(owner)) != owner:
                _fail("coverage owner must be a canonical local ID")
            _integer(int(owner), "coverage owner")
            if int(owner) not in ids:
                _fail("coverage references absent owner")
            _mapping(cov, "owner coverage")
            for state in _mapping(cov.get("fields"), "coverage.fields").values():
                _string(state, "coverage field state", True)
        rows = _array(view.get("rows"), "view.rows")
        total_rows += len(rows)
        if total_rows > limits.max_rows:
            _fail("relation row limit exceeded")
        for row in rows:
            _mapping(row, "view row")
            endpoints = (("source", "target") if name == "pfg_edges" else
                         ("callsite", "callee") if name == "call_edges" else ("owner", "object"))
            for field in endpoints:
                _integer(row.get(field), "row." + field)
                if row[field] not in ids:
                    _fail("relation endpoint is absent")
            if name == "pfg_edges":
                _string(row.get("edge_kind"), "row.edge_kind", True)
                if type(row.get("identity_copy")) is not bool:
                    _fail("row.identity_copy must be boolean")
            elif name == "call_edges":
                _string(row.get("call_kind"), "row.call_kind", True)
            else:
                _string(row.get("lane"), "row.lane", True)
    if "stats" in snap:
        _mapping(snap["stats"], "snapshot.stats")


def _directory(path, create=False):
    """Open each path component without following symlinks, including parents."""
    absolute = Path(os.path.abspath(os.fspath(path)))
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    fd = os.open(absolute.anchor, flags)
    try:
        for part in absolute.parts[1:]:
            if create:
                try:
                    os.mkdir(part, mode=0o700, dir_fd=fd)
                except FileExistsError:
                    pass
            next_fd = os.open(part, flags, dir_fd=fd)
            os.close(fd)
            fd = next_fd
        return absolute, fd
    except OSError as error:
        os.close(fd)
        raise InspectorError("bundle directory is unavailable or contains a symlink") from error


def _filename(value):
    _string(value, "artifact filename", True)
    if value in (".", "..") or "/" in value or "\\" in value or "\x00" in value:
        _fail("artifact paths must be plain basenames")
    return value


def _read(directory_fd, name, limit):
    name = _filename(name)
    try:
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                     dir_fd=directory_fd)
        with os.fdopen(fd, "rb") as handle:
            size = os.fstat(handle.fileno())
            if not stat.S_ISREG(size.st_mode) or size.st_size > limit:
                _fail("artifact is not a bounded regular file")
            data = handle.read(limit + 1)
            if len(data) > limit:
                _fail("artifact byte limit exceeded")
            return data
    except OSError as error:
        raise InspectorError("artifact file is unavailable or unsafe: " + name) from error


def _descriptor(value, label):
    _mapping(value, label)
    _filename(value.get("file"))
    _integer(value.get("bytes"), label + ".bytes")
    checksum = value.get("sha256")
    if type(checksum) is not str or len(checksum) != 64 or any(c not in "0123456789abcdef" for c in checksum):
        _fail(label + ".sha256 must be lowercase SHA-256")


def _trace_prefix(encoded, limits):
    events = []
    health = {"status": "complete", "complete_records": 0,
              "last_complete_record": None, "integrity": "matched"}
    parts = encoded.split(b"\n")
    lines = [part + b"\n" for part in parts[:-1]]
    if parts[-1]:
        lines.append(parts[-1])
    for index, line in enumerate(lines):
        if len(events) >= limits.max_events:
            health.update(status="reader_truncated", first_unread_record=index + 1)
            break
        try:
            event = _decode(line, limits, "trace record")
            _mapping(event, "trace record")
        except InspectorError as error:
            tail = index == len(lines) - 1
            status = ("truncated_tail" if not line.endswith(b"\n") else "corrupt_tail") if tail else "corrupt_middle"
            health.update(status=status,
                          first_invalid_record=index + 1, diagnostic=str(error))
            break
        events.append(event)
    health["complete_records"] = len(events)
    health["last_complete_record"] = len(events) if events else None
    return events, health


def load_bundle(path, limits: Optional[ReadLimits] = None):
    limits = limits or ReadLimits()
    if not isinstance(limits, ReadLimits):
        _fail("limits must be ReadLimits")
    _, fd = _directory(path)
    try:
        manifest_encoded = _read(fd, "manifest.json", limits.max_manifest_bytes)
        manifest = _mapping(_decode(manifest_encoded, limits, "manifest"), "manifest")
        _schema(manifest.get("schema"), "manifest schema")
        descriptor = manifest.get("snapshot")
        _descriptor(descriptor, "snapshot descriptor")
        encoded = _read(fd, descriptor["file"], limits.max_snapshot_bytes)
        if len(encoded) != descriptor["bytes"] or hashlib.sha256(encoded).hexdigest() != descriptor["sha256"]:
            _fail("snapshot digest/size mismatch")
        data = _decode(encoded, limits, "snapshot")
        _validate_snapshot(data, limits)
        events = []
        health = {"status": "not_recorded"}
        if manifest.get("trace") is not None:
            trace_descriptor = manifest["trace"]
            _descriptor(trace_descriptor, "trace descriptor")
            try:
                trace_encoded = _read(fd, trace_descriptor["file"], limits.max_trace_bytes)
            except InspectorError as error:
                health = {"status": "unavailable", "diagnostic": str(error)}
            else:
                events, health = _trace_prefix(trace_encoded, limits)
                if len(trace_encoded) != trace_descriptor["bytes"] or hashlib.sha256(trace_encoded).hexdigest() != trace_descriptor["sha256"]:
                    health["integrity"] = "mismatch"
                    health["integrity_diagnostic"] = "trace digest/size does not match sealed manifest"
        return Bundle(SealedSnapshot(encoded), canonical_bytes(manifest),
                      canonical_bytes(events), canonical_bytes(health))
    finally:
        os.close(fd)


def _install(fd, filename, encoded):
    temporary = ".inspector-" + uuid.uuid4().hex + ".tmp"
    raw_fd = None
    try:
        raw_fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                         0o600, dir_fd=fd)
        with os.fdopen(raw_fd, "wb") as handle:
            raw_fd = None
            handle.write(encoded)
        os.replace(temporary, filename, src_dir_fd=fd, dst_dir_fd=fd)
    finally:
        if raw_fd is not None:
            os.close(raw_fd)
        try:
            os.unlink(temporary, dir_fd=fd)
        except FileNotFoundError:
            pass


def write_bundle(snapshot, path, trace=None):
    """Seal validated owned bytes, then atomically install the manifest last.

    Content-addressed filenames preserve the old committed bundle if a later
    installation fails. Orphan files are harmless and never read implicitly.
    """
    if not isinstance(snapshot, SealedSnapshot):
        _fail("write_bundle requires SealedSnapshot")
    limits = ReadLimits()
    if len(snapshot.encoded) > limits.max_snapshot_bytes:
        _fail("snapshot byte limit exceeded")
    _validate_snapshot(_decode(snapshot.encoded, limits, "snapshot"), limits)
    snapshot_name = "snapshot-" + snapshot.sha256 + ".json"
    manifest = {"schema": {"major": SCHEMA_MAJOR, "minor": 0},
                "snapshot": {"file": snapshot_name, "bytes": len(snapshot.encoded),
                             "sha256": snapshot.sha256}}
    trace_encoded = None
    if trace is not None:
        # This is an explicit writer API, not an object-graph serializer.
        if type(trace) not in (list, tuple) or len(trace) > limits.max_events:
            _fail("trace must be a bounded list/tuple of JSON event objects")
        records = []
        size = 0
        for event in trace:
            _mapping(event, "trace event")
            _tree_limits(event, limits)
            record = canonical_bytes(event) + b"\n"
            size += len(record)
            if size > limits.max_trace_bytes:
                _fail("trace byte limit exceeded")
            records.append(record)
        trace_encoded = b"".join(records)
        checksum = hashlib.sha256(trace_encoded).hexdigest()
        manifest["trace"] = {"file": "trace-" + checksum + ".jsonl",
                             "bytes": len(trace_encoded), "sha256": checksum}
    absolute, fd = _directory(path, create=True)
    try:
        _install(fd, snapshot_name, snapshot.encoded)
        if trace_encoded is not None:
            _install(fd, manifest["trace"]["file"], trace_encoded)
        _install(fd, "manifest.json", canonical_bytes(manifest))
    except OSError as error:
        raise InspectorError("bundle installation failed") from error
    finally:
        os.close(fd)
    return absolute
