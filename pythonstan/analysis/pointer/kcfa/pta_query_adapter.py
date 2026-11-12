"""PtaQuery adapter for AI to query kcfa pointer analysis state.

This module provides an adapter that implements the AI's PtaQuery protocol
over the kcfa PointerAnalysisState, enabling AI to query points-to information,
field values, and class hierarchy (MRO).

Extended to support closure/scope queries:
- get_function_cell_vars: Get captured cell variable bindings
- get_function_global_vars: Get accessible global variable bindings
"""

from __future__ import annotations

import ast
from collections.abc import Callable
from typing import TYPE_CHECKING

from pythonstan.ir.ir_statements import IRCall

from .context import CallSite, Ctx

if TYPE_CHECKING:
    from .class_hierarchy import ClassHierarchyManager
    from .context import NativeContext, OrderedBindingToken, Scope
    from .context_selector import ContextSelector
    from .heap_model import Field
    from .object import AbstractObject
    from .points_to_set import PointsToSet
    from .state import PointerAnalysisState
    from .variable import FieldAccess, Variable

__all__ = ["PtaQueryAdapter"]


class PtaQueryAdapter:
    """Adapter implementing AI's PtaQuery protocol over kcfa state.

    This allows the AI engine to query the kcfa pointer analysis state
    for points-to information without depending on the full solver.
    """

    def __init__(
        self,
        state: PointerAnalysisState,
        class_hierarchy: ClassHierarchyManager | None = None,
        context_selector: ContextSelector | None = None,
        on_field_read: Callable[[Ctx[Variable | FieldAccess]], None] | None = None,
    ) -> None:
        """Initialize the adapter.

        Args:
            state: The kcfa pointer analysis state
            class_hierarchy: Optional class hierarchy manager for MRO queries
        """
        self._state = state
        self._class_hierarchy = class_hierarchy
        self._context_selector = context_selector
        self.on_field_read = on_field_read
        self.on_attribute_names_read: Callable[[AbstractObject], None] | None = None
        self._callsite_cache: dict[str, CallSite] = {}

    def _get_call_site(self, callsite_id: str) -> CallSite:
        if callsite_id in self._callsite_cache:
            return self._callsite_cache[callsite_id]
        statement = ast.parse("callsite()").body[0]
        if not isinstance(statement, ast.Expr) or not isinstance(statement.value, ast.Call):
            raise ValueError("Synthetic call-site parser did not produce a call")
        call_stmt = IRCall(statement.value)
        call_site = CallSite(statement=call_stmt, scope_name=callsite_id)
        self._callsite_cache[callsite_id] = call_site
        return call_site

    def points_to(
        self,
        var: Variable,
        scope: Scope,
        context: NativeContext,
    ) -> PointsToSet:
        """Get points-to set for a variable in given scope/context.

        Args:
            var: Variable to query
            scope: Scope containing the variable
            context: Analysis context

        Returns:
            PointsToSet of abstract objects the variable may point to
        """

        ctx_var = self._state.get_variable(scope, context, var)
        return self._state.get_points_to(ctx_var)

    def field_points_to(
        self,
        obj: AbstractObject,
        field: Field,
        scope: Scope | None,
        context: NativeContext | None,
    ) -> PointsToSet:
        """Get points-to set for an object's field.

        Args:
            obj: Abstract object
            field: Field key (attr/key/elem)
            scope: Current scope
            context: Analysis context

        Returns:
            PointsToSet of objects the field may point to
        """
        from .object import is_summary_object, summarize_object
        from .points_to_set import PointsToSet

        # A value-top receiver can select any field value; creating an empty
        # concrete slot for it would incorrectly turn unknown into bottom.
        # A legacy/custom object may expose this optional marker. Its truth
        # value is observed once here; no dynamic payload enters the model.
        top_marker: object = getattr(obj, "is_top_value", False)
        if top_marker:
            return PointsToSet.singleton(obj)

        # Summary objects: union over all concrete objects that map to this summary
        if is_summary_object(obj):
            summary_rep = summarize_object(obj)
            pts_acc = PointsToSet.empty()
            for concrete in self._state._heap.objects.values():
                if summarize_object(concrete) == summary_rep:
                    field_var = self._state.get_field(scope, context, concrete, field)
                    if self.on_field_read is not None:
                        self.on_field_read(field_var)
                    pts_acc = pts_acc.union(self._state.get_points_to(field_var))
            return pts_acc

        # Get field access variable for concrete object
        field_var = self._state.get_field(scope, context, obj, field)
        if self.on_field_read is not None:
            self.on_field_read(field_var)
        return self._state.get_points_to(field_var)

    def get_class_mro(self, cls_obj: AbstractObject) -> list[AbstractObject]:
        """Get MRO (method resolution order) for a class object.

        Args:
            cls_obj: Class object

        Returns:
            List of class objects in MRO order (most specific first)
        """
        from .mro_projection import project_class_mro

        callback = self.on_field_read

        def on_read(location: Ctx[Variable | FieldAccess] | None) -> None:
            # This adapter requests live reads, which always return a real
            # field identity. The general read-only MRO projection also admits
            # absent slots and must not manufacture an identity for them.
            if callback is not None and location is not None:
                callback(location)

        return project_class_mro(
            self._state, cls_obj, self._class_hierarchy, on_read if callback is not None else None
        )

    def known_attribute_names(
        self, obj: AbstractObject, scope: Scope, context: NativeContext
    ) -> frozenset[str]:
        """Return observed attribute names, never a proof of field absence."""
        from .heap_model import FieldKind
        from .object import InstanceObject

        owners = {obj}
        if isinstance(obj, InstanceObject):
            owners.update(self.get_class_mro(obj.class_obj))
        for owner in owners:
            if self.on_attribute_names_read is not None:
                self.on_attribute_names_read(owner)
        return frozenset(
            field.name
            for owner, field in self._state._field_accesses
            if owner in owners and field.kind == FieldKind.ATTRIBUTE and field.name is not None
        )

    def may_have_attr(
        self,
        obj: AbstractObject,
        attr_name: str,
        scope: Scope,
        context: NativeContext,
    ) -> bool:
        """Check if an object may have an attribute (for descriptor checks).

        Conservative implementation: returns True unless we can prove
        the attribute definitely doesn't exist.

        Args:
            obj: Object to check
            attr_name: Attribute name (e.g., "__get__", "__set__")
            scope: Current scope
            context: Analysis context

        Returns:
            True if the object may have the attribute
        """
        from .heap_model import attr

        # Check if field exists in state
        field_access = self._state.has_field(scope, context, obj, attr(attr_name))
        if field_access is not None:
            # Field exists, check if it has any points-to
            qualified_field = Ctx(obj.context, None, field_access)
            pts = self._state.get_points_to(qualified_field)
            if not pts.is_empty():
                return True

        # Conservative: assume it may have the attribute
        return True

    def select_call_context(
        self,
        callsite_id: str,
        argc: int,
        caller_ctx: NativeContext,
        receiver_obj: AbstractObject | None,
        params: tuple[AbstractObject, ...] | OrderedBindingToken | None = None,
    ) -> NativeContext | None:
        """Delegate call context selection to kcfa ContextSelector when available."""
        if self._context_selector is None:
            return None
        call_site = self._get_call_site(callsite_id)
        return self._context_selector.select_call_context(
            call_site,
            caller_ctx,
            receiver_obj,
            params=params,
        )

    def get_function_cell_vars(
        self,
        func_obj: AbstractObject,
    ) -> dict[str, frozenset[AbstractObject]]:
        """Get cell variable bindings captured by a function object.

        Cell variables are variables from enclosing scopes that are captured
        by the function for use in its body (closure variables).

        This method queries the KCFA heap model to find captured bindings
        stored in the function object's closure representation.

        Args:
            func_obj: Function object to query

        Returns:
            Dict mapping cell variable names to their captured points-to sets
        """
        from .heap_model import attr
        from .variable import VariableKind

        result: dict[str, frozenset[AbstractObject]] = {}

        # Get the function's internal scope to access cell_vars metadata
        scope = self._state.get_internal_scope(func_obj)
        if scope is None:
            return result

        # Try to get the IRFunc from the scope
        from pythonstan.ir.ir_statements import IRFunc

        ir_stmt = scope.stmt
        ir_func = ir_stmt if isinstance(ir_stmt, IRFunc) else None

        if ir_func is None:
            return result

        # Get cell_vars from the IRFunc
        cell_var_names = ir_func.cell_vars

        # For each cell var, try to find its binding in the function's closure
        # The closure is typically stored as a special field on the function object
        for var_name in cell_var_names:
            # Try to read from closure field (named __closure__ or similar)
            closure_field = attr(f"__cell_{var_name}__")
            try:
                pts = self.field_points_to(func_obj, closure_field, scope, None)
                if pts:
                    result[var_name] = frozenset(pts)
            except Exception:
                pass

            # Fallback: try to read from nonlocal tracking in state
            # This uses the KCFA variable tracking for cell/nonlocal vars
            from .variable import Variable

            cell_var = Variable(name=var_name, kind=VariableKind.CELL)
            try:
                ctx_var = self._state.get_variable(scope, None, cell_var)
                pts = self._state.get_points_to(ctx_var)
                if pts and not pts.is_empty():
                    result[var_name] = frozenset(pts) | result.get(var_name, frozenset())
            except Exception:
                pass

        return result

    def get_function_global_vars(
        self,
        func_obj: AbstractObject,
    ) -> dict[str, frozenset[AbstractObject]]:
        """Get global variable bindings accessible by a function object.

        Global variables are module-level names that the function may read/write.
        This method queries the KCFA state to find global variable bindings
        in the module namespace.

        Args:
            func_obj: Function object to query

        Returns:
            Dict mapping global variable names to their points-to sets
        """
        from .heap_model import attr
        from .variable import Variable, VariableKind

        result: dict[str, frozenset[AbstractObject]] = {}

        # Get the function's internal scope
        scope = self._state.get_internal_scope(func_obj)
        if scope is None:
            return result

        # Try to get the IRFunc from the scope
        from pythonstan.ir.ir_statements import IRFunc

        ir_stmt = scope.stmt
        ir_func = ir_stmt if isinstance(ir_stmt, IRFunc) else None

        if ir_func is None:
            return result

        # Get global_vars from the IRFunc
        global_var_names = ir_func.global_vars

        # Try to find the module object containing this function
        # This is a heuristic - look for MODULE kind objects
        from .object import AllocKind

        module_obj = None
        for obj in self._state._heap.objects.values():
            if hasattr(obj, "kind") and obj.kind == AllocKind.MODULE:
                # Check if this module contains our function
                # This is a simplified check
                module_obj = obj
                break

        for var_name in global_var_names:
            # Try to read from module attribute
            if module_obj is not None:
                field = attr(var_name)
                try:
                    pts = self.field_points_to(module_obj, field, scope, None)
                    if pts:
                        result[var_name] = frozenset(pts)
                except Exception:
                    pass

            # Fallback: read from global variable tracking
            global_var = Variable(name=var_name, kind=VariableKind.GLOBAL)
            try:
                ctx_var = self._state.get_variable(scope, None, global_var)
                pts = self._state.get_points_to(ctx_var)
                if pts and not pts.is_empty():
                    result[var_name] = frozenset(pts) | result.get(var_name, frozenset())
            except Exception:
                pass

        return result
