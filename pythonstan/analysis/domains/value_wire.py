"""Restore scalar/reference Value facets from the canonical JSON union.

Restoring reference identifiers never installs heap facts or must/fresh claims.
Incomplete records expose diagnostic retained facets and explicit losses; they
cannot be consumed as whole Values without their missing facets or context.
"""

from __future__ import annotations

import math
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
    retained_value: Value
    complete: bool
    losses: tuple[str, ...]
    reference_info: JsonObject

    def require_value(self) -> Value:
        """An incomplete fragment cannot enter an analysis as a whole Value."""
        if not self.complete:
            raise ValueError("Incomplete abstract value requires its missing context/facets")
        return self.retained_value

    @property
    def value(self) -> Value:
        return self.require_value()


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
    zero_signs: set[float] = set()
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
            if value == 0.0:
                zero_signs.add(math.copysign(1.0, value))
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
    if len(zero_signs) > 1:
        floating.clear()
        sorts.add("float")
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
        if not {"point", "key_type", "key", "handler_id"} <= row.keys():
            continue
        handler = require_string(row["handler_id"])
        display = require_string(row.get("handler", handler))
        display_name: str | None
        if "display_name" in row:
            display_name = (
                None if row["display_name"] is None else require_string(row["display_name"])
            )
        else:
            display_name = None if display == handler else display
        relations.add(
            PartitionToken(
                ProgramPointId(require_string(row["point"])),
                require_string(row["key_type"]),
                require_string(row["key"]),
                ObjectId(handler),
                _boolean(row.get("partitioned", True)),
                display_name,
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
    if wire.complete and _boolean(document["bottom"]) != result.is_bottom():
        raise ValueError("Bottom marker contradicts the complete value union")
    return RestoredValue(
        result,
        wire.complete,
        wire.losses,
        require_object(document.get("reference_info", reference_boundary())),
    )


def decode_value(data: object) -> Value:
    """Restore only complete v1 Value data; heap authority is never installed."""
    return restore_value(data).require_value()
