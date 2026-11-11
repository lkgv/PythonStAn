"""Bounded, detached observation records; never part of solver semantics.

Events describe observed execution and explicit dependency notifications. Their
order is not a proof of target-program event order or semantic completeness.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from hashlib import sha256
import json
import math
import threading
import time

from .identity import SourceModule
from .wire import freeze_snapshot, thaw_snapshot

OBSERVATION_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class SourceRef:
    point: str
    module: str = ""
    source_digest: str = ""
    filename: str = ""
    line: int | None = None
    column: int | None = None
    operation: str = ""
    excerpt: str = ""
    excerpt_truncated: bool = False

    def to_dict(self):
        return dict(self.__dict__)


@dataclass(frozen=True)
class CausalEvent:
    event_id: str
    sequence: int
    kind: str
    source: SourceRef | None
    unit: str | None
    revision: int | str | None
    parents: tuple[str, ...]
    payload: object
    payload_truncated: bool = False
    schema_version: int = OBSERVATION_SCHEMA_VERSION

    def __post_init__(self):
        object.__setattr__(self, "payload", freeze_snapshot(self.payload))
        object.__setattr__(self, "parents", tuple(self.parents))

    def to_dict(self):
        return {"schema_version": self.schema_version, "event_id": self.event_id,
                "sequence": self.sequence, "kind": self.kind,
                "source": self.source.to_dict() if self.source else None,
                "point": self.source.point if self.source else None,
                "unit": self.unit, "revision": self.revision,
                "parents": list(self.parents), "payload": thaw_snapshot(self.payload),
                "payload_truncated": self.payload_truncated}


class ObservationSink:
    """A passive per-run bounded recorder with immutable retained records.

    Capture failures and limits affect observation status only. The producer
    remains responsible for continuing all semantic work and obligations.
    """

    def __init__(self, *, run_id="", source_digest="", model_digest="",
                 policy_digest="", profile="", sources=(), max_events=10000,
                 max_bytes=4 * 1024 * 1024, max_event_bytes=32768,
                 max_payload_nodes=1024, max_depth=12, max_string=2048):
        limits = {"max_events": max_events, "max_bytes": max_bytes,
                  "max_event_bytes": max_event_bytes,
                  "max_payload_nodes": max_payload_nodes, "max_depth": max_depth,
                  "max_string": max_string}
        if any(type(v) is not int or v < 0 for v in limits.values()):
            raise ValueError("Observation limits must be nonnegative integers")
        self.identity = freeze_snapshot({"run_id": run_id, "source_digest": source_digest,
                                         "model_digest": model_digest,
                                         "policy_digest": policy_digest, "profile": profile})
        self.limits = freeze_snapshot(limits)
        token = run_id or sha256(json.dumps(thaw_snapshot(self.identity), sort_keys=True).encode()).hexdigest()[:24]
        self._prefix = str(token)
        iterable = sources.values() if isinstance(sources, Mapping) else sources
        self._sources = {}
        for source in iterable:
            if isinstance(source, SourceModule):
                self._sources[source.module] = (source.digest, source.filename, source.source)
            elif isinstance(source, Mapping):
                module = source.get("module")
                if type(module) is str:
                    self._sources[module] = (source.get("digest", ""), source.get("filename", ""),
                                             str(source.get("source", "")))
        self._line_offsets = {}
        self._records = []
        self._by_id = {}
        self._last_unit = {}
        self._summaries = {}
        self._dependencies = {}
        self._unit_revision = {}
        self._active_unit = None
        self._sequence = 0
        self._dropped = 0
        self._errors = 0
        self._bytes = 0
        self._payload_truncations = 0
        self._capture_seconds = 0.0
        self._lock = threading.RLock()

    def _own_payload(self, value):
        nodes = 0
        truncated = False
        def own(item, depth):
            nonlocal nodes, truncated
            nodes += 1
            if depth > self.limits["max_depth"] or nodes > self.limits["max_payload_nodes"]:
                truncated = True
                return {"observation_omitted": "depth_or_node_limit"}
            kind = type(item)
            if item is None or kind is bool:
                return item
            if kind is int:
                if item.bit_length() > 4096:
                    truncated = True
                    return {"observation_omitted": "large_integer", "bits": item.bit_length()}
                return item
            if kind is float:
                if not math.isfinite(item):
                    raise ValueError("Non-finite observation number")
                return item
            if kind is str:
                if len(item) > self.limits["max_string"]:
                    truncated = True
                    return {"observation_omitted": "long_string", "characters": len(item)}
                return item
            if kind in (list, tuple):
                # Stop traversal as well as output growth when the node budget
                # is consumed; a huge incoming list does not force a huge copy.
                result = []
                for child in item:
                    if nodes >= self.limits["max_payload_nodes"]:
                        truncated = True
                        result.append({"observation_omitted": "remaining_items"})
                        break
                    result.append(own(child, depth + 1))
                return result
            if kind is dict:
                result = {}
                for key, child in item.items():
                    if type(key) is not str or len(key) > self.limits["max_string"]:
                        raise TypeError("Observation keys must be bounded strings")
                    if nodes >= self.limits["max_payload_nodes"]:
                        truncated = True
                        result["observation_omitted"] = "remaining_fields"
                        break
                    result[key] = own(child, depth + 1)
                return result
            raise TypeError("Live objects cannot enter observation records")
        return own(value, 0), truncated

    def _source_ref(self, point):
        if type(point) is not str:
            return None
        pieces = point.rsplit(":", 4)
        if len(pieces) != 5:
            return SourceRef(point=point)
        module, digest_prefix, line, column, operation = pieces
        try:
            line, column = int(line), int(column)
        except ValueError:
            return SourceRef(point=point)
        source = self._sources.get(module)
        if source is None or not source[0].startswith(digest_prefix):
            return SourceRef(point, module=module, operation=operation,
                             line=line, column=column)
        digest, filename, text = source
        cap = min(512, self.limits["max_string"])
        candidates = [(number, offset) for (owner, number), offset in self._line_offsets.items()
                      if owner == module and number <= line]
        number, offset = max(candidates, default=(1, 0))
        while number < line:
            newline = text.find("\n", offset)
            if newline < 0:
                offset = len(text)
                break
            number += 1
            offset = newline + 1
        if len(self._line_offsets) < self.limits["max_events"]:
            self._line_offsets[(module, line)] = offset
        end = text.find("\n", offset, offset + cap + 1)
        end = min(len(text), offset + cap + 1) if end < 0 else end
        excerpt = text[offset:min(end, offset + cap)] if line > 0 else ""
        return SourceRef(point, module, digest, filename, line, column, operation,
                         excerpt, end - offset > cap)

    def emit(self, incoming):
        started = time.perf_counter()
        with self._lock:
            self._sequence += 1
            try:
                if type(incoming) is not dict:
                    raise TypeError("Observation producer must emit a primitive dictionary")
                schema = incoming.get("schema_version", OBSERVATION_SCHEMA_VERSION)
                if type(schema) is not int or schema != OBSERVATION_SCHEMA_VERSION:
                    raise ValueError("Unsupported observation producer schema version")
                kind = incoming.get("kind")
                if type(kind) is not str or not kind or len(kind) > 128:
                    raise ValueError("Observation kind must be a bounded nonempty string")
                unit = incoming.get("unit")
                if unit is not None and (type(unit) is not str or len(unit) > self.limits["max_string"]):
                    raise TypeError("Observation unit must be a bounded opaque string")
                if kind == "work_dequeue":
                    # If this event is dropped, later unit-less records must
                    # not inherit an unrelated previously observed unit.
                    self._active_unit = None
                active_unit = unit if kind == "work_dequeue" else self._active_unit
                unit = unit or active_unit
                revision = incoming.get("revision", self._unit_revision.get(unit))
                if revision is not None and (type(revision) not in (str, int)
                        or type(revision) is str and len(revision) > self.limits["max_string"]):
                    raise TypeError("Observation revision must be a bounded integer or string")
                point = incoming.get("point")
                if point is not None and (type(point) is not str or len(point) > self.limits["max_string"]):
                    raise TypeError("Observation point must be a bounded stable string")
                if len(self._records) >= self.limits["max_events"]:
                    self._dropped += 1
                    return False
                payload, clipped = self._own_payload({k: v for k, v in incoming.items()
                                                       if k not in ("schema_version", "kind", "unit", "revision", "parents")})
                parents = []
                explicit = incoming.get("parents", ())
                if type(explicit) not in (list, tuple):
                    raise TypeError("Observation parent references must be a sequence")
                if len(explicit) > self.limits["max_payload_nodes"]:
                    clipped = True
                for parent in explicit[:self.limits["max_payload_nodes"]]:
                    if type(parent) is not str:
                        raise TypeError("Observation parent references must be strings")
                    if parent in self._by_id:
                        parents.append(parent)
                if unit in self._last_unit:
                    parents.append(self._last_unit[unit])
                if kind == "call_dependency":
                    caller, callee = incoming.get("caller"), incoming.get("callee")
                    if type(caller) is str and type(callee) is str:
                        if callee in self._summaries:
                            parents.append(self._summaries[callee])
                for callee in self._dependencies.get(unit, ()):
                    if callee in self._summaries:
                        parents.append(self._summaries[callee])
                record = CausalEvent(f"event:{self._prefix}:{self._sequence:08d}", self._sequence,
                                     kind, self._source_ref(incoming.get("point")), unit,
                                     revision, tuple(dict.fromkeys(parents)), payload, clipped)
                size = len(json.dumps(record.to_dict(), sort_keys=True, ensure_ascii=False,
                                      allow_nan=False, separators=(",", ":")).encode("utf-8"))
                if size > self.limits["max_event_bytes"] or self._bytes + size > self.limits["max_bytes"]:
                    self._dropped += 1
                    return False
                self._records.append(record)
                self._by_id[record.event_id] = record
                self._bytes += size
                self._payload_truncations += int(clipped)
                if kind == "work_dequeue":
                    self._active_unit = unit
                if unit is not None:
                    self._last_unit[unit] = record.event_id
                    if revision is not None:
                        self._unit_revision[unit] = revision
                if kind == "call_dependency":
                    caller, callee = incoming.get("caller"), incoming.get("callee")
                    if type(caller) is str and type(callee) is str:
                        self._dependencies.setdefault(caller, set()).add(callee)
                if kind == "summary_update" and unit is not None:
                    self._summaries[unit] = record.event_id
                return True
            except Exception:
                self._errors += 1
                return False
            finally:
                self._capture_seconds += time.perf_counter() - started

    @property
    def records(self):
        with self._lock:
            return tuple(self._records)

    @property
    def stats(self):
        with self._lock:
            return {"total_events": self._sequence, "retained_events": len(self._records),
                    "dropped_events": self._dropped, "errors": self._errors,
                    "payload_truncations": self._payload_truncations,
                    "retained_bytes": self._bytes, "capture_seconds": self._capture_seconds}

    def snapshot(self):
        with self._lock:
            stats = self.stats
            truncated = bool(self._dropped or self._payload_truncations)
            return {"schema_version": OBSERVATION_SCHEMA_VERSION,
                    "identity": thaw_snapshot(self.identity),
                    "events": [record.to_dict() for record in self._records],
                    "truncated": truncated,
                    "observation_status": "partial" if truncated or self._errors else "recorded",
                    "claim": "observed_execution_and_dependencies_only",
                    **stats, "stats": stats, "limits": thaw_snapshot(self.limits)}


BoundedObservationSink = ObservationSink
