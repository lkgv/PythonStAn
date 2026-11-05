"""Builtin attribute function semantics for AI abstract interpretation.

This module encapsulates Python builtin getattr/setattr/hasattr semantics:
- getattr(obj, name, default?) -> attribute value with descriptor protocol
- setattr(obj, name, value) -> write with descriptor protocol
- hasattr(obj, name) -> conservative boolean

The interpreter delegates to BuiltinAttributeSemantics to keep value semantics
in the domains layer rather than the interpreter.
"""

from typing import (
    TYPE_CHECKING,
    Callable,
    FrozenSet,
    List,
    Optional,
    Tuple,
)

from .attributes import AttributeSemantics

if TYPE_CHECKING:
    from pythonstan.analysis.pointer.kcfa.object import AbstractObject
    from pythonstan.analysis.pointer.kcfa.context import AbstractContext, Scope
    from pythonstan.analysis.pointer.kcfa.heap_model import Field
    from ..state import AIState
    from ..value_domain import AbsVal
    from ..summary import SummaryAccumulator
    from ..api import PtaQuery, AnalysisBudget

__all__ = ["BuiltinAttributeSemantics"]


# Type alias for the callee invocation callback
InvokeCalleeFunc = Callable[
    [
        "AbstractObject",  # callee_obj
        List[Tuple[str, "AbsVal"]],  # arg_vals
        "SummaryAccumulator",  # summary
        Optional["AbsVal"],  # receiver_val
    ],
    "AbsVal",  # return value
]


