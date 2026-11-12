"""Read-only causal explanations over detached public analysis results.

Trace absence never proves semantic absence. This adapter cannot invoke engines
or add facts, and retains checked immutable JSON rather than live analysis state.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING

from pythonstan.analysis.contracts.json_data import (
    FrozenJsonObject,
    FrozenJsonValue,
    JsonObject,
    JsonValue,
    freeze_json_object,
    thaw_json,
    thaw_json_object,
    validate_json_object,
)

if TYPE_CHECKING:
    from pythonstan.results import AnalysisResult


def _object(value: FrozenJsonValue) -> FrozenJsonObject:
    if not isinstance(value, Mapping):
        raise TypeError("Inspector snapshot field must be a JSON object")
    return value


def _array(value: FrozenJsonValue) -> tuple[FrozenJsonValue, ...]:
    if not isinstance(value, tuple):
        raise TypeError("Inspector snapshot field must be a JSON array")
    return value


def _text(value: FrozenJsonValue) -> str:
    if not isinstance(value, str):
        raise TypeError("Inspector event identity must be a string")
    return value


def _unit(event: FrozenJsonObject) -> str | None:
    value = event.get("unit")
    return None if value is None else _text(value)


def _sequence(event: FrozenJsonObject) -> int:
    value = event.get("sequence", 0)
    if not isinstance(value, int):
        raise TypeError("Inspector event sequence must be an integer")
    return value


class FrozenResultInspector:
    def __init__(self, result: AnalysisResult | Mapping[str, object]) -> None:
        from pythonstan.results import AnalysisResult

        if isinstance(result, AnalysisResult):
            data = result.raw_snapshot()
        elif isinstance(result, Mapping):
            data = validate_json_object(result)
        else:
            raise TypeError("Inspector requires a detached AnalysisResult or JSON snapshot")
        self._data: FrozenJsonObject = freeze_json_object(data)

    def snapshot(self) -> JsonObject:
        return thaw_json_object(self._data)

    def _records(self) -> tuple[FrozenJsonObject, ...]:
        observation = self._data.get("events", {})
        records = observation.get("events", ()) if isinstance(observation, Mapping) else observation
        return tuple(_object(event) for event in _array(records))

    def events(
        self, *, kind: str | None = None, point: str | None = None, unit: str | None = None
    ) -> list[JsonObject]:
        selected: list[JsonObject] = []
        for event in self._records():
            if kind is not None and event.get("kind") != kind:
                continue
            if point is not None and event.get("point") != point:
                continue
            if unit is not None and event.get("unit") != unit:
                continue
            selected.append(thaw_json_object(event))
        return selected

    def explain(
        self, *, module: str, symbol: str, point: str = "module_exit", max_events: int = 256
    ) -> JsonObject:
        if point not in ("module_exit", "function_exit"):
            raise ValueError("Explanation point must be module_exit or function_exit")
        if type(max_events) is not int or max_events < 0:
            raise ValueError("max_events must be nonnegative")
        data = self._data
        if point == "module_exit":
            value = _object(_object(data.get("values", {})).get(module, {})).get(symbol)
            cell: str | None = f"global:{module}:{symbol}"
        else:
            value = _object(data.get("returns", {})).get(module + "." + symbol)
            cell = None
        observation = data.get("events", {})
        records = self._records()
        by_id = {_text(event["event_id"]): event for event in records if "event_id" in event}
        roots: list[str]
        if cell is not None:
            roots = [
                _text(event["event_id"])
                for event in records
                if event.get("kind") == "name_write"
                and _object(event.get("payload", {})).get("cell") == cell
            ]
            # Retain all alternatives at each unit's latest observed revision.
            revisions: dict[str | None, int] = {}
            for identifier in roots:
                event = by_id[identifier]
                key = _unit(event)
                revision = event.get("revision")
                if type(revision) is int:
                    revisions[key] = max(revisions.get(key, -1), revision)
            roots = [
                identifier
                for identifier in roots
                if not revisions
                or by_id[identifier].get("revision") == revisions.get(_unit(by_id[identifier]))
            ]
        else:
            qualname = module + "." + symbol
            roots = [
                _text(event["event_id"])
                for event in records
                if event.get("kind") == "summary_update"
                and _object(event.get("payload", {})).get("qualname") == qualname
            ]
        pending = list(roots)
        seen: set[str] = set()
        missing_parents: set[str] = set()
        while pending and len(seen) < max_events:
            identifier = pending.pop()
            if identifier in seen:
                continue
            retained = by_id.get(identifier)
            if retained is None:
                missing_parents.add(identifier)
                continue
            seen.add(identifier)
            pending.extend(_text(parent) for parent in _array(retained.get("parents", ())))
        chain = sorted((by_id[identifier] for identifier in seen), key=_sequence)
        partial = bool(
            pending
            or missing_parents
            or (
                isinstance(observation, Mapping)
                and (observation.get("truncated") or observation.get("errors"))
            )
        )
        source = [event["source"] for event in chain if event.get("source")]
        inputs = [event for event in chain if event.get("kind") == "name_read"]
        summaries = [event for event in chain if event.get("kind") == "summary_update"]
        dependencies = [event for event in chain if event.get("kind") == "call_dependency"]
        missing: list[JsonValue] = []
        if not roots:
            missing.append("no_observed_producer_for_query")
        if roots and not inputs:
            missing.append("no_retained_input_read_values")
        if value is None:
            missing.append("query_value_unavailable_in_snapshot")
        return {
            "schema_version": 1,
            "status": "observed_chain" if roots else "unavailable",
            "claim": "diagnostic_observation_only",
            "truncated": partial,
            "query": {"module": module, "symbol": symbol, "point": point},
            "analysis_status": {
                key: thaw_json(data.get(key))
                for key in (
                    "actual_engine",
                    "solver_status",
                    "coverage_status",
                    "proof_status",
                    "profile",
                )
            },
            "identity": {
                key: thaw_json(data.get(key))
                for key in ("run_id", "source_digest", "model_digest", "policy_digest")
            },
            "sources": thaw_json(tuple(source)),
            "inputs": thaw_json(tuple(inputs)),
            "summaries": thaw_json(tuple(summaries)),
            "dependencies": thaw_json(tuple(dependencies)),
            "chain": thaw_json(tuple(chain)),
            "projection": {
                "kind": "query_projection",
                "value": thaw_json(value),
                "producer_events": list[JsonValue](roots),
            },
            "missing": missing,
            "missing_parents": list[JsonValue](sorted(missing_parents)),
        }


def inspect_result(result: AnalysisResult | Mapping[str, object]) -> FrozenResultInspector:
    return FrozenResultInspector(result)
