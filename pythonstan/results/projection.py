"""Read-only projection of retained facts; excluded payloads are never thawed."""

from __future__ import annotations

import fnmatch
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass

from pythonstan.analysis.contracts.json_data import (
    FrozenJsonObject,
    FrozenJsonValue,
    JsonObject,
    JsonValue,
)
from pythonstan.analysis.contracts.wire import freeze_snapshot, thaw_snapshot

from .output import OutputSpec
from .queries import ValueQuery

type Visitor = Callable[[int], None]


@dataclass(frozen=True)
class ProjectedPayload:
    value: JsonValue
    total: int = 0
    count: int = 0
    truncated: bool = False
    status: str | None = None
    reason: str | None = None


def _object(value: FrozenJsonValue) -> FrozenJsonObject:
    if not isinstance(value, Mapping):
        raise TypeError("Expected retained JSON object")
    return value


def _array(value: FrozenJsonValue) -> tuple[FrozenJsonValue, ...]:
    if not isinstance(value, tuple):
        raise TypeError("Expected retained JSON array")
    return value


def _text(value: FrozenJsonValue) -> str:
    return value if isinstance(value, str) else ""


def _visit(visit: Visitor | None, amount: int = 1) -> None:
    if visit is not None:
        visit(amount)


def matches(value: str, patterns: tuple[str, ...]) -> bool:
    return not patterns or any(fnmatch.fnmatch(value, pattern) for pattern in patterns)


def retained_module_for_qualname(
    name: str, snapshot: FrozenJsonObject, *, inspect: Visitor | None = None
) -> str:
    inventory = _array(snapshot.get("inventory", ()))
    values = _object(snapshot.get("values", {}))
    _visit(inspect, len(inventory) + len(values))
    modules = {_text(_object(row).get("module")) for row in inventory}
    modules.update(values)
    _visit(inspect, len(modules))
    candidates = [
        module for module in modules if module and (name == module or name.startswith(module + "."))
    ]
    return max(candidates, key=len) if candidates else name.rpartition(".")[0]


def _point_module(point: str) -> str:
    pieces = point.rsplit(":", 4)
    return pieces[0] if len(pieces) == 5 else ""


def _context_matches(context: FrozenJsonValue, patterns: tuple[str, ...]) -> bool:
    if not patterns:
        return True
    values = (context,) if isinstance(context, str) else _array(context if context else ())
    context_values: list[str] = [_text(value) for value in values]
    text = json.dumps(context_values, separators=(",", ":"))
    return matches(text, patterns) or any(matches(_text(value), patterns) for value in values)


def _region_matches(
    point: str, row: FrozenJsonObject, spec: OutputSpec, snapshot: FrozenJsonObject
) -> bool:
    if not spec.regions:
        return True
    decisions = _object(_object(snapshot.get("plan", {})).get("decisions", {}))
    decision = _object(decisions.get(point, {}))
    return any(
        matches(_text(value), spec.regions)
        for value in (
            row.get("region_id", ""),
            decision.get("region_id", ""),
            decision.get("qualname", ""),
            point,
        )
        if value
    )


def _record_matches(
    row: FrozenJsonObject,
    spec: OutputSpec,
    snapshot: FrozenJsonObject,
    point: str = "",
    *,
    inspect: Visitor | None = None,
) -> bool:
    raw_payload = row.get("payload", {})
    payload = raw_payload if isinstance(raw_payload, Mapping) else {}
    function = _text(row.get("caller", row.get("qualname", payload.get("qualname", ""))))
    source = _object(row.get("source") or {})
    point = point or _text(row.get("site", row.get("point", payload.get("point", ""))))
    module = _text(row.get("module", source.get("module", ""))) or _point_module(point)
    module = module or retained_module_for_qualname(function, snapshot, inspect=inspect)
    return (
        matches(module, spec.modules)
        and (
            not spec.functions
            or matches(function, spec.functions)
            or matches(_text(row.get("callee", "")), spec.functions)
        )
        and _region_matches(point, row, spec, snapshot)
        and _context_matches(row.get("contexts", row.get("context", ())), spec.contexts)
    )


