"""IR interpreter with transfer functions for abstract interpretation.

This module implements transfer functions for IR statements per ai_spec.md §7:
- IRAssign/IRCopy: variable assignment
- IRPhi: SSA phi nodes
- IRLoadAttr/IRStoreAttr: attribute access with descriptor semantics
- IRLoadSubscr/IRStoreSubscr: container subscription
- IRCall: function calls with builtin handling
- IRReturn: return value propagation
- Control flow: JumpIf*, Goto, Label (now with typed Label objects)

Architecture:
- IRInterpreter: Statement dispatcher with transfer function table
- Delegates to semantics services (AttributeSemantics, CallSemantics, etc.)
- Uses CFG Label objects for successor identification (not strings)
- Returns TransferResult with typed successor labels

Exception Handling:
- Internal exceptions now propagate naturally to AISummaryEngine boundary
- No silent failures; debugging is straightforward
"""

import ast
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
)

from .state import AIState, AbsVal
from .domains.calls import CallSemantics
from .domains.subscripts import SubscriptSemantics
from .domains.conditions import ConditionRefiner
from .domains.scopes import ScopeSemantics, CellStore, NameKind
from .summary import SummaryAccumulator
from .domains.attributes import AttributeSemantics
from .domains.builtins import BuiltinAttributeSemantics

