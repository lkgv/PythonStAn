"""Read-only selection of retained JSON result facts."""

from __future__ import annotations

import fnmatch
import json
from dataclasses import dataclass

from pythonstan.analysis.contracts.json_data import (
    JsonObject,
    JsonValue,
    require_array,
    require_object,
    require_string,
)

from .output import OutputSpec
from .queries import ValueQuery


@dataclass(frozen=True)
class ProjectedPayload:
    value: JsonValue
    total: int = 0
    count: int = 0
    truncated: bool = False
    status: str | None = None
    reason: str | None = None


def matches(value: str, patterns: tuple[str, ...]) -> bool:
    return not patterns or any(fnmatch.fnmatch(value, pattern) for pattern in patterns)


def module_for_qualname(name: str, snapshot: JsonObject) -> str:
    modules: set[str] = set()
    for value in require_array(snapshot.get("inventory", [])):
        row = require_object(value)
        module = row.get("module")
        if isinstance(module, str):
            modules.add(module)
    modules.update(require_object(snapshot.get("values", {})))
    candidates = [module for module in modules if name == module or name.startswith(module + ".")]
    return max(candidates, key=len) if candidates else name.rpartition(".")[0]


def _point_module(point: str) -> str:
    pieces = point.rsplit(":", 4)
    return pieces[0] if len(pieces) == 5 else ""


def _record_text(value: JsonValue) -> str:
    """Absent optional event attribution does not become a target-language name."""
    return value if isinstance(value, str) else ""


def _context_matches(context: JsonValue, patterns: tuple[str, ...]) -> bool:
    if not patterns:
        return True
    values = [context] if isinstance(context, str) else require_array(context if context else [])
    return matches(json.dumps(values, separators=(",", ":")), patterns) or any(
        matches(str(value), patterns) for value in values
    )


def _region_matches(point: str, row: JsonObject, spec: OutputSpec, snapshot: JsonObject) -> bool:
    if not spec.regions:
        return True
    plan = require_object(snapshot.get("plan", {}))
    decisions = require_object(plan.get("decisions", {}))
    decision = require_object(decisions.get(point, {}))
    return any(
        matches(str(value), spec.regions)
        for value in (
            row.get("region_id", ""),
            decision.get("region_id", ""),
            decision.get("qualname", ""),
            point,
        )
        if value
    )


def _record_matches(
    row: JsonObject, spec: OutputSpec, snapshot: JsonObject, point: str = ""
) -> bool:
    raw_payload = row.get("payload", {})
    payload = raw_payload if isinstance(raw_payload, dict) else {}
    function = _record_text(row.get("caller", row.get("qualname", payload.get("qualname", ""))))
    source = require_object(row.get("source") or {})
    point = point or _record_text(row.get("site", row.get("point", payload.get("point", ""))))
    module = _record_text(row.get("module", source.get("module", ""))) or _point_module(point)
    module = module or module_for_qualname(function, snapshot)
    if not matches(module, spec.modules):
        return False
    if spec.functions and not (
        matches(function, spec.functions)
        or matches(_record_text(row.get("callee", "")), spec.functions)
    ):
        return False
    if not _region_matches(point, row, spec, snapshot):
        return False
    return _context_matches(row.get("contexts", row.get("context", [])), spec.contexts)


def _fields(row: JsonValue, spec: OutputSpec, required: tuple[str, ...] = ()) -> JsonValue:
    if not spec.fields or not isinstance(row, dict):
        return row
    return {key: item for key, item in row.items() if key in spec.fields or key in required}


def _joined_unavailable(spec: OutputSpec) -> ProjectedPayload | None:
    if spec.contexts or spec.regions:
        return ProjectedPayload(
            None,
            status="unavailable",
            reason="Context/region-specific value snapshots were not retained; joined values cannot be split. Use retained outcomes for context records.",
        )
    return None


def _project_effects(value: JsonObject, spec: OutputSpec) -> ProjectedPayload:
    if spec.modules or spec.functions or spec.regions or spec.contexts:
        return ProjectedPayload(
            None,
            status="unavailable",
            reason="Joined effects lack a retained scope-specific frame and residual attribution",
        )
    selected = {key: item for key, item in value.items() if not spec.fields or key in spec.fields}
    total = sum(len(item) if isinstance(item, (list, dict)) else 1 for item in selected.values())
    remaining = spec.max_items
    bounded: JsonObject = {}
    for key, item in selected.items():
        if isinstance(item, list):
            items = item[:remaining]
            bounded[key] = items
            remaining -= len(items)
        elif isinstance(item, dict):
            fields = dict(list(item.items())[:remaining])
            bounded[key] = fields
            remaining -= len(fields)
        elif remaining:
            bounded[key] = item
            remaining -= 1
    count = spec.max_items - remaining
    return ProjectedPayload(bounded, total, count, count < total)


