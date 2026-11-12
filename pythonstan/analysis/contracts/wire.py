"""Deep immutable JSON data and full scalar-aware abstract value wire."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from types import MappingProxyType

from .json_data import FrozenJsonObject, FrozenJsonValue, JsonObject, JsonValue


def freeze_snapshot(value: object) -> FrozenJsonValue:
    """Deep-copy JSON data into immutable containers, rejecting live objects."""
    return _freeze(value, set())


def _freeze(value: object, active: set[int]) -> FrozenJsonValue:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("snapshot JSON numbers must be finite")
        return value
    if isinstance(value, Mapping):
        if id(value) in active:
            raise ValueError("snapshot JSON contains a reference cycle")
        result: dict[str, FrozenJsonValue] = {}
        active.add(id(value))
        try:
            for key, item in value.items():
                if not isinstance(key, str):
                    raise TypeError("snapshot mappings require string keys")
                result[key] = _freeze(item, active)
        finally:
            active.remove(id(value))
        return MappingProxyType(result)
    if isinstance(value, (tuple, list)):
        if id(value) in active:
            raise ValueError("snapshot JSON contains a reference cycle")
        active.add(id(value))
        try:
            return tuple(_freeze(item, active) for item in value)
        finally:
            active.remove(id(value))
    raise TypeError(f"non-JSON object cannot enter an engine snapshot: {type(value).__name__}")


def thaw_snapshot(value: object) -> JsonValue:
    """Return independent JSON data; reading never invokes an analysis."""
    return _thaw(value, set())


def _thaw(value: object, active: set[int]) -> JsonValue:
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    if isinstance(value, Mapping):
        if id(value) in active:
            raise ValueError("snapshot JSON contains a reference cycle")
        result: JsonObject = {}
        active.add(id(value))
        try:
            for key, item in value.items():
                if not isinstance(key, str):
                    raise TypeError("snapshot mappings require string keys")
                result[key] = _thaw(item, active)
        finally:
            active.remove(id(value))
        return result
    if isinstance(value, (tuple, list)):
        if id(value) in active:
            raise ValueError("snapshot JSON contains a reference cycle")
        active.add(id(value))
        try:
            return [_thaw(item, active) for item in value]
        finally:
            active.remove(id(value))
    raise TypeError(f"not snapshot data: {type(value).__name__}")


def value_snapshot(
    *,
    constants: Sequence[object] = (),
    objects: Sequence[str] = (),
    types: Sequence[str] = (),
    top: bool = False,
    relations: Sequence[object] = (),
    items: Sequence[object] | None = None,
    slots: Mapping[str, object] | None = None,
) -> FrozenJsonObject:
    """Canonical full-value wire; bool/int remain distinct in tagged members."""
    tagged: list[FrozenJsonValue] = []
    scalars: list[FrozenJsonValue] = []
    for value in constants:
        if value is None:
            kind = "none"
        elif isinstance(value, bool):
            kind = "bool"
        elif isinstance(value, int):
            kind = "int"
        elif isinstance(value, float):
            kind = "float"
        elif isinstance(value, str):
            kind = "str"
        else:
            raise TypeError("constant must be a JSON scalar")
        scalar = freeze_snapshot(value)
        scalars.append(scalar)
        tagged.append(MappingProxyType({"type": kind, "value": scalar}))
    result: dict[str, FrozenJsonValue] = {
        "constants": tuple(scalars),
        "tagged_constants": tuple(tagged),
        "objects": tuple(freeze_snapshot(item) for item in objects),
        "types": tuple(freeze_snapshot(item) for item in types),
        "top": freeze_snapshot(top),
        "bottom": not top and not constants and not objects and not types,
        "relations": tuple(freeze_snapshot(item) for item in relations),
    }
    if items is not None:
        result["items"] = tuple(freeze_snapshot(item) for item in items)
    if slots is not None:
        result["slots"] = freeze_snapshot(slots)
    return MappingProxyType(result)
