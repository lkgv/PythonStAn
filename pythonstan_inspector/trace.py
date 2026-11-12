"""Passive scalar event recorder; buffered observation never performs sink I/O.

The byte budget covers retained encoded events and a fixed reservation for all
serialized metadata (including stream identity and the bounded loss summary).
It is not a promise about Python allocator RSS. Returned DTOs belong to callers.
No runtime entity identities, descriptors, live objects, or replay state exist.
"""

from __future__ import annotations

import json
import math
import uuid
from collections import deque
from collections.abc import Mapping

from .contract import InspectorError, canonical_bytes

_MAX_INTEGER = 2**63 - 1
_METADATA_BYTES = 2048
_MAX_FIELDS = 16
_MAX_KEY_CHARS = 64
_MAX_STRING_CHARS = 1024
_MAX_STRING_BYTES = 4096
_WORK_KINDS = ("static", "constraint_replay", "builtin_replay", "points_to")
_WORK_FIELDS = frozenset(("iteration", "kind", "cursor", "pending_work_by_kind"))
_LIFECYCLE_KINDS = frozenset(("run_start", "run_stop", "phase_start", "phase_stop"))
_RESERVED = frozenset(("stream_id", "sequence", "kind", "evidence_kind"))


def _increment(value: int) -> int:
    return min(value + 1, _MAX_INTEGER)


def _text(value: object, maximum: int, label: str) -> None:
    if type(value) is not str or not value or len(value) > maximum:
        raise InspectorError(label + " must be a bounded nonempty string")
    try:
        if len(value.encode("utf-8")) > _MAX_STRING_BYTES:
            raise InspectorError(label + " exceeds encoded string budget")
    except UnicodeError as error:
        raise InspectorError(label + " is not valid UTF-8 text") from error


def _scalar(value: object) -> None:
    if value is None or type(value) is bool:
        return
    if type(value) is int and -_MAX_INTEGER - 1 <= value <= _MAX_INTEGER:
        return
    if type(value) is float and math.isfinite(value):
        return
    if type(value) is str:
        if not value:
            return
        _text(value, _MAX_STRING_CHARS, "event value")
        return
    # Do not stringify, compare, iterate, getattr or serialize foreign objects.
    raise InspectorError("event fields must be bounded JSON scalars")


def _decode_event(encoded: bytes) -> dict[str, object]:
    """Decode an internally encoded event without an untyped JSON result escaping."""
    value: object = json.loads(encoded)
    if not isinstance(value, dict):
        raise InspectorError("recorded event is not an object")
    result: dict[str, object] = {}
    for key, item in value.items():
        if not isinstance(key, str):
            raise InspectorError("recorded event key is not text")
        result[key] = item
    return result


def _native_mapping(value: object) -> dict[object, object]:
    """Narrow an exact primitive dict without invoking a foreign Mapping."""
    if type(value) is not dict:
        raise InspectorError("invalid native diagnostic object")
    raw: dict[object, object] = value
    return raw


