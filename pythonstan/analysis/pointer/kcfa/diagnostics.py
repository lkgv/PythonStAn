"""Optional native counters and bounded, memory-only source observations.

This port has no Inspector import, I/O, observer callback, secondary fact store
or native-object ownership. Events describe actual instrumented operations;
only weak references can later associate them with a frozen snapshot.
"""

import json
import math
import weakref

from . import points_to_set


_KINDS = ("static", "constraint_replay", "builtin_replay", "points_to")
_COUNTERS = (
    "selected_work_items",
    "completed_work_items",
    "points_enqueue_requests",
    "points_enqueue_accepted",
    "points_enqueue_processed_skips",
    "points_enqueue_trimmed",
    "points_delta_objects",
    "points_noop_work_items",
    "constraints_registered",
    "builtin_invocations_registered",
    "bootstrap_candidates",
    "bootstrap_eager_attempts",
    "bootstrap_publications",
    "bootstrap_fallbacks",
    "bootstrap_queued_requested",
    "bootstrap_seed_enqueued",
) + tuple("selected_" + k for k in _KINDS)
_FALLBACKS = (
    "after_work_started",
    "inspector_boundaries",
    "targeted_diagnostics",
    "cold_checkpoint",
    "work_step_observer",
    "debug_monitor",
    "native_contract_not_admitted",
    "invalid_seed_shape",
    "custom_seed_storage",
    "existing_facts_or_consumers",
)
_EVENT_KINDS = frozenset(
    (
        "model_enter",
        "collector_binding",
        "dependency_registration_returned",
        "publication_requested",
        "model_complete",
        "output_delta",
    )
)
_MAX = 2**63 - 1
_METADATA_RESERVATION = 4096


def _text(value, limit=512):
    return (
        type(value) is str
        and 0 < len(value) <= limit
        and not any(0xD800 <= ord(char) <= 0xDFFF for char in value)
    )


def _encoded(value):
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def _scalar(value):
    return (
        value is None
        or type(value) is bool
        or type(value) is int
        and -_MAX <= value <= _MAX
        or type(value) is float
        and math.isfinite(value)
        or type(value) is str
        and len(value) <= 256
        and _text(value or " ", 256)
    )


def _source(scope, call):
    site = call.call_site
    node = vars(site.statement).get("stmt")
    filename = vars(scope.module.stmt).get("filename")
    line, column = getattr(node, "lineno", None), getattr(node, "col_offset", None)
    if (
        not _text(filename)
        or type(line) is not int
        or line < 1
        or type(column) is not int
        or column < 0
        or type(site.index) is not int
    ):
        return None
    return {"file": filename, "line": line, "column": column, "call_index": site.index}


def validate_selector(selector):
    if (
        type(selector) is not dict
        or set(selector) - {"file", "line", "column", "model"}
        or not {"file", "line", "model"}.issubset(selector)
        or not _text(selector["file"])
        or type(selector["line"]) is not int
        or selector["line"] < 1
        or selector["model"] != "keyword_collector"
        or "column" in selector
        and (type(selector["column"]) is not int or selector["column"] < 0)
    ):
        raise ValueError(
            "targeted selector requires exact file,line,model=keyword_collector and optional column"
        )
    return dict(selector)


