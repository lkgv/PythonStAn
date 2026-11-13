"""Versioned, engine-independent abstract-value JSON records.

The value is a union of scalar facets and opaque reference identities. Reference
identities do not grant heap, presence, freshness, or must-alias authority.
Unknown extensions and omitted fields are detected by the decoder rather than
silently interpreted as an empty facet.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from .json_data import (
    JsonObject,
    JsonScalar,
    JsonValue,
    require_array,
    require_object,
    require_string,
    validate_json_object,
)

VALUE_SCHEMA_VERSION = 1
VALUE_WIRE_KIND = "abstract_value"
_FIELDS = frozenset(
    {
        "wire_kind",
        "value_schema_version",
        "constants",
        "tagged_constants",
        "objects",
        "types",
        "top",
        "top_sorts",
        "may_unbound",
        "bottom",
        "relations",
        "integer_interval",
        "string_patterns",
        "reference_info",
        "projection",
        "items",
        "slots",
    }
)
_REQUIRED = _FIELDS - {"integer_interval", "string_patterns", "items", "slots"}
_FACET_FIELDS = {
    "integer_interval": frozenset({"lower", "upper", "parity"}),
    "string_patterns": frozenset({"prefixes", "suffixes", "unknown", "conjunctions"}),
    "projection": frozenset({"complete", "omitted_fields", "truncated"}),
}


def scalar_kind(value: JsonScalar) -> str:
    if value is None:
        return "none"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "float"
    return "str"


def json_scalar(value: object) -> JsonScalar:
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    raise TypeError("Abstract scalar constants require a JSON scalar")


def reference_boundary() -> JsonObject:
    return {
        "scope": "enclosing_snapshot",
        "identity": "may_alias",
        "facts": {},
        "freshness": "not_exported",
        "presence": "not_exported",
    }


def canonical_value_document(data: object) -> JsonObject:
    """Own a full v1 record without consulting or mutating an analysis store."""
    document = validate_json_object(data)
    version = document.get("value_schema_version", VALUE_SCHEMA_VERSION)
    if type(version) is not int or version != VALUE_SCHEMA_VERSION:
        raise ValueError("Unsupported abstract value schema version")
    kind = document.get("wire_kind", VALUE_WIRE_KIND)
    if kind != VALUE_WIRE_KIND:
        raise ValueError("Unsupported abstract value wire kind")
    document["wire_kind"] = VALUE_WIRE_KIND
    document["value_schema_version"] = VALUE_SCHEMA_VERSION
    document.setdefault("constants", [])
    if "tagged_constants" not in document:
        document["tagged_constants"] = [
            {"type": scalar_kind(json_scalar(item)), "value": item}
            for item in require_array(document["constants"])
        ]
    for name in ("objects", "types", "top_sorts", "relations"):
        document.setdefault(name, [])
    document.setdefault("top", False)
    document.setdefault("may_unbound", "unbound" in require_array(document["types"]))
    document.setdefault(
        "bottom",
        not any(
            document[name]
            for name in ("constants", "objects", "types", "top_sorts", "top", "may_unbound")
        ),
    )
    document.setdefault("reference_info", reference_boundary())
    document.setdefault("projection", {"complete": True, "omitted_fields": []})
    validate_value_versions(document)
    return document


def encode_value_wire(
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
) -> JsonObject:
    """Produce the same union schema used by the domain's Value exporter."""
    sorts = set(top_sorts)
    retained: list[JsonValue] = []
    tagged: list[JsonValue] = []
    for raw in constants:
        value = json_scalar(raw)
        if isinstance(value, float) and not math.isfinite(value):
            sorts.add("float")
            continue
        retained.append(value)
        tagged.append({"type": scalar_kind(value), "value": value})
    type_names = set(types) | sorts | {scalar_kind(json_scalar(item)) for item in retained}
    if may_unbound:
        type_names.add("unbound")
    data: dict[str, object] = {
        "constants": retained,
        "tagged_constants": tagged,
        "objects": list(objects),
        "types": sorted(type_names),
        "top": top,
        "top_sorts": sorted(sorts),
        "may_unbound": may_unbound,
        "relations": list(relations),
    }
    if items is not None:
        data["items"] = list(items)
    if slots is not None:
        data["slots"] = dict(slots)
    return canonical_value_document(data)


def validate_value_versions(value: JsonValue) -> None:
    """Reject future major versions, including nested slot/item value records."""
    if isinstance(value, dict):
        if "value_schema_version" in value:
            version = value["value_schema_version"]
            if type(version) is not int or version != VALUE_SCHEMA_VERSION:
                raise ValueError("Unsupported abstract value schema version")
        if "wire_kind" in value and value["wire_kind"] != VALUE_WIRE_KIND:
            raise ValueError("Unsupported abstract value wire kind")
        for item in value.values():
            validate_value_versions(item)
    elif isinstance(value, list):
        for item in value:
            validate_value_versions(item)


@dataclass(frozen=True)
class DecodedValueWire:
    document: JsonObject
    complete: bool
    losses: tuple[str, ...]


def decode_value_wire(data: object) -> DecodedValueWire:
    """Validate a record; legacy or projected forms carry explicit losses."""
    document = validate_json_object(data)
    validate_value_versions(document)
    losses: list[str] = []
    if "value_schema_version" not in document:
        losses.append("legacy_schema")
    if "reference_info" not in document:
        losses.append("reference_context_not_exported")
    losses.extend("missing_field:" + name for name in sorted(_REQUIRED - document.keys()))
    if unknown := document.keys() - _FIELDS:
        losses.extend("unknown_field:" + name for name in sorted(unknown))
    for field, allowed in _FACET_FIELDS.items():
        if field in document:
            facet = require_object(document[field])
            losses.extend(
                "unknown_field:" + field + "." + name for name in sorted(facet.keys() - allowed)
            )
            if field != "projection":
                losses.extend(
                    "missing_field:" + field + "." + name for name in sorted(allowed - facet.keys())
                )
    projection = require_object(document.get("projection", {}))
    if projection.get("complete", True) is not True or projection.get("truncated", False):
        losses.append("projected_value")
    if projection.get("omitted_fields"):
        losses.append("omitted_value_fields")
    for name in ("top", "may_unbound", "bottom"):
        if name in document and type(document[name]) is not bool:
            raise TypeError(f"{name} requires a boolean")
    for name in ("objects", "types", "top_sorts"):
        for item in require_array(document.get(name, [])):
            require_string(item)
    for raw in require_array(document.get("tagged_constants", [])):
        tagged = require_object(raw)
        scalar = json_scalar(tagged["value"])
        if require_string(tagged["type"]) != scalar_kind(scalar):
            raise TypeError("Tagged constant kind disagrees with its value")
    if "constants" in document and "tagged_constants" in document:
        scalar_keys = {
            (scalar_kind(json_scalar(item)), repr(json_scalar(item)))
            for item in require_array(document["constants"])
        }
        tagged_keys = {
            (require_string(require_object(item)["type"]), repr(require_object(item)["value"]))
            for item in require_array(document["tagged_constants"])
        }
        if scalar_keys != tagged_keys:
            raise ValueError("Scalar and tagged constant presentations disagree")
    return DecodedValueWire(document, not losses, tuple(losses))
