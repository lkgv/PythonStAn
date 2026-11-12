"""Real plugin validation and canonical identity namespace controls."""

from __future__ import annotations

from dataclasses import dataclass
from typing import override

import pytest

from pythonstan.analysis.contracts.identity import CellId, ObjectId
from pythonstan.analysis.domains.base import Domain, DomainRegistry
from pythonstan.analysis.domains.reabstract import IdentityArena, IdentitySubstitution
from pythonstan.analysis.domains.values import Value
from pythonstan.compat.legacy_ai.domains.base import create_default_registry


@dataclass(frozen=True)
class Boundary:
    top: bool


@dataclass(frozen=True)
class Finite:
    values: frozenset[int]


type UnionElement = Boundary | Finite


class UnionProvider(Domain[UnionElement]):
    """Its finite representation class differs from both bottom and top."""

    @property
    @override
    def name(self) -> str:
        return "union-control"

    @override
    def element_types(self) -> tuple[type[UnionElement], ...]:
        return (Boundary, Finite)

    @override
    def bottom(self) -> UnionElement:
        return Boundary(False)

    @override
    def top(self) -> UnionElement:
        return Boundary(True)

    @override
    def join(self, a: UnionElement, b: UnionElement) -> UnionElement:
        if isinstance(a, Boundary):
            return a if a.top else b
        if isinstance(b, Boundary):
            return b if b.top else a
        return Finite(a.values | b.values)

    @override
    def meet(self, a: UnionElement, b: UnionElement) -> UnionElement:
        if isinstance(a, Boundary):
            return b if a.top else a
        if isinstance(b, Boundary):
            return a if b.top else b
        values = a.values & b.values
        return Finite(values) if values else self.bottom()

    @override
    def leq(self, a: UnionElement, b: UnionElement) -> bool:
        return self.join(a, b) == b


def test_registered_union_provider_preserves_nonboundary_variant() -> None:
    registry = DomainRegistry()
    registry.register(UnionProvider())
    registry.freeze("union-control-v1")
    registered = registry.get("union-control")
    assert registered is not None
    left, right = Finite(frozenset({1})), Finite(frozenset({2}))
    assert registered.join(left, right) == Finite(frozenset({1, 2}))
    assert registered.leq(left, registered.join(left, right))
    with pytest.raises(TypeError, match="Invalid element representation"):
        registered.join(left, "unvalidated-component")


def test_cells_and_objects_with_same_display_id_have_separate_aliases() -> None:
    arena = IdentityArena()
    old_object, new_object = ObjectId("same"), ObjectId("object-summary")
    old_cell, new_cell = CellId("same"), CellId("cell-summary")
    detached = IdentitySubstitution({old_object: new_object}, {old_cell: new_cell})
    epoch = arena.publish(
        arena.prepare(detached.object_aliases), cell_aliases=detached.cell_aliases
    )
    assert epoch == 1
    assert arena.resolve(old_object) == new_object
    assert arena.resolve_cell(old_cell) == new_cell
    assert Value.ref(old_object).refs == frozenset({old_object})
    assert arena.publish(dict(detached.object_aliases), cell_aliases=detached.cell_aliases) == epoch


def test_identity_substitution_detaches_mutable_caller_maps() -> None:
    old, new = ObjectId("old"), ObjectId("new")
    objects = {old: new}
    substitution = IdentitySubstitution(objects, {})
    objects.clear()
    assert substitution.object_aliases == {old: new}


@pytest.mark.parametrize("name", ["callable", "type", "descriptor"])
def test_existing_token_provider_settings_remain_visible(name: str) -> None:
    registered = create_default_registry().get(name)
    assert registered is not None
    assert registered.max_tokens == 20