class TraceRecorder:
    """Run-local ring with monotonically increasing stream-scoped sequences.

    ``record`` rejects malformed explicit requests. ``observe_step`` contains
    malformed observed records and records a gap instead of changing execution.
    ``flush`` is the only method that invokes the optional user sink. Call it
    outside solving; arbitrary sink latency is not controlled by this recorder.
    """

    def __init__(self, max_bytes: int = 256 * 1024, sink: object = None) -> None:
        if type(max_bytes) is not int or not _METADATA_BYTES <= max_bytes <= _MAX_INTEGER:
            raise InspectorError("trace byte budget must be an integer of at least 2048")
        if sink is not None and not callable(sink):
            raise InspectorError("trace sink must be callable or None")
        self._max_bytes: int = max_bytes
        self._stream_id: str = str(uuid.uuid4())
        self._records: deque[tuple[int, bytes]] = deque()
        self._event_bytes: int = 0
        self._sequence: int = 0
        self._dropped: int = 0
        self._overwritten: int = 0
        self._rejected: int = 0
        self._first_lost: int | None = None
        self._last_lost: int | None = None
        self._sink: object = sink
        self._sink_failures: int = 0
        self._sink_skipped: int = 0
        self._flushed_sequence: int = 0
        self._flushing: bool = False
        self._sequence_exhausted: bool = False
        self._native_batch_failures: int = 0
        self._native_descriptor_frames: int = 0
        self._native_operation_frames: int = 0

    def _loss(self, sequence: int) -> None:
        if self._first_lost is None or sequence < self._first_lost:
            self._first_lost = sequence
        if self._last_lost is None or sequence > self._last_lost:
            self._last_lost = sequence

    def _next_sequence(self) -> int:
        if self._sequence >= _MAX_INTEGER:
            self._sequence_exhausted = True
            raise InspectorError("trace sequence space exhausted")
        self._sequence += 1
        return self._sequence

    def _record(self, kind: object, fields: Mapping[str, object], evidence_kind: str) -> None:
        sequence = self._next_sequence()
        try:
            _text(kind, _MAX_KEY_CHARS, "event kind")
            if len(fields) > _MAX_FIELDS:
                raise InspectorError("too many event fields")
            for key, value in fields.items():
                _text(key, _MAX_KEY_CHARS, "event key")
                if key in _RESERVED:
                    raise InspectorError("event field overwrites reserved metadata")
                _scalar(value)
            event = {
                "stream_id": self._stream_id,
                "sequence": sequence,
                "kind": kind,
                "evidence_kind": evidence_kind,
                **fields,
            }
            encoded = canonical_bytes(event)
        except Exception:
            self._rejected = _increment(self._rejected)
            self._dropped = _increment(self._dropped)
            self._loss(sequence)
            # Validation and encoding are observation-only.  Do not expose a
            # foreign exception message (or call repr on its arguments).
            # BaseException deliberately propagates, including interrupts.
            raise InspectorError("event validation or encoding failed") from None
        capacity = self._max_bytes - _METADATA_BYTES
        if len(encoded) > capacity:
            self._dropped = _increment(self._dropped)
            self._loss(sequence)
            return
        while self._records and self._event_bytes + len(encoded) > capacity:
            old_sequence, old_encoded = self._records.popleft()
            self._event_bytes -= len(old_encoded)
            self._overwritten = _increment(self._overwritten)
            self._loss(old_sequence)
            if self._sink is not None and old_sequence > self._flushed_sequence:
                self._sink_skipped = _increment(self._sink_skipped)
        self._records.append((sequence, encoded))
        self._event_bytes += len(encoded)

    def record(self, kind: object, **scalars: object) -> None:
        evidence = (
            "lifecycle" if type(kind) is str and kind in _LIFECYCLE_KINDS else "observed_only"
        )
        self._record(kind, scalars, evidence)

    def observe_step(self, event: object) -> None:
        """Accept only the audited constant-size solver._finish_work_step DTO."""
        try:
            if type(event) is not dict:
                raise InspectorError("unsupported work observation fields")
            raw: dict[object, object] = event
            if len(raw) != 4 or any(type(key) is not str or key not in _WORK_FIELDS for key in raw):
                raise InspectorError("unsupported work observation fields")
            pending_value = raw["pending_work_by_kind"]
            if type(pending_value) is not dict:
                raise InspectorError("unsupported pending-work observation")
            pending: dict[object, object] = pending_value
            if len(pending) != 4 or any(
                type(key) is not str or key not in _WORK_KINDS for key in pending
            ):
                raise InspectorError("unsupported pending-work observation")
            work_kind = raw["kind"]
            if type(work_kind) is not str or work_kind not in _WORK_KINDS:
                raise InspectorError("unsupported work kind")
            iteration, cursor = raw["iteration"], raw["cursor"]
            if (
                type(iteration) is not int
                or not 0 <= iteration <= _MAX_INTEGER
                or type(cursor) is not int
                or not 0 <= cursor <= _MAX_INTEGER
            ):
                raise InspectorError("work clocks must be nonnegative bounded integers")
            if cursor >= len(_WORK_KINDS):
                raise InspectorError("work cursor outside scheduler kind range")
            for value in pending.values():
                if type(value) is not int or not 0 <= value <= _MAX_INTEGER:
                    raise InspectorError("pending counts must be nonnegative bounded integers")
            fields: dict[str, object] = {
                "iteration": iteration,
                "work_kind": work_kind,
                "cursor": cursor,
            }
            fields.update(("pending_" + key, pending[key]) for key in _WORK_KINDS)
        except InspectorError:
            try:
                sequence = self._next_sequence()
            except InspectorError:
                return
            self._rejected = _increment(self._rejected)
            self._dropped = _increment(self._dropped)
            self._loss(sequence)
            return
        try:
            self._record("work_step", fields, "work_completion")
        except InspectorError:
            return

    @property
    def events(self) -> list[dict[str, object]]:
        return [_decode_event(encoded) for _, encoded in self._records]

    def cold_batch(
        self, after_sequence: int = 0, *, max_events: int = 128, max_bytes: int = 65536
    ) -> dict[str, object]:
        """Copy one bounded batch outside atomic solver work; invoke no sink.

        The ring remains the only event inventory. A sequence gap is explicit;
        consumers must not join records across it into a continuous history.
        """
        if (
            type(after_sequence) is not int
            or after_sequence < 0
            or type(max_events) is not int
            or not 1 <= max_events <= 1024
            or type(max_bytes) is not int
            or not 1 <= max_bytes <= self._max_bytes
        ):
            raise InspectorError("invalid cold trace batch budget")
        events: list[dict[str, object]] = []
        size = 0
        for sequence, encoded in self._records:
            if sequence <= after_sequence:
                continue
            if len(events) >= max_events or size + len(encoded) + 1 > max_bytes:
                break
            events.append(_decode_event(encoded))
            size += len(encoded) + 1
        return {
            "events": events,
            "encoded_event_bytes": size,
            "after_sequence": after_sequence,
            "last_sequence": events[-1]["sequence"] if events else after_sequence,
            "gap_before_batch": bool(events and events[0]["sequence"] != after_sequence + 1),
            "health": self.health,
        }

    def record_native_batch(self, batch: object) -> None:
        """Cold-only ingestion of the exact bounded native diagnostic DTO.

        Public scalar ``record`` remains closed to nested payloads. Native
        run/epoch refs stay in their own namespace, never snapshot local IDs.
        No live object, descriptor callback or resolution is accepted here.
        """
        from .incident import bounded_tree

        try:
            bounded_tree(batch, max_nodes=16384, max_depth=12)
            raw_batch = _native_mapping(batch)
            events_value = raw_batch.get("events")
            descriptors_value = raw_batch.get("descriptors")
            if (
                type(raw_batch.get("schema_version")) is not int
                or raw_batch.get("schema_version") != 1
                or type(raw_batch.get("run_id")) is not str
                or type(events_value) is not list
                or len(events_value) > 256
                or type(descriptors_value) is not list
                or len(descriptors_value) > 512
                or len(canonical_bytes(raw_batch)) > 512 * 1024
            ):
                raise InspectorError("invalid native diagnostic batch")
            descriptors: list[object] = descriptors_value
            events: list[object] = events_value
            records: list[tuple[str, str, dict[object, object]]] = []
            for descriptor in descriptors:
                descriptor_row = _native_mapping(descriptor)
                if (
                    set(descriptor_row) - {"ref", "kind", "source"}
                    or type(descriptor_row.get("kind")) is not str
                ):
                    raise InspectorError("invalid native descriptor")
                self._native_ref(descriptor_row.get("ref"), raw_batch)
                records.append(("native_descriptor", "native_descriptor", descriptor_row))
            for event in events:
                event_row = _native_mapping(event)
                native_sequence = event_row.get("sequence")
                source, refs, fields = (
                    event_row.get("source"),
                    event_row.get("refs"),
                    event_row.get("fields"),
                )
                if (
                    set(event_row)
                    - {"run_id", "epoch", "sequence", "kind", "model", "source", "refs", "fields"}
                    or event_row.get("run_id") != raw_batch["run_id"]
                    or event_row.get("epoch") != raw_batch.get("epoch")
                    or type(native_sequence) is not int
                    or native_sequence < 1
                    or type(event_row.get("kind")) is not str
                    or event_row.get("model") != "keyword_collector"
                    or type(source) is not dict
                    or type(refs) is not dict
                    or type(fields) is not dict
                ):
                    raise InspectorError("invalid native operation")
                references: dict[object, object] = refs
                scalar_fields: dict[object, object] = fields
                for reference in references.values():
                    self._native_ref(reference, raw_batch)
                for scalar_value in scalar_fields.values():
                    _scalar(scalar_value)
                records.append(("targeted_operation", "native_event", event_row))
            for kind, field, value in records:
                sequence = self._next_sequence()
                native_event: dict[str, object] = {
                    "stream_id": self._stream_id,
                    "sequence": sequence,
                    "kind": kind,
                    "evidence_kind": "native_operation_observation",
                    field: value,
                }
                encoded = canonical_bytes(native_event)
                capacity = self._max_bytes - _METADATA_BYTES
                if len(encoded) > capacity:
                    self._dropped = _increment(self._dropped)
                    self._loss(sequence)
                    continue
                while self._records and self._event_bytes + len(encoded) > capacity:
                    old_sequence, old_encoded = self._records.popleft()
                    self._event_bytes -= len(old_encoded)
                    self._overwritten = _increment(self._overwritten)
                    self._loss(old_sequence)
                self._records.append((sequence, encoded))
                self._event_bytes += len(encoded)
                if field == "native_descriptor":
                    self._native_descriptor_frames = _increment(self._native_descriptor_frames)
                else:
                    self._native_operation_frames = _increment(self._native_operation_frames)
        except (InspectorError, ValueError, UnicodeError, TypeError, RecursionError, OverflowError):
            self._native_batch_failures = _increment(self._native_batch_failures)
            self._rejected = _increment(self._rejected)
            try:
                sequence = self._next_sequence()
                self._dropped = _increment(self._dropped)
                self._loss(sequence)
            except InspectorError:
                pass

    @staticmethod
    def _native_ref(reference: object, batch: Mapping[object, object]) -> None:
        raw = _native_mapping(reference)
        local_ref = raw.get("ref")
        if (
            set(raw) != {"run_id", "epoch", "ref"}
            or raw["run_id"] != batch["run_id"]
            or raw["epoch"] != batch.get("epoch")
            or type(local_ref) is not int
            or local_ref < 1
        ):
            raise InspectorError("invalid run-scoped native reference")

    @property
    def health(self) -> dict[str, object]:
        loss = (
            None
            if self._first_lost is None
            else {
                "first_sequence": self._first_lost,
                "last_sequence": self._last_lost,
                "precision": "enclosing_interval_not_all_sequences_lost",
            }
        )
        return {
            "stream_id": self._stream_id,
            "status": "degraded"
            if loss is not None or self._sink_failures or self._sequence_exhausted
            else "recording",
            "max_bytes": self._max_bytes,
            "retained_events": len(self._records),
            "retained_event_bytes": self._event_bytes,
            "metadata_reserved_bytes": _METADATA_BYTES,
            "accounted_bytes": _METADATA_BYTES + self._event_bytes,
            "byte_accounting": "encoded_events_and_reserved_metadata_not_allocator_RSS",
            "last_sequence": self._sequence,
            "dropped_events": self._dropped,
            "overwritten_events": self._overwritten,
            "rejected_events": self._rejected,
            "loss": loss,
            "sink_failures": self._sink_failures,
            "sink_skipped_events": self._sink_skipped,
            "last_sink_attempt_sequence": self._flushed_sequence,
            "sequence_exhausted": self._sequence_exhausted,
            "native_batch_failures": self._native_batch_failures,
            "native_descriptor_frames": self._native_descriptor_frames,
            "native_operation_frames": self._native_operation_frames,
            "native_reference_mapping": "requires_explicit_snapshot_mapping_not_sequence_or_id_equality",
            "entity_mapping": "unsupported",
            "descriptor_state": "bounded_native_run_ref_descriptors"
            if self._native_descriptor_frames
            else "not_recorded",
            "history_claim": "observed_records_only_not_semantic_producer_or_replay",
        }

    def flush(self) -> dict[str, object]:
        """Deliver currently retained, not previously attempted records once.

        Sink exceptions are counted and contained. Interrupts, exit and other
        BaseExceptions propagate. This method is never called by observation.
        """
        if self._sink is None or self._flushing:
            return self.health
        self._flushing = True
        try:
            for sequence, encoded in tuple(self._records):
                if sequence <= self._flushed_sequence:
                    continue
                self._flushed_sequence = sequence
                try:
                    sink = self._sink
                    if not callable(sink):
                        raise TypeError("trace sink is not callable")
                    _ignored: object = sink(_decode_event(encoded))
                except Exception:
                    # Do not retain exception/traceback objects or stringify them.
                    self._sink_failures = _increment(self._sink_failures)
        finally:
            self._flushing = False
        return self.health
