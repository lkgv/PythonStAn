"""Bounded, detached observation records; never part of solver semantics.

Events describe observed execution and explicit dependency notifications. Their
order is not a proof of target-program event order or semantic completeness.
"""

from __future__ import annotations

import json
import math
import threading
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from hashlib import sha256
from typing import Protocol, runtime_checkable

from .identity import SourceModule
from .json_data import (
    FrozenJsonObject,
    FrozenJsonValue,
    JsonObject,
    JsonValue,
    freeze_json,
    freeze_json_object,
    require_string,
    thaw_json,
    thaw_json_object,
    validate_json,
)

OBSERVATION_SCHEMA_VERSION = 1


type ObservationInput = dict[str, object]


@runtime_checkable
class ObservationEmitter(Protocol):
    """One actual producer port; recorder validation owns the raw wire boundary."""

    def emit(self, incoming: ObservationInput) -> object:
        """Receive a primitive event; the solver does not consume the return value."""
        ...


type ObserverPort = ObservationEmitter | Callable[[ObservationInput], object]


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

    def to_dict(self) -> JsonObject:
        return {
            "point": self.point,
            "module": self.module,
            "source_digest": self.source_digest,
            "filename": self.filename,
            "line": self.line,
            "column": self.column,
            "operation": self.operation,
            "excerpt": self.excerpt,
            "excerpt_truncated": self.excerpt_truncated,
        }


@dataclass(frozen=True, init=False)
class CausalEvent:
    event_id: str
    sequence: int
    kind: str
    source: SourceRef | None
    unit: str | None
    revision: int | str | None
    parents: tuple[str, ...]
    payload: FrozenJsonValue
    payload_truncated: bool = False
    schema_version: int = OBSERVATION_SCHEMA_VERSION

    def __init__(
        self,
        event_id: str,
        sequence: int,
        kind: str,
        source: SourceRef | None,
        unit: str | None,
        revision: int | str | None,
        parents: tuple[str, ...],
        payload: object,
        payload_truncated: bool = False,
        schema_version: int = OBSERVATION_SCHEMA_VERSION,
    ) -> None:
        owned_parents: tuple[str, ...] = tuple(parents)
        object.__setattr__(self, "event_id", event_id)
        object.__setattr__(self, "sequence", sequence)
        object.__setattr__(self, "kind", kind)
        object.__setattr__(self, "source", source)
        object.__setattr__(self, "unit", unit)
        object.__setattr__(self, "revision", revision)
        object.__setattr__(self, "parents", owned_parents)
        object.__setattr__(self, "payload", freeze_json(validate_json(payload)))
        object.__setattr__(self, "payload_truncated", payload_truncated)
        object.__setattr__(self, "schema_version", schema_version)

    def to_dict(self) -> JsonObject:
        return {
            "schema_version": self.schema_version,
            "event_id": self.event_id,
            "sequence": self.sequence,
            "kind": self.kind,
            "source": self.source.to_dict() if self.source else None,
            "point": self.source.point if self.source else None,
            "unit": self.unit,
            "revision": self.revision,
            "parents": list(self.parents),
            "payload": thaw_json(self.payload),
            "payload_truncated": self.payload_truncated,
        }


@dataclass(frozen=True)
class ObservationLimits:
    """One immutable set of limits consumed directly by the recorder."""

    max_events: int
    max_bytes: int
    max_event_bytes: int
    max_payload_nodes: int
    max_depth: int
    max_string: int

    def to_dict(self) -> JsonObject:
        return {
            "max_events": self.max_events,
            "max_bytes": self.max_bytes,
            "max_event_bytes": self.max_event_bytes,
            "max_payload_nodes": self.max_payload_nodes,
            "max_depth": self.max_depth,
            "max_string": self.max_string,
        }


@dataclass(frozen=True)
class _ObservedSource:
    digest: str
    filename: str
    source: str