def _fields(
    row: FrozenJsonValue,
    spec: OutputSpec,
    required: tuple[str, ...] = (),
    *,
    inspect: Visitor | None = None,
) -> tuple[FrozenJsonValue, bool]:
    if not spec.fields or not isinstance(row, Mapping):
        return row, False
    _visit(inspect, len(row))
    retained = set(spec.fields) | set(required)
    if "wire_kind" in row or "top" in row or "bottom" in row:
        retained.update(("wire_kind", "value_schema_version", "top", "bottom", "top_sorts"))
    selected: dict[str, FrozenJsonValue] = {
        key: item for key, item in row.items() if key in retained
    }
    omitted = set(row) - selected.keys()
    if omitted and ("top" in row or "wire_kind" in row):
        prior = _object(row.get("projection", {}))
        old_omitted = {_text(item) for item in _array(prior.get("omitted_fields", ()))}
        selected["projection"] = {
            **prior,
            "complete": False,
            "omitted_fields": tuple(sorted(old_omitted | omitted)),
        }
    return selected, bool(omitted)


def _finish(
    value: FrozenJsonValue,
    total: int = 0,
    count: int = 0,
    truncated: bool = False,
    status: str | None = None,
    reason: str | None = None,
    *,
    visit: Visitor | None = None,
) -> ProjectedPayload:
    return ProjectedPayload(
        thaw_snapshot(value, visit=visit), total, count, truncated, status, reason
    )


def _joined_unavailable(spec: OutputSpec) -> ProjectedPayload | None:
    if spec.contexts or spec.regions:
        return ProjectedPayload(
            None,
            status="unavailable",
            reason=(
                "Context/region-specific value snapshots were not retained; joined values "
                "cannot be split. Use retained outcomes for context records."
            ),
        )
    return None


def project_retained_payload(
    view: str,
    value: FrozenJsonValue,
    spec: OutputSpec,
    snapshot: FrozenJsonObject,
    *,
    visit: Visitor | None = None,
    inspect: Visitor | None = None,
) -> ProjectedPayload:
    """Select immutable nodes first; only retained output is copied and paid."""
    if value is None:
        return ProjectedPayload(None)
    if view in {"relations", "values", "returns"}:
        unavailable = _joined_unavailable(spec)
        if unavailable:
            return unavailable
    if view == "proof":
        if spec.modules or spec.functions or spec.regions or spec.contexts:
            return ProjectedPayload(
                None,
                status="unavailable",
                reason=(
                    "A global certificate cannot be relabeled as a scoped proof by output filtering"
                ),
            )
        if spec.max_items == 0:
            return ProjectedPayload(
                None,
                1,
                0,
                True,
                "unavailable",
                "The complete proof envelope was omitted by the item limit",
            )
        return _finish(value, 1, 1, visit=visit)
    if view == "effects" and isinstance(value, Mapping):
        if spec.modules or spec.functions or spec.regions or spec.contexts:
            return ProjectedPayload(
                None,
                status="unavailable",
                reason=(
                    "Joined effects lack a retained scope-specific frame and residual attribution"
                ),
            )
        selected: dict[str, FrozenJsonValue] = {}
        total = count = 0
        omitted = False
        for key, item in value.items():
            _visit(inspect)
            if spec.fields and key not in spec.fields:
                omitted = True
                continue
            size = len(item) if isinstance(item, (tuple, Mapping)) else 1
            total += size
            remaining = max(0, spec.max_items - count)
            if isinstance(item, tuple):
                selected[key] = item[:remaining]
                count += min(size, remaining)
            elif isinstance(item, Mapping):
                _visit(inspect, len(item))
                selected[key] = dict(list(item.items())[:remaining])
                count += min(size, remaining)
            elif remaining:
                selected[key] = item
                count += 1
        return _finish(selected, total, count, omitted or count < total, visit=visit)
    if view in {"values", "returns"} and isinstance(value, Mapping):
        selected_values: dict[str, FrozenJsonValue] = {}
        total = count = 0
        omitted = False
        for owner in sorted(value):
            _visit(inspect)
            module = (
                owner
                if view == "values"
                else retained_module_for_qualname(owner, snapshot, inspect=inspect)
            )
            if not matches(module, spec.modules):
                continue
            rows = _object(value[owner]) if view == "values" else {owner: value[owner]}
            for symbol in sorted(rows):
                _visit(inspect)
                function = owner + "." + symbol if view == "values" else owner
                if not matches(function, spec.functions):
                    continue
                total += 1
                if count >= spec.max_items:
                    continue
                row, cut = _fields(rows[symbol], spec, inspect=inspect)
                omitted |= cut
                if view == "values":
                    module_rows = dict(_object(selected_values.get(owner, {})))
                    module_rows[symbol] = row
                    selected_values[owner] = module_rows
                else:
                    selected_values[owner] = row
                count += 1
        return _finish(selected_values, total, count, omitted or count < total, visit=visit)
    if view == "outcomes" and isinstance(value, Mapping):
        selected_outcomes: dict[str, FrozenJsonValue] = {}
        total = count = 0
        omitted = False
        for function in sorted(value):
            _visit(inspect)
            if not matches(function, spec.functions) or not matches(
                retained_module_for_qualname(function, snapshot, inspect=inspect), spec.modules
            ):
                continue
            for raw in _array(value[function]):
                _visit(inspect)
                row = _object(raw)
                context = row.get("context", ())
                if not _context_matches(context, spec.contexts):
                    continue
                if spec.regions and not any(
                    _region_matches(_text(point), {}, spec, snapshot) for point in _array(context)
                ):
                    continue
                total += 1
                if count >= spec.max_items:
                    continue
                selected_row, cut = _fields(
                    row,
                    spec,
                    (
                        "kind",
                        "exception",
                        "context",
                        "value_present",
                    ),
                    inspect=inspect,
                )
                prior = _array(selected_outcomes.get(function, ()))
                selected_outcomes[function] = (*prior, selected_row)
                omitted |= cut
                count += 1
        return _finish(selected_outcomes, total, count, omitted or count < total, visit=visit)
    if view in {"plan", "events"} and isinstance(value, Mapping):
        field = "decisions" if view == "plan" else "events"
        records = (
            _object(value.get(field, {}))
            if view == "plan"
            else {str(index): item for index, item in enumerate(_array(value.get(field, ())))}
        )
        kept: dict[str, FrozenJsonValue] = {}
        total = count = 0
        omitted = False
        for key in sorted(records):
            _visit(inspect)
            row = _object(records[key])
            if not _record_matches(
                row, spec, snapshot, key if view == "plan" else "", inspect=inspect
            ):
                continue
            total += 1
            if count < spec.max_items:
                kept[key], cut = _fields(row, spec, inspect=inspect)
                omitted |= cut
                count += 1
        selected_frame = {key: item for key, item in value.items() if key != field}
        selected_frame[field] = kept if view == "plan" else tuple(kept.values())
        return _finish(selected_frame, total, count, omitted or count < total, visit=visit)
    if isinstance(value, tuple):
        selected_rows: list[FrozenJsonValue] = []
        total = 0
        omitted = False
        for row in value:
            _visit(inspect)
            if isinstance(row, Mapping):
                if not _record_matches(row, spec, snapshot, inspect=inspect):
                    continue
            elif spec.modules or spec.functions or spec.regions or spec.contexts:
                continue
            total += 1
            if len(selected_rows) < spec.max_items:
                selected_row, cut = _fields(row, spec, inspect=inspect)
                selected_rows.append(selected_row)
                omitted |= cut
        return _finish(
            tuple(selected_rows),
            total,
            len(selected_rows),
            omitted or len(selected_rows) < total,
            visit=visit,
        )
    if isinstance(value, Mapping):
        fields, omitted = _fields(value, spec, inspect=inspect)
        selected_object = _object(fields)
        _visit(inspect, len(selected_object))
        selected = dict(list(selected_object.items())[: spec.max_items])
        return _finish(
            selected,
            len(selected_object),
            len(selected),
            omitted or len(selected) < len(selected_object),
            visit=visit,
        )
    return _finish(value, visit=visit)