if TYPE_CHECKING:
    from pythonstan.ir.ir_statements import (
        IRStatement, IRAssign, IRCopy, IRPhi, IRLoadAttr, IRStoreAttr,
        IRLoadSubscr, IRStoreSubscr, IRCall, IRReturn, IRRaise,
        JumpIfFalse, JumpIfTrue, Goto, Label, IRPass, IRDel, IRAnno, IRYield,
        IRFunc, IRClass, IRScope, IRAstStmt,
    )
    from pythonstan.analysis.pointer.kcfa.object import AbstractObject
    from pythonstan.analysis.pointer.kcfa.context import AbstractContext, Scope
    from .api import PtaQuery, AnalysisBudget
    from .domains.classinfo import ClassInfoCache

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
            successor_label can be:
            - None (fallthrough to next block)
            - Label object (from pythonstan.ir.ir_statements)
            - str (legacy compatibility)
        is_terminal: True if this ends the block (return/raise)
    """
    states: List[Tuple[Optional[Any], AIState]] = field(default_factory=list)
    is_terminal: bool = False

    @classmethod
    def single(cls, state: AIState, next_label: Optional[Any] = None) -> 'TransferResult':
        """Single successor state (normal flow)."""
        return cls(states=[(next_label, state)])

    @classmethod
    def branch(cls, true_state: AIState, false_state: AIState, 
               true_label: Any, false_label: Any) -> 'TransferResult':
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
        scope_ir: Optional['IRScope'] = None,
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
        self.scope_ir = scope_ir
        self.caller_scope = caller_scope
        self.call_context = call_context
        self.recursion_depth = recursion_depth

        # Current execution location for callsite IDs
        self._current_block_id: Optional[int] = None
        self._current_stmt_index: Optional[int] = None

        # Shared ClassInfoCache for attribute semantics (created lazily)
        self._class_info_cache: Optional['ClassInfoCache'] = None

        # Scope semantics for name resolution (closure/global/nonlocal support)
        self._scope_semantics: Optional[ScopeSemantics] = None
        if scope_ir is not None:
            self._scope_semantics = ScopeSemantics(
                pta_query=pta_query,
                budget=budget,
                scope_ir=scope_ir,
                caller_scope=caller_scope,
                call_context=call_context,
            )

        # Dispatch table for statement types
        self._handlers: Dict[type, Callable] = {}
        self._init_handlers()


    def _init_handlers(self) -> None:
        """Initialize statement handler dispatch table."""
        from pythonstan.ir.ir_statements import (
            IRAssign, IRCopy, IRPhi, IRLoadAttr, IRStoreAttr,
            IRLoadSubscr, IRStoreSubscr, IRCall, IRReturn, IRRaise,
            JumpIfFalse, JumpIfTrue, Goto, Label, IRPass, IRDel, IRAnno, IRYield,
            IRFunc, IRClass, IRAstStmt,
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
            IRYield: self._transfer_yield,
            JumpIfFalse: self._transfer_jump_if_false,
            JumpIfTrue: self._transfer_jump_if_true,
            Goto: self._transfer_goto,
            Label: self._transfer_label,
            IRPass: self._transfer_pass,
            IRDel: self._transfer_del,
            IRAnno: self._transfer_anno,
            # Top-level/declaration statements (not executable, pass through)
            IRFunc: self._transfer_pass,
            IRClass: self._transfer_pass,
            IRAstStmt: self._transfer_pass,
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

    def set_current_location(self, block_id: Optional[int], stmt_index: Optional[int]) -> None:
        """Set the current CFG location for callsite identification."""
        self._current_block_id = block_id
        self._current_stmt_index = stmt_index

    def _get_callsite_id(self) -> str:
        """Return a stable callsite identifier for the current statement."""
        scope_name = "<unknown>"
        if self.scope_ir is not None:
            if hasattr(self.scope_ir, "get_qualname"):
                scope_name = self.scope_ir.get_qualname()
            else:
                scope_name = str(self.scope_ir)
        if self._current_block_id is not None and self._current_stmt_index is not None:
            return f"{scope_name}:blk{self._current_block_id}:stmt{self._current_stmt_index}"
        return f"{scope_name}:unknown"

    def _get_attr_semantics(self, summary: SummaryAccumulator) -> AttributeSemantics:
        """Get or create the AttributeSemantics service.
        
        This injects invoke_callee, budget, and a shared class_info_cache
        to avoid circular imports between the interpreter and the
        domains/attributes module.
        
        The class_info_cache is shared across all AttributeSemantics instances
        for this interpreter to enable caching of class MRO and namespace info.
        """
        def invoke_callee_wrapper(
            callee_obj: 'AbstractObject',
            arg_vals: List[Tuple[str, AbsVal]],
            summary_acc: SummaryAccumulator,
            receiver_val: Optional[AbsVal],
        ) -> AbsVal:
            return self.invoke_callee(
                callee_obj=callee_obj,
                arg_vals=arg_vals,
                summary=summary_acc,
                receiver_val=receiver_val,
            )

        # Create shared ClassInfoCache lazily
        if self._class_info_cache is None:
            from .domains.classinfo import ClassInfoCache
            self._class_info_cache = ClassInfoCache(self.pta_query, self.budget)

        return AttributeSemantics(
            pta_query=self.pta_query,
            caller_scope=self.caller_scope,
            call_context=self.call_context,
            invoke_callee=invoke_callee_wrapper,
            budget=self.budget,
            class_info_cache=self._class_info_cache,
        )

    def _get_builtin_attr_semantics(
        self,
        summary: SummaryAccumulator,
    ) -> BuiltinAttributeSemantics:
        """Get the BuiltinAttributeSemantics service for getattr/setattr/hasattr.
        
        This injects invoke_callee and budget to avoid circular imports between
        the interpreter and the domains/builtins module.
        """
        def invoke_callee_wrapper(
            callee_obj: 'AbstractObject',
            arg_vals: List[Tuple[str, AbsVal]],
            summary_acc: SummaryAccumulator,
            receiver_val: Optional[AbsVal],
        ) -> AbsVal:
            return self.invoke_callee(
                callee_obj=callee_obj,
                arg_vals=arg_vals,
                summary=summary_acc,
                receiver_val=receiver_val,
            )

        return BuiltinAttributeSemantics(
            pta_query=self.pta_query,
            caller_scope=self.caller_scope,
            call_context=self.call_context,
            invoke_callee=invoke_callee_wrapper,
            budget=self.budget,
        )

    def _get_call_semantics(self) -> CallSemantics:
        """Get the CallSemantics service for call dispatch.

        This injects interpreter callbacks to avoid circular imports between
        the interpreter and the domains/calls module.
        """
        def invoke_callee_wrapper(
            callee_obj: 'AbstractObject',
            arg_vals: List[Tuple[str, AbsVal]],
            summary_acc: SummaryAccumulator,
            receiver_val: Optional[AbsVal],
            callsite_id: Optional[str],
            call_context: Optional['AbstractContext'],
            argc: Optional[int],
        ) -> AbsVal:
            return self.invoke_callee(
                callee_obj=callee_obj,
                arg_vals=arg_vals,
                summary=summary_acc,
                receiver_val=receiver_val,
                callsite_id=callsite_id,
                call_context=call_context,
                argc=argc,
            )

        return CallSemantics(
            pta_query=self.pta_query,
            caller_scope=self.caller_scope,
            call_context=self.call_context,
            invoke_callee=invoke_callee_wrapper,
            get_callee_scope_ir=self._get_callee_scope_ir,
            select_call_context=self._select_call_context,
            build_arg_vals_for_scope=self._build_arg_vals_for_scope,
            get_receiver_for_method=self._get_receiver_for_method,
            collect_param_addrs=self._collect_param_addrs,
        )

    def _get_iterator_semantics(
        self,
        summary: SummaryAccumulator,
    ):
        """Get iterator/generator semantics service."""
        from .domains.iterators import IteratorBuiltinsSemantics

        return IteratorBuiltinsSemantics(
            attr_semantics=self._get_attr_semantics(summary),
            call_semantics=self._get_call_semantics(),
            pta_query=self.pta_query,
            caller_scope=self.caller_scope,
            call_context=self.call_context,
            budget=self.budget,
            recursion_depth=self.recursion_depth,
        )

    def _get_subscript_semantics(self) -> SubscriptSemantics:
        """Get the SubscriptSemantics service for subscript access."""
        return SubscriptSemantics(
            caller_scope=self.caller_scope,
            call_context=self.call_context,
            budget=self.budget,
        )

    def _get_condition_refiner(self) -> ConditionRefiner:
        """Get the ConditionRefiner service for branch refinement."""
        return ConditionRefiner(budget=self.budget)

    def _get_scope_semantics(self) -> ScopeSemantics:
        """Get or create the ScopeSemantics service.
        
        Creates a ScopeSemantics instance for name resolution that respects
        Python's scoping rules (global/nonlocal/cell/local).
        """
        if self._scope_semantics is not None:
            return self._scope_semantics
        
        # Create on demand if not initialized
        return ScopeSemantics(
            pta_query=self.pta_query,
            budget=self.budget,
            scope_ir=self.scope_ir,
            caller_scope=self.caller_scope,
            call_context=self.call_context,
        )

    def _read_name(
        self,
        name: str,
        state: AIState,
        summary: SummaryAccumulator,
    ) -> AbsVal:
        """Read a variable respecting Python scoping rules.
        
        Delegates to ScopeSemantics for proper handling of:
        - global variables (module namespace)
        - nonlocal variables (outer scope cells)
        - cell variables (closure storage)
        - local variables (env)
        
        Args:
            name: Variable name to read
            state: Current abstract state
            summary: Summary accumulator
            
        Returns:
            Abstract value for the variable
        """
        scope_sem = self._get_scope_semantics()
        return scope_sem.read_name(name, state, summary)

    def _write_name(
        self,
        name: str,
        value: AbsVal,
        state: AIState,
        summary: SummaryAccumulator,
    ) -> AIState:
        """Write a variable respecting Python scoping rules.
        
        Delegates to ScopeSemantics for proper handling of:
        - global variables (module namespace writes)
        - nonlocal variables (outer scope cell writes)
        - cell variables (closure storage writes)
        - local variables (env)
        
        Args:
            name: Variable name to write
            value: Value to write
            state: Current abstract state
            summary: Summary accumulator
            
        Returns:
            Updated abstract state
        """
        scope_sem = self._get_scope_semantics()
        return scope_sem.write_name(name, value, state, summary)

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
        For complex expressions (tuple/list literals), queries PTA for addresses.
        Special handling for:
        - Dict literals: Initialize SlotMap with key-value pairs
        - Comparisons with In/NotIn: Record predicate info for value partition
        """
        import ast
        
        lval = stmt.get_lval().id
        rval = stmt.stmt.value

        # Determine abstract value from RHS
        if isinstance(rval, ast.Constant):
            val = self._abstract_constant(rval.value)
            new_state = state.set_var(lval, val)
        elif isinstance(rval, ast.Name):
            val = state.get_var(rval.id)
            new_state = state.set_var(lval, val)
        elif isinstance(rval, ast.Dict):
            # Dict literal: Initialize dict SlotMap (via DictSemantics)
            new_state = self._handle_dict_literal(lval, rval, state, summary)
        elif isinstance(rval, ast.Compare) and len(rval.ops) == 1:
            # Check for In/NotIn comparisons: record predicate for value partition
            new_state = self._handle_compare_assign(lval, rval, state, summary)
        else:
            # Complex expression: try to get addresses from PTA for the lval
            val = self._get_pta_addrs_for_lval(lval, state)
            new_state = state.set_var(lval, val)

        return TransferResult.single(new_state)
    
    def _handle_dict_literal(
        self,
        lval: str,
        dict_expr: 'ast.Dict',
        state: AIState,
        summary: SummaryAccumulator,
    ) -> AIState:
        """Handle dict literal initialization: x = {k1: v1, k2: v2, ...}.
        
        Creates SlotMap and populates with key-value pairs.
        Also handles **unpack: {**d1, **d2, k: v} uses key=None entries in AST.
        """
        import ast
        from .domains.mapslots import DictSemantics
        
        # Get PTA addresses for the dict object
        dict_val = self._get_pta_addrs_for_lval(lval, state)
        new_state = state.set_var(lval, dict_val)
        
        if dict_val.is_bottom() or not dict_val.addrs:
            return new_state
        
        # Create DictSemantics for store operations
        dict_sem = DictSemantics(
            caller_scope=self.caller_scope,
            call_context=self.call_context,
            budget=self.budget,
        )
        
        # Process each key-value pair
        for key_expr, val_expr in zip(dict_expr.keys, dict_expr.values):
            if key_expr is None:
                # **unpack: {**d1} - update from the source dict
                if isinstance(val_expr, ast.Name):
                    other_val = new_state.get_var(val_expr.id)
                    new_state = dict_sem.dict_update(dict_val, other_val, new_state, summary)
                continue
            
            # Get abstract value for key
            if isinstance(key_expr, ast.Constant):
                key_val = self._abstract_constant(key_expr.value)
            elif isinstance(key_expr, ast.Name):
                key_val = new_state.get_var(key_expr.id)
            else:
                # Complex key expression - use top
                key_val = AbsVal.top()
            
            # Get abstract value for value
            if isinstance(val_expr, ast.Constant):
                value_val = self._abstract_constant(val_expr.value)
            elif isinstance(val_expr, ast.Name):
                value_val = new_state.get_var(val_expr.id)
            else:
                # Complex value expression - use top
                value_val = AbsVal.top()
            
            # Store key-value pair
            new_state = dict_sem.store_item(dict_val, key_val, value_val, new_state, summary)
        
        return new_state
    
    def _handle_compare_assign(
        self,
        lval: str,
        compare_expr: 'ast.Compare',
        state: AIState,
        summary: SummaryAccumulator,
    ) -> AIState:
        """Handle comparison assignment, recording predicate for In/NotIn.
        
        For: t = (k in d) or t = (k not in d)
        Records predicate info so ConditionRefiner can refine dict membership.
        """
        import ast
        from .state import PredicateInfo
        
        # Get PTA value for the comparison result (typically bool)
        val = self._get_pta_addrs_for_lval(lval, state)
        
        # For In/NotIn, also set bool top and record predicate
        op = compare_expr.ops[0]
        if isinstance(op, (ast.In, ast.NotIn)):
            # Set result to bool top (we don't know the result yet)
            val = AbsVal.bool_top()
            
            # Try to extract dict and key variables
            left = compare_expr.left  # This is the key
            right = compare_expr.comparators[0]  # This is the container
            
            key_var = None
            key_literal = None
            dict_var = None
            
            # Get key info
            if isinstance(left, ast.Name):
                key_var = left.id
            elif isinstance(left, ast.Constant):
                key_literal = left.value
            
            # Get dict info
            if isinstance(right, ast.Name):
                dict_var = right.id
            
            # Record predicate if we have enough info
            if dict_var is not None and (key_var is not None or key_literal is not None):
                pred_info = PredicateInfo(
                    kind="dict_contains" if isinstance(op, ast.In) else "dict_not_contains",
                    dict_var=dict_var,
                    key_var=key_var,
                    key_literal=key_literal,
                    negated=isinstance(op, ast.NotIn),
                )
                new_state = state.set_var(lval, val)
                return new_state.add_predicate(lval, pred_info)
        
        return state.set_var(lval, val)

    def _get_pta_addrs_for_lval(
        self,
        lval_name: str,
        state: AIState,
    ) -> AbsVal:
        """Get PTA addresses for a variable after assignment.
        
        For complex RHS expressions (tuple/list literals, calls, etc.),
        query PTA to get the allocation addresses for the LHS variable.
        This preserves the connection between AI and PTA heap.
        
        Args:
            lval_name: LHS variable name
            state: Current AI state (has pta_query)
            
        Returns:
            AbsVal with PTA addresses, or top() if not available
        """
        if self.pta_query is None or self.caller_scope is None or self.call_context is None:
            return AbsVal.top()
        
        from pythonstan.analysis.pointer.kcfa.variable import Variable, VariableKind
        
        # Create variable for the LHS
        # Use TEMPORARY for $tmp vars, LOCAL otherwise
        kind = VariableKind.TEMPORARY if lval_name.startswith("$") else VariableKind.LOCAL
        var = Variable(name=lval_name, kind=kind)
        
        # Query PTA for points-to set
        pts = self.pta_query.points_to(var, self.caller_scope, self.call_context)
        if pts:
            return AbsVal.from_addrs(frozenset(pts))
        
        return AbsVal.top()

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
        
        Delegates to AttributeSemantics.read_attr for the full Python
        attribute resolution protocol.
        
        Args:
            receiver_addrs: Set of receiver objects
            attr_name: Attribute name to look up
            state: Current abstract state
            summary: Summary accumulator
            
        Returns:
            AbsVal representing the attribute value
        """
        attr_semantics = self._get_attr_semantics(summary)
        return attr_semantics.read_attr(receiver_addrs, attr_name, state, summary)

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
        new_state = self._set_attr_const(
            receiver_addrs=obj_val.addrs,
            attr_name=attr_name,
            value_val=value_val,
            state=state,
            summary=summary,
        )

        return TransferResult.single(new_state)

    def _set_attr_const(
        self,
        receiver_addrs: FrozenSet['AbstractObject'],
        attr_name: str,
        value_val: AbsVal,
        state: AIState,
        summary: SummaryAccumulator,
    ) -> AIState:
        """Shared helper for attribute writes with descriptor/hook semantics.
        
        Delegates to AttributeSemantics.write_attr for the full Python
        attribute assignment protocol.
        
        Args:
            receiver_addrs: Set of receiver objects
            attr_name: Attribute name to write
            value_val: Value to write
            state: Current abstract state
            summary: Summary accumulator
        """
        attr_semantics = self._get_attr_semantics(summary)
        return attr_semantics.write_attr(
            receiver_addrs, attr_name, value_val, state, summary
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

        Delegates to SubscriptSemantics for bucket management.
        """
        import ast

        target = stmt.get_lval().id
        obj_name = stmt.get_obj().id
        index_expr = stmt.get_slice()

        obj_val = state.get_var(obj_name)

        # Determine index value
        index_val: Optional[AbsVal] = None
        if isinstance(index_expr, ast.Name):
            index_val = state.get_var(index_expr.id)

        # Delegate to SubscriptSemantics
        subscr_semantics = self._get_subscript_semantics()
        load_result = subscr_semantics.load_subscr(
            obj_val=obj_val,
            index_val=index_val,
            state=state,
            summary=summary,
        )

        new_state = state.set_var(target, load_result.result_val)
        return TransferResult.single(new_state)

    def _transfer_store_subscr(
        self,
        stmt: 'IRStoreSubscr',
        state: AIState,
        summary: SummaryAccumulator,
    ) -> TransferResult:
        """Transfer for IRStoreSubscr: obj[idx] = v.

        Delegates to SubscriptSemantics for bucket management.
        """
        import ast

        obj_name = stmt.get_obj().id
        value_name = stmt.get_rval().id
        index_expr = stmt.get_slice()

        obj_val = state.get_var(obj_name)
        value_val = state.get_var(value_name)

        # Determine index value
        index_val: Optional[AbsVal] = None
        if isinstance(index_expr, ast.Name):
            index_val = state.get_var(index_expr.id)

        # Delegate to SubscriptSemantics
        subscr_semantics = self._get_subscript_semantics()
        new_state = subscr_semantics.store_subscr(
            obj_val=obj_val,
            index_val=index_val,
            value_val=value_val,
            state=state,
            summary=summary,
        )

        return TransferResult.single(new_state)

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
        other calls via CallSemantics delegation.
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
        elif func_name == "iter":
            return self._handle_iter_builtin(stmt, state, summary)
        elif func_name == "next":
            return self._handle_next_builtin(stmt, state, summary)
        elif func_name == "list":
            return self._handle_list_intrinsic(stmt, state, summary)
        elif func_name == "tuple":
            return self._handle_tuple_intrinsic(stmt, state, summary)
        elif func_name == "globals":
            return self._handle_globals_builtin(stmt, state, summary)
        elif func_name == "locals":
            return self._handle_locals_builtin(stmt, state, summary)

        # Generic call: look up callee
        callee_val = state.get_var(func_name)
        callsite_id = self._get_callsite_id()

        # Gather argument values from IR, handling *args expansion
        pos_arg_vals: List[AbsVal] = []
        has_unexpanded_starred = False
        starred_expansion_alts: Optional[List[Tuple[Tuple[AbsVal, ...], int]]] = None

        for arg_name, is_starred in args:
            if arg_name.startswith("<"):
                pos_arg_vals.append(AbsVal.top_non_none())
            elif is_starred:
                # Try to expand starred argument
                starred_val = state.get_var(arg_name)
                expansion = self._try_expand_starred_arg(starred_val, state)
                if expansion is not None:
                    # Single expansion or will branch on multiple
                    if len(expansion) == 1:
                        # Single alternative: inline elements
                        elems, _ = expansion[0]
                        pos_arg_vals.extend(elems)
                    else:
                        # Multiple alternatives: need to branch
                        starred_expansion_alts = expansion
                        # Mark position where starred args should go
                        pos_arg_vals.append(AbsVal.top())  # Placeholder
                else:
                    # Cannot expand: mark as unknown
                    has_unexpanded_starred = True
                    pos_arg_vals.append(AbsVal.top())
            else:
                pos_arg_vals.append(state.get_var(arg_name))

        kw_arg_vals: Dict[str, AbsVal] = {}
        for kw_name, arg_name in kwargs:
            if kw_name is None:
                continue
            if arg_name.startswith("<"):
                kw_arg_vals[kw_name] = AbsVal.top_non_none()
            else:
                kw_arg_vals[kw_name] = state.get_var(arg_name)

        # If we have multiple starred expansion alternatives, branch on them
        if starred_expansion_alts is not None and len(starred_expansion_alts) > 1:
            return self._handle_call_with_starred_alts(
                stmt, state, summary, callee_val, callsite_id,
                args, pos_arg_vals, kw_arg_vals, starred_expansion_alts
            )

        # If we have unexpanded starred, treat as unknown call
        if has_unexpanded_starred:
            summary.add_unknown_effect("may_call", frozenset(callee_val.addrs))

        # Delegate call dispatch to CallSemantics
        call_semantics = self._get_call_semantics()
        dispatch_result = call_semantics.dispatch_call(
            callee_val=callee_val,
            pos_arg_vals=pos_arg_vals,
            kw_arg_vals=kw_arg_vals,
            callsite_id=callsite_id,
            state=state,
            summary=summary,
        )

        result_val = dispatch_result.result_val
        resolved_any = dispatch_result.resolved_any
        callee_addrs = dispatch_result.callee_addrs
        unresolved_addrs = dispatch_result.unresolved_addrs

        if callee_addrs:
            summary.add_call(callsite_id, frozenset(callee_addrs))

        new_state = state
        if dispatch_result.heap_updates:
            for ai_addr, ai_obj in dispatch_result.heap_updates:
                new_state = new_state.update_obj(ai_addr, ai_obj)
        if unresolved_addrs or not resolved_any:
            # Conservative: unknown call target
            if unresolved_addrs:
                summary.add_unknown_effect("may_call", frozenset(unresolved_addrs))
            elif callee_val.addrs:
                summary.add_unknown_effect("may_call", frozenset(callee_val.addrs))
            # Record unknown effect on arguments (they may be mutated)
            for arg_name, _is_starred in args:
                if not arg_name.startswith("<"):
                    arg_val = state.get_var(arg_name)
                    if arg_val.addrs:
                        summary.add_unknown_effect(
                            "may_write",
                            frozenset(arg_val.addrs),
                            None,  # All fields
                        )
                        for obj in arg_val.addrs:
                            new_state = new_state.add_overlay_havoc(obj)
            for kw_name, arg_name in kwargs:
                if arg_name.startswith("<"):
                    continue
                arg_val = state.get_var(arg_name)
                if arg_val.addrs:
                    summary.add_unknown_effect(
                        "may_write",
                        frozenset(arg_val.addrs),
                        None,
                    )
                    for obj in arg_val.addrs:
                        new_state = new_state.add_overlay_havoc(obj)

            # Unknown return
            result_val = result_val.join(AbsVal.top())

        if target:
            new_state = new_state.set_var(target, result_val if resolved_any else AbsVal.top())

        return TransferResult.single(new_state)

    def _handle_getattr(
        self,
        stmt: 'IRCall',
        state: AIState,
        summary: SummaryAccumulator,
    ) -> TransferResult:
        """Handle getattr(obj, name, default?) builtin.
        
        From ai_spec.md §8.1. Delegates to BuiltinAttributeSemantics.
        """
        target = stmt.get_target()
        args = stmt.get_args()

        if len(args) < 2:
            return self._handle_unknown_call(stmt, state, summary)

        obj_name = args[0][0]
        name_name = args[1][0]
        default_name = args[2][0] if len(args) > 2 else None

        obj_val = state.get_var(obj_name)
        name_val = state.get_var(name_name)
        
        # Get default value if provided
        default_val: Optional[AbsVal] = None
        if default_name and not default_name.startswith("<"):
            default_val = state.get_var(default_name)

        # Delegate to BuiltinAttributeSemantics
        builtin_semantics = self._get_builtin_attr_semantics(summary)
        result_val = builtin_semantics.handle_getattr(
            obj_addrs=obj_val.addrs,
            name_val=name_val,
            default_val=default_val,
            state=state,
            summary=summary,
        )

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
        
        From ai_spec.md §8.2. Delegates to BuiltinAttributeSemantics.
        """
        args = stmt.get_args()

        if len(args) < 3:
            return self._handle_unknown_call(stmt, state, summary)

        obj_name = args[0][0]
        name_name = args[1][0]
        value_name = args[2][0]

        obj_val = state.get_var(obj_name)
        name_val = state.get_var(name_name)
        value_val = state.get_var(value_name)

        # Delegate to BuiltinAttributeSemantics
        builtin_semantics = self._get_builtin_attr_semantics(summary)
        new_state = builtin_semantics.handle_setattr(
            obj_addrs=obj_val.addrs,
            name_val=name_val,
            value_val=value_val,
            state=state,
            summary=summary,
        )

        # setattr returns None
        if stmt.get_target():
            new_state = new_state.set_var(stmt.get_target(), AbsVal.none_val())

        return TransferResult.single(new_state)

    def _handle_hasattr(
        self,
        stmt: 'IRCall',
        state: AIState,
        summary: SummaryAccumulator,
    ) -> TransferResult:
        """Handle hasattr(obj, name) builtin.
        
        From ai_spec.md §8.3. Delegates to BuiltinAttributeSemantics.
        """
        target = stmt.get_target()
        args = stmt.get_args()

        if len(args) < 2:
            return self._handle_unknown_call(stmt, state, summary)

        obj_name = args[0][0]
        name_name = args[1][0]

        obj_val = state.get_var(obj_name)
        name_val = state.get_var(name_name)

        # Delegate to BuiltinAttributeSemantics
        builtin_semantics = self._get_builtin_attr_semantics(summary)
        result_val = builtin_semantics.handle_hasattr(
            obj_addrs=obj_val.addrs,
            name_val=name_val,
            summary=summary,
        )

        if target:
            new_state = state.set_var(target, result_val)
        else:
            new_state = state

        return TransferResult.single(new_state)

    def _handle_iter_builtin(
        self,
        stmt: 'IRCall',
        state: AIState,
        summary: SummaryAccumulator,
    ) -> TransferResult:
        """Handle iter(obj) builtin."""
        target = stmt.get_target()
        args = stmt.get_args()
        if len(args) < 1:
            return self._handle_unknown_call(stmt, state, summary)

        obj_name, is_starred = args[0]
        if is_starred or obj_name.startswith("<"):
            return self._handle_unknown_call(stmt, state, summary)

        obj_val = state.get_var(obj_name)
        iter_sem = self._get_iterator_semantics(summary)
        new_state, result_val = iter_sem.eval_iter(
            obj_val=obj_val,
            state=state,
            summary=summary,
            callsite_id=self._get_callsite_id(),
        )

        if target:
            new_state = new_state.set_var(target, result_val)
        return TransferResult.single(new_state)

    def _handle_next_builtin(
        self,
        stmt: 'IRCall',
        state: AIState,
        summary: SummaryAccumulator,
    ) -> TransferResult:
        """Handle next(it, default?) builtin."""
        target = stmt.get_target()
        args = stmt.get_args()
        if len(args) < 1:
            return self._handle_unknown_call(stmt, state, summary)

        it_name, is_starred = args[0]
        if is_starred or it_name.startswith("<"):
            return self._handle_unknown_call(stmt, state, summary)

        it_val = state.get_var(it_name)
        default_val: Optional[AbsVal] = None
        if len(args) > 1:
            default_name, default_starred = args[1]
            if default_starred or default_name.startswith("<"):
                return self._handle_unknown_call(stmt, state, summary)
            default_val = state.get_var(default_name)

        iter_sem = self._get_iterator_semantics(summary)
        new_state, result_val = iter_sem.eval_next(
            it_val=it_val,
            state=state,
            summary=summary,
            callsite_id=self._get_callsite_id(),
            default_val=default_val,
        )

        if target:
            new_state = new_state.set_var(target, result_val)
        return TransferResult.single(new_state)

    def _handle_globals_builtin(
        self,
        stmt: 'IRCall',
        state: AIState,
        summary: SummaryAccumulator,
    ) -> TransferResult:
        """Handle globals() builtin.
        
        Returns a dict-like object representing the module namespace.
        For constant key access (globals()['k']), precision is maintained.
        
        Delegates to ScopeSemantics.eval_globals_dict for the actual modeling.
        """
        target = stmt.get_target()
        
        scope_sem = self._get_scope_semantics()
        result_val = scope_sem.eval_globals_dict(state, summary)
        
        if target:
            new_state = state.set_var(target, result_val)
        else:
            new_state = state
        
        return TransferResult.single(new_state)

    def _handle_locals_builtin(
        self,
        stmt: 'IRCall',
        state: AIState,
        summary: SummaryAccumulator,
    ) -> TransferResult:
        """Handle locals() builtin.
        
        Returns a dict-like snapshot of current local variables.
        Writes to the returned dict are conservative (CPython doesn't
        guarantee writeback to actual locals).
        
        Delegates to ScopeSemantics.eval_locals_dict for the actual modeling.
        """
        target = stmt.get_target()
        
        scope_sem = self._get_scope_semantics()
        result_val = scope_sem.eval_locals_dict(state, summary)
        
        if target:
            new_state = state.set_var(target, result_val)
        else:
            new_state = state
        
        return TransferResult.single(new_state)

    def _handle_list_intrinsic(
        self,
        stmt: 'IRCall',
        state: AIState,
        summary: SummaryAccumulator,
    ) -> TransferResult:
        """Handle list(iterable) builtin for tuple unpacking precision.
        
        When list() is called with a tuple argument, we materialize the tuple
        shape and write key(i) fields into the resulting list object. This
        enables precise per-position access for destructuring patterns like:
            tmp = list(t)
            x = tmp[0]
            y = tmp[1]
        """
        from pythonstan.analysis.pointer.kcfa.heap_model import key, elem
        from .domains.tuples import materialize_tuple_disj
        
        target = stmt.get_target()
        args = stmt.get_args()
        
        if not target or len(args) != 1:
            # Not the form we can handle precisely
            return self._handle_unknown_call(stmt, state, summary)
        
        arg_name, is_starred = args[0]
        if is_starred or arg_name.startswith("<"):
            return self._handle_unknown_call(stmt, state, summary)
        
        # Get argument value (the tuple/iterable)
        arg_val = state.get_var(arg_name)
        
        # Get PTA addresses for the target list variable
        target_val = self._get_pta_addrs_for_lval(target, state)
        if not target_val.addrs:
            # No addresses from PTA - fall back to unknown
            return self._handle_unknown_call(stmt, state, summary)
        
        # Try to materialize tuple shape from argument
        max_len = getattr(self.budget, "max_tuple_len_exact", 8)
        max_alts = getattr(self.budget, "max_tuple_alts", 4)
        
        disj = materialize_tuple_disj(
            arg_val, state, self.caller_scope, self.call_context,
            max_len=max_len, max_alts=max_alts
        )
        
        new_state = state.set_var(target, target_val)
        
        if not disj.is_empty():
            # Write key(i) fields for each position
            exact_alts = disj.get_exact_alts()
            if exact_alts:
                # Use first exact alt for max length
                max_exact_len = max(alt.length for alt in exact_alts)
                
                for i in range(max_exact_len):
                    # Join all values at position i across alternatives
                    pos_val = disj.get(i)
                    if pos_val is not None:
                        field_key = key(str(i))
                        for list_obj in target_val.addrs:
                            new_state = new_state.write_field_value(
                                list_obj, field_key, pos_val
                            )
                            if pos_val.addrs:
                                summary.add_write(
                                    frozenset({list_obj}),
                                    field_key,
                                    frozenset(pos_val.addrs),
                                )
                            # Also write to elem() for soundness
                            new_state = new_state.write_field_value(
                                list_obj, elem(), pos_val
                            )
            else:
                # Only summaries - write to elem() only
                for i in range(max_len):
                    pos_val = disj.get(i)
                    if pos_val is not None:
                        for list_obj in target_val.addrs:
                            new_state = new_state.write_field_value(
                                list_obj, elem(), pos_val
                            )
        
        return TransferResult.single(new_state)

    def _handle_tuple_intrinsic(
        self,
        stmt: 'IRCall',
        state: AIState,
        summary: SummaryAccumulator,
    ) -> TransferResult:
        """Handle tuple(iterable) builtin.
        
        Similar to list() but for creating tuples. Materializes input and
        writes key(i) fields into resulting tuple object.
        """
        from pythonstan.analysis.pointer.kcfa.heap_model import key, elem
        from .domains.tuples import materialize_tuple_disj
        
        target = stmt.get_target()
        args = stmt.get_args()
        
        if not target or len(args) != 1:
            return self._handle_unknown_call(stmt, state, summary)
        
        arg_name, is_starred = args[0]
        if is_starred or arg_name.startswith("<"):
            return self._handle_unknown_call(stmt, state, summary)
        
        arg_val = state.get_var(arg_name)
        target_val = self._get_pta_addrs_for_lval(target, state)
        
        if not target_val.addrs:
            return self._handle_unknown_call(stmt, state, summary)
        
        max_len = getattr(self.budget, "max_tuple_len_exact", 8)
        max_alts = getattr(self.budget, "max_tuple_alts", 4)
        
        disj = materialize_tuple_disj(
            arg_val, state, self.caller_scope, self.call_context,
            max_len=max_len, max_alts=max_alts
        )
        
        new_state = state.set_var(target, target_val)
        
        if not disj.is_empty():
            exact_alts = disj.get_exact_alts()
            if exact_alts:
                max_exact_len = max(alt.length for alt in exact_alts)
                
                for i in range(max_exact_len):
                    pos_val = disj.get(i)
                    if pos_val is not None:
                        field_key = key(str(i))
                        for tuple_obj in target_val.addrs:
                            new_state = new_state.write_field_value(
                                tuple_obj, field_key, pos_val
                            )
                            if pos_val.addrs:
                                summary.add_write(
                                    frozenset({tuple_obj}),
                                    field_key,
                                    frozenset(pos_val.addrs),
                                )
                            new_state = new_state.write_field_value(
                                tuple_obj, elem(), pos_val
                            )
            else:
                for i in range(max_len):
                    pos_val = disj.get(i)
                    if pos_val is not None:
                        for tuple_obj in target_val.addrs:
                            new_state = new_state.write_field_value(
                                tuple_obj, elem(), pos_val
                            )
        
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
        new_state = state
        for arg_name, is_starred in args:
            if not arg_name.startswith("<"):
                arg_val = state.get_var(arg_name)
                if arg_val.addrs:
                    summary.add_unknown_effect(
                        "may_write",
                        frozenset(arg_val.addrs),
                        None,
                    )
                    for obj in arg_val.addrs:
                        new_state = new_state.add_overlay_havoc(obj)

        if target:
            new_state = new_state.set_var(target, AbsVal.top())

        return TransferResult.single(new_state)

    def _try_expand_starred_arg(
        self,
        starred_val: AbsVal,
        state: AIState,
    ) -> Optional[List[Tuple[Tuple[AbsVal, ...], int]]]:
        """Try to expand a starred argument value.
        
        If the value can be materialized as small TupleExact alternatives,
        returns list of (elements, length) pairs.
        Otherwise returns None (caller should treat as unknown).
        
        Args:
            starred_val: Abstract value representing *args
            state: Current AI state
            
        Returns:
            List of (element_tuple, length) pairs, or None if not expandable
        """
        from .domains.tuples import expand_starred_arg
        
        max_len = getattr(self.budget, "max_star_expand_len", 8)
        max_alts = getattr(self.budget, "max_star_expand_alts", 4)
        
        return expand_starred_arg(
            starred_val, state, self.caller_scope, self.call_context,
            max_expand_len=max_len, max_expand_alts=max_alts
        )

    def _handle_call_with_starred_alts(
        self,
        stmt: 'IRCall',
        state: AIState,
        summary: SummaryAccumulator,
        callee_val: AbsVal,
        callsite_id: str,
        args: List[Tuple[str, bool]],
        base_pos_arg_vals: List[AbsVal],
        kw_arg_vals: Dict[str, AbsVal],
        starred_alts: List[Tuple[Tuple[AbsVal, ...], int]],
    ) -> TransferResult:
        """Handle call with multiple starred expansion alternatives.
        
        Branches on each starred alternative, dispatching the call with
        the expanded positional arguments.
        
        Args:
            stmt: The IRCall statement
            state: Current AI state
            summary: Summary accumulator
            callee_val: Callee abstract value
            callsite_id: Callsite identifier
            args: Original IR args list
            base_pos_arg_vals: Positional arg values before starred expansion
            kw_arg_vals: Keyword argument values
            starred_alts: List of (elements, length) for each expansion alternative
            
        Returns:
            TransferResult with joined result from all alternatives
        """
        target = stmt.get_target()
        call_semantics = self._get_call_semantics()
        
        # Find the position of the starred arg placeholder
        starred_pos = -1
        for i, (arg_name, is_starred) in enumerate(args):
            if is_starred and not arg_name.startswith("<"):
                starred_pos = i
                break
        
        if starred_pos < 0:
            # No starred arg found, shouldn't happen
            return self._handle_unknown_call(stmt, state, summary)
        
        result_states: List[Tuple[Optional[str], AIState]] = []
        
        for elems, _ in starred_alts:
            # Build pos_arg_vals with this alternative's expansion
            # Note: base_pos_arg_vals already has a placeholder at starred_pos
            expanded_pos_args: List[AbsVal] = []
            for i, val in enumerate(base_pos_arg_vals):
                if i == starred_pos:
                    # Replace placeholder with expanded elements
                    expanded_pos_args.extend(elems)
                else:
                    expanded_pos_args.append(val)
            
            # Dispatch call with expanded args
            # Use a copy of state for each alternative to avoid cross-contamination
            alt_state = state.copy()
            dispatch_result = call_semantics.dispatch_call(
                callee_val=callee_val,
                pos_arg_vals=expanded_pos_args,
                kw_arg_vals=kw_arg_vals,
                callsite_id=callsite_id,
                state=alt_state,
                summary=summary,
            )
            
            result_val = dispatch_result.result_val
            callee_addrs = dispatch_result.callee_addrs
            
            if callee_addrs:
                summary.add_call(callsite_id, frozenset(callee_addrs))
            
            if target:
                alt_state = alt_state.set_var(target, result_val)
            
            result_states.append((None, alt_state))
        
        # Join all alternative states
        if len(result_states) == 0:
            return self._handle_unknown_call(stmt, state, summary)
        elif len(result_states) == 1:
            return TransferResult.single(result_states[0][1])
        else:
            # Join all states
            joined_state = result_states[0][1]
            for _, alt_state in result_states[1:]:
                joined_state = joined_state.join(alt_state)
            return TransferResult.single(joined_state)

    # =========================================================================
    # Return/Raise transfers
    # =========================================================================

    def _transfer_yield(
        self,
        stmt: 'IRYield',
        state: AIState,
        summary: SummaryAccumulator,
    ) -> TransferResult:
        """Transfer for IRYield: [x =] yield v."""
        yield_val = AbsVal.none_val()
        if stmt.value is not None and isinstance(stmt.value, ast.Name):
            yield_val = self._read_name(stmt.value.id, state, summary)

        new_state = state
        gen_val = state.get_var("$__gen")
        if gen_val.addrs:
            from .domains.mapslots import Presence
            from .heap import AIAddr

            for gen_obj in gen_val.addrs:
                ai_addr = AIAddr.from_kcfa_obj(gen_obj)
                ai_obj = new_state.get_obj(ai_addr)
                if ai_obj is None:
                    continue
                new_slots = ai_obj.slots.weak_update_str(
                    "__yielded__",
                    yield_val,
                    Presence.PRESENT,
                )
                new_state = new_state.set_obj(ai_addr, ai_obj.with_slots(new_slots))

        if stmt.target is not None and isinstance(stmt.target, ast.Name):
            send_val = state.get_var("$__send")
            if send_val.is_bottom():
                send_val = AbsVal.none_val()
            new_state = self._write_name(stmt.target.id, send_val, new_state, summary)

        return TransferResult.single(new_state)

    def _transfer_return(
        self,
        stmt: 'IRReturn',
        state: AIState,
        summary: SummaryAccumulator,
    ) -> TransferResult:
        """Transfer for IRReturn: return v."""
        ret_var = stmt.value  # IRReturn.value is the returned variable ID (str) or None
        
        if ret_var:
            ret_val = state.get_var(ret_var)
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
        test_expr = getattr(stmt, "test", None)
        target_label = stmt.label  # Use Label object directly

        # Delegate to ConditionRefiner
        refiner = self._get_condition_refiner()
        true_state, false_state = refiner.refine(
            state, test_expr, is_jump_if_true=False
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
        test_expr = getattr(stmt, "test", None)
        target_label = stmt.label  # Use Label object directly

        # Delegate to ConditionRefiner
        refiner = self._get_condition_refiner()
        true_state, false_state = refiner.refine(
            state, test_expr, is_jump_if_true=True
        )

        return TransferResult(states=[
            (target_label, true_state),  # Jump (condition is true)
            (None, false_state),  # Fall through (condition is false)
        ])

    def _refine_states_for_condition(
        self,
        state: AIState,
        cond_name: str,
        is_jump_if_true: bool,
    ) -> Tuple[AIState, AIState]:
        """Legacy wrapper used by older tests; delegates to ConditionRefiner."""
        refiner = self._get_condition_refiner()
        # Create a simple Name node for the variable
        name_node = ast.Name(id=cond_name, ctx=ast.Load())
        return refiner.refine(state, name_node, is_jump_if_true=is_jump_if_true)

    def _transfer_goto(
        self,
        stmt: 'Goto',
        state: AIState,
        summary: SummaryAccumulator,
    ) -> TransferResult:
        """Transfer for Goto: goto label."""
        target_label = stmt.label  # Use Label object directly
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
        """Create AbsVal from Python constant.

        Abstracts Python constants into their domain representations:
        - None -> none domain
        - bool -> bool domain (True/False)
        - int -> int domain (singleton constant)
        - str -> string domain (singleton constant)
        - float -> top (no float domain yet)
        """
        if value is None:
            return AbsVal.none_val()
        # Important: check bool before int because bool is subclass of int
        if isinstance(value, bool):
            return AbsVal.bool_true() if value else AbsVal.bool_false()
        if isinstance(value, int):
            return AbsVal.from_int(value)
        if isinstance(value, str):
            return AbsVal.from_str(value)
        if isinstance(value, float):
            # Float domain not implemented; use top
            return AbsVal.top_non_none()
        return AbsVal.top()

    def _collect_param_addrs(
        self,
        arg_vals: List[AbsVal],
        kw_vals: Dict[str, AbsVal],
    ) -> Tuple['AbstractObject', ...]:
        """Collect argument addresses for context selection."""
        addrs: List['AbstractObject'] = []
        for val in arg_vals:
            addrs.extend(val.addrs)
        for val in kw_vals.values():
            addrs.extend(val.addrs)
        # Deterministic ordering for hashing
        return tuple(sorted(addrs, key=lambda o: str(o)))

    def _select_call_context(
        self,
        callsite_id: str,
        argc: int,
        receiver_obj: Optional['AbstractObject'],
        params: Optional[Tuple['AbstractObject', ...]],
    ) -> Optional['AbstractContext']:
        """Select a call context via kcfa adapter if available."""
        if self.call_context is None:
            return None
        if not hasattr(self.pta_query, "select_call_context"):
            return None
        return self.pta_query.select_call_context(
            callsite_id,
            argc,
            self.call_context,
            receiver_obj,
            params=params,
        )

    def _build_arg_vals_for_scope(
        self,
        scope_ir: 'IRFunc',
        pos_args: List[AbsVal],
        kw_args: Dict[str, AbsVal],
        receiver_val: Optional[AbsVal],
    ) -> List[Tuple[str, AbsVal]]:
        """Map call arguments to callee parameter names (best-effort)."""
        param_map: Dict[str, AbsVal] = {}
        param_names: List[str] = []
        
        func_args = scope_ir.args
        posonly = getattr(func_args, "posonlyargs", [])
        param_names.extend([arg.arg for arg in posonly])
        param_names.extend([arg.arg for arg in func_args.args])

        self_name = self._get_self_param_name(scope_ir) if receiver_val is not None else None
        if self_name and param_names and param_names[0] == self_name:
            param_names = param_names[1:]

        # Positional arguments
        for idx, arg_val in enumerate(pos_args):
            if idx < len(param_names):
                param_name = param_names[idx]
            else:
                param_name = f"arg{idx}"
            if param_name in param_map:
                param_map[param_name] = param_map[param_name].join(arg_val)
            else:
                param_map[param_name] = arg_val

        # Keyword arguments
        for name, arg_val in kw_args.items():
            if name is None:
                continue
            if name in param_map:
                param_map[name] = param_map[name].join(arg_val)
            else:
                param_map[name] = arg_val

        # *args collection (join of extras)
        if func_args.vararg and len(pos_args) > len(param_names):
            vararg_name = func_args.vararg.arg
            extra_vals = pos_args[len(param_names):]
            joined_extra = AbsVal.bottom()
            for v in extra_vals:
                joined_extra = joined_extra.join(v)
            if vararg_name in param_map:
                param_map[vararg_name] = param_map[vararg_name].join(joined_extra)
            else:
                param_map[vararg_name] = joined_extra

        # **kwargs collection (join of extras)
        if func_args.kwarg:
            kwarg_name = func_args.kwarg.arg
            extra_vals = [v for k, v in kw_args.items() if k not in param_map]
            if extra_vals:
                joined_extra = AbsVal.bottom()
                for v in extra_vals:
                    joined_extra = joined_extra.join(v)
                if kwarg_name in param_map:
                    param_map[kwarg_name] = param_map[kwarg_name].join(joined_extra)
                else:
                    param_map[kwarg_name] = joined_extra

        return list(param_map.items())

    # =========================================================================
    # Bounded callee invocation (for descriptor/hook calls)
    # =========================================================================

    def invoke_callee(
        self,
        callee_obj: 'AbstractObject',
        arg_vals: List[Tuple[str, AbsVal]],
        summary: SummaryAccumulator,
        receiver_val: Optional[AbsVal] = None,
        callsite_id: Optional[str] = None,
        call_context: Optional['AbstractContext'] = None,
        argc: Optional[int] = None,
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

        # Select per-call context if available
        callsite_id = callsite_id or self._get_callsite_id()
        argc_val = argc if argc is not None else len(arg_vals)
        receiver_obj = None
        if actual_receiver is not None and actual_receiver.addrs:
            receiver_obj = next(iter(actual_receiver.addrs))
        params = self._collect_param_addrs([v for _, v in arg_vals], {})
        callee_ctx = call_context or self._select_call_context(callsite_id, argc_val, receiver_obj, params)
        if callee_ctx is None:
            callee_ctx = self.call_context
        
        # Create engine and analyze callee with incremented recursion depth
        # Note: AISummaryEngine.analyze_callee() handles exceptions at its boundary
        engine = AISummaryEngine(self.pta_query, self.budget)
        callee_summary = engine.analyze_callee(
            scope_ir=scope_ir,
            call_context=callee_ctx,
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

    def _get_callee_scope_ir(self, callee_obj: 'AbstractObject') -> Optional['IRFunc']:
        """Get the IR scope for a callable object.
        
        For kcfa FunctionObject/MethodObject, use their direct `ir` attribute.
        Falls back to World lookup if the direct attribute is not available.
        
        Args:
            callee_obj: The callable object
            
        Returns:
            IRFunc if available, None otherwise
        """
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
        from pythonstan.world import World
        
        world = World()
        if not hasattr(world, 'scope_manager') or world.scope_manager is None:
            return None
        
        # Extract qualname from callee_obj using available attributes
        qualname = None
        if hasattr(callee_obj, 'qualname'):
            qualname = callee_obj.qualname
        elif hasattr(callee_obj, 'name'):
            qualname = callee_obj.name
        elif hasattr(callee_obj, 'get_qualname'):
            qualname = callee_obj.get_qualname()
        
        if qualname is None:
            return None
        
        scope = world.scope_manager.get_module(qualname)
        if scope is not None and isinstance(scope, IRFunc):
            return scope
        
        return None

    def _get_self_param_name(self, scope_ir: 'IRFunc') -> Optional[str]:
        """Get the 'self' or 'cls' parameter name from a function scope.
        
        Args:
            scope_ir: The IRFunc to examine
            
        Returns:
            The first parameter name, or None if no parameters
        """
        if hasattr(scope_ir, 'args') and scope_ir.args is not None:
            args = scope_ir.args
            if hasattr(args, 'args') and len(args.args) > 0:
                return args.args[0].arg
        return None

    def _should_inject_receiver(self, scope_ir: 'IRFunc') -> bool:
        """Check if receiver (self/cls) should be injected for this function.
        
        Static methods don't get receiver injection.
        
        Args:
            scope_ir: The IRFunc to check
            
        Returns:
            True if receiver should be injected
        """
        if hasattr(scope_ir, 'is_static_method') and scope_ir.is_static_method:
            return False
        return True

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
        
        return None