type ObservationSource = SourceModule | Mapping[str, JsonValue]


class ObservationSink:
    """A passive per-run bounded recorder with immutable retained records.

    Capture failures and limits affect observation status only. The producer
    remains responsible for continuing all semantic work and obligations.
    """

    def __init__(
        self,
        *,
        run_id: str = "",
        source_digest: str = "",
        model_digest: str = "",
        policy_digest: str = "",
        profile: str = "",
        sources: Mapping[str, ObservationSource] | Iterable[ObservationSource] = (),
        max_events: int = 10000,
        max_bytes: int = 4 * 1024 * 1024,
        max_event_bytes: int = 32768,
        max_payload_nodes: int = 1024,
        max_depth: int = 12,
        max_string: int = 2048,
    ) -> None:
        limits: dict[str, int] = {
            "max_events": max_events,
            "max_bytes": max_bytes,
            "max_event_bytes": max_event_bytes,
            "max_payload_nodes": max_payload_nodes,
            "max_depth": max_depth,
            "max_string": max_string,
        }
        if any(type(v) is not int or v < 0 for v in limits.values()):
            raise ValueError("Observation limits must be nonnegative integers")
        self.identity: FrozenJsonObject = freeze_json_object(
            {
                "run_id": run_id,
                "source_digest": source_digest,
                "model_digest": model_digest,
                "policy_digest": policy_digest,
                "profile": profile,
            }
        )
        self._limits: ObservationLimits = ObservationLimits(
            max_events, max_bytes, max_event_bytes, max_payload_nodes, max_depth, max_string
        )
        self.limits: FrozenJsonObject = freeze_json_object(self._limits.to_dict())
        token = (
            run_id
            or sha256(
                json.dumps(thaw_json_object(self.identity), sort_keys=True).encode()
            ).hexdigest()[:24]
        )
        self._prefix: str = str(token)
        iterable = sources.values() if isinstance(sources, Mapping) else sources
        self._sources: dict[str, _ObservedSource] = {}
        for source in iterable:
            if isinstance(source, SourceModule):
                self._sources[source.module] = _ObservedSource(
                    source.digest, source.filename, source.source
                )
            elif isinstance(source, Mapping):
                module = source.get("module")
                if type(module) is str:
                    self._sources[module] = _ObservedSource(
                        require_string(source.get("digest", "")),
                        require_string(source.get("filename", "")),
                        require_string(source.get("source", "")),
                    )
        self._line_offsets: dict[tuple[str, int], int] = {}
        self._records: list[CausalEvent] = []
        self._by_id: dict[str, CausalEvent] = {}
        self._last_unit: dict[str, str] = {}
        self._summaries: dict[str, str] = {}
        self._dependencies: dict[str, set[str]] = {}
        self._unit_revision: dict[str, int | str] = {}
        self._active_unit: str | None = None
        self._sequence: int = 0
        self._dropped: int = 0
        self._errors: int = 0
        self._bytes: int = 0
        self._payload_truncations: int = 0
        self._capture_seconds: float = 0.0
        self._lock: threading.RLock = threading.RLock()

    def _own_payload(self, value: object) -> tuple[JsonValue, bool]:
        nodes = 0
        truncated = False

        def own(item: object, depth: int) -> JsonValue:
            nonlocal nodes, truncated
            nodes += 1
            if depth > self._limits.max_depth or nodes > self._limits.max_payload_nodes:
                truncated = True
                return {"observation_omitted": "depth_or_node_limit"}
            if item is None:
                return None
            if type(item) is bool:
                return item
            if type(item) is int:
                if item.bit_length() > 4096:
                    truncated = True
                    return {"observation_omitted": "large_integer", "bits": item.bit_length()}
                return item
            if type(item) is float:
                if not math.isfinite(item):
                    raise ValueError("Non-finite observation number")
                return item
            if type(item) is str:
                if len(item) > self._limits.max_string:
                    truncated = True
                    return {"observation_omitted": "long_string", "characters": len(item)}
                return item
            if type(item) is list or type(item) is tuple:
                # Traversal stops with the output budget; a large caller list
                # never forces a complete copy merely to report truncation.
                result_items: list[JsonValue] = []
                for child in item:
                    if nodes >= self._limits.max_payload_nodes:
                        truncated = True
                        result_items.append({"observation_omitted": "remaining_items"})
                        break
                    result_items.append(own(child, depth + 1))
                return result_items
            if type(item) is dict:
                result_fields: JsonObject = {}
                for raw_key, child in item.items():
                    key: object = raw_key
                    if type(key) is not str or len(key) > self._limits.max_string:
                        raise TypeError("Observation keys must be bounded strings")
                    if nodes >= self._limits.max_payload_nodes:
                        truncated = True
                        result_fields["observation_omitted"] = "remaining_fields"
                        break
                    result_fields[key] = own(child, depth + 1)
                return result_fields
            raise TypeError("Live objects cannot enter observation records")

        return own(value, 0), truncated

    def _source_ref(self, point: str | None) -> SourceRef | None:
        if type(point) is not str:
            return None
        pieces = point.rsplit(":", 4)
        if len(pieces) != 5:
            return SourceRef(point=point)
        module, digest_prefix, line_text, column_text, operation = pieces
        try:
            line, column = int(line_text), int(column_text)
        except ValueError:
            return SourceRef(point=point)
        source = self._sources.get(module)
        if source is None or not source.digest.startswith(digest_prefix):
            return SourceRef(point, module=module, operation=operation, line=line, column=column)
        digest, filename, text = source.digest, source.filename, source.source
        cap = min(512, self._limits.max_string)
        candidates = [
            (number, offset)
            for (owner, number), offset in self._line_offsets.items()
            if owner == module and number <= line
        ]
        number, offset = max(candidates, default=(1, 0))
        while number < line:
            newline = text.find("\n", offset)
            if newline < 0:
                offset = len(text)
                break
            number += 1
            offset = newline + 1
        if len(self._line_offsets) < self._limits.max_events:
            self._line_offsets[(module, line)] = offset
        end = text.find("\n", offset, offset + cap + 1)
        end = min(len(text), offset + cap + 1) if end < 0 else end
        excerpt = text[offset : min(end, offset + cap)] if line > 0 else ""
        return SourceRef(
            point, module, digest, filename, line, column, operation, excerpt, end - offset > cap
        )

    def emit(self, incoming: object) -> bool:
        started = time.perf_counter()
        with self._lock:
            self._sequence += 1
            try:
                if type(incoming) is not dict:
                    raise TypeError("Observation producer must emit a primitive dictionary")
                raw_event: dict[object, object] = incoming
                schema: object = raw_event.get("schema_version", OBSERVATION_SCHEMA_VERSION)
                if type(schema) is not int or schema != OBSERVATION_SCHEMA_VERSION:
                    raise ValueError("Unsupported observation producer schema version")
                kind: object = raw_event.get("kind")
                if type(kind) is not str or not kind or len(kind) > 128:
                    raise ValueError("Observation kind must be a bounded nonempty string")
                unit: object = raw_event.get("unit")
                if unit is not None and (
                    type(unit) is not str or len(unit) > self._limits.max_string
                ):
                    raise TypeError("Observation unit must be a bounded opaque string")
                if kind == "work_dequeue":
                    # If this event is dropped, later unit-less records must
                    # not inherit an unrelated previously observed unit.
                    self._active_unit = None
                active_unit = unit if kind == "work_dequeue" else self._active_unit
                unit = unit or active_unit
                revision: object = raw_event.get(
                    "revision", self._unit_revision.get(unit) if unit is not None else None
                )
                if revision is not None and (
                    not (type(revision) is str or type(revision) is int)
                    or type(revision) is str
                    and len(revision) > self._limits.max_string
                ):
                    raise TypeError("Observation revision must be a bounded integer or string")
                point: object = raw_event.get("point")
                if point is not None and (
                    type(point) is not str or len(point) > self._limits.max_string
                ):
                    raise TypeError("Observation point must be a bounded stable string")
                if len(self._records) >= self._limits.max_events:
                    self._dropped += 1
                    return False
                payload, clipped = self._own_payload(
                    {
                        k: v
                        for k, v in raw_event.items()
                        if k not in ("schema_version", "kind", "unit", "revision", "parents")
                    }
                )
                parents: list[str] = []
                explicit: object = raw_event.get("parents", ())
                if not (type(explicit) is list or type(explicit) is tuple):
                    raise TypeError("Observation parent references must be a sequence")
                if len(explicit) > self._limits.max_payload_nodes:
                    clipped = True
                for raw_parent in explicit[: self._limits.max_payload_nodes]:
                    parent: object = raw_parent
                    if type(parent) is not str:
                        raise TypeError("Observation parent references must be strings")
                    if parent in self._by_id:
                        parents.append(parent)
                if unit is not None and unit in self._last_unit:
                    parents.append(self._last_unit[unit])
                if kind == "call_dependency":
                    caller: object = raw_event.get("caller")
                    callee: object = raw_event.get("callee")
                    if type(caller) is str and type(callee) is str and callee in self._summaries:
                        parents.append(self._summaries[callee])
                for callee in self._dependencies.get(unit, ()) if unit is not None else ():
                    if callee in self._summaries:
                        parents.append(self._summaries[callee])
                parent_keys: dict[str, None] = dict.fromkeys(parents, None)
                unique_parents: tuple[str, ...] = tuple(parent_keys)
                record = CausalEvent(
                    f"event:{self._prefix}:{self._sequence:08d}",
                    self._sequence,
                    kind,
                    self._source_ref(point),
                    unit,
                    revision,
                    unique_parents,
                    payload,
                    clipped,
                )
                size = len(
                    json.dumps(
                        record.to_dict(),
                        sort_keys=True,
                        ensure_ascii=False,
                        allow_nan=False,
                        separators=(",", ":"),
                    ).encode("utf-8")
                )
                if (
                    size > self._limits.max_event_bytes
                    or self._bytes + size > self._limits.max_bytes
                ):
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
                    published_caller: object = raw_event.get("caller")
                    published_callee: object = raw_event.get("callee")
                    if type(published_caller) is str and type(published_callee) is str:
                        self._dependencies.setdefault(published_caller, set()).add(published_callee)
                if kind == "summary_update" and unit is not None:
                    self._summaries[unit] = record.event_id
                return True
            except Exception:
                self._errors += 1
                return False
            finally:
                self._capture_seconds += time.perf_counter() - started

    @property
    def records(self) -> tuple[CausalEvent, ...]:
        with self._lock:
            return tuple(self._records)

    @property
    def stats(self) -> JsonObject:
        with self._lock:
            return {
                "total_events": self._sequence,
                "retained_events": len(self._records),
                "dropped_events": self._dropped,
                "errors": self._errors,
                "payload_truncations": self._payload_truncations,
                "retained_bytes": self._bytes,
                "capture_seconds": self._capture_seconds,
            }

    def snapshot(self) -> JsonObject:
        with self._lock:
            stats = self.stats
            truncated = bool(self._dropped or self._payload_truncations)
            return {
                "schema_version": OBSERVATION_SCHEMA_VERSION,
                "identity": thaw_json_object(self.identity),
                "events": [record.to_dict() for record in self._records],
                "truncated": truncated,
                "observation_status": "partial" if truncated or self._errors else "recorded",
                "claim": "observed_execution_and_dependencies_only",
                **stats,
                "stats": stats,
                "limits": thaw_json_object(self.limits),
            }


BoundedObservationSink = ObservationSink
