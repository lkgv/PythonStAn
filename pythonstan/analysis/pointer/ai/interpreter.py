"""IR interpreter with transfer functions for abstract interpretation.

This module implements transfer functions for IR statements per ai_spec.md §7:
- IRAssign/IRCopy: variable assignment
- IRPhi: SSA phi nodes
- IRLoadAttr/IRStoreAttr: attribute access with descriptor semantics
- IRLoadSubscr/IRStoreSubscr: container subscription
- IRCall: function calls with builtin handling
- IRReturn: return value propagation
- Control flow: JumpIf*, Goto, Label
"""

import logging
from dataclasses import dataclass, field
from typing import (
    TYPE_CHECKING,
    Any,
    Callable,
    Dict,
    FrozenSet,
    List,
    Optional,
    Set,
    Tuple,
    Union,
)

from .state import AIState, AbsVal
from .heap import AIAddr, AbsObj, FieldStore, ObjKind, AddrSet
from .summary import SummaryAccumulator
from .domains.strings import StrLattice
from .domains.bool import BoolLattice

if TYPE_CHECKING:
    from pythonstan.ir.ir_statements import (
        IRStatement, IRAssign, IRCopy, IRPhi, IRLoadAttr, IRStoreAttr,
        IRLoadSubscr, IRStoreSubscr, IRCall, IRReturn, IRRaise,
        JumpIfFalse, JumpIfTrue, Goto, Label, IRPass, IRDel, IRAnno,
        IRFunc, IRClass, IRModule, IRScope,
    )
    from pythonstan.analysis.pointer.kcfa.object import AbstractObject
    from pythonstan.analysis.pointer.kcfa.heap_model import Field
    from pythonstan.analysis.pointer.kcfa.context import AbstractContext, Scope
    from .api import PtaQuery, AnalysisBudget

__all__ = ["IRInterpreter", "TransferResult"]

logger = logging.getLogger(__name__)


# =============================================================================
# TransferResult - Result of a transfer function
# =============================================================================

@dataclass
class TransferResult:
    """Result of applying a transfer function.
    
    A transfer function may produce:
    - One or more successor states (for branching)
    - Updates to the summary accumulator
    
    Attributes:
        states: List of (successor_label, state) pairs
        is_terminal: True if this ends the block (return/raise)
    """
    states: List[Tuple[Optional[str], AIState]] = field(default_factory=list)
    is_terminal: bool = False

    @classmethod
    def single(cls, state: AIState, next_label: Optional[str] = None) -> 'TransferResult':
        """Single successor state (normal flow)."""
        return cls(states=[(next_label, state)])

    @classmethod
    def branch(cls, true_state: AIState, false_state: AIState, 
               true_label: str, false_label: str) -> 'TransferResult':
        """Branching result for conditional jumps."""
        return cls(states=[
            (true_label, true_state),
            (false_label, false_state),
        ])

    @classmethod
    def terminal(cls) -> 'TransferResult':
        """Terminal result (return/raise)."""
        return cls(is_terminal=True)

    def is_empty(self) -> bool:
        """Check if no successors."""
        return len(self.states) == 0 and not self.is_terminal


# =============================================================================
# IRInterpreter - Transfer functions
# =============================================================================