class NativeDiagnostics:
    """Exact native implementation; its scalar-only mode has an eager audit."""

    _counts: dict[str, int]
    _window_counts: dict[str, int]

    def __init__(
        self, mode, run_id, selector=None, *, max_events=256, max_bytes=262144, max_refs=512
    ):
        if mode not in ("counters", "targeted") or not _text(run_id, 128):
            raise ValueError("native diagnostics mode/run_id unsupported")
        if (
            type(max_events) is not int
            or not 1 <= max_events <= 256
            or type(max_refs) is not int
            or not 1 <= max_refs <= 512
            or type(max_bytes) is not int
            or not 8192 <= max_bytes <= 262144
        ):
            raise ValueError("native diagnostic budgets are outside the bounded contract")
        self.mode, self.run_id = mode, run_id
        self.selector = validate_selector(selector) if mode == "targeted" else None
        if mode == "counters" and selector is not None:
            raise ValueError("counter mode has no source selector")
        self.max_events, self.max_bytes, self.max_refs = max_events, max_bytes, max_refs
        self._counts = dict.fromkeys(_COUNTERS, 0)
        self._window_counts = dict.fromkeys(_COUNTERS, 0)
        self._fallbacks = dict.fromkeys(_FALLBACKS, 0)
        self.window = 0
        self._window_valid = True
        self._counter_complete = True
        self.failures = 0
        self.phase = "configured"
        self.last_action = None
        self.epoch = None
        self._sequence = 0
        self._events = []
        self._event_bytes = 0
        self._descriptor_bytes = 0
        self._watch_bytes = 0
        self._refs = {}
        self._object_refs = {}
        self._descriptors = {}
        self._descriptor_cursor = 0
        self._watched_owners = {}
        self._watched_objects = {}
        self._filtered = 0
        self._dropped_events = 0
        self._dropped_descriptors = 0
        self._epoch_changes = 0
        self._window_end = None

    def bump(self, name: str, amount: int = 1) -> None:
        try:
            self._counts[name] = min(_MAX, self._counts[name] + amount)
            self._window_counts[name] = min(_MAX, self._window_counts[name] + amount)
        except Exception:
            self._counter_complete = False
            self.failures += 1

    def start_window(self):
        try:
            fresh = dict.fromkeys(_COUNTERS, 0)
            self._window_counts = fresh
            self.window += 1
            self._window_valid = True
        except Exception:
            # A diagnostic allocation failure cannot skip selected native work.
            self._window_valid = False
            self.failures += 1

    def seed_decision(self, result):
        self.bump("bootstrap_candidates")
        if result == "queued_requested":
            self.bump("bootstrap_queued_requested")
        elif result == "published":
            self.bump("bootstrap_eager_attempts")
            self.bump("bootstrap_publications")
        elif result in self._fallbacks:
            self.bump("bootstrap_eager_attempts")
            self.bump("bootstrap_fallbacks")
            self._fallbacks[result] = min(_MAX, self._fallbacks[result] + 1)
        else:
            self.failures += 1

    def work_selected(self, kind):
        self.phase, self.last_action = "solve", kind
        self.bump("selected_work_items")
        self.bump("selected_" + kind)

    def work_completed(self):
        self.bump("completed_work_items")

    def selected(self, scope, call, model):
        if self.mode != "targeted" or self._window_end is not None:
            return False
        try:
            selector = self.selector
            # Check the model before even reading source descriptors.
            if model != selector["model"]:
                self._filtered += 1
                return False
            site = call.call_site
            node = vars(site.statement).get("stmt")
            if (
                getattr(node, "lineno", None) != selector["line"]
                or "column" in selector
                and getattr(node, "col_offset", None) != selector["column"]
                or vars(scope.module.stmt).get("filename") != selector["file"]
            ):
                self._filtered += 1
                return False
            return True
        except Exception:
            self.failures += 1
            return False

    def _arena(self):
        current = points_to_set._object_table_generation
        if self.epoch is None:
            self.epoch = current
        elif self.epoch != current:
            # IDs/descriptors cannot be reinterpreted after an arena reset.
            self._epoch_changes += 1
            self._window_end = "arena_changed"
            return False
        return True

    def _space(self, amount):
        return (
            _METADATA_RESERVATION
            + self._event_bytes
            + self._descriptor_bytes
            + self._watch_bytes
            + 128 * len(self._refs)
            + amount
            <= self.max_bytes
        )

    def _reference(self, obj):
        identity = id(obj)
        existing = self._object_refs.get(identity)
        if existing is not None and self._refs[existing]() is obj:
            return {"run_id": self.run_id, "epoch": self.epoch, "ref": existing}
        if len(self._refs) >= self.max_refs:
            self._dropped_descriptors += 1
            return None
        try:
            reference = weakref.ref(obj)
        except TypeError:
            self._dropped_descriptors += 1
            return None
        number = len(self._refs) + 1
        ref = {"run_id": self.run_id, "epoch": self.epoch, "ref": number}
        descriptor = _encoded({"ref": ref, "kind": type(obj).__name__[:64]})
        if not self._space(128 + len(descriptor)):
            self._dropped_descriptors += 1
            return None
        self._refs[number], self._object_refs[identity] = reference, number
        self._descriptors[number] = descriptor
        self._descriptor_bytes += len(descriptor)
        return ref

    def _watch(self, obj, role, source, model, sequence):
        target = self._watched_objects if role == "collector_object" else self._watched_owners
        identity = id(obj)
        if identity in target:
            previous = target[identity]
            if previous[0]() is obj:
                return
            # Python may reuse an address after the old weak referent dies.
            # A stale address must not suppress the new object's watch.
            self._watch_bytes -= 128 + len(_encoded(previous[2]))
            del target[identity]
        size = 128 + len(_encoded(source))
        if not self._space(size):
            self._dropped_descriptors += 1
            return
        target[identity] = (weakref.ref(obj), role, source, model, sequence)
        self._watch_bytes += size

    def record(self, kind, scope, call, model, *, refs=None, fields=None):
        if not self.selected(scope, call, model):
            return None
        try:
            source = _source(scope, call)
            if source is None:
                self.failures += 1
                return None
            return self._record(kind, source, model, refs or {}, fields or {})
        except Exception:
            self.failures += 1
            return None

    def _record(self, kind, source, model, refs, fields):
        if self._window_end is not None or not self._arena():
            return None
        if (
            kind not in _EVENT_KINDS
            or type(refs) is not dict
            or len(refs) > 12
            or type(fields) is not dict
            or len(fields) > 16
            or any(not _text(key, 64) for key in refs)
            or any(not _text(key, 64) or not _scalar(value) for key, value in fields.items())
        ):
            self.failures += 1
            return None
        if self._sequence >= self.max_events:
            self._dropped_events += 1
            self._window_end = "event_budget"
            return None
        projected = {}
        for role, obj in refs.items():
            ref = self._reference(obj)
            if ref is None:
                self._dropped_events += 1
                return None
            projected[role] = ref
        sequence = self._sequence + 1
        event = _encoded(
            {
                "run_id": self.run_id,
                "epoch": self.epoch,
                "sequence": sequence,
                "kind": kind,
                "model": model,
                "source": source,
                "refs": projected,
                "fields": dict(fields),
            }
        )
        if not self._space(len(event)):
            self._dropped_events += 1
            self._window_end = "byte_budget"
            return None
        self._events.append(event)
        self._event_bytes += len(event)
        self._sequence = sequence
        for role in ("target", "collector_parameter", "collector_object"):
            if role in refs:
                self._watch(refs[role], role, source, model, sequence)
        return sequence

    def output_delta(self, owner, delta):
        """Called after the actual Ctx store, using its existing fresh delta."""
        if self.mode != "targeted" or self._window_end is not None:
            return
        try:
            watched = self._watched_owners.get(id(owner))
            if watched is None or watched[0]() is not owner:
                content = vars(owner).get("content")
                obj = vars(content).get("obj") if content is not None else None
                watched = self._watched_objects.get(id(obj))
                if watched is None or watched[0]() is not obj:
                    return
                role = "collector_field"
            else:
                role = watched[1]
            _, _, source, model, sequence = watched
            fields = {
                "owner_role": role,
                "owner_registered_by_event": sequence,
                "objects_count": delta.objs_mask.bit_count(),
                "classmethods_count": delta.classmethods_mask.bit_count(),
                "instancemethods_count": delta.instancemethods_mask.bit_count(),
            }
            refs = {"owner": owner}
            # The event bounds this *delta*, not the owner's entire points-to.
            mask = delta.objs_mask
            table = points_to_set._global_table
            index = 0
            while mask and index < 8:
                low = mask & -mask
                mask ^= low
                number = low.bit_length() - 1
                if table is not None and number < len(table._id_to_obj):
                    refs["object_" + str(index)] = table._id_to_obj[number]
                    index += 1
            fields["object_refs_omitted"] = mask.bit_count()
            self._record("output_delta", source, model, refs, fields)
        except Exception:
            self.failures += 1

    def resolve(self, run_ref):
        if (
            type(run_ref) is not dict
            or set(run_ref) != {"run_id", "epoch", "ref"}
            or run_ref["run_id"] != self.run_id
            or type(run_ref["ref"]) is not int
            or type(run_ref["epoch"]) is not int
            or run_ref["epoch"] != self.epoch
            or self.epoch != points_to_set._object_table_generation
        ):
            return None
        reference = self._refs.get(run_ref["ref"])
        return reference() if reference is not None else None

    def reference_bindings(self):
        """Cold bounded weak identities; never a strong native-object table."""
        return [
            ({"run_id": self.run_id, "epoch": self.epoch, "ref": number}, reference, self.epoch)
            for number, reference in self._refs.items()
        ]

    def health(self):
        return {
            "mode": self.mode,
            "status": "degraded"
            if self.failures
            or self._dropped_events
            or self._dropped_descriptors
            or self._epoch_changes
            else "observing",
            "max_events": self.max_events,
            "max_bytes": self.max_bytes,
            "max_refs": self.max_refs,
            "recorded_events": self._sequence,
            "retained_events": len(self._events),
            "event_bytes": self._event_bytes,
            "descriptor_bytes": self._descriptor_bytes,
            "reference_count": len(self._refs),
            "reference_reserved_bytes": 128 * len(self._refs),
            "watch_bytes": self._watch_bytes,
            "metadata_reserved_bytes": _METADATA_RESERVATION,
            "accounted_bytes": _METADATA_RESERVATION
            + self._event_bytes
            + self._descriptor_bytes
            + self._watch_bytes
            + 128 * len(self._refs),
            "byte_scope": "encoded_records_and_bounded_reference_reservations_not_allocator_RSS",
            "filtered": self._filtered,
            "dropped_events": self._dropped_events,
            "dropped_descriptors": self._dropped_descriptors,
            "failures": self.failures,
            "arena_changes": self._epoch_changes,
            "window_end": self._window_end,
            "context_selection": "unsupported",
            "history_claim": "observed_operations_not_replay_or_complete_causality",
        }

    def drain(self):
        events = [json.loads(value) for value in self._events]
        descriptors = [
            json.loads(value)
            for number, value in self._descriptors.items()
            if number > self._descriptor_cursor
        ]
        self._descriptor_cursor = len(self._descriptors)
        self._events.clear()
        self._event_bytes = 0
        return {
            "schema_version": 1,
            "run_id": self.run_id,
            "epoch": self.epoch,
            "selector": dict(self.selector) if self.selector is not None else None,
            "events": events,
            "descriptors": descriptors,
            "health": self.health(),
        }


