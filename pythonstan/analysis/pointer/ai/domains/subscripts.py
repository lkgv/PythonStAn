"""Subscript access semantics for abstract interpretation.

This module encapsulates Python subscript access (indexing) semantics:
- Reading obj[idx] with constant-string and dynamic indices
- Writing obj[idx] = value with constant-string and dynamic indices
- Bucket management (key(k) for constants, elem() for all)
- Dispatch to DictSemantics for dict objects (Route A)

The interpreter delegates to SubscriptSemantics to keep subscript logic
in the domains layer rather than the interpreter.
"""

from dataclasses import dataclass
from typing import (
    TYPE_CHECKING,
    FrozenSet,
    Optional,
    Tuple,
)

if TYPE_CHECKING:
    from pythonstan.analysis.pointer.kcfa.object import AbstractObject
    from pythonstan.analysis.pointer.kcfa.context import AbstractContext, Scope
    from ..state import AIState
    from ..value_domain import AbsVal
    from ..summary import SummaryAccumulator
    from ..api import AnalysisBudget

__all__ = ["SubscriptSemantics", "SubscriptLoadResult"]

# Import DictSemantics for Route A dict dispatch
try:
    from .mapslots import DictSemantics
except ImportError:
    DictSemantics = None  # type: ignore


@dataclass
class SubscriptLoadResult:
    """Result of subscript load operation.

    Attributes:
        result_val: The loaded value
        saw_havoc: Whether havoc was encountered
    """

    result_val: "AbsVal"
    saw_havoc: bool


