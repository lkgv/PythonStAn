"""Restore scalar/reference Value facets from the canonical JSON union.

Restoring reference identifiers never installs heap facts or must/fresh claims.
Unknown value extensions and output omissions produce a covering Top residual.
"""

from __future__ import annotations

from dataclasses import dataclass

from pythonstan.analysis.contracts.identity import ObjectId, ProgramPointId
from pythonstan.analysis.contracts.json_data import (
    JsonObject,
    JsonValue,
    require_array,
    require_object,
    require_string,
)
from pythonstan.analysis.contracts.value_wire import (
    decode_value_wire,
    json_scalar,
    reference_boundary,
)

from .ints import IntDomain, IntLattice, Parity
from .relations import PartitionToken
from .strings import StrLattice
from .values import Value


@dataclass(frozen=True)
class RestoredValue:
    value: Value
    complete: bool
    losses: tuple[str, ...]
    reference_info: JsonObject


def _strings(value: JsonValue) -> frozenset[str]:
    return frozenset(require_string(item) for item in require_array(value))


def _optional_int(value: JsonValue) -> int | None:
    if value is None or type(value) is int:
        return value
    raise TypeError("Integer interval bounds require integers or null")


def _boolean(value: JsonValue) -> bool:
    if type(value) is not bool:
        raise TypeError("Expected a boolean value facet")
    return value


def restore_value(data: object) -> RestoredValue:
    wire = decode_value_wire(data)
    document = wire.document
    integers: set[int] = set()
    strings: set[str] = set()
    booleans: set[bool] = set()
    floating: set[float] = set()
    none = False
    constants = (
        [require_object(item)["value"] for item in require_array(document["tagged_constants"])]
        if "tagged_constants" in document
        else require_array(document.get("constants", []))
    )
    for raw in constants:
        value = json_scalar(raw)
        if value is None:
            none = True
        elif isinstance(value, bool):
            booleans.add(value)
        elif isinstance(value, int):
            integers.add(value)
        elif isinstance(value, float):
            floating.add(value)
        else:
            strings.add(value)
    int_value = IntLattice.from_consts(frozenset(integers))
    if "integer_interval" in document:
        interval = require_object(document["integer_interval"])
        bound = IntLattice.from_interval(
            _optional_int(interval.get("lower")),
            _optional_int(interval.get("upper")),
            Parity(require_string(interval.get("parity", "top"))),
        )
        int_value = IntDomain(max_consts=max(1, len(integers))).join(int_value, bound)
    patterns = require_object(document.get("string_patterns", {}))
    conjunctions: set[tuple[str, str]] = set()
    for raw in require_array(patterns.get("conjunctions", [])):
        pair = require_array(raw)
        if len(pair) != 2:
            raise TypeError("String conjunctions require a prefix and suffix")
        conjunctions.add((require_string(pair[0]), require_string(pair[1])))
    string_value = StrLattice(
        frozenset(strings),
        _strings(patterns.get("prefixes", [])),
        _strings(patterns.get("suffixes", [])),
        _boolean(patterns.get("unknown", False)),
        frozenset(conjunctions),
    )
    refs = frozenset(ObjectId(item) for item in _strings(document.get("objects", [])))
    sorts = set(_strings(document.get("top_sorts", [])))
    types = _strings(document.get("types", []))
    # Legacy factory records can advertise an unknown sort without a facet.
    represented = {"unbound"}
    if not int_value.is_bottom:
        represented.add("int")
    if not string_value.is_bottom():
        represented.add("str")
    if booleans:
        represented.add("bool")
    if floating:
        represented.add("float")
    if none:
        represented.add("none")
    if not refs:
        sorts.update(types - represented)
    relations: set[PartitionToken] = set()
    for raw in require_array(document.get("relations", [])):
        row = require_object(raw)
        handler = require_string(row.get("handler_id", row.get("handler")))
        display = require_string(row.get("handler", handler))
        relations.add(
            PartitionToken(
                ProgramPointId(require_string(row["point"])),
                require_string(row["key_type"]),
                require_string(row["key"]),
                ObjectId(handler),
                _boolean(row.get("partitioned", True)),
                None if display == handler else display,
            )
        )
    incomplete = any(
        loss.startswith(("unknown_field:", "missing_field:"))
        or loss in {"projected_value", "omitted_value_fields"}
        for loss in wire.losses
    )
    result = Value(
        ints=int_value,
        strings=string_value,
        bools=frozenset(booleans),
        floats=frozenset(floating),
        none=none,
        refs=refs,
        top_sorts=frozenset(sorts),
        top=_boolean(document.get("top", False)) or incomplete,
        unbound=_boolean(document.get("may_unbound", "unbound" in types)) or incomplete,
        relations=frozenset(relations),
    )
    if document.get("bottom") is False and result.is_bottom():
        result = Value.unknown()
    return RestoredValue(
        result,
        wire.complete,
        wire.losses,
        require_object(document.get("reference_info", reference_boundary())),
    )


def decode_value(data: object) -> Value:
    """Convenience restoration; reference context remains external and untrusted."""
    return restore_value(data).value
