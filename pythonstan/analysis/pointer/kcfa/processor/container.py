from __future__ import annotations

from typing import TYPE_CHECKING, override

from ..constraints import (
    LoadConstraint,
    LoadSubscrConstraint,
    StoreConstraint,
    StoreSubscrConstraint,
)
from ..heap_model import elem, key
from ..native_mapping import NativeMappingProtocolConstraint, apply_native_mapping_protocol
from ..object import AllocKind, ConstantObject, DictObject, ListObject, SetObject, TupleObject
from ..points_to_set import PointsToSet
from ..variable import FieldAccess, Variable
from .processor import Processor

if TYPE_CHECKING:
    from ..constraints import AllocConstraint, Constraint
    from ..context import Ctx, NativeContext, Scope
    from ..solver import PointerSolver


class ContainerProcessor(Processor):
    """
    Handle container allocations and load/store of container elements.
    """

    def __init__(self, index_sensitive: bool = True) -> None:
        """
        Initialize container processor.
        """
        self.index_sensitive = index_sensitive

    @override
    def handle_allocation(
        self,
        solver: PointerSolver,
        target: Ctx[Variable | FieldAccess],
        scope: Scope,
        context: NativeContext,
        c: AllocConstraint,
    ) -> bool:
        state = solver.state

        obj: ListObject | TupleObject | DictObject | SetObject | None = None
        heap_context = state.select_allocation_context(context, c.alloc_site)

        if c.alloc_site.kind == AllocKind.LIST:
            obj = self._alloc_list(scope, heap_context, c)

        elif c.alloc_site.kind == AllocKind.TUPLE:
            obj = self._alloc_tuple(scope, heap_context, c)

        elif c.alloc_site.kind == AllocKind.DICT:
            obj = self._alloc_dict(scope, heap_context, c)

        elif c.alloc_site.kind == AllocKind.SET:
            obj = self._alloc_set(scope, heap_context, c)

        if obj is not None:
            state._heap.set_obj(scope, context, c.alloc_site, obj)
            state.obj_scope[obj] = scope
            solver.handle_new_points_to(target, scope, PointsToSet.singleton(obj))
            return True

        return False

    def _alloc_list(self, scope: Scope, context: NativeContext, c: AllocConstraint) -> ListObject:
        """Allocate list object."""
        return ListObject(context, c.alloc_site)

    def _alloc_tuple(self, scope: Scope, context: NativeContext, c: AllocConstraint) -> TupleObject:
        """Allocate tuple object."""
        return TupleObject(context, c.alloc_site)

    def _alloc_dict(self, scope: Scope, context: NativeContext, c: AllocConstraint) -> DictObject:
        """Allocate dict object."""
        return DictObject(context, c.alloc_site)

    def _alloc_set(self, scope: Scope, context: NativeContext, c: AllocConstraint) -> SetObject:
        """Allocate set object."""
        return SetObject(context, c.alloc_site)

    @override
    def handle_constraint(
        self,
        solver: PointerSolver,
        target: Ctx[Variable | FieldAccess],
        scope: Scope,
        constraint: Constraint,
        pts: PointsToSet,
    ) -> bool:
        if isinstance(constraint, NativeMappingProtocolConstraint):
            apply_native_mapping_protocol(solver, scope, constraint)
            return True
        if isinstance(constraint, LoadSubscrConstraint):
            return self._apply_load_subscr(solver, scope, target, constraint, pts)
        if isinstance(constraint, StoreSubscrConstraint):
            return self._apply_store_subscr(solver, scope, target, constraint, pts)
        return False

    def _apply_load_subscr(
        self,
        solver: PointerSolver,
        scope: Scope,
        variable: Ctx[Variable | FieldAccess],
        c: LoadSubscrConstraint,
        pts: PointsToSet,
    ) -> bool:
        """Apply load constraint: target = base[index].

        Finite dictionary keys use a key field plus the wildcard-write field.
        Unknown indices retain the aggregate element read. Sequence reads keep
        their aggregate fallback because mutations may shift positions.
        """
        if not self.index_sensitive:
            solver.add_constraint(scope, scope.context, LoadConstraint(c.base, elem(), c.target))
            return True
        for index_obj in pts:
            if isinstance(index_obj, ConstantObject) and isinstance(
                index_obj.value, (str, bytes, int, float, complex, type(None))
            ):
                field = key(index_obj.value)
                solver.add_constraint(
                    scope, scope.context, LoadConstraint(c.base, field, c.target, index=c.index)
                )
            else:
                solver.add_constraint(
                    scope, scope.context, LoadConstraint(c.base, elem(), c.target)
                )
        return True

    def _apply_store_subscr(
        self,
        solver: PointerSolver,
        scope: Scope,
        variable: Ctx[Variable | FieldAccess],
        c: StoreSubscrConstraint,
        pts: PointsToSet,
    ) -> bool:
        """Apply store constraint: base[index] = source."""

        if not self.index_sensitive:
            solver.add_constraint(scope, scope.context, StoreConstraint(c.base, elem(), c.source))
            return True
        _state = solver.state
        unknown_index = False
        stored_elem = False
        for index_obj in pts:
            if isinstance(index_obj, ConstantObject) and isinstance(
                index_obj.value, (str, bytes, int, float, complex, type(None))
            ):
                field = key(index_obj.value)
                solver.add_constraint(
                    scope, scope.context, StoreConstraint(c.base, field, c.source)
                )
                stored_elem = True
            else:
                unknown_index = True
        if unknown_index or stored_elem:
            solver.add_constraint(
                scope,
                scope.context,
                StoreConstraint(
                    c.base,
                    elem(),
                    c.source,
                    summary_only=not unknown_index,
                ),
            )
        return True

    @override
    def handle_new_constraint(
        self, solver: PointerSolver, scope: Scope, constraint: Constraint
    ) -> bool:
        state = solver.state
        if isinstance(constraint, NativeMappingProtocolConstraint):
            for variable in (constraint.base, constraint.index):
                solver._register_dynamic_constraint(
                    scope, state.get_variable(scope, scope.context, variable), constraint
                )
            return True
        if isinstance(constraint, LoadSubscrConstraint):
            index = state.get_variable(scope, scope.context, constraint.index)
            solver._register_dynamic_constraint(scope, index, constraint)
            return True
        if isinstance(constraint, StoreSubscrConstraint):
            index = state.get_variable(scope, scope.context, constraint.index)
            solver._register_dynamic_constraint(scope, index, constraint)
            return True
        return False