class SubscriptSemantics:
    """Encapsulates Python subscript access semantics.

    This class owns the logic for:
    - Reading from containers via obj[idx]
    - Writing to containers via obj[idx] = value
    - Bucket management for constant vs dynamic indices

    Policy:
    - Constant-string indices (up to max_const_strings) use key(k)
    - Always also read/write elem() for soundness with unknown indices
    - Havoc triggers top() for loaded values
    """

    def __init__(
        self,
        caller_scope: Optional["Scope"],
        call_context: Optional["AbstractContext"],
        budget: "AnalysisBudget",
    ):
        """Initialize subscript semantics.

        Args:
            caller_scope: Scope context for field reads
            call_context: Call context for field reads
            budget: Analysis budget for caps
        """
        self.caller_scope = caller_scope
        self.call_context = call_context
        self.budget = budget

    def load_subscr(
        self,
        obj_val: "AbsVal",
        index_val: Optional["AbsVal"],
        state: "AIState",
        summary: "SummaryAccumulator",
    ) -> SubscriptLoadResult:
        """Load value via subscript: obj[idx].

        Implements subscript load policy:
        1. For dict objects, dispatch to DictSemantics (Route A)
        2. If index is constant string(s) within cap, read from key(k)
        3. If index is constant int(s) within cap, read from key(str(i))
        4. If index has prefix/suffix constraints, filter known keys
        5. Always also read from elem() for soundness
        6. If havoc encountered, join with top()

        Args:
            obj_val: Abstract value representing the container
            index_val: Abstract value representing the index (None = unknown)
            state: Current abstract state
            summary: Summary accumulator

        Returns:
            SubscriptLoadResult with loaded value and havoc status
        """
        from pythonstan.analysis.pointer.kcfa.heap_model import elem, key
        from pythonstan.analysis.pointer.kcfa.object import AllocKind
        from ..value_domain import AbsVal

        result_val = AbsVal.bottom()
        saw_havoc = False

        # Separate dict objects from other containers
        dict_addrs = set()
        other_addrs = set()
        
        for obj in obj_val.addrs:
            kind = getattr(obj, "kind", None)
            is_dict = (
                kind == AllocKind.DICT or
                (hasattr(kind, "value") and kind.value == "dict")
            )
            if is_dict:
                dict_addrs.add(obj)
            else:
                other_addrs.add(obj)

        # Handle dict objects via DictSemantics (Route A)
        if dict_addrs and DictSemantics is not None and index_val is not None:
            dict_sem = DictSemantics(
                caller_scope=self.caller_scope,
                call_context=self.call_context,
                budget=self.budget,
            )
            dict_result = dict_sem.load_item(
                AbsVal.from_addrs(frozenset(dict_addrs)),
                index_val,
                state,
                summary,
            )
            result_val = result_val.join(dict_result.result_val)
            if dict_result.saw_havoc:
                saw_havoc = True

        # Handle non-dict objects with original logic
        if other_addrs:
            # Get constant strings from index if available
            index_strs: Optional[FrozenSet[str]] = None
            if index_val is not None:
                index_strs = index_val.get_const_strs()

            # Get constant ints from index if available (for tuple/list indexing)
            index_ints: Optional[FrozenSet[int]] = None
            if index_val is not None:
                int_lattice = index_val.get_int_lattice()
                index_ints = int_lattice.get_consts()

            max_const_str = getattr(self.budget, "max_const_strings", 4)
            max_const_int = getattr(self.budget, "max_const_ints", 4)

            for obj in other_addrs:
                keys_to_read: Optional[FrozenSet[str]] = None

                # 1. Read from constant string keys if available and within cap
                if index_strs is not None and len(index_strs) <= max_const_str:
                    keys_to_read = index_strs
                # 2. Read from constant int keys if available and within cap
                elif index_ints is not None and len(index_ints) <= max_const_int:
                    # Convert ints to strings (matching PTA translator's key(i))
                    keys_to_read = frozenset(str(i) for i in index_ints)
                elif index_val is not None:
                    # Try candidate filtering with known keys
                    known_keys = self._get_known_keys(obj, state)
                    if known_keys and not index_val.get_string_lattice().is_top():
                        filtered = index_val.filter_string_candidates(known_keys)
                        if filtered and len(filtered) <= max_const_str:
                            keys_to_read = filtered

                if keys_to_read is not None:
                    for idx in keys_to_read:
                        field_key = key(idx)
                        field_val, field_havoc = state.read_field_value(
                            obj, field_key, self.caller_scope, self.call_context
                        )
                        if field_havoc:
                            saw_havoc = True
                        result_val = result_val.join(field_val)
                        summary.add_read(frozenset({obj}), field_key)

                # Always also read elem() for soundness
                elem_val, elem_havoc = state.read_field_value(
                    obj, elem(), self.caller_scope, self.call_context
                )
                if elem_havoc:
                    saw_havoc = True
                result_val = result_val.join(elem_val)
                summary.add_read(frozenset({obj}), elem())

        # If havoc or bottom, join with top
        if saw_havoc or result_val.is_bottom():
            result_val = result_val.join(AbsVal.top())

        return SubscriptLoadResult(result_val=result_val, saw_havoc=saw_havoc)

    def _get_known_keys(
        self,
        obj: "AbstractObject",
        state: "AIState",
    ) -> FrozenSet[str]:
        """Get known string keys for a container object.

        Collects key(k) fields from overlay that are known for this object.
        """
        from pythonstan.analysis.pointer.kcfa.heap_model import key

        known: set = set()
        for (overlay_obj, field), _ in state.overlay.items():
            if overlay_obj == obj:
                # Check if field is a key field
                if hasattr(field, "kind") and hasattr(field, "name"):
                    if getattr(field, "kind", None) == "key":
                        key_name = getattr(field, "name", None)
                        if key_name is not None:
                            known.add(key_name)
        return frozenset(known)

    def store_subscr(
        self,
        obj_val: "AbsVal",
        index_val: Optional["AbsVal"],
        value_val: "AbsVal",
        state: "AIState",
        summary: "SummaryAccumulator",
    ) -> "AIState":
        """Store value via subscript: obj[idx] = value.

        Implements subscript store policy:
        1. For dict objects, dispatch to DictSemantics (Route A)
        2. If index is constant string(s) within cap, write to key(k)
        3. If index is constant int(s) within cap, write to key(str(i))
        4. If index has prefix/suffix constraints, filter known keys
        5. Always also write to elem() for soundness

        Args:
            obj_val: Abstract value representing the container
            index_val: Abstract value representing the index (None = unknown)
            value_val: Abstract value to store
            state: Current abstract state
            summary: Summary accumulator

        Returns:
            Updated AIState
        """
        from pythonstan.analysis.pointer.kcfa.heap_model import elem, key  # noqa: F401
        from pythonstan.analysis.pointer.kcfa.object import AllocKind

        new_state = state

        # Separate dict objects from other containers
        dict_addrs = set()
        other_addrs = set()
        
        for obj in obj_val.addrs:
            kind = getattr(obj, "kind", None)
            is_dict = (
                kind == AllocKind.DICT or
                (hasattr(kind, "value") and kind.value == "dict")
            )
            if is_dict:
                dict_addrs.add(obj)
            else:
                other_addrs.add(obj)

        # Handle dict objects via DictSemantics (Route A)
        if dict_addrs and DictSemantics is not None and index_val is not None:
            from ..value_domain import AbsVal as AV
            dict_sem = DictSemantics(
                caller_scope=self.caller_scope,
                call_context=self.call_context,
                budget=self.budget,
            )
            new_state = dict_sem.store_item(
                AV.from_addrs(frozenset(dict_addrs)),
                index_val,
                value_val,
                new_state,
                summary,
            )

        # Handle non-dict objects with original logic
        if other_addrs:
            # Get constant strings from index if available
            index_strs: Optional[FrozenSet[str]] = None
            if index_val is not None:
                index_strs = index_val.get_const_strs()

            # Get constant ints from index if available (for tuple/list indexing)
            index_ints: Optional[FrozenSet[int]] = None
            if index_val is not None:
                int_lattice = index_val.get_int_lattice()
                index_ints = int_lattice.get_consts()

            max_const_str = getattr(self.budget, "max_const_strings", 4)
            max_const_int = getattr(self.budget, "max_const_ints", 4)

            for obj in other_addrs:
                keys_to_write: Optional[FrozenSet[str]] = None

                # 1. Write to constant string keys if available and within cap
                if index_strs is not None and len(index_strs) <= max_const_str:
                    keys_to_write = index_strs
                # 2. Write to constant int keys if available and within cap
                elif index_ints is not None and len(index_ints) <= max_const_int:
                    # Convert ints to strings (matching PTA translator's key(i))
                    keys_to_write = frozenset(str(i) for i in index_ints)
                elif index_val is not None:
                    # Try candidate filtering with known keys
                    known_keys = self._get_known_keys(obj, new_state)
                    if known_keys and not index_val.get_string_lattice().is_top():
                        filtered = index_val.filter_string_candidates(known_keys)
                        if filtered and len(filtered) <= max_const_str:
                            keys_to_write = filtered

                if keys_to_write is not None:
                    for idx in keys_to_write:
                        if value_val.addrs:
                            summary.add_write(
                                frozenset({obj}),
                                key(idx),
                                frozenset(value_val.addrs),
                            )
                        new_state = new_state.write_field_value(obj, key(idx), value_val)

                # Always also write to elem() for soundness
                if value_val.addrs:
                    summary.add_write(
                        frozenset({obj}),
                        elem(),
                        frozenset(value_val.addrs),
                    )
                new_state = new_state.write_field_value(obj, elem(), value_val)

        return new_state
