"""Finite coverage and schema-boundary witnesses for the rebuilt value codec."""

from __future__ import annotations

import json

import pytest

from pythonstan.analysis.contracts.identity import ObjectId, ProgramPointId
from pythonstan.analysis.contracts.json_data import require_object
from pythonstan.analysis.contracts.value_wire import decode_value_wire
from pythonstan.analysis.contracts.wire import thaw_snapshot, value_snapshot
from pythonstan.analysis.domains.ints import IntLattice, Parity
from pythonstan.analysis.domains.relations import PartitionToken
from pythonstan.analysis.domains.strings import StrLattice
from pythonstan.analysis.domains.value_wire import decode_value, restore_value
from pythonstan.analysis.domains.values import Value


def _string_member(value: StrLattice, item: str) -> bool:
    return (
        value.has_unknown
        or item in value.consts
        or any(item.startswith(prefix) for prefix in value.prefixes)
        or any(item.endswith(suffix) for suffix in value.suffixes)
        or any(
            item.startswith(prefix) and item.endswith(suffix)
            for prefix, suffix in value.conjunctions
        )
    )


def test_pattern_union_retains_residual_and_reconstructs_both_components() -> None:
    value = Value(strings=StrLattice(frozenset({"z"}), frozenset({"a"}), frozenset(), False))
    wire = value.export()
    restored = restore_value(json.loads(json.dumps(wire)))
    assert restored.complete
    assert restored.value == value
    assert _string_member(restored.value.strings, "ab")
    assert _string_member(restored.value.strings, "z")
    assert not _string_member(restored.value.strings, "q")


def test_all_value_facets_roundtrip_without_heap_authority() -> None:
    value = Value(
        ints=IntLattice.from_interval(-4, 10, Parity.EVEN),
        strings=StrLattice(
            frozenset({"z"}), frozenset(), frozenset({"tail"}), False, frozenset({("pre", "post")})
        ),
        bools=frozenset({True, False}),
        floats=frozenset({-0.0, 1.5}),
        none=True,
        refs=frozenset({ObjectId("object:one")}),
        top_sorts=frozenset({"bytes"}),
        unbound=True,
        relations=frozenset(
            {
                PartitionToken(
                    ProgramPointId("point"),
                    "str",
                    "'read'",
                    ObjectId("handler:one"),
                    False,
                    "module.handler",
                )
            }
        ),
    )
    restored = restore_value(json.loads(json.dumps(value.export())))
    assert restored.value == value
    assert restored.reference_info["presence"] == "not_exported"
    assert restored.reference_info["freshness"] == "not_exported"
    assert restored.reference_info["facts"] == {}


@pytest.mark.parametrize(
    "value",
    [
        Value.bottom(),
        Value.unknown(),
        Value.constant(True),
        Value.constant(1),
        Value.constant(None),
        Value(top_sorts=frozenset({"int", "float"})),
    ],
)
def test_scalar_and_bottom_roundtrips_are_distinct(value: Value) -> None:
    assert decode_value(value.export()) == value


def test_engine_factory_and_domain_use_the_same_schema() -> None:
    factory = require_object(thaw_snapshot(value_snapshot(constants=(True, 1, "z", None))))
    domain = Value(
        ints=IntLattice.const(1), strings=StrLattice.const("z"), bools=frozenset({True}), none=True
    ).export()
    assert factory["value_schema_version"] == domain["value_schema_version"] == 1
    assert decode_value(factory) == decode_value(domain)
    assert factory["wire_kind"] == "abstract_value"


@pytest.mark.parametrize("number", [float("nan"), float("inf"), -float("inf")])
def test_nonfinite_factory_constants_produce_covering_float_top(number: float) -> None:
    wire = require_object(thaw_snapshot(value_snapshot(constants=(number,))))
    json.dumps(wire, allow_nan=False)
    assert wire["constants"] == []
    assert "float" in decode_value(wire).top_sorts


@pytest.mark.parametrize("field", ["items", "slots"])
def test_future_schema_is_rejected_even_when_nested(field: str) -> None:
    nested = {"wire_kind": "abstract_value", "value_schema_version": 2}
    wire = Value.constant(1).export()
    wire[field] = [nested] if field == "items" else {"item": nested}
    with pytest.raises(ValueError, match="schema version"):
        decode_value_wire(wire)


def test_unknown_extension_cannot_be_silently_discarded_as_closed() -> None:
    wire = Value.constant(1).export()
    wire["future_union_facet"] = {"possible": "additional values"}
    restored = restore_value(wire)
    assert not restored.complete and restored.value.top
    assert restored.value.ints.may_contain(1)


def test_projected_value_decodes_with_a_covering_residual() -> None:
    wire = Value.constant("known").export()
    wire["projection"] = {"complete": False, "omitted_fields": ["integer_interval"]}
    restored = restore_value(wire)
    assert not restored.complete and restored.value.top
    assert _string_member(restored.value.strings, "known")


def test_legacy_reference_is_explicitly_not_a_heap_fact() -> None:
    restored = restore_value({"objects": ["object:one"], "types": ["list"], "top": False})
    assert restored.value.refs == {"object:one"}
    assert not restored.complete
    assert restored.reference_info["identity"] == "may_alias"
    assert restored.reference_info["facts"] == {}


def test_relation_handler_identity_survives_a_different_display_name() -> None:
    token = PartitionToken(
        ProgramPointId("site"), "str", "'key'", ObjectId("runtime:17"), True, "pretty.function"
    )
    wire = Value(relations=frozenset({token}), top=True).export()
    row = require_object(wire["relations"][0])
    assert row["handler"] == "pretty.function"
    assert row["handler_id"] == "runtime:17"
    assert decode_value(wire).relations == {token}