def project_retained_value_query(
    query: ValueQuery,
    value: FrozenJsonValue,
    spec: OutputSpec,
    *,
    visit: Visitor | None = None,
    inspect: Visitor | None = None,
) -> ProjectedPayload:
    if not matches(query.module, spec.modules) or not matches(
        query.module + "." + query.symbol, spec.functions
    ):
        return ProjectedPayload(
            None, status="unavailable", reason="Value excluded by output module/function selectors"
        )
    if value is None:
        return ProjectedPayload(None)
    unavailable = _joined_unavailable(spec)
    if unavailable:
        return unavailable
    if spec.max_items == 0:
        return ProjectedPayload(
            None, 1, 0, True, "unavailable", "Value omitted by output item limit"
        )
    selected, omitted = _fields(value, spec, inspect=inspect)
    return _finish(selected, 1, 1, omitted, visit=visit)


def project_payload(
    view: str, value: JsonValue, spec: OutputSpec, snapshot: JsonObject
) -> ProjectedPayload:
    """Compatibility entry for untrusted mutable data; own its boundary first."""
    retained = freeze_snapshot(snapshot)
    return project_retained_payload(view, freeze_snapshot(value), spec, _object(retained))


def project_value_query(query: ValueQuery, value: JsonValue, spec: OutputSpec) -> ProjectedPayload:
    return project_retained_value_query(query, freeze_snapshot(value), spec)


def module_for_qualname(name: str, snapshot: JsonObject) -> str:
    return retained_module_for_qualname(name, _object(freeze_snapshot(snapshot)))