class IRInterpreter:
    """Interpreter applying transfer functions for IR statements.
    
    Each transfer function takes:
    - stmt: The IR statement
    - state: Current abstract state
    - summary: Summary accumulator for writes/reads/effects
    
    And returns a TransferResult with successor states.
    """

    def __init__(
        self,
        pta_query: 'PtaQuery',
        budget: 'AnalysisBudget',
        caller_scope: Optional['Scope'] = None,
        call_context: Optional['AbstractContext'] = None,
        recursion_depth: int = 0,
    ):
        """Initialize interpreter.
        
        Args:
            pta_query: Interface to query PTA
            budget: Analysis budget limits
            caller_scope: Scope that called this function
            call_context: Context at the call site
            recursion_depth: Current recursion depth for nested AI calls
        """
        self.pta_query = pta_query
        self.budget = budget
        self.caller_scope = caller_scope
        self.call_context = call_context
        self.recursion_depth = recursion_depth

        # Dispatch table for statement types
        self._handlers: Dict[type, Callable] = {}
        self._init_handlers()

    def _init_handlers(self) -> None:
        """Initialize statement handler dispatch table."""
        from pythonstan.ir.ir_statements import (
            IRAssign, IRCopy, IRPhi, IRLoadAttr, IRStoreAttr,
            IRLoadSubscr, IRStoreSubscr, IRCall, IRReturn, IRRaise,
            JumpIfFalse, JumpIfTrue, Goto, Label, IRPass, IRDel, IRAnno,
        )
        
        self._handlers = {
            IRAssign: self._transfer_assign,
            IRCopy: self._transfer_copy,
            IRPhi: self._transfer_phi,
            IRLoadAttr: self._transfer_load_attr,
            IRStoreAttr: self._transfer_store_attr,
            IRLoadSubscr: self._transfer_load_subscr,
            IRStoreSubscr: self._transfer_store_subscr,
            IRCall: self._transfer_call,
            IRReturn: self._transfer_return,
            IRRaise: self._transfer_raise,
            JumpIfFalse: self._transfer_jump_if_false,
            JumpIfTrue: self._transfer_jump_if_true,
            Goto: self._transfer_goto,
            Label: self._transfer_label,
            IRPass: self._transfer_pass,
            IRDel: self._transfer_del,
            IRAnno: self._transfer_anno,
        }

    def transfer(
        self,
        stmt: 'IRStatement',
        state: AIState,
        summary: SummaryAccumulator,
    ) -> TransferResult:
        """Apply transfer function for a statement.
        
        Args:
            stmt: IR statement to interpret
            state: Current abstract state
            summary: Summary accumulator
            
        Returns:
            TransferResult with successor states
        """
        handler = self._handlers.get(type(stmt))
        if handler:
            return handler(stmt, state, summary)
        else:
            # Unknown statement: pass through unchanged
            logger.warning(f"Unknown IR statement type: {type(stmt).__name__}")
            return TransferResult.single(state)

    # =========================================================================
    # Assignment transfers
    # =========================================================================

    def _transfer_assign(
        self,
        stmt: 'IRAssign',
        state: AIState,
        summary: SummaryAccumulator,
    ) -> TransferResult:
        """Transfer for IRAssign: x = <expr>.
        
        Handles constant assignments and simple expressions.
        """
        import ast
        
        lval = stmt.get_lval().id
        rval = stmt.stmt.value

        # Determine abstract value from RHS
        if isinstance(rval, ast.Constant):
            val = self._abstract_constant(rval.value)
        elif isinstance(rval, ast.Name):
            val = state.get_var(rval.id)
        else:
            # Complex expression: approximate as unknown
            val = AbsVal.top()

        new_state = state.set_var(lval, val)
        return TransferResult.single(new_state)

    def _transfer_copy(
        self,
        stmt: 'IRCopy',
        state: AIState,
        summary: SummaryAccumulator,
    ) -> TransferResult:
        """Transfer for IRCopy: x = y."""
        lval = stmt.get_lval().id
        rval = stmt.get_rval().id

        val = state.get_var(rval)
        new_state = state.set_var(lval, val)
        return TransferResult.single(new_state)

    def _transfer_phi(
        self,
        stmt: 'IRPhi',
        state: AIState,
        summary: SummaryAccumulator,
    ) -> TransferResult:
        """Transfer for IRPhi: x = Phi(y1, y2, ...).
        
        Join all incoming values.
        """
        lval = stmt.get_lval().id
        items = stmt.get_items()

        # Join all non-None incoming values
        result = AbsVal.bottom()
        for item in items:
            if item is not None:
                val = state.get_var(item.id)
                result = result.join(val)

        new_state = state.set_var(lval, result)
        return TransferResult.single(new_state)

    # =========================================================================
    # Attribute access transfers (with descriptor semantics)
    # =========================================================================

    def _transfer_load_attr(
        self,
        stmt: 'IRLoadAttr',
        state: AIState,
        summary: SummaryAccumulator,
    ) -> TransferResult:
        """Transfer for IRLoadAttr: t = obj.attr.
        
        Implements ai_spec.md §6.4 GetAttrConst# with descriptor precedence.
        
        Resolution order (Python semantics):
        1. __getattribute__ override (if present)
        2. Data descriptors (__get__ with __set__ or __delete__)
        3. Instance __dict__
        4. Non-data descriptors (__get__ only)
        5. __getattr__ fallback (if attribute missing)
        """
        target = stmt.get_lval().id
        obj_name = stmt.get_obj().id
        attr_name = stmt.get_attr()

        obj_val = state.get_var(obj_name)
        
        # Use shared helper for attribute resolution
        result_val = self._get_attr_const(
            receiver_addrs=obj_val.addrs,
            attr_name=attr_name,
            state=state,
            summary=summary,
        )

        new_state = state.set_var(target, result_val)
        return TransferResult.single(new_state)

    def _get_attr_const(
        self,
        receiver_addrs: FrozenSet['AbstractObject'],
        attr_name: str,
        state: AIState,
        summary: SummaryAccumulator,
    ) -> AbsVal:
        """Shared helper for attribute reads with descriptor/hook semantics.
        
        Implements the full Python attribute resolution protocol:
        1. Check for __getattribute__ override
        2. Check for data descriptors (__get__ + __set__/__delete__)
        3. Instance/object __dict__ lookup
        4. Check for non-data descriptors (__get__ only)
        5. Fallback to __getattr__ if attribute not found
        6. Module __getattr__ (PEP 562) for module objects
        
        Args:
            receiver_addrs: Set of receiver objects
            attr_name: Attribute name to look up
            state: Current abstract state
            summary: Summary accumulator
            
        Returns:
            AbsVal representing the attribute value
        """
        from pythonstan.analysis.pointer.kcfa.heap_model import attr, unknown
        
        result_addrs: Set['AbstractObject'] = set()
        found_attr = False
        
        for obj in receiver_addrs:
            field_key = attr(attr_name)
            
            # Step 1: Check for __getattribute__ override
            getattribute_addrs = self._lookup_method(obj, "__getattribute__")
            if getattribute_addrs:
                # Invoke __getattribute__(self, "name")
                for ga_obj in getattribute_addrs:
                    ga_result = self.invoke_callee(
                        callee_obj=ga_obj,
                        arg_vals=[("name", AbsVal.from_str(attr_name))],
                        summary=summary,
                        receiver_val=AbsVal.from_addrs(frozenset({obj})),
                    )
                    result_addrs.update(ga_result.addrs)
                found_attr = True
                # Continue to also check direct lookup for soundness
            
            # Step 2 & 3: Check class slots for descriptors and instance __dict__
            # First, query PTA for field points-to (direct lookup)
            field_pts = self.pta_query.field_points_to(
                obj, field_key, self.caller_scope, self.call_context
            )
            
            # Check if any field values are descriptors with __get__
            for field_val in field_pts:
                get_method_addrs = self._lookup_method(field_val, "__get__")
                if get_method_addrs:
                    # It's a descriptor - invoke __get__(self, obj, type)
                    for gm_obj in get_method_addrs:
                        desc_result = self.invoke_callee(
                            callee_obj=gm_obj,
                            arg_vals=[
                                ("obj", AbsVal.from_addrs(frozenset({obj}))),
                                ("type", AbsVal.top()),  # type object
                            ],
                            summary=summary,
                            receiver_val=AbsVal.from_addrs(frozenset({field_val})),
                        )
                        result_addrs.update(desc_result.addrs)
                    found_attr = True
                else:
                    # Not a descriptor, use directly
                    result_addrs.add(field_val)
                    found_attr = True
            
            # Also try unknown field for dynamic attributes
            unknown_pts = self.pta_query.field_points_to(
                obj, unknown(), self.caller_scope, self.call_context
            )
            result_addrs.update(unknown_pts)
            if unknown_pts:
                found_attr = True
            
            # Record read in summary
            summary.add_read(frozenset({obj}), field_key)
            
            # Step 5: Check for __getattr__ fallback if attribute might be missing
            if not found_attr or len(field_pts) == 0:
                getattr_addrs = self._lookup_method(obj, "__getattr__")
                if getattr_addrs:
                    for ga_obj in getattr_addrs:
                        ga_result = self.invoke_callee(
                            callee_obj=ga_obj,
                            arg_vals=[("name", AbsVal.from_str(attr_name))],
                            summary=summary,
                            receiver_val=AbsVal.from_addrs(frozenset({obj})),
                        )
                        result_addrs.update(ga_result.addrs)
                    found_attr = True
            
            # Step 6: Module __getattr__ (PEP 562)
            if self._is_module_object(obj) and not found_attr:
                module_getattr_addrs = self._lookup_method(obj, "__getattr__")
                if module_getattr_addrs:
                    for mg_obj in module_getattr_addrs:
                        mg_result = self.invoke_callee(
                            callee_obj=mg_obj,
                            arg_vals=[("name", AbsVal.from_str(attr_name))],
                            summary=summary,
                            receiver_val=None,  # Module __getattr__ takes just name
                        )
                        result_addrs.update(mg_result.addrs)
                    found_attr = True

        # Create result value
        if result_addrs:
            return AbsVal.from_addrs(frozenset(result_addrs))
        else:
            # No known value: could be AttributeError or dynamic
            summary.add_may_raise("AttributeError")
            return AbsVal.top()

    def _lookup_method(self, obj: 'AbstractObject', method_name: str) -> FrozenSet['AbstractObject']:
        """Look up a method on an object via PTA.
        
        Args:
            obj: Object to look up on
            method_name: Method name (e.g., "__get__", "__getattr__")
            
        Returns:
            Set of callable objects for the method, or empty set
        """
        from pythonstan.analysis.pointer.kcfa.heap_model import attr
        
        try:
            method_pts = self.pta_query.field_points_to(
                obj, attr(method_name), self.caller_scope, self.call_context
            )
            return frozenset(method_pts)
        except Exception:
            return frozenset()

    def _is_module_object(self, obj: 'AbstractObject') -> bool:
        """Check if an object represents a module.
        
        Args:
            obj: Object to check
            
        Returns:
            True if obj is a module object
        """
        try:
            # Check for module indicators
            if hasattr(obj, 'kind'):
                return str(obj.kind).lower() == 'module'
            if hasattr(obj, 'is_module'):
                return obj.is_module
            # Check class name
            class_name = type(obj).__name__.lower()
            return 'module' in class_name
        except Exception:
            return False

    def _transfer_store_attr(
        self,
        stmt: 'IRStoreAttr',
        state: AIState,
        summary: SummaryAccumulator,
    ) -> TransferResult:
        """Transfer for IRStoreAttr: obj.attr = v.
        
        Implements ai_spec.md §6.6 SetAttrConst# with descriptor/hook semantics.
        
        Resolution order (Python semantics):
        1. __setattr__ override (if present)
        2. Data descriptors (__set__)
        3. Direct instance __dict__ write
        """
        obj_name = stmt.get_obj().id
        attr_name = stmt.get_attr()
        value_name = stmt.get_rval().id

        obj_val = state.get_var(obj_name)
        value_val = state.get_var(value_name)

        # Use shared helper for attribute writes
        self._set_attr_const(
            receiver_addrs=obj_val.addrs,
            attr_name=attr_name,
            value_val=value_val,
            state=state,
            summary=summary,
        )

        return TransferResult.single(state)

    def _set_attr_const(
        self,
        receiver_addrs: FrozenSet['AbstractObject'],
        attr_name: str,
        value_val: AbsVal,
        state: AIState,
        summary: SummaryAccumulator,
    ) -> None:
        """Shared helper for attribute writes with descriptor/hook semantics.
        
        Implements the full Python attribute assignment protocol:
        1. Check for __setattr__ override
        2. Check for data descriptors (__set__)
        3. Direct write to instance __dict__
        
        Args:
            receiver_addrs: Set of receiver objects
            attr_name: Attribute name to write
            value_val: Value to write
            state: Current abstract state
            summary: Summary accumulator
        """
        from pythonstan.analysis.pointer.kcfa.heap_model import attr
        
        field_key = attr(attr_name)
        
        for obj in receiver_addrs:
            setattr_invoked = False
            descriptor_invoked = False
            
            # Step 1: Check for __setattr__ override
            setattr_addrs = self._lookup_method(obj, "__setattr__")
            if setattr_addrs:
                for sa_obj in setattr_addrs:
                    self.invoke_callee(
                        callee_obj=sa_obj,
                        arg_vals=[
                            ("name", AbsVal.from_str(attr_name)),
                            ("value", value_val),
                        ],
                        summary=summary,
                        receiver_val=AbsVal.from_addrs(frozenset({obj})),
                    )
                setattr_invoked = True
            
            # Step 2: Check for data descriptors (__set__)
            # Look up the class slot for attr_name to find potential descriptors
            field_pts = self.pta_query.field_points_to(
                obj, field_key, self.caller_scope, self.call_context
            )
            
            for field_val in field_pts:
                # Check if this is a data descriptor (has __set__)
                set_method_addrs = self._lookup_method(field_val, "__set__")
                if set_method_addrs:
                    # It's a data descriptor - invoke __set__(self, obj, value)
                    for sm_obj in set_method_addrs:
                        self.invoke_callee(
                            callee_obj=sm_obj,
                            arg_vals=[
                                ("obj", AbsVal.from_addrs(frozenset({obj}))),
                                ("value", value_val),
                            ],
                            summary=summary,
                            receiver_val=AbsVal.from_addrs(frozenset({field_val})),
                        )
                    descriptor_invoked = True
            
            # Step 3: Direct write to instance __dict__
            # Always record the write for soundness (unless we can prove
            # the __setattr__ or descriptor dominates)
            if value_val.addrs:
                summary.add_write(
                    frozenset({obj}),
                    field_key,
                    frozenset(value_val.addrs),
                )

    # =========================================================================
    # Subscript access transfers
    # =========================================================================

    def _transfer_load_subscr(
        self,
        stmt: 'IRLoadSubscr',
        state: AIState,
        summary: SummaryAccumulator,
    ) -> TransferResult:
        """Transfer for IRLoadSubscr: t = obj[idx].
        
        Uses elem() bucket for unknown indices, key(k) for constants.
        """
        from pythonstan.analysis.pointer.kcfa.heap_model import elem, key
        import ast

        target = stmt.get_lval().id
        obj_name = stmt.get_obj().id
        index_expr = stmt.get_slice()

        obj_val = state.get_var(obj_name)
        
        # Determine index
        if isinstance(index_expr, ast.Name):
            index_val = state.get_var(index_expr.id)
            # Check if index is a constant string
            index_strs = index_val.get_const_strs()
        else:
            index_strs = None

        result_addrs: Set['AbstractObject'] = set()
        
        for obj in obj_val.addrs:
            if index_strs is not None and len(index_strs) <= self.budget.max_const_strings:
                # Known constant indices
                for idx in index_strs:
                    field_key = key(idx)
                    field_pts = self.pta_query.field_points_to(
                        obj, field_key, self.caller_scope, self.call_context
                    )
                    result_addrs.update(field_pts)
                    summary.add_read(frozenset({obj}), field_key)
            
            # Always also read elem() for soundness
            elem_pts = self.pta_query.field_points_to(
                obj, elem(), self.caller_scope, self.call_context
            )
            result_addrs.update(elem_pts)
            summary.add_read(frozenset({obj}), elem())

        if result_addrs:
            result_val = AbsVal.from_addrs(frozenset(result_addrs))
        else:
            result_val = AbsVal.top()

        new_state = state.set_var(target, result_val)
        return TransferResult.single(new_state)

    def _transfer_store_subscr(
        self,
        stmt: 'IRStoreSubscr',
        state: AIState,
        summary: SummaryAccumulator,
    ) -> TransferResult:
        """Transfer for IRStoreSubscr: obj[idx] = v."""
        from pythonstan.analysis.pointer.kcfa.heap_model import elem, key
        import ast

        obj_name = stmt.get_obj().id
        value_name = stmt.get_rval().id
        index_expr = stmt.get_slice()

        obj_val = state.get_var(obj_name)
        value_val = state.get_var(value_name)

        # Determine index
        if isinstance(index_expr, ast.Name):
            index_val = state.get_var(index_expr.id)
            index_strs = index_val.get_const_strs()
        else:
            index_strs = None

        for obj in obj_val.addrs:
            if index_strs is not None and len(index_strs) <= self.budget.max_const_strings:
                # Write to specific keys
                for idx in index_strs:
                    summary.add_write(
                        frozenset({obj}),
                        key(idx),
                        frozenset(value_val.addrs),
                    )
            
            # Always also write to elem() for soundness
            summary.add_write(
                frozenset({obj}),
                elem(),
                frozenset(value_val.addrs),
            )

        return TransferResult.single(state)

    # =========================================================================
    # Call transfer (with builtin handling)
    # =========================================================================

    def _transfer_call(
        self,
        stmt: 'IRCall',
        state: AIState,
        summary: SummaryAccumulator,
    ) -> TransferResult:
        """Transfer for IRCall: t = f(args...).
        
        Handles builtins (getattr/setattr/hasattr) precisely,
        other calls conservatively.
        """
        target = stmt.get_target()
        func_name = stmt.get_func_name()
        args = stmt.get_args()
        kwargs = stmt.get_keywords()

        # Check for known builtins
        if func_name == "getattr":
            return self._handle_getattr(stmt, state, summary)
        elif func_name == "setattr":
            return self._handle_setattr(stmt, state, summary)
        elif func_name == "hasattr":
            return self._handle_hasattr(stmt, state, summary)

        # Generic call: look up callee
        callee_val = state.get_var(func_name)

        # Conservative: return unknown, record scoped effect
        result_val = AbsVal.top()
        
        # Record unknown effect on arguments (they may be mutated)
        for arg_name, is_starred in args:
            if not arg_name.startswith("<"):  # Skip constants
                arg_val = state.get_var(arg_name)
                if arg_val.addrs:
                    summary.add_unknown_effect(
                        "may_write",
                        frozenset(arg_val.addrs),
                        None,  # All fields
                    )

        if target:
            new_state = state.set_var(target, result_val)
        else:
            new_state = state

        return TransferResult.single(new_state)

    def _handle_getattr(
        self,
        stmt: 'IRCall',
        state: AIState,
        summary: SummaryAccumulator,
    ) -> TransferResult:
        """Handle getattr(obj, name, default?) builtin.
        
        From ai_spec.md §8.1.
        
        Uses _get_attr_const for constant attribute names to get full
        descriptor/hook semantics. Falls back to direct PTA lookup for
        unknown names.
        """
        from pythonstan.analysis.pointer.kcfa.heap_model import attr, unknown

        target = stmt.get_target()
        args = stmt.get_args()

        if len(args) < 2:
            return self._handle_unknown_call(stmt, state, summary)

        obj_name = args[0][0]
        name_name = args[1][0]
        default_name = args[2][0] if len(args) > 2 else None

        obj_val = state.get_var(obj_name)
        name_val = state.get_var(name_name)
        
        # Get constant attribute names
        const_names = name_val.get_const_strs()

        result_addrs: Set['AbstractObject'] = set()

        if const_names is not None and len(const_names) <= self.budget.max_const_strings:
            # Enumerable attribute names: use full descriptor/hook semantics
            for attr_name in const_names:
                attr_result = self._get_attr_const(
                    receiver_addrs=obj_val.addrs,
                    attr_name=attr_name,
                    state=state,
                    summary=summary,
                )
                result_addrs.update(attr_result.addrs)
        else:
            # Unknown attribute name: read from unknown bucket (no descriptor semantics)
            for obj in obj_val.addrs:
                unknown_pts = self.pta_query.field_points_to(
                    obj, unknown(), self.caller_scope, self.call_context
                )
                result_addrs.update(unknown_pts)
                summary.add_read(frozenset({obj}), unknown())

        # Include default if provided
        if default_name and not default_name.startswith("<"):
            default_val = state.get_var(default_name)
            result_addrs.update(default_val.addrs)

        if result_addrs:
            result_val = AbsVal.from_addrs(frozenset(result_addrs))
        else:
            result_val = AbsVal.top()

        if target:
            new_state = state.set_var(target, result_val)
        else:
            new_state = state

        return TransferResult.single(new_state)

    def _handle_setattr(
        self,
        stmt: 'IRCall',
        state: AIState,
        summary: SummaryAccumulator,
    ) -> TransferResult:
        """Handle setattr(obj, name, value) builtin.
        
        From ai_spec.md §8.2.
        
        Uses _set_attr_const for constant attribute names to get full
        descriptor/hook semantics. Falls back to direct write for unknown names.
        """
        from pythonstan.analysis.pointer.kcfa.heap_model import attr, unknown

        args = stmt.get_args()

        if len(args) < 3:
            return self._handle_unknown_call(stmt, state, summary)

        obj_name = args[0][0]
        name_name = args[1][0]
        value_name = args[2][0]

        obj_val = state.get_var(obj_name)
        name_val = state.get_var(name_name)
        value_val = state.get_var(value_name)

        const_names = name_val.get_const_strs()

        if const_names is not None and len(const_names) <= self.budget.max_const_strings:
            # Enumerable names: use full descriptor/hook semantics
            for attr_name in const_names:
                self._set_attr_const(
                    receiver_addrs=obj_val.addrs,
                    attr_name=attr_name,
                    value_val=value_val,
                    state=state,
                    summary=summary,
                )
        else:
            # Unknown name: write to unknown bucket (no descriptor semantics)
            summary.add_write(
                frozenset(obj_val.addrs),
                unknown(),
                frozenset(value_val.addrs),
            )

        # setattr returns None
        if stmt.get_target():
            new_state = state.set_var(stmt.get_target(), AbsVal.none_val())
        else:
            new_state = state

        return TransferResult.single(new_state)

    def _handle_hasattr(
        self,
        stmt: 'IRCall',
        state: AIState,
        summary: SummaryAccumulator,
    ) -> TransferResult:
        """Handle hasattr(obj, name) builtin.
        
        From ai_spec.md §8.3.
        """
        from pythonstan.analysis.pointer.kcfa.heap_model import attr

        target = stmt.get_target()
        args = stmt.get_args()

        if len(args) < 2:
            return self._handle_unknown_call(stmt, state, summary)

        obj_name = args[0][0]
        name_name = args[1][0]

        obj_val = state.get_var(obj_name)
        name_val = state.get_var(name_name)

        const_names = name_val.get_const_strs()

        # Conservative: return unknown boolean
        # Could be more precise with must-not analysis
        result_val = AbsVal(bool_val=None)  # Unknown bool

        if const_names is not None:
            # Record reads for dependency
            for obj in obj_val.addrs:
                for attr_name in const_names:
                    summary.add_read(frozenset({obj}), attr(attr_name))

        if target:
            new_state = state.set_var(target, result_val)
        else:
            new_state = state

        return TransferResult.single(new_state)

    def _handle_unknown_call(
        self,
        stmt: 'IRCall',
        state: AIState,
        summary: SummaryAccumulator,
    ) -> TransferResult:
        """Handle unknown/generic call conservatively."""
        target = stmt.get_target()
        args = stmt.get_args()

        # Record unknown effects on arguments
        for arg_name, is_starred in args:
            if not arg_name.startswith("<"):
                arg_val = state.get_var(arg_name)
                if arg_val.addrs:
                    summary.add_unknown_effect(
                        "may_write",
                        frozenset(arg_val.addrs),
                        None,
                    )

        if target:
            new_state = state.set_var(target, AbsVal.top())
        else:
            new_state = state

        return TransferResult.single(new_state)

    # =========================================================================
    # Return/Raise transfers
    # =========================================================================

    def _transfer_return(
        self,
        stmt: 'IRReturn',
        state: AIState,
        summary: SummaryAccumulator,
    ) -> TransferResult:
        """Transfer for IRReturn: return v."""
        ret_var = stmt.get_ret_var()
        
        if ret_var:
            ret_val = state.get_var(ret_var.id)
            summary.add_return(frozenset(ret_val.addrs))
        else:
            # Bare return -> None
            pass  # Don't add anything (None is not an object address)

        return TransferResult.terminal()

    def _transfer_raise(
        self,
        stmt: 'IRRaise',
        state: AIState,
        summary: SummaryAccumulator,
    ) -> TransferResult:
        """Transfer for IRRaise: raise exc."""
        # Record that exception may be raised
        summary.add_may_raise("Exception")
        return TransferResult.terminal()

    # =========================================================================
    # Control flow transfers (with value partition refinement)
    # =========================================================================

    def _transfer_jump_if_false(
        self,
        stmt: 'JumpIfFalse',
        state: AIState,
        summary: SummaryAccumulator,
    ) -> TransferResult:
        """Transfer for JumpIfFalse: if not cond goto label.
        
        Implements value partitioning by refining states on each branch:
        - True branch (fall-through): condition is truthy
        - False branch (jump): condition is falsy
        """
        cond_name = stmt.get_cond().id if hasattr(stmt.get_cond(), 'id') else None
        target_label = stmt.label.to_s() if stmt.label else None

        true_state, false_state = self._refine_states_for_condition(
            state, cond_name, is_jump_if_true=False
        )

        return TransferResult(states=[
            (None, true_state),  # Fall through (condition is true)
            (target_label, false_state),  # Jump (condition is false)
        ])

    def _transfer_jump_if_true(
        self,
        stmt: 'JumpIfTrue',
        state: AIState,
        summary: SummaryAccumulator,
    ) -> TransferResult:
        """Transfer for JumpIfTrue: if cond goto label.
        
        Implements value partitioning by refining states on each branch:
        - True branch (jump): condition is truthy
        - False branch (fall-through): condition is falsy
        """
        cond_name = stmt.get_cond().id if hasattr(stmt.get_cond(), 'id') else None
        target_label = stmt.label.to_s() if stmt.label else None

        true_state, false_state = self._refine_states_for_condition(
            state, cond_name, is_jump_if_true=True
        )

        return TransferResult(states=[
            (target_label, true_state),  # Jump (condition is true)
            (None, false_state),  # Fall through (condition is false)
        ])

    def _refine_states_for_condition(
        self,
        state: AIState,
        cond_name: Optional[str],
        is_jump_if_true: bool,
    ) -> Tuple[AIState, AIState]:
        """Refine states based on condition value for value partitioning.
        
        This is the key mechanism for value partitioning:
        - If cond has known bool_val=True, only true branch is reachable
        - If cond has known bool_val=False, only false branch is reachable
        - If cond is unknown, propagate both branches with refined condition
        - For None-ness checks, refine may_be_none on each branch
        
        Controlled by budget.enable_value_partition. When disabled, both
        branches get the same unrefined state.
        
        Args:
            state: Current abstract state
            cond_name: Name of condition variable (or None)
            is_jump_if_true: True if this is JumpIfTrue (affects which branch gets which refinement)
            
        Returns:
            (true_state, false_state) tuple with refined states
        """
        # Check if value partitioning is enabled
        if not getattr(self.budget, 'enable_value_partition', True):
            return state, state
        
        if cond_name is None:
            return state, state
        
        cond_val = state.get_var(cond_name)
        
        # Check if condition is definitely known
        if cond_val.bool_val is True:
            # Condition is definitely true
            # True branch is reachable, false branch is unreachable (but sound to propagate)
            true_state = state
            false_state = state  # Keep sound (could use bottom)
        elif cond_val.bool_val is False:
            # Condition is definitely false
            true_state = state  # Keep sound
            false_state = state
        else:
            # Condition is unknown - refine on each branch
            # True branch: set cond's bool_val to True
            true_state = state.set_var(cond_name, AbsVal(
                addrs=cond_val.addrs,
                str_const=cond_val.str_const,
                may_be_none=False,  # Truthy value cannot be None
                bool_val=True,
            ))
            
            # False branch: set cond's bool_val to False
            # Note: False could be None, False, 0, "", [], etc.
            false_state = state.set_var(cond_name, AbsVal(
                addrs=cond_val.addrs,
                str_const=cond_val.str_const,
                may_be_none=cond_val.may_be_none,  # None is falsy
                bool_val=False,
            ))
        
        return true_state, false_state

    def _transfer_goto(
        self,
        stmt: 'Goto',
        state: AIState,
        summary: SummaryAccumulator,
    ) -> TransferResult:
        """Transfer for Goto: goto label."""
        target_label = stmt.label.to_s() if stmt.label else None
        return TransferResult.single(state, target_label)

    def _transfer_label(
        self,
        stmt: 'Label',
        state: AIState,
        summary: SummaryAccumulator,
    ) -> TransferResult:
        """Transfer for Label: label_N:."""
        # Labels are just markers, no state change
        return TransferResult.single(state)

    def _transfer_pass(
        self,
        stmt: 'IRPass',
        state: AIState,
        summary: SummaryAccumulator,
    ) -> TransferResult:
        """Transfer for IRPass: no-op."""
        return TransferResult.single(state)

    def _transfer_del(
        self,
        stmt: 'IRDel',
        state: AIState,
        summary: SummaryAccumulator,
    ) -> TransferResult:
        """Transfer for IRDel: del x."""
        # Remove variable from environment
        var_name = stmt.get_name()
        new_state = state.copy()
        if var_name in new_state.env:
            del new_state.env[var_name]
        return TransferResult.single(new_state)

    def _transfer_anno(
        self,
        stmt: 'IRAnno',
        state: AIState,
        summary: SummaryAccumulator,
    ) -> TransferResult:
        """Transfer for IRAnno: x: type."""
        # Annotations have no runtime effect in v1
        return TransferResult.single(state)

    # =========================================================================
    # Utility methods
    # =========================================================================

    def _abstract_constant(self, value: Any) -> AbsVal:
        """Create AbsVal from Python constant."""
        if value is None:
            return AbsVal.none_val()
        if isinstance(value, bool):
            return AbsVal(bool_val=value)
        if isinstance(value, str):
            return AbsVal.from_str(value)
        if isinstance(value, (int, float)):
            return AbsVal()  # No address, no special tracking
        return AbsVal()

    # =========================================================================
    # Bounded callee invocation (for descriptor/hook calls)
    # =========================================================================

    def invoke_callee(
        self,
        callee_obj: 'AbstractObject',
        arg_vals: List[Tuple[str, AbsVal]],
        summary: SummaryAccumulator,
        receiver_val: Optional[AbsVal] = None,
    ) -> AbsVal:
        """Invoke a user-defined callee and return its summary result.
        
        This is a bounded interprocedural AI call used for:
        - Descriptor __get__/__set__/__delete__ invocations
        - __getattribute__, __getattr__, __setattr__ hooks
        - Module __getattr__ (PEP 562)
        
        Uses self.recursion_depth to enforce budget limits and increments it
        for nested calls.
        
        Args:
            callee_obj: The callable object (FunctionObject/MethodObject)
            arg_vals: List of (param_name, AbsVal) pairs for positional args
            summary: Summary accumulator to merge effects into
            receiver_val: Optional receiver for method binding (self/cls)
            
        Returns:
            AbsVal representing the return addresses, or top() if analysis fails
        """
        from .api import AISummaryEngine, ArgBinding
        
        # Check recursion depth budget using self.recursion_depth
        max_depth = self.budget.max_recursion_depth if self.budget else 3
        if self.recursion_depth >= max_depth:
            # Budget exhausted: return conservative result
            summary.add_unknown_effect("may_call", frozenset({callee_obj}))
            return AbsVal.top()
        
        # Try to get IR scope for the callee
        scope_ir = self._get_callee_scope_ir(callee_obj)
        if scope_ir is None:
            # No IR available: return conservative result
            summary.add_unknown_effect("may_call", frozenset({callee_obj}))
            return AbsVal.top()
        
        # Build ArgBindings from arg_vals
        arg_bindings: List[ArgBinding] = []
        
        # Inject receiver binding for methods using proper method binding rules
        actual_receiver = self._get_receiver_for_method(callee_obj, scope_ir, receiver_val)
        if actual_receiver is not None:
            # Determine receiver param name from scope_ir
            self_name = self._get_self_param_name(scope_ir)
            if self_name:
                arg_bindings.append(ArgBinding(
                    param_name=self_name,
                    points_to=actual_receiver.addrs,
                    value=actual_receiver,
                ))
        
        # Add positional arguments
        for param_name, arg_val in arg_vals:
            arg_bindings.append(ArgBinding(
                param_name=param_name,
                points_to=arg_val.addrs,
                value=arg_val,
            ))
        
        # Create engine and analyze callee with incremented recursion depth
        try:
            engine = AISummaryEngine(self.pta_query, self.budget)
            callee_summary = engine.analyze_callee(
                scope_ir=scope_ir,
                call_context=self.call_context,
                arg_bindings=arg_bindings,
                caller_scope=self.caller_scope,
                recursion_depth=self.recursion_depth + 1,
            )
            
            # Merge callee summary's EFFECTS into our accumulator
            # NOTE: Do NOT merge callee_summary.ret into summary.ret_addrs!
            # The callee's return addresses flow into the expression result (AbsVal),
            # not the caller's return values. The caller's returns should only come
            # from the caller's own return statements.
            for write in callee_summary.writes:
                summary.add_write(write.obj_addrs, write.field_key, write.value_addrs)
            for read_obj, read_field in callee_summary.reads:
                summary.add_read(read_obj, read_field)
            for effect in callee_summary.unknown_effects:
                summary.unknown_effects.append(effect)
            summary.may_raise.update(callee_summary.may_raise)
            
            # Return the callee's return addresses as the expression result
            if callee_summary.ret:
                return AbsVal.from_addrs(callee_summary.ret)
            else:
                return AbsVal.none_val()
                
        except Exception as e:
            # Analysis failed: return conservative result
            logger.warning(f"Callee invocation failed: {e}")
            summary.add_unknown_effect("may_call", frozenset({callee_obj}))
            return AbsVal.top()

    def _get_callee_scope_ir(self, callee_obj: 'AbstractObject') -> Optional['IRFunc']:
        """Get the IR scope for a callable object.
        
        For kcfa FunctionObject/MethodObject, use their direct `ir` attribute.
        Falls back to World lookup if the direct attribute is not available.
        
        Args:
            callee_obj: The callable object
            
        Returns:
            IRFunc if available, None otherwise
        """
        try:
            from pythonstan.ir.ir_statements import IRFunc
            
            # Primary path: FunctionObject/MethodObject have an `ir` attribute
            # that directly references the IRFunc
            if hasattr(callee_obj, 'ir'):
                ir = callee_obj.ir
                if ir is not None and isinstance(ir, IRFunc):
                    return ir
            
            # Fallback: try to get from alloc_site.stmt (some objects store it there)
            if hasattr(callee_obj, 'alloc_site'):
                alloc_site = callee_obj.alloc_site
                if hasattr(alloc_site, 'stmt'):
                    stmt = alloc_site.stmt
                    if isinstance(stmt, IRFunc):
                        return stmt
            
            # Last resort: try World lookup (may not work for all cases)
            try:
                from pythonstan.world import World
                
                qualname = None
                if hasattr(callee_obj, 'qualname'):
                    qualname = callee_obj.qualname
                elif hasattr(callee_obj, 'name'):
                    qualname = callee_obj.name
                elif hasattr(callee_obj, 'get_qualname'):
                    qualname = callee_obj.get_qualname()
                
                if qualname is not None:
                    scope = World().scope_manager.get_module(qualname)
                    if scope is not None and isinstance(scope, IRFunc):
                        return scope
            except Exception:
                pass
                
            return None
        except Exception:
            return None

    def _get_self_param_name(self, scope_ir: 'IRFunc') -> Optional[str]:
        """Get the 'self' or 'cls' parameter name from a function scope.
        
        Args:
            scope_ir: The IRFunc to examine
            
        Returns:
            The first parameter name, or None if no parameters
        """
        try:
            if hasattr(scope_ir, 'args') and scope_ir.args is not None:
                args = scope_ir.args
                if hasattr(args, 'args') and len(args.args) > 0:
                    return args.args[0].arg
        except Exception:
            pass
        return None

    def _should_inject_receiver(self, scope_ir: 'IRFunc') -> bool:
        """Check if receiver (self/cls) should be injected for this function.
        
        Static methods don't get receiver injection.
        
        Args:
            scope_ir: The IRFunc to check
            
        Returns:
            True if receiver should be injected
        """
        try:
            if hasattr(scope_ir, 'is_static_method') and scope_ir.is_static_method:
                return False
            return True
        except Exception:
            return True  # Default to injecting receiver

    def _get_receiver_for_method(
        self,
        callee_obj: 'AbstractObject',
        scope_ir: 'IRFunc',
        explicit_receiver: Optional[AbsVal],
    ) -> Optional[AbsVal]:
        """Get the appropriate receiver value for method binding.
        
        Implements kcfa rules:
        - Instance methods: use instance_obj if available, else class_obj
        - Class methods: use class_obj
        - Static methods: no receiver (returns None)
        
        Args:
            callee_obj: The callable object (may be MethodObject)
            scope_ir: The IRFunc
            explicit_receiver: Explicitly provided receiver (takes priority)
            
        Returns:
            AbsVal for the receiver, or None if no receiver needed
        """
        # Static methods don't get a receiver
        if not self._should_inject_receiver(scope_ir):
            return None
        
        # Explicit receiver takes priority
        if explicit_receiver is not None:
            return explicit_receiver
        
        # Try to extract receiver from MethodObject
        try:
            if hasattr(callee_obj, 'is_class_method') and callee_obj.is_class_method:
                # Class methods get the class object
                if hasattr(callee_obj, 'class_obj') and callee_obj.class_obj is not None:
                    return AbsVal.from_addrs(frozenset({callee_obj.class_obj}))
            
            if hasattr(scope_ir, 'is_class_method') and scope_ir.is_class_method:
                # Check scope IR for classmethod decorator
                if hasattr(callee_obj, 'class_obj') and callee_obj.class_obj is not None:
                    return AbsVal.from_addrs(frozenset({callee_obj.class_obj}))
            
            # Instance method - prefer instance_obj, fall back to class_obj
            if hasattr(callee_obj, 'instance_obj') and callee_obj.instance_obj is not None:
                return AbsVal.from_addrs(frozenset({callee_obj.instance_obj}))
            
            if hasattr(callee_obj, 'class_obj') and callee_obj.class_obj is not None:
                return AbsVal.from_addrs(frozenset({callee_obj.class_obj}))
        except Exception:
            pass
        
        return None

