"""Query interface for pointer analysis results."""

from __future__ import annotations

from collections.abc import Iterable
from typing import (
    TYPE_CHECKING,
    Protocol,
)

from pythonstan.analysis.contracts.json_data import JsonObject
from pythonstan.ir.ir_statements import (
    IRAssign,
    IRCall,
    IRClass,
    IRCopy,
    IRFunc,
    IRLoadAttr,
    IRLoadSubscr,
    IRScope,
    IRStatement,
    IRStoreAttr,
)

if TYPE_CHECKING:
    from pythonstan.graph.call_graph.call_graph import AbstractCallGraph

    from .context import CallSite, Ctx, NativeContext, Scope
    from .heap_model import Field
    from .object import AbstractObject
    from .points_to_set import PointsToSet
    from .variable import Variable

__all__ = ["ISolverQuery"]


class ISolverQuery(Protocol):
    """Protocol for querying pointer analysis results."""

    def points_to_at(
        self,
        var_name: str,
        scope: IRScope,
        context: NativeContext | None = None,
    ) -> PointsToSet:
        """Get points-to set for a variable by name in given scope."""
        ...

    def points_to_of_target(
        self,
        stmt: IRAssign | IRCopy | IRLoadAttr | IRLoadSubscr | IRCall,
        scope: IRScope,
        context: NativeContext | None = None,
    ) -> PointsToSet:
        """Get points-to set for the LHS target of an assignment-like statement."""
        ...

    def load_attr_result(
        self,
        stmt: IRLoadAttr,
        scope: IRScope,
        context: NativeContext | None = None,
    ) -> PointsToSet:
        """Get points-to result of an attribute load: `x = obj.attr`."""
        ...

    def store_attr_value(
        self,
        stmt: IRStoreAttr,
        scope: IRScope,
        context: NativeContext | None = None,
    ) -> PointsToSet:
        """Get points-to of the value being stored: `obj.attr = value`."""
        ...

    def field_of_objects(
        self,
        base_pts: PointsToSet,
        attr_name: str,
    ) -> PointsToSet:
        """Get attr from all objects in a points-to set."""
        ...

    def subscript_of_objects(
        self,
        base_pts: PointsToSet,
        index: int | str | None = None,
    ) -> PointsToSet:
        """Get container element(s) from objects in points-to set."""
        ...

    def call_graph(self) -> AbstractCallGraph[Ctx[CallSite], Scope]:
        """Get the full call graph."""
        ...

    def callees_of(
        self,
        call_stmt: IRCall,
        scope: IRScope,
        context: NativeContext | None = None,
    ) -> set[AbstractObject]:
        """Get possible callee objects at a call site."""
        ...

    def callees_as_ir(
        self,
        call_stmt: IRCall,
        scope: IRScope,
        context: NativeContext | None = None,
    ) -> set[IRFunc | IRClass]:
        """Get possible callee IR scopes at a call site."""
        ...

    def callers_of(
        self,
        callee: IRFunc | IRClass,
    ) -> set[tuple[IRStatement, IRScope]]:
        """Get call sites that may invoke a function/class."""
        ...

    def receiver_objects(
        self,
        call_stmt: IRCall,
        scope: IRScope,
        context: NativeContext | None = None,
    ) -> PointsToSet:
        """Get receiver objects at a method call site."""
        ...

    def may_alias(
        self,
        var1: str,
        var2: str,
        scope: IRScope,
        context: NativeContext | None = None,
    ) -> bool:
        """Check if two variables in the same scope may alias."""
        ...

    def may_alias_field(
        self,
        var1: str,
        attr1: str,
        var2: str,
        attr2: str | None,
        scope: IRScope,
        context: NativeContext | None = None,
    ) -> bool:
        """Check if var1.attr1 may alias var2[.attr2]."""
        ...

    def get_class_mro(
        self,
        cls_obj: AbstractObject,
    ) -> list[AbstractObject]:
        """Get method resolution order for a class object."""
        ...

    def resolve_method(
        self,
        receiver_pts: PointsToSet,
        method_name: str,
    ) -> PointsToSet:
        """Resolve method by name on receiver objects following MRO."""
        ...

    def get_instance_class(
        self,
        instance_obj: AbstractObject,
    ) -> AbstractObject | None:
        """Get the class object of an instance."""
        ...

    def get_bound_receiver(
        self,
        method_obj: AbstractObject,
    ) -> AbstractObject | None:
        """Get the bound instance of a method object."""
        ...

    def get_captured_vars(
        self,
        func: IRFunc,
        context: NativeContext | None = None,
    ) -> dict[str, PointsToSet]:
        """Get cell/nonlocal variable bindings captured by a closure."""
        ...

    def may_have_attr(
        self,
        obj: AbstractObject,
        attr_name: str,
    ) -> bool:
        """Conservative check if object may have an attribute."""
        ...

    def list_elements(
        self,
        list_obj: AbstractObject,
    ) -> PointsToSet:
        """Get all elements in a list object (elem() field)."""
        ...

    def dict_keys(
        self,
        dict_obj: AbstractObject,
    ) -> PointsToSet:
        """Get all keys in a dict object (if tracked)."""
        ...

    def dict_values(
        self,
        dict_obj: AbstractObject,
    ) -> PointsToSet:
        """Get all values in a dict object (value() field)."""
        ...

    def dict_get(
        self,
        dict_obj: AbstractObject,
        key: str,
    ) -> PointsToSet:
        """Get value for specific key in dict (key(k) field if index-sensitive)."""
        ...

    def tuple_element(
        self,
        tuple_obj: AbstractObject,
        index: int,
    ) -> PointsToSet:
        """Get element at specific index in tuple (if index-sensitive)."""
        ...

    def set_elements(
        self,
        set_obj: AbstractObject,
    ) -> PointsToSet:
        """Get all elements in a set object (elem() field)."""
        ...

    def get_object_type(
        self,
        obj: AbstractObject,
    ) -> AbstractObject | None:
        """Get type (class) of an object."""
        ...

    def is_instance_of(
        self,
        obj: AbstractObject,
        cls: AbstractObject,
    ) -> bool:
        """Check if obj is instance of cls (considering inheritance)."""
        ...

    def objects_of_type(
        self,
        cls: AbstractObject,
    ) -> PointsToSet:
        """Get all instances of a class (including subclass instances)."""
        ...

    def get_reachable_scopes(self) -> set[IRScope]:
        """Get all IR scopes reachable from entry points."""
        ...

    def get_reachable_functions(self) -> set[IRFunc]:
        """Get all reachable function definitions."""
        ...

    def get_reachable_classes(self) -> set[IRClass]:
        """Get all reachable class definitions."""
        ...

    def is_reachable(
        self,
        scope: IRScope,
    ) -> bool:
        """Check if a scope is reachable."""
        ...

    def get_statistics(self) -> JsonObject:
        """Get analysis statistics (points-to set sizes, constraint counts, etc.)."""
        ...

    def get_all_objects(self) -> Iterable[AbstractObject]:
        """Iterate over all abstract objects in the heap."""
        ...

    def get_unknown_summary(self) -> dict[str, int]:
        """Get summary of unknown/unresolved flows."""
        ...

    def get_unknown_details(self) -> list[JsonObject]:
        """Get detailed report of unknown/unresolved flows."""
        ...

    def get_unknown_effect_events(self) -> list[JsonObject]:
        """Read semantic may-events without changing source coverage claims."""
        ...

    def get_residual_effects(self) -> list[dict[str, object]]:
        """Get explicit unresolved return/mutation/callback/exception obligations."""
        ...

    def get_semantic_status(self) -> JsonObject:
        """Separate constraint convergence from source/effect coverage evidence."""
        ...

    def points_to(self, var: Variable) -> PointsToSet:
        """DEPRECATED: Use points_to_at(var_name, scope, context)."""
        ...

    def get_field(self, obj: AbstractObject, field: Field) -> PointsToSet:
        """DEPRECATED: Use field_of_objects() or container-specific methods."""
        ...
