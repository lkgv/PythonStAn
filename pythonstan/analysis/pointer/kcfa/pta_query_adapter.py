"""PtaQuery adapter for AI to query kcfa pointer analysis state.

This module provides an adapter that implements the AI's PtaQuery protocol
over the kcfa PointerAnalysisState, enabling AI to query points-to information,
field values, and class hierarchy (MRO).
"""

from typing import List, TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from .state import PointerAnalysisState
    from .object import AbstractObject
    from .heap_model import Field
    from .variable import Variable
    from .context import AbstractContext, Scope
    from .class_hierarchy import ClassHierarchyManager
    from .points_to_set import PointsToSet

__all__ = ["PtaQueryAdapter"]


class PtaQueryAdapter:
    """Adapter implementing AI's PtaQuery protocol over kcfa state.
    
    This allows the AI engine to query the kcfa pointer analysis state
    for points-to information without depending on the full solver.
    """

    def __init__(
        self,
        state: 'PointerAnalysisState',
        class_hierarchy: Optional['ClassHierarchyManager'] = None,
    ):
        """Initialize the adapter.
        
        Args:
            state: The kcfa pointer analysis state
            class_hierarchy: Optional class hierarchy manager for MRO queries
        """
        self._state = state
        self._class_hierarchy = class_hierarchy

    def points_to(
        self,
        var: 'Variable',
        scope: 'Scope',
        context: 'AbstractContext',
    ) -> 'PointsToSet':
        """Get points-to set for a variable in given scope/context.
        
        Args:
            var: Variable to query
            scope: Scope containing the variable
            context: Analysis context
            
        Returns:
            PointsToSet of abstract objects the variable may point to
        """
        from .points_to_set import PointsToSet
        
        ctx_var = self._state.get_variable(scope, context, var)
        return self._state.get_points_to(ctx_var)

    def field_points_to(
        self,
        obj: 'AbstractObject',
        field: 'Field',
        scope: 'Scope',
        context: 'AbstractContext',
    ) -> 'PointsToSet':
        """Get points-to set for an object's field.
        
        Args:
            obj: Abstract object
            field: Field key (attr/key/elem)
            scope: Current scope
            context: Analysis context
            
        Returns:
            PointsToSet of objects the field may point to
        """
        from .points_to_set import PointsToSet
        
        # Get field access variable
        field_var = self._state.get_field(scope, context, obj, field)
        return self._state.get_points_to(field_var)

    def get_class_mro(self, cls_obj: 'AbstractObject') -> List['AbstractObject']:
        """Get MRO (method resolution order) for a class object.
        
        Args:
            cls_obj: Class object
            
        Returns:
            List of class objects in MRO order (most specific first)
        """
        if self._class_hierarchy is not None:
            try:
                return self._class_hierarchy.get_mro(cls_obj)
            except Exception:
                # Fall back to just the class itself if MRO fails
                pass
        return [cls_obj]

    def may_have_attr(
        self,
        obj: 'AbstractObject',
        attr_name: str,
        scope: 'Scope',
        context: 'AbstractContext',
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
            pts = self._state.get_points_to(field_access)
            if not pts.is_empty():
                return True
        
        # Conservative: assume it may have the attribute
        return True