def _project_values(
    view: str, value: JsonObject, spec: OutputSpec, snapshot: JsonObject
) -> ProjectedPayload:
    unavailable = _joined_unavailable(spec)
    if unavailable:
        return unavailable
    records: list[tuple[str, str, JsonValue]] = []
    if view == "values":
        for module, values in sorted(value.items()):
            if matches(module, spec.modules):
                for symbol, row in sorted(require_object(values).items()):
                    if matches(module + "." + symbol, spec.functions):
                        records.append((module, symbol, row))
        selected: JsonObject = {}
        for module, symbol, row in records[: spec.max_items]:
            module_values = require_object(selected.setdefault(module, {}))
            module_values[symbol] = _fields(row, spec, ("top", "bottom", "top_sorts"))
    else:
        records = [
            (function, "", row)
            for function, row in sorted(value.items())
            if matches(function, spec.functions)
            and matches(module_for_qualname(function, snapshot), spec.modules)
        ]
        selected = {
            function: _fields(row, spec, ("top", "bottom", "top_sorts"))
            for function, _, row in records[: spec.max_items]
        }
    total = len(records)
    count = min(total, spec.max_items)
    return ProjectedPayload(selected, total, count, count < total)


def _project_outcomes(
    value: JsonObject, spec: OutputSpec, snapshot: JsonObject
) -> ProjectedPayload:
    records: list[tuple[str, JsonObject]] = []
    for function, rows in sorted(value.items()):
        if not matches(function, spec.functions) or not matches(
            module_for_qualname(function, snapshot), spec.modules
        ):
            continue
        for item in require_array(rows):
            row = require_object(item)
            context = row.get("context", [])
            if not _context_matches(context, spec.contexts):
                continue
            if spec.regions and not any(
                _region_matches(require_string(point), {}, spec, snapshot)
                for point in require_array(context)
            ):
                continue
            records.append((function, row))
    selected: JsonObject = {}
    for function, row in records[: spec.max_items]:
        require_array(selected.setdefault(function, [])).append(
            _fields(row, spec, ("kind", "exception", "context", "value_present"))
        )
    count = min(len(records), spec.max_items)
    return ProjectedPayload(selected, len(records), count, count < len(records))


def project_payload(
    view: str, value: JsonValue, spec: OutputSpec, snapshot: JsonObject
) -> ProjectedPayload:
    """Filter only retained facts, without executing analysis or changing claims."""
    if value is None:
        return ProjectedPayload(None)
    if view == "relations":
        unavailable = _joined_unavailable(spec)
        if unavailable:
            return unavailable
    if view == "proof":
        if spec.modules or spec.functions or spec.regions or spec.contexts:
            return ProjectedPayload(
                None,
                status="unavailable",
                reason="A global certificate cannot be relabeled as a scoped proof by output filtering",
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
        return ProjectedPayload(value, 1, 1)
    if view == "effects" and isinstance(value, dict):
        return _project_effects(value, spec)
    if view in ("values", "returns") and isinstance(value, dict):
        return _project_values(view, value, spec, snapshot)
    if view == "outcomes" and isinstance(value, dict):
        return _project_outcomes(value, spec, snapshot)
    if view == "plan" and isinstance(value, dict):
        records = [
            (point, require_object(row))
            for point, row in sorted(require_object(value.get("decisions", {})).items())
            if _record_matches(require_object(row), spec, snapshot, point)
        ]
        selected = {key: item for key, item in value.items() if key != "decisions"}
        selected["decisions"] = {
            point: _fields(row, spec) for point, row in records[: spec.max_items]
        }
        count = min(len(records), spec.max_items)
        return ProjectedPayload(selected, len(records), count, count < len(records))
    if view == "events" and isinstance(value, dict):
        rows = [
            require_object(row)
            for row in require_array(value.get("events", []))
            if _record_matches(require_object(row), spec, snapshot)
        ]
        selected = {key: item for key, item in value.items() if key != "events"}
        selected["events"] = [_fields(row, spec) for row in rows[: spec.max_items]]
        count = min(len(rows), spec.max_items)
        return ProjectedPayload(selected, len(rows), count, count < len(rows))
    if isinstance(value, list):
        selected_rows = [
            row
            for row in value
            if isinstance(row, dict)
            and _record_matches(row, spec, snapshot)
            or not isinstance(row, dict)
            and not (spec.modules or spec.functions or spec.regions or spec.contexts)
        ]
        selected_list = [_fields(row, spec) for row in selected_rows[: spec.max_items]]
        return ProjectedPayload(
            selected_list,
            len(selected_rows),
            len(selected_list),
            len(selected_list) < len(selected_rows),
        )
    if isinstance(value, dict):
        selected_object = require_object(_fields(value, spec))
        total = len(selected_object)
        selected_object = dict(list(selected_object.items())[: spec.max_items])
        return ProjectedPayload(
            selected_object, total, len(selected_object), len(selected_object) < total
        )
    return ProjectedPayload(value)


def project_value_query(query: ValueQuery, value: JsonValue, spec: OutputSpec) -> ProjectedPayload:
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
    return ProjectedPayload(_fields(value, spec, ("top", "bottom", "top_sorts")), 1, 1)
