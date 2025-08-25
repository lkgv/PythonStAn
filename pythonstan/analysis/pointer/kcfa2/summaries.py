"""Summaries for builtin and external functions.

This module provides function summaries for builtin functions and external
libraries that cannot be analyzed directly. Summaries specify the pointer
effects of these functions in a conservative manner.
"""

from typing import Dict, List, Optional, Any, Callable
from .model import AbstractLocation, AbstractObject, PointsToSet
from .context import Context
from .config import KCFAConfig

__all__ = [
    "BuiltinSummaryManager",
    "FunctionSummary", 
    "get_builtin_summary",
    "register_builtin_summary"
]


class FunctionSummary:
    """Summary of a function's pointer effects."""
    
    def __init__(self, name: str, handler: Optional[Callable] = None, conservative: bool = True):
        self.name = name
        self.handler = handler
        self.conservative = conservative
        
    def apply(self, target: Optional[str], args: List[str], ctx: Context, analysis: Any) -> None:
        """Apply the summary to an analysis state."""
        if self.handler:
            self.handler(target, args, ctx, analysis)
        elif self.conservative:
            self._apply_conservative(target, args, ctx, analysis)
            
    def _apply_conservative(self, target: Optional[str], args: List[str], ctx: Context, analysis: Any) -> None:
        """Apply conservative summary."""
        if target:
            # Create a conservative top object for the return value
            from .heap_model import make_object
            top_obj = make_object(f"conservative_{self.name}_ret", ctx)
            from .model import PointsToSet
            ret_pts = PointsToSet(frozenset([top_obj]))
            analysis._set_var_pts(ctx, target, ret_pts)
            
        # Conservatively assume all arguments may escape
        for arg in args:
            arg_pts = analysis._get_var_pts(ctx, arg)
            # Mark as potentially escaped (implementation detail)


