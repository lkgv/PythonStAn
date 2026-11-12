"""Actual handler identity is independent of its stable public display label."""

from __future__ import annotations

from pythonstan.analysis.contracts.identity import ObjectId, ProgramPointId
from pythonstan.analysis.domains.relations import KeyValueRelation, PartitionToken
from pythonstan.analysis.domains.values import Value, ValueOperations


def test_same_display_name_keeps_distinct_handler_context_identities() -> None:
    point = ProgramPointId("point")
    first = PartitionToken.make(
        point, "read", ObjectId("function:one"), display_name="subject.handler"
    )
    second = PartitionToken.make(
        point, "read", ObjectId("function:two"), display_name="subject.handler"
    )
    assert first.handler != second.handler and first.identity != second.identity
    assert first.export()["handler"] == second.export()["handler"] == "subject.handler"


def test_handler_remapping_preserves_public_label_and_changes_actual_reference() -> None:
    token = PartitionToken.make(
        ProgramPointId("point"), "read", ObjectId("function:old"), display_name="subject.handler"
    )
    mapped = ValueOperations().remap(
        Value(relations=frozenset({token})),
        {ObjectId("function:old"): ObjectId("function:summary")},
    )
    current = next(iter(mapped.relations))
    assert current.handler == ObjectId("function:summary")
    assert current.display_name == "subject.handler"
    assert current.export()["handler"] == token.export()["handler"]
    assert current.identity != token.identity


def test_overflow_coarsening_retains_handler_display_pairs() -> None:
    point, handler = ProgramPointId("point"), ObjectId("function")
    relation = KeyValueRelation(
        frozenset(
            {
                PartitionToken.make(point, "read", handler, display_name="subject.first"),
                PartitionToken.make(point, "write", handler, display_name="subject.second"),
            }
        )
    )
    coarsened = relation.coarsen(1)
    assert len(coarsened.tokens) == 4
    assert {(token.handler, token.display_name) for token in coarsened.tokens} == {
        (handler, "subject.first"),
        (handler, "subject.second"),
    }
    assert not any(token.partitioned for token in coarsened.tokens)
    assert {token.export()["handler"] for token in coarsened.tokens} == {
        "subject.first",
        "subject.second",
    }
