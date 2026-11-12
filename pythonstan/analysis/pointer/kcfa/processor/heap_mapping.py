"""Persistent own-namespace views, kept separate from inherited attribute reads.

These are weak may-value relations. They do not prove key presence, deletion,
snapshot timing, slots/custom descriptors or a custom ``__dict__`` replacement.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

from ..context import Scope
from ..object import AbstractObject
from ..pointer_flow_graph import PointerFlowEdge
from ..points_to_set import PointsToSet

if TYPE_CHECKING:
    from ..state import PointerAnalysisState

from ..allocation import canonical_constant
from ..heap_model import Field, FieldKind, attr, elem, key, unknown, wildcard_key
from ..object import AllocKind, AllocSite, DictObject, InstanceObject


class FieldCreatedObserver(Protocol):
    def field_created(self, scope: Scope | None, obj: AbstractObject, field: Field) -> None: ...


@runtime_checkable
class _NamespaceViewGetter(Protocol):
    def __call__(self, scope: Scope, owner: InstanceObject) -> object: ...


@runtime_checkable
class _MappingRelationAdder(Protocol):
    def __call__(
        self,
        scope: Scope,
        source: AbstractObject,
        target: AbstractObject,
        excluded: frozenset[str],
        index_sensitive: bool,
    ) -> object: ...


type _CopyRelation = tuple[Scope, AbstractObject, frozenset[str], bool]


class InstanceNamespaceViews:
    def __init__(self, state: PointerAnalysisState) -> None:
        self.state = state
        self.views: dict[AbstractObject, DictObject] = {}
        self.owners: dict[AbstractObject, AbstractObject] = {}
        self.scopes: dict[AbstractObject, Scope] = {}
        self.links: set[tuple[AbstractObject, Field, Field]] = set()

    def get(self, scope: Scope, owner: InstanceObject) -> DictObject:
        view = self.views.get(owner)
        if view is None:
            site = AllocSite(("instance_namespace", owner), AllocKind.DICT)
            view = DictObject(
                self.state.select_allocation_context(owner.context, site, identity="alias"), site
            )
            self.views[owner] = view
            self.owners[view] = owner
            self.scopes[owner] = scope
            self.state._heap.set_obj(scope, owner.context, site, view)
            self.state.obj_scope[view] = scope
            # Existing own slots and all subsequently created slots share the
            # same relation. Never enumerate logical inherited attributes here.
            for obj, field in list(self.state._field_accesses):
                if obj == owner:
                    self.field_created(scope, owner, field)
            self._link(owner, Field(FieldKind.OWN_UNKNOWN), wildcard_key())
        return view

    def field_created(self, scope: Scope | None, obj: AbstractObject, field: Field) -> None:
        if obj in self.views:
            if field.kind == FieldKind.OWN_ATTRIBUTE:
                assert field.name is not None
                self._link(obj, field, key(field.name))
            elif field.kind == FieldKind.OWN_UNKNOWN:
                self._link(obj, field, wildcard_key())
        elif obj in self.owners:
            owner = self.owners[obj]
            if field.kind == FieldKind.KEY and field.key_type is None:
                self._link(owner, Field(FieldKind.OWN_ATTRIBUTE, field.name), field)
            elif field.kind in {FieldKind.WILDCARD_KEY, FieldKind.UNKNOWN}:
                self._link(owner, Field(FieldKind.OWN_UNKNOWN), field)

    def _link(self, owner: AbstractObject, own_field: Field, mapping_field: Field) -> None:
        identity = (owner, own_field, mapping_field)
        if identity in self.links:
            return
        # Install the guard before recursive get_field creates the counterpart.
        self.links.add(identity)
        scope, view = self.scopes[owner], self.views[owner]
        own = self.state.get_field(scope, scope.context, owner, own_field)
        mapped = self.state.get_field(scope, scope.context, view, mapping_field)
        self.state._add_var_points_flow(own, mapped)
        self.state._add_var_points_flow(mapped, own)
        if (
            own_field.kind == FieldKind.OWN_ATTRIBUTE
            and mapping_field.kind == FieldKind.KEY
            and mapping_field.key_type is None
        ):
            from ..native_mapping import NATIVE_MAPPING_KEYS
            from ..pointer_flow_graph import GuardNode, NormalNode, PointerFlowEdge, PointerFlowKind
            from ..points_to_set import PointsToSet

            # A read can create an empty own slot. Publish its exact string
            # name only after actual may-values reach that slot, including
            # None and later writes through the live namespace view.
            name = canonical_constant(
                self.state,
                view.context,
                AllocSite(("instance_namespace_key", view, own_field.name), AllocKind.CONSTANT),
                own_field.name,
            )
            keys = self.state.get_field(scope, scope.context, view, NATIVE_MAPPING_KEYS)

            def stored_key(_edge: PointerFlowEdge, points: PointsToSet) -> PointsToSet:
                if points.is_empty():
                    return PointsToSet.empty()
                self.state._residual_effects[
                    (scope, view, mapping_field, "namespace_key_provenance")
                ] = {
                    "origin": str(view.alloc_site),
                    "context": str(scope.context),
                    "reason": "Own-slot may-values retain a namespace key; exact presence after deletion, snapshot timing and custom namespace behavior remain unproved",
                    "effects": [
                        "native_mapping_key_provenance",
                        "key_presence",
                        "namespace_mutation",
                        "may_raise",
                    ],
                }
                return PointsToSet.singleton(name)

            guard = GuardNode(stored_key)
            self.state._add_points_flow_edge(
                PointerFlowEdge(NormalNode(own), guard, PointerFlowKind.NORMAL)
            )
            self.state._add_points_flow_edge(
                PointerFlowEdge(guard, NormalNode(keys), PointerFlowKind.NORMAL)
            )
        aggregate = self.state.get_field(scope, scope.context, view, elem())
        self.state._add_var_points_flow(mapped, aggregate)
        # A key created through the live mapping must activate ordinary reads.
        if own_field.kind == FieldKind.OWN_ATTRIBUTE:
            assert own_field.name is not None
            logical = attr(own_field.name)
        else:
            logical = unknown()
        self.state.get_field(scope, scope.context, owner, logical)


def instance_namespace_view(
    state: PointerAnalysisState, scope: Scope, owner: AbstractObject
) -> DictObject:
    assert isinstance(owner, InstanceObject)
    manager = state.peek_instance_namespace_views()
    if manager is None:
        manager = state._instance_namespace_views = InstanceNamespaceViews(state)
    service_name = "get"
    getter: object = getattr(manager, service_name)
    if not isinstance(getter, _NamespaceViewGetter):
        raise TypeError("Instance namespace observer does not expose a view service")
    view = getter(scope, owner)
    if not isinstance(view, DictObject):
        raise TypeError("Instance namespace view service must return a Native dictionary")
    return view


class MappingFieldCopies:
    """One-way copy relations subscribe to field creation as well as values."""

    def __init__(self, state: PointerAnalysisState) -> None:
        self.state = state
        self.relations: dict[AbstractObject, set[_CopyRelation]] = {}
        self.links: set[tuple[AbstractObject, Field, _CopyRelation]] = set()

    def add(
        self,
        scope: Scope,
        source: AbstractObject,
        target: AbstractObject,
        excluded: frozenset[str],
        index_sensitive: bool,
    ) -> None:
        relation = (scope, target, excluded, index_sensitive)
        relations = self.relations.setdefault(source, set())
        if relation not in relations:
            # Guard nodes have identity equality. Install native metadata flows
            # only for a new relation, before replay can create more fields.
            relations.add(relation)
            from ..native_mapping import copy_native_mapping_keys

            copy_native_mapping_keys(self.state, scope, source, target, excluded)
        fields = {field for obj, field in self.state._field_accesses if obj == source}
        fields.add(wildcard_key())
        if not index_sensitive or not isinstance(source, DictObject):
            fields.add(elem())
        for field in fields:
            self.field_created(scope, source, field)

    def field_created(self, scope: Scope | None, source: AbstractObject, field: Field) -> None:
        for relation in tuple(self.relations.get(source, ())):
            relation_scope, target, excluded, index_sensitive = relation
            if field.kind == FieldKind.KEY:
                if field.key_type is None and field.name in excluded:
                    continue
                target_field = field
            elif (
                field.kind == FieldKind.WILDCARD_KEY
                or field.kind == FieldKind.ELEMENT
                and (not index_sensitive or not isinstance(source, DictObject))
            ):
                target_field = wildcard_key()
            else:
                continue
            identity = (source, field, relation)
            if identity in self.links:
                continue
            self.links.add(identity)
            source_slot = self.state.get_field(
                relation_scope, relation_scope.context, source, field
            )
            target_slot = self.state.get_field(
                relation_scope, relation_scope.context, target, target_field
            )
            aggregate = self.state.get_field(relation_scope, relation_scope.context, target, elem())
            self.state._add_var_points_flow(source_slot, target_slot)
            self.state._add_var_points_flow(source_slot, aggregate)


def copy_mapping_fields(
    state: PointerAnalysisState,
    scope: Scope,
    source: AbstractObject,
    target: AbstractObject,
    excluded: frozenset[str],
    index_sensitive: bool,
) -> None:
    if not isinstance(source, DictObject):
        state._residual_effects[(scope, source, target, "native_key_copy_provenance")] = {
            "origin": str(source.alloc_site),
            "context": str(scope.context),
            "reason": "Mapping copy from an opaque/custom source has no complete native key identity relation",
            "effects": [
                "native_mapping_key_provenance",
                "mapping_protocol",
                "may_invoke_callbacks",
                "may_raise",
            ],
        }
    manager = state.peek_mapping_field_copies()
    if manager is None:
        manager = state._mapping_field_copies = MappingFieldCopies(state)
    service_name = "add"
    adder: object = getattr(manager, service_name)
    if not isinstance(adder, _MappingRelationAdder):
        raise TypeError("Mapping field observer does not expose a copy service")
    adder(scope, source, target, excluded, index_sensitive)