class BuiltinSummaryManager:
    """Manager for builtin function summaries."""
    
    def __init__(self, config: Optional[KCFAConfig] = None):
        self.config = config or KCFAConfig()
        self._summaries: Dict[str, FunctionSummary] = {}
        self._initialize_builtins()
        
    def get_summary(self, function_name: str) -> Optional[FunctionSummary]:
        """Get summary for a function."""
        return self._summaries.get(function_name)
        
    def register_summary(self, summary: FunctionSummary) -> None:
        """Register a function summary."""
        self._summaries[summary.name] = summary
        
    def has_summary(self, function_name: str) -> bool:
        """Check if a function has a summary."""
        return function_name in self._summaries
        
    def _initialize_builtins(self) -> None:
        """Initialize summaries for Python builtin functions."""
        # Object construction functions
        self.register_summary(FunctionSummary("len", handler=self._handle_len))
        self.register_summary(FunctionSummary("iter", handler=self._handle_iter))
        self.register_summary(FunctionSummary("list", handler=self._handle_list_constructor))
        self.register_summary(FunctionSummary("tuple", handler=self._handle_tuple_constructor))
        self.register_summary(FunctionSummary("dict", handler=self._handle_dict_constructor))
        self.register_summary(FunctionSummary("set", handler=self._handle_set_constructor))
        
    def _handle_len(self, target: Optional[str], args: List[str], ctx: Context, analysis: Any) -> None:
        """Handle len() builtin - returns integer, no pointer effects."""
        if target and len(args) >= 1:
            # Create integer object for return value
            from .heap_model import make_object
            from .model import PointsToSet
            
            int_obj = make_object(f"builtin_len_ret_{hash((ctx, tuple(args)))}", ctx)
            ret_pts = PointsToSet(frozenset([int_obj]))
            analysis._set_var_pts(ctx, target, ret_pts)
        
    def _handle_iter(self, target: Optional[str], args: List[str], ctx: Context, analysis: Any) -> None:
        """Handle iter() builtin - creates iterator pointing to container elements."""
        if target and len(args) >= 1:
            from .heap_model import make_object, attr_key, elem_key
            from .model import PointsToSet
            
            # Create iterator object
            iter_obj = make_object(f"builtin_iter_{hash((ctx, tuple(args)))}", ctx)
            ret_pts = PointsToSet(frozenset([iter_obj]))
            analysis._set_var_pts(ctx, target, ret_pts)
            
            # Link iterator to container elements
            container_var = args[0]
            container_pts = analysis._get_var_pts(ctx, container_var)
            
            # Iterator points to elements of the container
            elem_field = elem_key()
            iter_value_field = attr_key("__iter_value")
            
            for container_obj in container_pts.objects:
                container_elem_pts = analysis._get_field_pts(container_obj, elem_field)
                analysis._set_field_pts(iter_obj, iter_value_field, container_elem_pts)
        
    def _handle_list_constructor(self, target: Optional[str], args: List[str], ctx: Context, analysis: Any) -> None:
        """Handle list() constructor."""
        if target:
            from .heap_model import make_object, elem_key
            from .model import PointsToSet
            
            # Create list object
            list_obj = make_object(f"builtin_list_{hash((ctx, tuple(args)))}", ctx)
            ret_pts = PointsToSet(frozenset([list_obj]))
            analysis._set_var_pts(ctx, target, ret_pts)
            
            # If given an iterable argument, copy its elements
            if len(args) >= 1:
                iterable_var = args[0]
                iterable_pts = analysis._get_var_pts(ctx, iterable_var)
                
                elem_field = elem_key()
                combined_elems = PointsToSet()
                
                # Collect elements from all possible iterables
                for iterable_obj in iterable_pts.objects:
                    iterable_elem_pts = analysis._get_field_pts(iterable_obj, elem_field)
                    combined_elems = combined_elems.join(iterable_elem_pts)
                
                # Set list elements
                analysis._set_field_pts(list_obj, elem_field, combined_elems)
        
    def _handle_tuple_constructor(self, target: Optional[str], args: List[str], ctx: Context, analysis: Any) -> None:
        """Handle tuple() constructor."""
        if target:
            from .heap_model import make_object, elem_key
            from .model import PointsToSet
            
            # Create tuple object
            tuple_obj = make_object(f"builtin_tuple_{hash((ctx, tuple(args)))}", ctx)
            ret_pts = PointsToSet(frozenset([tuple_obj]))
            analysis._set_var_pts(ctx, target, ret_pts)
            
            # If given an iterable argument, copy its elements
            if len(args) >= 1:
                iterable_var = args[0]
                iterable_pts = analysis._get_var_pts(ctx, iterable_var)
                
                elem_field = elem_key()
                combined_elems = PointsToSet()
                
                for iterable_obj in iterable_pts.objects:
                    iterable_elem_pts = analysis._get_field_pts(iterable_obj, elem_field)
                    combined_elems = combined_elems.join(iterable_elem_pts)
                
                analysis._set_field_pts(tuple_obj, elem_field, combined_elems)
        
    def _handle_dict_constructor(self, target: Optional[str], args: List[str], ctx: Context, analysis: Any) -> None:
        """Handle dict() constructor."""
        if target:
            from .heap_model import make_object, value_key, elem_key
            from .model import PointsToSet
            
            # Create dict object
            dict_obj = make_object(f"builtin_dict_{hash((ctx, tuple(args)))}", ctx)
            ret_pts = PointsToSet(frozenset([dict_obj]))
            analysis._set_var_pts(ctx, target, ret_pts)
            
            # If given an iterable argument, copy its values
            if len(args) >= 1:
                # Could be dict(other_dict) or dict(key_value_pairs)
                source_var = args[0]
                source_pts = analysis._get_var_pts(ctx, source_var)
                
                value_field = value_key()
                combined_values = PointsToSet()
                
                for source_obj in source_pts.objects:
                    # Try both value field (for dicts) and elem field (for sequences)
                    source_values = analysis._get_field_pts(source_obj, value_field)
                    source_elems = analysis._get_field_pts(source_obj, elem_key())
                    combined_values = combined_values.join(source_values).join(source_elems)
                
                analysis._set_field_pts(dict_obj, value_field, combined_values)
        
    def _handle_set_constructor(self, target: Optional[str], args: List[str], ctx: Context, analysis: Any) -> None:
        """Handle set() constructor."""
        if target:
            from .heap_model import make_object, elem_key
            from .model import PointsToSet
            
            # Create set object
            set_obj = make_object(f"builtin_set_{hash((ctx, tuple(args)))}", ctx)
            ret_pts = PointsToSet(frozenset([set_obj]))
            analysis._set_var_pts(ctx, target, ret_pts)
            
            # If given an iterable argument, copy its elements
            if len(args) >= 1:
                iterable_var = args[0]
                iterable_pts = analysis._get_var_pts(ctx, iterable_var)
                
                elem_field = elem_key()
                combined_elems = PointsToSet()
                
                for iterable_obj in iterable_pts.objects:
                    iterable_elem_pts = analysis._get_field_pts(iterable_obj, elem_field)
                    combined_elems = combined_elems.join(iterable_elem_pts)
                
                analysis._set_field_pts(set_obj, elem_field, combined_elems)


# Global summary manager instance
_builtin_manager = BuiltinSummaryManager()


def get_builtin_summary(function_name: str) -> Optional[FunctionSummary]:
    """Get builtin function summary."""
    return _builtin_manager.get_summary(function_name)


def register_builtin_summary(summary: FunctionSummary) -> None:
    """Register a builtin function summary."""
    _builtin_manager.register_summary(summary)