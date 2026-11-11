"""Deep immutable JSON data and full scalar-aware abstract value wire."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import math
from types import MappingProxyType


def freeze_snapshot(value: object) -> object:
    """Deep-copy JSON data into immutable containers, rejecting live objects."""
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("snapshot JSON numbers must be finite")
        return value
    if isinstance(value, Mapping):
        if not all(isinstance(k, str) for k in value):
            raise TypeError("snapshot mappings require string keys")
        return MappingProxyType({k: freeze_snapshot(v) for k, v in value.items()})
    if isinstance(value, (tuple, list)):
        return tuple(freeze_snapshot(v) for v in value)
    raise TypeError(f"non-JSON object cannot enter an engine snapshot: {type(value).__name__}")


def thaw_snapshot(value: object) -> object:
    """Return independent JSON data; reading never invokes an analysis."""
    if isinstance(value, Mapping):
        return {k: thaw_snapshot(v) for k, v in value.items()}
    if isinstance(value, tuple):
        return [thaw_snapshot(v) for v in value]
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    if isinstance(value, list):
        return [thaw_snapshot(v) for v in value]
    raise TypeError(f"not snapshot data: {type(value).__name__}")


def value_snapshot(*, constants: Sequence[object] = (), objects: Sequence[str] = (),
                   types: Sequence[str] = (), top: bool = False,
                   relations: Sequence[object] = (),
                   items: Sequence[object] | None = None,
                   slots: Mapping[str, object] | None = None) -> Mapping[str, object]:
    """Canonical full-value wire; bool/int remain distinct in tagged members."""
    tagged = []
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
        tagged.append({"type": kind, "value": value})
    result = {
        "constants": list(constants), "tagged_constants": tagged,
        "objects": list(objects), "types": list(types), "top": top,
        "bottom": not top and not constants and not objects and not types,
        "relations": list(relations),
    }
    if items is not None:
        result["items"] = list(items)
    if slots is not None:
        result["slots"] = dict(slots)
    return freeze_snapshot(result)