# Pure counters can be admitted only while these exact native methods/code and
# fixed scalar container types are intact. Targeted memory hooks remain queued.
_PURE_METHODS = tuple(
    (name, getattr(NativeDiagnostics, name), getattr(NativeDiagnostics, name).__code__)
    for name in (
        "bump",
        "start_window",
        "seed_decision",
        "work_selected",
        "work_completed",
        "selected",
        "output_delta",
    )
)


def pure_counter_signature(port):
    if (
        type(port) is not NativeDiagnostics
        or type(port.mode) is not str
        or port.mode != "counters"
        or not _text(port.run_id, 128)
        or port.selector is not None
        or type(port.window) is not int
        or port.window < 0
        or type(port.failures) is not int
        or port.failures < 0
        or type(port._window_valid) is not bool
        or type(port._counter_complete) is not bool
        or type(port._counts) is not dict
        or type(port._window_counts) is not dict
        or type(port._fallbacks) is not dict
        or set(port._counts) != set(_COUNTERS)
        or set(port._window_counts) != set(_COUNTERS)
        or set(port._fallbacks) != set(_FALLBACKS)
    ):
        return None
    for name, function, code in _PURE_METHODS:
        actual = getattr(port, name)
        if (
            name in vars(port)
            or getattr(actual, "__func__", None) is not function
            or getattr(actual, "__code__", None) is not code
        ):
            return None
    if any(
        type(value) is not int or value < 0
        for store in (port._counts, port._window_counts, port._fallbacks)
        for value in store.values()
    ):
        return None
    return (id(port), port.mode, port.run_id, id(port._counts), id(port._fallbacks))