class BuiltinAttributeSemantics:
    """Encapsulates Python builtin attribute function semantics.
    
    This class owns the logic for:
    - getattr(obj, name, default?) with full descriptor/hook protocol
    - setattr(obj, name, value) with full descriptor/hook protocol
    - hasattr(obj, name) with conservative boolean result
    
    The interpreter injects its invoke_callee method and budget so we can
    handle descriptor calls without circular imports.
    """

    def __init__(
        self,
        pta_query: Optional["PtaQuery"],
        caller_scope: Optional["Scope"],
        call_context: Optional["AbstractContext"],
        invoke_callee: InvokeCalleeFunc,
        budget: Optional["AnalysisBudget"],
    ):
        """Initialize builtin attribute semantics.
        
        Args:
            pta_query: PTA query interface for MRO and field lookups
            caller_scope: Scope that called this function
            call_context: Context at the call site
            invoke_callee: Callback to invoke callees (avoids interpreter import)
            budget: Analysis budget for caps (e.g., max_const_strings)
        """
        self.pta_query = pta_query
        self.caller_scope = caller_scope
        self.call_context = call_context
        self.invoke_callee = invoke_callee
        self.budget = budget
        # Create AttributeSemantics for delegation
        self._attr_semantics = AttributeSemantics(
            pta_query, caller_scope, call_context, invoke_callee
        )

    def handle_getattr(
        self,
        obj_addrs: FrozenSet["AbstractObject"],
        name_val: "AbsVal",
        default_val: Optional["AbsVal"],
        state: "AIState",
        summary: "SummaryAccumulator",
    ) -> "AbsVal":
        """Handle getattr(obj, name, default?) builtin.

        For constant attribute names (bounded by budget.max_const_strings):
        delegates to AttributeSemantics.read_attr for full descriptor/hook
        semantics.

        For non-enumerable names with prefix/suffix constraints:
        attempts to filter known attribute names from the receiver objects
        before falling back to the unknown bucket.

        Args:
            obj_addrs: Set of receiver objects
            name_val: Abstract value for the attribute name
            default_val: Optional default value if attribute is missing
            state: Current abstract state
            summary: Summary accumulator

        Returns:
            AbsVal representing the result of getattr
        """
        from pythonstan.analysis.pointer.kcfa.heap_model import unknown
        from ..value_domain import AbsVal

        const_names = name_val.get_const_strs()
        max_const_strings = 10  # default
        if self.budget is not None and hasattr(self.budget, "max_const_strings"):
            max_const_strings = self.budget.max_const_strings

        result_val = AbsVal.bottom()
        saw_havoc = False

        if const_names is not None and len(const_names) <= max_const_strings:
            # Enumerable attribute names: use full descriptor/hook semantics
            for attr_name in const_names:
                attr_result = self._attr_semantics.read_attr(
                    obj_addrs, attr_name, state, summary
                )
                result_val = result_val.join(attr_result)
        else:
            # Non-enumerable names: try candidate filtering first
            filtered_names = self._filter_known_attr_names(
                obj_addrs, name_val, state, max_const_strings
            )

            if filtered_names is not None and len(filtered_names) <= max_const_strings:
                # Filtered candidate set is small enough: use precise semantics
                for attr_name in filtered_names:
                    attr_result = self._attr_semantics.read_attr(
                        obj_addrs, attr_name, state, summary
                    )
                    result_val = result_val.join(attr_result)
            else:
                # Fall back to unknown bucket (no descriptor)
                for obj in obj_addrs:
                    unknown_val, unknown_havoc = state.read_field_value(
                        obj, unknown(), self.caller_scope, self.call_context
                    )
                    if unknown_havoc:
                        saw_havoc = True
                    result_val = result_val.join(unknown_val)
                    summary.add_read(frozenset({obj}), unknown())

        # Include default if provided
        if default_val is not None:
            result_val = result_val.join(default_val)

        if saw_havoc or result_val.is_bottom():
            result_val = result_val.join(AbsVal.top())

        return result_val

    def _filter_known_attr_names(
        self,
        obj_addrs: FrozenSet["AbstractObject"],
        name_val: "AbsVal",
        state: "AIState",
        max_candidates: int,
    ) -> Optional[FrozenSet[str]]:
        """Attempt to filter known attribute names using string constraints.

        Collects known attribute names from the receiver objects (from overlay,
        heap, and PTA) and filters them using the string lattice constraints.

        Args:
            obj_addrs: Set of receiver objects
            name_val: Abstract value for the attribute name (with string constraints)
            state: Current abstract state
            max_candidates: Maximum filtered candidates to return

        Returns:
            Filtered set of candidate attribute names, or None if filtering
            cannot help (too many candidates or string lattice is top)
        """
        # If string lattice is top, filtering won't help
        str_lattice = name_val.get_string_lattice()
        if str_lattice.is_top():
            return None

        # Collect known attribute names from all receiver objects
        known_names: set = set()
        for obj in obj_addrs:
            # Collect from overlay
            for (overlay_obj, field), _ in state.overlay.items():
                if overlay_obj == obj:
                    field_name = self._extract_attr_name(field)
                    if field_name is not None:
                        known_names.add(field_name)

            # Collect from PTA if available
            if self.pta_query is not None:
                try:
                    # Query known fields from PTA
                    pta_names = self._get_pta_field_names(obj)
                    known_names.update(pta_names)
                except Exception:
                    pass  # PTA query failed, continue with what we have

        if len(known_names) == 0:
            # No known names to filter
            return None

        # Apply string lattice filtering
        filtered = name_val.filter_string_candidates(frozenset(known_names))

        if len(filtered) == 0:
            # Filtering resulted in empty set - unusual, return None to use unknown
            return None

        if len(filtered) > max_candidates:
            # Too many candidates even after filtering
            return None

        return filtered

    def _extract_attr_name(self, field: "Field") -> Optional[str]:
        """Extract attribute name from a Field if it's an attr field."""
        # Field.kind == "attr" and has a name
        if hasattr(field, "kind") and hasattr(field, "name"):
            if getattr(field, "kind", None) == "attr":
                return getattr(field, "name", None)
        return None

    def _get_pta_field_names(self, obj: "AbstractObject") -> FrozenSet[str]:
        """Get known field names for an object from PTA.

        This queries the PTA for known attribute fields on the object.
        """
        # Note: This is a simplified implementation. The actual API depends
        # on the PTA interface. We'll collect attribute names from MRO.
        names: set = set()

        if self.pta_query is None:
            return frozenset()

        try:
            # Try to get class MRO and collect attribute names
            # For now, we cannot easily enumerate all fields from PTA.
            # If PTA provides such an API in the future, we would use it here.
            pass
        except Exception:
            pass

        return frozenset(names)

    def handle_setattr(
        self,
        obj_addrs: FrozenSet["AbstractObject"],
        name_val: "AbsVal",
        value_val: "AbsVal",
        state: "AIState",
        summary: "SummaryAccumulator",
    ) -> "AIState":
        """Handle setattr(obj, name, value) builtin.

        For constant attribute names (bounded by budget.max_const_strings):
        delegates to AttributeSemantics.write_attr for full descriptor/hook
        semantics.

        For non-enumerable names with prefix/suffix constraints:
        attempts to filter known attribute names from the receiver objects
        before falling back to the unknown bucket.

        Args:
            obj_addrs: Set of receiver objects
            name_val: Abstract value for the attribute name
            value_val: Value to write
            state: Current abstract state
            summary: Summary accumulator

        Returns:
            Updated AIState
        """
        from pythonstan.analysis.pointer.kcfa.heap_model import unknown

        const_names = name_val.get_const_strs()
        max_const_strings = 10  # default
        if self.budget is not None and hasattr(self.budget, "max_const_strings"):
            max_const_strings = self.budget.max_const_strings

        new_state = state

        if const_names is not None and len(const_names) <= max_const_strings:
            # Enumerable names: use full descriptor/hook semantics
            for attr_name in const_names:
                new_state = self._attr_semantics.write_attr(
                    obj_addrs, attr_name, value_val, new_state, summary
                )
        else:
            # Non-enumerable names: try candidate filtering first
            filtered_names = self._filter_known_attr_names(
                obj_addrs, name_val, state, max_const_strings
            )

            if filtered_names is not None and len(filtered_names) <= max_const_strings:
                # Filtered candidate set is small enough: use precise semantics
                for attr_name in filtered_names:
                    new_state = self._attr_semantics.write_attr(
                        obj_addrs, attr_name, value_val, new_state, summary
                    )
            else:
                # Fall back to unknown bucket (no descriptor semantics)
                if value_val.addrs:
                    summary.add_write(
                        frozenset(obj_addrs),
                        unknown(),
                        frozenset(value_val.addrs),
                    )
                for obj in obj_addrs:
                    new_state = new_state.write_field_value(obj, unknown(), value_val)

        return new_state

    def handle_hasattr(
        self,
        obj_addrs: FrozenSet["AbstractObject"],
        name_val: "AbsVal",
        summary: "SummaryAccumulator",
    ) -> "AbsVal":
        """Handle hasattr(obj, name) builtin.
        
        Conservatively returns unknown boolean. Records reads for dependency
        tracking when attribute names are constant.
        
        Args:
            obj_addrs: Set of receiver objects
            name_val: Abstract value for the attribute name
            summary: Summary accumulator
            
        Returns:
            AbsVal representing boolean result (always top)
        """
        from pythonstan.analysis.pointer.kcfa.heap_model import attr
        from ..value_domain import AbsVal

        const_names = name_val.get_const_strs()

        # Conservative: return unknown boolean
        # Could be more precise with must-not analysis
        result_val = AbsVal.bool_top()

        if const_names is not None:
            # Record reads for dependency
            for obj in obj_addrs:
                for attr_name in const_names:
                    summary.add_read(frozenset({obj}), attr(attr_name))

        return result_val
