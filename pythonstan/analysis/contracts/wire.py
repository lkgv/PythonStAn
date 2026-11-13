"""Deep immutable JSON data and full scalar-aware abstract value wire."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from types import MappingProxyType

from .json_data import FrozenJsonObject, FrozenJsonValue, JsonObject, JsonValue
from .value_wire import encode_value_wire


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
    top_sorts: Sequence[str] = (),
    may_unbound: bool = False,
    relations: Sequence[object] = (),
    items: Sequence[object] | None = None,
    slots: Mapping[str, object] | None = None,
) -> FrozenJsonObject:
    """Canonical v1 union; bool/int tagging and nonfinite covering agree with Value."""
    result = freeze_snapshot(
        encode_value_wire(
            constants=constants,
            objects=objects,
            types=types,
            top=top,
            top_sorts=top_sorts,
            may_unbound=may_unbound,
            relations=relations,
            items=items,
            slots=slots,
        )
    )
    if not isinstance(result, Mapping):
        raise TypeError("Value wire did not freeze to an object")
    return result
