"""Canonical validated query records and their external wire adapters."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import ClassVar, Literal, assert_never

from pythonstan.analysis.contracts.json_data import JsonObject, require_string, validate_json_object
from pythonstan.config.errors import ConfigurationError

type ViewKind = Literal[
    "summary",
    "calls",
    "values",
    "returns",
    "effects",
    "diagnostics",
    "stats",
    "proof",
    "obligations",
    "outcomes",
    "relations",
    "plan",
    "events",
]
type ValuePoint = Literal["module_exit", "function_exit"]

VIEWS: tuple[ViewKind, ...] = (
    "summary",
    "calls",
    "values",
    "returns",
    "effects",
    "diagnostics",
    "stats",
    "proof",
    "obligations",
    "outcomes",
    "relations",
    "plan",
    "events",
)


def parse_view(value: object) -> ViewKind:
    match value:
        case "summary":
            return "summary"
        case "calls":
            return "calls"
        case "values":
            return "values"
        case "returns":
            return "returns"
        case "effects":
            return "effects"
        case "diagnostics":
            return "diagnostics"
        case "stats":
            return "stats"
        case "proof":
            return "proof"
        case "obligations":
            return "obligations"
        case "outcomes":
            return "outcomes"
        case "relations":
            return "relations"
        case "plan":
            return "plan"
        case "events":
            return "events"
        case _:
            raise ConfigurationError(f"Unknown query view: {value!r}")


def _value_point(value: object) -> ValuePoint:
    match value:
        case "module_exit":
            return "module_exit"
        case "function_exit":
            return "function_exit"
        case _:
            raise ConfigurationError("ValueQuery.point must be module_exit or function_exit")


def _nonempty_string(value: object, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise ConfigurationError(f"{name} must be a nonempty string")
    return value


@dataclass(frozen=True, init=False)
class ValueQuery:
    module: str
    symbol: str
    point: ValuePoint
    kind: ClassVar[Literal["value"]] = "value"

    def __init__(self, module: str, symbol: str, point: str = "module_exit") -> None:
        object.__setattr__(self, "module", _nonempty_string(module, "ValueQuery.module"))
        object.__setattr__(self, "symbol", _nonempty_string(symbol, "ValueQuery.symbol"))
        object.__setattr__(self, "point", _value_point(point))

    def to_dict(self) -> JsonObject:
        return {
            "kind": self.kind,
            "module": self.module,
            "symbol": self.symbol,
            "point": self.point,
        }


@dataclass(frozen=True, init=False)
class PropertyQuery:
    property: Literal["guarded_dispatch_return_relation"]
    point: str
    kind: ClassVar[Literal["property"]] = "property"

    def __init__(self, property: str, point: str = "all") -> None:
        if property != "guarded_dispatch_return_relation":
            raise ConfigurationError("Unsupported property query")
        object.__setattr__(self, "property", property)
        object.__setattr__(self, "point", _nonempty_string(point, "PropertyQuery.point"))

    def to_dict(self) -> JsonObject:
        return {"kind": self.kind, "property": self.property, "point": self.point}


@dataclass(frozen=True)
class ViewQuery:
    kind: ViewKind

    def __post_init__(self) -> None:
        parse_view(self.kind)

    def to_dict(self) -> JsonObject:
        return {"kind": self.kind}


type QuerySpec = ValueQuery | PropertyQuery | ViewQuery
type QueryInput = QuerySpec | str | Mapping[str, object]


def _check_fields(raw: JsonObject, *, allowed: set[str], required: set[str]) -> None:
    unknown = raw.keys() - allowed
    missing = required - raw.keys()
    if unknown:
        raise ConfigurationError(f"Unknown query fields: {sorted(unknown)}")
    if missing:
        raise ConfigurationError(f"Missing query fields: {sorted(missing)}")


def parse_query(raw: object) -> QuerySpec:
    """Normalize an external input once; validated records pass through unchanged."""
    if isinstance(raw, (ValueQuery, PropertyQuery, ViewQuery)):
        return raw
    if isinstance(raw, str):
        if raw.startswith("property:"):
            return PropertyQuery(raw.split(":", 1)[1])
        return ViewQuery(parse_view(raw))
    if not isinstance(raw, Mapping):
        raise ConfigurationError("Query must be a registered view or query object")
    try:
        data = validate_json_object(raw)
        _check_fields(
            data, allowed={"kind", "module", "symbol", "point", "property"}, required={"kind"}
        )
        kind = require_string(data["kind"])
        if kind in {"value", "return"}:
            _check_fields(
                data,
                allowed={"kind", "module", "symbol", "point"},
                required={"kind", "module", "symbol"},
            )
            point = require_string(
                data.get("point", "function_exit" if kind == "return" else "module_exit")
            )
            return ValueQuery(require_string(data["module"]), require_string(data["symbol"]), point)
        if kind == "property":
            _check_fields(
                data, allowed={"kind", "property", "point"}, required={"kind", "property"}
            )
            return PropertyQuery(
                require_string(data["property"]), require_string(data.get("point", "all"))
            )
        _check_fields(data, allowed={"kind"}, required={"kind"})
        return ViewQuery(parse_view(kind))
    except (TypeError, ValueError) as error:
        if isinstance(error, ConfigurationError):
            raise
        raise ConfigurationError(str(error)) from error


def parse_queries(raw: object) -> tuple[QuerySpec, ...]:
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        raise ConfigurationError("queries must be a sequence of registered query inputs")
    return tuple(parse_query(value) for value in raw)


def query_to_wire(query: QuerySpec) -> JsonObject:
    match query:
        case ValueQuery() | PropertyQuery() | ViewQuery():
            return query.to_dict()
        case _:
            assert_never(query)


def resolved_query_key(query: QuerySpec) -> str:
    return json.dumps(query_to_wire(query), sort_keys=True, separators=(",", ":"))


def normalize_query(query: object) -> JsonObject:
    """Compatibility endpoint for callers needing a JSON query description."""
    return query_to_wire(parse_query(query))


def query_key(query: object) -> str:
    """Compatibility key adapter; internal consumers use resolved_query_key."""
    return resolved_query_key(parse_query(query))
