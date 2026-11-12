"""Checked JSON boundary types, preserving the existing snapshot operations."""

from __future__ import annotations

import math
from collections.abc import Mapping
from types import MappingProxyType
from typing import TypeGuard

from .wire import freeze_snapshot, thaw_snapshot

type JsonScalar = None | bool | int | float | str
type JsonValue = JsonScalar | list[JsonValue] | dict[str, JsonValue]
type JsonObject = dict[str, JsonValue]
type FrozenJsonValue = JsonScalar | tuple[FrozenJsonValue, ...] | Mapping[str, FrozenJsonValue]
type FrozenJsonObject = Mapping[str, FrozenJsonValue]


def validate_json(value: object) -> JsonValue:
    """Validate and copy an external JSON-shaped value into owned containers."""
    return _validate_json(value, active=set(), depth=0)


def _validate_json(value: object, *, active: set[int], depth: int) -> JsonValue:
    if depth > 128:
        raise ValueError("JSON data exceeds the supported nesting depth (128)")
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("JSON numbers must be finite")
        return value
    if isinstance(value, Mapping):
        if id(value) in active:
            raise ValueError("JSON data contains a reference cycle")
        result: JsonObject = {}
        active.add(id(value))
        try:
            for key, item in value.items():
                if not isinstance(key, str):
                    raise TypeError("JSON object keys must be strings")
                result[key] = _validate_json(item, active=active, depth=depth + 1)
        finally:
            active.remove(id(value))
        return result
    if isinstance(value, (tuple, list)):
        if id(value) in active:
            raise ValueError("JSON data contains a reference cycle")
        active.add(id(value))
        try:
            return [_validate_json(item, active=active, depth=depth + 1) for item in value]
        finally:
            active.remove(id(value))
    raise TypeError(f"Expected JSON data, got {type(value).__name__}")


def validate_json_object(value: object) -> JsonObject:
    result = validate_json(value)
    return require_object(result)


def _is_json(value: object) -> TypeGuard[JsonValue]:
    if value is None or isinstance(value, (str, bool, int)):
        return True
    if isinstance(value, float):
        return math.isfinite(value)
    if isinstance(value, dict):
        return all(isinstance(key, str) and _is_json(item) for key, item in value.items())
    return isinstance(value, list) and all(_is_json(item) for item in value)


def _is_frozen_json(value: object) -> TypeGuard[FrozenJsonValue]:
    if value is None or isinstance(value, (str, bool, int)):
        return True
    if isinstance(value, float):
        return math.isfinite(value)
    if isinstance(value, MappingProxyType):
        return all(isinstance(key, str) and _is_frozen_json(item) for key, item in value.items())
    return isinstance(value, tuple) and all(_is_frozen_json(item) for item in value)


def freeze_json(value: JsonValue) -> FrozenJsonValue:
    frozen = freeze_snapshot(value)
    if not _is_frozen_json(frozen):
        raise TypeError("Snapshot did not produce immutable JSON data")
    return frozen


def freeze_json_object(value: JsonObject) -> FrozenJsonObject:
    frozen = freeze_json(value)
    if not isinstance(frozen, Mapping):
        raise TypeError("Expected immutable JSON object")
    return frozen


def thaw_json(value: FrozenJsonValue) -> JsonValue:
    thawed = thaw_snapshot(value)
    if not _is_json(thawed):
        raise TypeError("Snapshot did not produce JSON data")
    return thawed


def thaw_json_object(value: FrozenJsonObject) -> JsonObject:
    return require_object(thaw_json(value))


def require_object(value: JsonValue) -> JsonObject:
    if not isinstance(value, dict):
        raise TypeError("Expected JSON object")
    return value


def require_array(value: JsonValue) -> list[JsonValue]:
    if not isinstance(value, list):
        raise TypeError("Expected JSON array")
    return value


def require_string(value: JsonValue) -> str:
    if not isinstance(value, str):
        raise TypeError("Expected JSON string")
    return value


def require_bool(value: JsonValue) -> bool:
    if not isinstance(value, bool):
        raise TypeError("Expected JSON boolean")
    return value


def require_int(value: JsonValue) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise TypeError("Expected JSON integer")
    return value


def require_number(value: JsonValue) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise TypeError("Expected JSON number")
    return float(value)
