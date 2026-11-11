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
    exceptional_states: List[Tuple[str, AIState]] = field(default_factory=list)

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
        solver_config: Optional[Any] = None,
        work_ledger: Optional[Any] = None,
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
        from copy import deepcopy
        self.solver_config = deepcopy(solver_config)
        self.work_ledger = work_ledger

        # Current execution location for callsite IDs
        self._current_block_id: Optional[int] = None
        self._current_stmt_index: Optional[int] = None

        # Shared ClassInfoCache for attribute semantics (created lazily)
        self._class_info_cache: Optional['ClassInfoCache'] = None

        # Scope semantics for name resolution (closure/global/nonlocal support)
        self._scope_semantics: Optional[ScopeSemantics] = None
        self._active_state: Optional[AIState] = None
        self._call_exit_states: List[AIState] = []
        self._call_normal_exits: List[bool] = []
        self._active_summary: Optional[SummaryAccumulator] = None
        self._pending_exceptions: List[Tuple[str, AIState]] = []
        self._owned_locals: Set[str] = set()
        self._captured_locals: Set[str] = set()
        if scope_ir is not None and getattr(scope_ir, "stmt", None) is not None:
            from .lexical_bindings import local_bindings
            import ast
            node = scope_ir.stmt
            if hasattr(node, "body"):
                self._owned_locals = set(local_bindings(node))
                for child in ast.walk(node):
                    if child is node or not isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                        continue
                    child_locals = set(local_bindings(child)) if hasattr(child, "body") and isinstance(child.body, list) else set()
                    free_reads = {load.id for load in ast.walk(child) if isinstance(load, ast.Name) and isinstance(load.ctx, ast.Load)} - child_locals
                    nonlocals = {name for declaration in ast.walk(child) if isinstance(declaration, ast.Nonlocal) for name in declaration.names}
                    self._captured_locals.update((free_reads | nonlocals) & self._owned_locals)
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
            IRContextEnter, IRContextExit,
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
            IRFunc: self._transfer_definition,
            IRClass: self._transfer_pass,
            IRAstStmt: self._transfer_opaque_ast,
            IRContextEnter: self._transfer_context_enter,
            IRContextExit: self._transfer_context_exit,
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
        # Incomplete source lowering must survive successful local transfers.
        # This is an obligation marker, not an invented heap-top transfer.
        if self.work_ledger is not None and self.work_ledger.consume(kind="abstract_transfer") is False:
            summary.mark_incomplete("shared_work_budget")
            return TransferResult.terminal()
        for residual in getattr(stmt, "get_semantic_residuals", lambda: ())():
            summary.mark_incomplete(residual.code)
        handler = self._handlers.get(type(stmt))
        if handler:
            self._active_state = state
            self._active_summary = summary
            self._pending_exceptions = []
            loads = stmt.get_loads() if hasattr(stmt, "get_loads") else ()
            if any(state.get_var(name).addr_top for name in loads):
                summary.mark_incomplete("unknown_address_input")
            result = handler(stmt, state, summary)
            result.exceptional_states.extend(self._pending_exceptions)
            return self._route_exceptions(stmt, result, summary)
        else:
            # Unknown statement: pass through unchanged
            logger.warning(f"Unknown IR statement type: {type(stmt).__name__}")
            summary.mark_incomplete("unsupported_statement")
            return TransferResult.single(self._forget_mutable_sequence_lengths(state))

    def _route_exceptions(self, stmt, result, summary):
        """Select source-derived handlers while preserving exceptional states."""
        import builtins
        for exception_type, exception_state in result.exceptional_states:
            summary.prefix_exceptions.add(exception_type)
            if exception_type == "<unknown>":
                # Unknown exception classes may select any source handler.
                # Preserve escaping behavior as well; no missing edge proves
                # that a catch list is exhaustive.
                for plan in getattr(stmt, "exception_handlers", ()):
                    caught = exception_state.copy()
                    caught.exn = AbsVal.top()
                    caught = caught.set_var("@caught_except", AbsVal.top())
                    result.states.append((plan.goto_label, caught))
                summary.add_may_raise("BaseException", exception_state)
                summary.mark_incomplete("unknown_exception_type")
                continue
            selected = None
            for plan in getattr(stmt, "exception_handlers", ()):
                names = getattr(plan, "exception", None)
                if names and ("@dynamic_exception_type" in names or any(
                        not isinstance(getattr(builtins, name, None), type) for name in names)):
                    caught = exception_state.copy()
                    caught.exn = AbsVal.from_str(exception_type)
                    caught = caught.set_var("@caught_except", caught.exn)
                    result.states.append((plan.goto_label, caught))
                    summary.mark_incomplete("unresolved_handler_type")
                    continue
                if not names:
                    selected = plan
                    break
                concrete = getattr(builtins, exception_type, None)
                if any(name == exception_type or
                       (isinstance(concrete, type) and isinstance(getattr(builtins, name, None), type)
                        and issubclass(concrete, getattr(builtins, name))) for name in names):
                    selected = plan
                    break
            if selected is None:
                summary.add_may_raise(exception_type, exception_state)
                continue
            caught = exception_state.copy()
            payload = AbsVal.from_str(exception_type)
            caught.exn = payload
            caught = caught.set_var("@caught_except", payload)
            result.states.append((selected.goto_label, caught))
        if result.states and result.exceptional_states:
            result.is_terminal = False
        result.exceptional_states = []
        return result

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
                positional_args=True,
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
                positional_args=True,
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
        kind = self._name_kind(name)
        shared = self._shared_name_value(name, state, kind)
        if shared is not None:
            return shared
        value = scope_sem.read_name(name, state, summary)
        value = self._value_with_constant_projection(value)
        if name not in state.env:
            # TAC hoists constants and functions into enclosing scopes. They
            # need not have a local seed when entering a function summary.
            external = self._get_pta_addrs_for_lval(name, state)
            if external.addrs:
                return state._reduce_value(name, self._value_with_constant_projection(external))
            import builtins
            candidate = getattr(builtins, name, None)
            if isinstance(candidate, type) and issubclass(candidate, BaseException) and self._unshadowed_builtin(name):
                from pythonstan.analysis.pointer.kcfa.object import BuiltinClassObject, AllocSite, AllocKind
                from pythonstan.analysis.pointer.kcfa.context import CallStringContext
                obj = BuiltinClassObject(CallStringContext(), AllocSite("builtin:" + name, AllocKind.BUILTIN), name)
                return AbsVal.from_addrs(frozenset({obj}))
        return value

    def _unshadowed_builtin(self, name):
        """Prove absence of source lexical bindings before using a model."""
        import ast
        from .lexical_bindings import local_bindings
        from pythonstan.world import World
        qualname = self.scope_ir.get_qualname() if self.scope_ir is not None else ""
        manager = getattr(World(), "scope_manager", None)
        if not qualname or manager is None:
            return False
        parts = qualname.split(".")
        checked_module = False
        for end in range(len(parts), 0, -1):
            scope = manager.get_module(".".join(parts[:end]))
            if scope is None or not hasattr(scope, "get_ast"):
                continue
            node = scope.get_ast()
            if node is None or not hasattr(node, "body"):
                continue
            if name in local_bindings(node):
                return False
            if isinstance(node, ast.Module):
                checked_module = True
                if any(isinstance(item, ast.ImportFrom) and any(alias.name == "*" for alias in item.names)
                       for item in node.body):
                    return False
        return checked_module

    def _value_with_constant_projection(self, value):
        """Translate a closed scalar-object projection into full domain facts.

        Native constant addresses carry values as well as identities. Keeping
        the full address set and deriving scalar components is an exact bridge,
        independent of Native return facts or a second analysis result.
        """
        from pythonstan.analysis.pointer.kcfa.object import ConstantObject
        if value.scalar_kinds is not None:
            # Existing scalar facts can be stricter than retained allocation
            # witnesses after a guard or narrowing; do not undo that bound.
            return value
        if value.addr_top or not value.addrs or any(not isinstance(obj, ConstantObject) for obj in value.addrs):
            return value
        scalar = AbsVal.bottom()
        for obj in value.addrs:
            scalar = scalar.join(self._abstract_constant(obj.value))
        return AbsVal(addrs=value.addrs, components=scalar.components,
                      addr_top=False, registry=value.registry, scalar_kinds=scalar.scalar_kinds)

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
        kind = self._name_kind(name)
        from pythonstan.ir.ir_statements import IRModule
        if kind in (NameKind.CELL, NameKind.NONLOCAL, NameKind.GLOBAL) or isinstance(self.scope_ir, IRModule):
            result = state.set_var(name, value)
            keys = self._shared_name_keys(name, state, kind)
            if not keys:
                scope = self.scope_ir.get_qualname() if self.scope_ir is not None else "<unknown>"
                if kind == NameKind.GLOBAL:
                    scope = scope.split(".", 1)[0]
                keys = [(scope + "@" + repr(self.call_context), name)]
            for key in keys:
                result.shared_bindings[key] = value
            if len(keys) > 1:
                summary.mark_incomplete("ambiguous_lexical_activation")
            return result
        return state.set_var(name, value)

    def _name_kind(self, name):
        kind = self._get_scope_semantics().classify_name(name)
        if kind == NameKind.CELL and name not in self._owned_locals:
            return NameKind.NONLOCAL
        if kind == NameKind.LOCAL and name in self._captured_locals:
            return NameKind.CELL
        return kind

    def _shared_name_keys(self, name, state, kind):
        scope = self.scope_ir.get_qualname() if self.scope_ir is not None else ""
        own = scope + "@" + repr(self.call_context)
        if kind == NameKind.CELL:
            return [(own, name)] if (own, name) in state.shared_bindings else []
        candidates = []
        for owner, binding in state.shared_bindings:
            defining_scope = owner.split("@", 1)[0]
            if binding != name:
                continue
            if kind == NameKind.GLOBAL:
                if defining_scope == scope.split(".", 1)[0]:
                    candidates.append((owner, binding))
            elif scope.startswith(defining_scope + "."):
                candidates.append((owner, binding))
        if not candidates:
            return []
        depth = max(len(owner.split("@", 1)[0]) for owner, _ in candidates)
        return [(owner, binding) for owner, binding in candidates if len(owner.split("@", 1)[0]) == depth]

    def _shared_name_value(self, name, state, kind):
        # A true local binding is never shadowed by an enclosing cell.
        if kind == NameKind.LOCAL:
            from .lexical_bindings import local_bindings
            ast_node = getattr(self.scope_ir, "stmt", None)
            if name in state.env or ast_node is not None and name in local_bindings(ast_node):
                return None
        keys = self._shared_name_keys(name, state, kind)
        if not keys:
            return None
        value = state.shared_bindings[keys[0]]
        for key in keys[1:]:
            value = value.join(state.shared_bindings[key])
        return value

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
        if not (isinstance(rval, (ast.Constant, ast.Name)) or
                isinstance(rval, (ast.List, ast.Tuple)) and not any(isinstance(item, ast.Starred) for item in rval.elts)):
            # Dictionary keys and generic operators may invoke user hooks.
            state = self._forget_mutable_sequence_lengths(state)

        # Determine abstract value from RHS
        if isinstance(rval, ast.Constant):
            val = self._abstract_constant(rval.value)
            # Scalar components alone cannot cross the current address-based
            # AISummary boundary. Keep a constant allocation witness as well;
            # this is an exact literal, not an invented unknown heap object.
            from pythonstan.analysis.pointer.kcfa.object import ConstantObject, AllocKind, AllocSite
            from pythonstan.analysis.pointer.kcfa.context import CallStringContext
            witness = ConstantObject(
                context=self.call_context or CallStringContext(),
                alloc_site=AllocSite(stmt, AllocKind.CONSTANT), value=rval.value,
            )
            val = AbsVal(addrs=frozenset({witness}), components=val.components, addr_top=val.addr_top, registry=val.registry, scalar_kinds=val.scalar_kinds)
            new_state = self._write_name(lval, val, state, summary)
        elif isinstance(rval, ast.Name):
            val = self._read_name(rval.id, state, summary)
            new_state = self._write_name(lval, val, state, summary)
            new_state = self._preserve_copied_predicate(state, new_state, rval.id, lval)
        elif isinstance(rval, (ast.List, ast.Tuple)) and not any(isinstance(item, ast.Starred) for item in rval.elts):
            new_state = self._handle_sequence_literal(stmt, lval, rval, state, summary)
        elif isinstance(rval, ast.Dict):
            # Dict literal: Initialize dict SlotMap (via DictSemantics)
            new_state = self._handle_dict_literal(lval, rval, state, summary)
        elif isinstance(rval, ast.Compare) and len(rval.ops) == 1:
            # Check for In/NotIn comparisons: record predicate for value partition
            new_state = self._handle_compare_assign(lval, rval, state, summary, statement=stmt)
        elif isinstance(rval, (ast.BinOp, ast.UnaryOp)):
            from .scalar_transfer import finite_integer_transfer
            val = finite_integer_transfer(rval, lambda name: self._read_name(name, state, summary), stmt, self.call_context)
            if val is None:
                summary.mark_incomplete("unsupported_or_unbounded_scalar_operation")
                val = self._get_pta_addrs_for_lval(lval, state)
            new_state = self._write_name(lval, val, state, summary)
        else:
            # Complex expression: try to get addresses from PTA for the lval
            val = self._get_pta_addrs_for_lval(lval, state)
            new_state = self._write_name(lval, val, state, summary)

        return TransferResult.single(new_state)
    
    def _handle_sequence_literal(self, stmt, lval, expression, state, summary):
        """Allocate exact literal sequence contents and a separate size fact."""
        from .heap import AIAddr, AbsObj, ObjKind, ContainerSlots
        from .domains.ints import IntLattice
        from pythonstan.analysis.pointer.kcfa.object import AbstractObject, AllocKind, AllocSite
        from pythonstan.analysis.pointer.kcfa.context import CallStringContext
        from pythonstan.analysis.pointer.kcfa.heap_model import key, elem
        import ast
        is_list = isinstance(expression, ast.List)
        value = self._get_pta_addrs_for_lval(lval, state)
        if not value.addrs:
            obj = AbstractObject(self.call_context or CallStringContext(),
                                 AllocSite(stmt, AllocKind.LIST if is_list else AllocKind.TUPLE))
            value = AbsVal.from_addrs(frozenset({obj}))
        slots = ContainerSlots.empty_sequence()
        values = []
        for item in expression.elts:
            item_value = (self._read_name(item.id, state, summary) if isinstance(item, ast.Name)
                          else self._abstract_constant(item.value) if isinstance(item, ast.Constant)
                          else AbsVal.top())
            values.append(item_value)
            slots = slots.append(item_value)
        slots.length = IntLattice.const(len(values))
        new_state = self._write_name(lval, value, state, summary)
        for obj in value.addrs:
            address = AIAddr.from_kcfa_obj(obj)
            new_state = new_state.update_obj(address, AbsObj(address, ObjKind.LIST if is_list else ObjKind.TUPLE,
                                                             container_slots=slots))
            for index, item_value in enumerate(values):
                new_state = new_state.write_field_value(obj, key(str(index)), item_value)
                new_state = new_state.write_field_value(obj, elem(), item_value)
        return new_state

    @staticmethod
    def _forget_mutable_sequence_lengths(state):
        from .heap import ObjKind
        from .domains.ints import IntLattice
        result = state.copy()
        for address, obj in state.heap.items():
            if obj.kind in (ObjKind.LIST, ObjKind.SET, ObjKind.DICT) and obj.container_slots is not None:
                copied = obj.copy()
                copied.container_slots.length = IntLattice.nonneg()
                copied.container_slots.may_be_empty = True
                result.heap[address] = copied
        return result

    def _handle_len_builtin(self, stmt, state, summary):
        from .heap import AIAddr
        from .domains.ints import IntLattice, IntDomain
        from pythonstan.analysis.pointer.kcfa.object import BuiltinFunctionObject, ConstantObject
        callee = self._read_name(stmt.get_func_name(), state, summary)
        arguments = stmt.get_args()
        if (callee.addr_top or not callee.addrs
                or any(not isinstance(obj, BuiltinFunctionObject) or obj.function_name != "len" for obj in callee.addrs)
                or len(arguments) != 1 or arguments[0][1] or stmt.get_keywords()):
            return self._handle_unknown_call(stmt, state, summary)
        operand = self._read_name(arguments[0][0], state, summary)
        length = IntLattice.bottom()
        if operand.addr_top or not operand.addrs:
            return self._handle_unknown_call(stmt, state, summary)
        for obj in operand.addrs:
            heap_obj = state.get_obj(AIAddr.from_kcfa_obj(obj))
            if obj in state.overlay_havoc:
                return self._handle_unknown_call(stmt, state, summary)
            if heap_obj is not None and heap_obj.container_slots is not None:
                part = heap_obj.container_slots.length
            elif isinstance(obj, ConstantObject) and type(obj.value) in (str, bytes):
                part = IntLattice.const(len(obj.value))
            else:
                # User __len__ hooks or containers without a size fact need the
                # conservative call path, including possible side effects.
                return self._handle_unknown_call(stmt, state, summary)
            length = IntDomain().join(length, part)
        # Summaries export addresses; finite size facts need constant witnesses
        # just like literal/scalar assignments. Unbounded size stays explicit top.
        from pythonstan.analysis.pointer.kcfa.object import AllocSite, AllocKind
        from pythonstan.analysis.pointer.kcfa.context import CallStringContext
        constants = length.get_consts()
        cap = getattr(self.budget, "max_const_ints", 4)
        if constants is None and length.lo is not None and length.hi is not None and length.hi - length.lo < cap:
            constants = frozenset(range(length.lo, length.hi + 1))
        finite = constants is not None and len(constants) <= cap
        witnesses = frozenset(ConstantObject(self.call_context or CallStringContext(),
                                             AllocSite(stmt, AllocKind.CONSTANT), size)
                              for size in constants) if finite else frozenset()
        result = AbsVal(addrs=witnesses, components={"int": length}, addr_top=not finite)
        if stmt.get_target():
            state = self._write_name(stmt.get_target(), result, state, summary)
        return TransferResult.single(state)

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
        new_state = self._write_name(lval, dict_val, state, summary)
        
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
                    other_val = self._read_name(val_expr.id, new_state, summary)
                    new_state = dict_sem.dict_update(dict_val, other_val, new_state, summary)
                continue
            
            # Get abstract value for key
            if isinstance(key_expr, ast.Constant):
                key_val = self._abstract_constant(key_expr.value)
            elif isinstance(key_expr, ast.Name):
                key_val = self._read_name(key_expr.id, new_state, summary)
            else:
                # Complex key expression - use top
                key_val = AbsVal.top()
            
            # Get abstract value for value
            if isinstance(val_expr, ast.Constant):
                value_val = self._abstract_constant(val_expr.value)
            elif isinstance(val_expr, ast.Name):
                value_val = self._read_name(val_expr.id, new_state, summary)
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
        statement=None,
    ) -> AIState:
        """Handle comparison assignment, recording predicate for In/NotIn.
        
        For: t = (k in d) or t = (k not in d)
        Records predicate info so ConditionRefiner can refine dict membership.
        """
        import ast
        from .state import PredicateInfo

        scalar_predicate = None
        op_names = {ast.Eq: "eq_int", ast.NotEq: "neq_int", ast.Lt: "lt_int",
                    ast.LtE: "le_int", ast.Gt: "gt_int", ast.GtE: "ge_int"}
        left, right = compare_expr.left, compare_expr.comparators[0]
        if isinstance(left, ast.Name) and type(compare_expr.ops[0]) in op_names:
            right_value = (self._abstract_constant(right.value) if isinstance(right, ast.Constant)
                           else self._read_name(right.id, state, summary) if isinstance(right, ast.Name)
                           else AbsVal.top())
            constants = right_value.get_int_lattice().get_consts()
            if constants and len(constants) == 1:
                scalar_predicate = PredicateInfo("scalar:" + op_names[type(compare_expr.ops[0])],
                                                 dict_var=left.id, key_literal=next(iter(constants)))

        if statement is not None:
            from .scalar_transfer import finite_integer_transfer
            exact = finite_integer_transfer(compare_expr, lambda name: self._read_name(name, state, summary), statement, self.call_context)
            if exact is not None:
                result = self._write_name(lval, exact, state, summary)
                return result.add_predicate(lval, scalar_predicate) if scalar_predicate is not None else result
        
        # Get PTA value for the comparison result (typically bool)
        val = AbsVal.bool_top() if scalar_predicate is not None else self._get_pta_addrs_for_lval(lval, state)
        
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
                    # The kind already represents ``not in``. ``negated`` is
                    # reserved for an additional logical negation, otherwise
                    # the condition refiner would invert the branch twice.
                    negated=False,
                )
                new_state = self._write_name(lval, val, state, summary)
                return new_state.add_predicate(lval, pred_info)
        
        result = self._write_name(lval, val, state, summary)
        return result.add_predicate(lval, scalar_predicate) if scalar_predicate is not None else result

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
        
        # Match IRTranslator/SolverQuery: synthetic constants are globals,
        # ordinary module bindings are globals, and other temporaries are local.
        from pythonstan.ir.ir_statements import IRModule, IRFunc, IRClass
        scope_ir = self.scope_ir or getattr(self.caller_scope, "stmt", None)
        if lval_name.startswith("$const_"):
            kind = VariableKind.GLOBAL
        elif lval_name.startswith("$"):
            kind = VariableKind.TEMPORARY
        elif isinstance(scope_ir, IRModule) or (scope_ir and lval_name in scope_ir.get_global_vars()):
            kind = VariableKind.GLOBAL
        elif scope_ir and lval_name in scope_ir.get_nonlocal_vars():
            kind = VariableKind.NONLOCAL
        elif scope_ir and lval_name in scope_ir.get_cell_vars():
            kind = VariableKind.CELL
        elif isinstance(scope_ir, (IRFunc, IRClass)):
            # get_global_vars contains explicit ``global`` declarations only.
            # Unbound lexical names (including len) are implicitly global,
            # matching the translator rather than querying an empty local slot.
            from .lexical_bindings import local_bindings
            local_names = local_bindings(scope_ir.stmt)
            kind = VariableKind.LOCAL if lval_name in local_names else VariableKind.GLOBAL
        else:
            kind = VariableKind.LOCAL
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

        val = self._read_name(rval, state, summary)
        new_state = self._write_name(lval, val, state, summary)
        new_state = self._preserve_copied_predicate(state, new_state, rval, lval)
        return TransferResult.single(new_state)

    @staticmethod
    def _preserve_copied_predicate(old_state, new_state, source, target):
        """Keep a guard relation through TAC temporaries, not operand changes."""
        predicate = old_state.get_predicate(source)
        if predicate is not None and target not in {predicate.dict_var, predicate.key_var}:
            return new_state.add_predicate(target, predicate)
        return new_state

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
                val = self._read_name(item.id, state, summary)
                result = result.join(val)

        new_state = self._write_name(lval, result, state, summary)
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
        state = self._forget_mutable_sequence_lengths(state)
        target = stmt.get_lval().id
        obj_name = stmt.get_obj().id
        attr_name = stmt.get_attr()

        obj_val = self._read_name(obj_name, state, summary)
        
        # Use shared helper for attribute resolution
        result_val = self._get_attr_const(
            receiver_addrs=obj_val.addrs,
            attr_name=attr_name,
            state=state,
            summary=summary,
        )

        new_state = self._write_name(target, result_val, state, summary)
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
        state = self._forget_mutable_sequence_lengths(state)
        obj_name = stmt.get_obj().id
        attr_name = stmt.get_attr()
        value_name = stmt.get_rval().id

        obj_val = self._read_name(obj_name, state, summary)
        value_val = self._read_name(value_name, state, summary)

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
        state = self._forget_mutable_sequence_lengths(state)
        import ast

        target = stmt.get_lval().id
        obj_name = stmt.get_obj().id
        index_expr = stmt.get_slice()

        obj_val = self._read_name(obj_name, state, summary)

        # Determine index value
        index_val: Optional[AbsVal] = None
        if isinstance(index_expr, ast.Name):
            index_val = self._read_name(index_expr.id, state, summary)

        # Delegate to SubscriptSemantics
        subscr_semantics = self._get_subscript_semantics()
        load_result = subscr_semantics.load_subscr(
            obj_val=obj_val,
            index_val=index_val,
            state=state,
            summary=summary,
        )

        new_state = self._write_name(target, load_result.result_val, state, summary)
        result = TransferResult.single(new_state)
        if getattr(load_result, "may_raise_key_error", False):
            result.exceptional_states.append(("KeyError", state))
            if load_result.result_val.is_bottom():
                result.states = []
                result.is_terminal = True
        return result

    def _transfer_store_subscr(
        self,
        stmt: 'IRStoreSubscr',
        state: AIState,
        summary: SummaryAccumulator,
    ) -> TransferResult:
        """Transfer for IRStoreSubscr: obj[idx] = v.

        Delegates to SubscriptSemantics for bucket management.
        """
        state = self._forget_mutable_sequence_lengths(state)
        import ast

        obj_name = stmt.get_obj().id
        value_name = stmt.get_rval().id
        index_expr = stmt.get_slice()

        obj_val = self._read_name(obj_name, state, summary)
        value_val = self._read_name(value_name, state, summary)

        # Determine index value
        index_val: Optional[AbsVal] = None
        if isinstance(index_expr, ast.Name):
            index_val = self._read_name(index_expr.id, state, summary)

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

        # Recognize exact plain builtin exception bindings, but bind/read all
        # evaluated arguments before allocating their result below.
        from pythonstan.models.builtins import EXCEPTION_TYPES
        from pythonstan.analysis.pointer.kcfa.object import BuiltinClassObject
        modeled_exception = None
        if func_name in EXCEPTION_TYPES:
            class_value = self._read_name(func_name, state, summary)
            if class_value.addrs and not class_value.addr_top and all(
                    isinstance(obj, BuiltinClassObject) and obj.builtin_name == func_name for obj in class_value.addrs):
                modeled_exception = func_name

        # Check for known builtins
        if func_name == "len":
            return self._handle_len_builtin(stmt, state, summary)
        state = self._forget_mutable_sequence_lengths(state)
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

        # Generic calls can mutate a list through an argument, receiver,
        # closure, or global. Size effects are not yet part of AISummary, so a
        # literal size must not survive that boundary as an exact fact.
        state = self._forget_mutable_sequence_lengths(state)
        # Generic call: resolve local, captured, and global callable bindings.
        callee_val = self._read_name(func_name, state, summary)
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
                starred_val = self._read_name(arg_name, state, summary)
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
                pos_arg_vals.append(self._read_name(arg_name, state, summary))

        kw_arg_vals: Dict[str, AbsVal] = {}
        duplicate_keywords = False
        for kw_name, arg_name in kwargs:
            if kw_name is None:
                from .domains.kwargs import materialize_kwargs_shape, expand_kwargs
                mapping = self._read_name(arg_name, state, summary)
                shape = materialize_kwargs_shape(mapping, state, max_keys=getattr(self.budget, "max_dict_parts", 20))
                expanded = expand_kwargs(shape)
                if expanded is None or mapping.addr_top:
                    summary.mark_incomplete("unbounded_keyword_arguments")
                    has_unexpanded_starred = True
                    continue
                for expanded_name, expanded_value in expanded.items():
                    if expanded_name in kw_arg_vals:
                        duplicate_keywords = True
                    kw_arg_vals[expanded_name] = expanded_value
                continue
            if kw_name in kw_arg_vals:
                duplicate_keywords = True
            if arg_name.startswith("<"):
                kw_arg_vals[kw_name] = AbsVal.top_non_none()
            else:
                kw_arg_vals[kw_name] = self._read_name(arg_name, state, summary)

        if duplicate_keywords:
            return TransferResult(is_terminal=True, exceptional_states=[("TypeError", state)])

        if modeled_exception is not None:
            if has_unexpanded_starred or starred_expansion_alts is not None:
                summary.mark_incomplete("unexpanded_exception_arguments")
                return self._handle_unknown_call(stmt, state, summary)
            allowed_keywords = ({"name", "obj"} if modeled_exception == "AttributeError"
                                else {"name"} if modeled_exception in {"NameError", "UnboundLocalError"}
                                else {"name", "path"} if modeled_exception == "ImportError" else set())
            if set(kw_arg_vals) - allowed_keywords:
                return TransferResult(is_terminal=True, exceptional_states=[("TypeError", state)])
            for (name, _starred), value in zip(args, pos_arg_vals):
                if value.is_bottom():
                    kind = "UnboundLocalError" if name in self._owned_locals else "NameError"
                    return TransferResult(is_terminal=True, exceptional_states=[(kind, state)])
            from pythonstan.analysis.pointer.kcfa.object import BuiltinInstanceObject, AllocSite, AllocKind, TupleObject
            from pythonstan.analysis.pointer.kcfa.context import CallStringContext
            from pythonstan.analysis.pointer.kcfa.heap_model import attr, key, elem
            exception = BuiltinInstanceObject(self.call_context or CallStringContext(),
                                              AllocSite(stmt, AllocKind.INSTANCE), modeled_exception)
            arguments = TupleObject(self.call_context or CallStringContext(),
                                    AllocSite(("exception_args", stmt), AllocKind.TUPLE))
            state = state.write_field_value(exception, attr("args"), AbsVal.from_addrs(frozenset({arguments})))
            for index, argument in enumerate(pos_arg_vals):
                state = state.write_field_value(arguments, key(index), argument)
                state = state.write_field_value(arguments, elem(), argument)
            if modeled_exception == "StopIteration":
                state = state.write_field_value(exception, attr("value"),
                    pos_arg_vals[0] if pos_arg_vals else AbsVal.none_val())
            for keyword in allowed_keywords:
                state = state.write_field_value(exception, attr(keyword),
                    kw_arg_vals.get(keyword, AbsVal.none_val()))
            value = AbsVal.from_addrs(frozenset({exception}))
            summary.add_call(self._get_callsite_id(), frozenset(class_value.addrs))
            return TransferResult.single(self._write_name(target, value, state, summary) if target else state)

        # If we have multiple starred expansion alternatives, branch on them
        if starred_expansion_alts is not None and len(starred_expansion_alts) > 1:
            return self._handle_call_with_starred_alts(
                stmt, state, summary, callee_val, callsite_id,
                args, pos_arg_vals, kw_arg_vals, starred_expansion_alts
            )

        # If we have unexpanded starred, treat as unknown call
        if has_unexpanded_starred:
            summary.mark_incomplete("unexpanded_starred_arguments")
            summary.add_unknown_effect("may_call", frozenset(callee_val.addrs))

        from pythonstan.analysis.pointer.kcfa.object import ClassObject
        if not callee_val.addr_top and callee_val.addrs and all(isinstance(obj, ClassObject) for obj in callee_val.addrs):
            return self._construct_source_classes(stmt, callee_val, pos_arg_vals, kw_arg_vals,
                                                  state, summary)

        # Delegate call dispatch to CallSemantics
        self._active_state = state
        self._call_exit_states = []
        self._call_normal_exits = []
        self._binding_errors = []
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
        if self._call_exit_states:
            exit_state = self._call_exit_states[0]
            for alternative in self._call_exit_states[1:]:
                exit_state = exit_state.join(alternative)
            new_state = self._apply_callee_memory(state, exit_state)
        if dispatch_result.heap_updates:
            for ai_addr, ai_obj in dispatch_result.heap_updates:
                new_state = new_state.update_obj(ai_addr, ai_obj)
        if unresolved_addrs or not resolved_any:
            # Conservative: unknown call target
            summary.mark_incomplete("unknown_call")
            if unresolved_addrs:
                summary.add_unknown_effect("may_call", frozenset(unresolved_addrs))
            elif callee_val.addrs:
                summary.add_unknown_effect("may_call", frozenset(callee_val.addrs))
            # Record unknown effect on arguments (they may be mutated)
            for arg_name, _is_starred in args:
                if not arg_name.startswith("<"):
                    arg_val = self._read_name(arg_name, state, summary)
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
                arg_val = self._read_name(arg_name, state, summary)
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
            new_state = self._write_name(target, result_val if resolved_any else AbsVal.top(), new_state, summary)
        result = TransferResult.single(new_state)
        if self._binding_errors:
            result.exceptional_states.append(("TypeError", state))
        if self._call_normal_exits and not any(self._call_normal_exits) and not unresolved_addrs:
            result.states = []
            result.is_terminal = True
        return result

    @staticmethod
    def _apply_callee_memory(caller, callee_exit):
        result = caller.copy()
        result.heap = {key: obj.copy() for key, obj in callee_exit.heap.items()}
        result.overlay = dict(callee_exit.overlay)
        result.overlay_havoc = set(callee_exit.overlay_havoc)
        result.shared_bindings = dict(callee_exit.shared_bindings)
        return result

    def _construct_source_classes(self, stmt, classes, positional, keywords, state, summary):
        """Ordinary source-class construction with stateful __init__ calls.

        Custom metaclasses and __new__ require the broader protocol planner;
        the supported path is explicit and cannot rely on a Native call result.
        """
        from .heap import AIAddr, AbsObj, ObjKind
        from pythonstan.analysis.pointer.kcfa.object import InstanceObject, AllocSite, AllocKind
        from pythonstan.analysis.pointer.kcfa.context import CallStringContext
        outcomes = []
        values = AbsVal.bottom()
        for cls in classes.addrs:
            receiver_obj = InstanceObject(self.call_context or CallStringContext(),
                                          AllocSite(stmt, AllocKind.INSTANCE), cls)
            receiver = AbsVal.from_addrs(frozenset({receiver_obj}))
            new_state = state.update_obj(AIAddr.from_kcfa_obj(receiver_obj),
                                         AbsObj(AIAddr.from_kcfa_obj(receiver_obj), ObjKind.INSTANCE))
            special_new = self._context_special_methods(receiver, "__new__", new_state, summary)
            if getattr(cls.ir, "keywords", ()) or special_new:
                summary.mark_incomplete("unsupported_custom_construction")
                return self._handle_unknown_call(stmt, state, summary)
            initializers = self._context_special_methods(receiver, "__init__", new_state, summary)
            self._active_state = new_state
            self._call_exit_states = []
            self._call_normal_exits = []
            self._binding_errors = []
            for initializer in initializers:
                scope = self._get_callee_scope_ir(initializer)
                bound = self._build_arg_vals_for_scope(scope, positional, keywords, receiver)
                self.invoke_callee(initializer, bound, summary, receiver_val=receiver,
                                   callsite_id=self._get_callsite_id())
            if self._binding_errors:
                self._pending_exceptions.append(("TypeError", new_state))
            if initializers and self._call_normal_exits and not any(self._call_normal_exits):
                continue
            if self._call_exit_states:
                outgoing = self._call_exit_states[0]
                for other in self._call_exit_states[1:]:
                    outgoing = outgoing.join(other)
                new_state = self._apply_callee_memory(new_state, outgoing)
            elif not initializers and (positional or keywords):
                self._pending_exceptions.append(("TypeError", new_state))
                continue
            outcomes.append(new_state)
            values = values.join(receiver)
        if not outcomes:
            return TransferResult.terminal()
        outgoing = outcomes[0]
        for alternative in outcomes[1:]:
            outgoing = outgoing.join(alternative)
        target = stmt.get_target()
        if target:
            outgoing = self._write_name(target, values, outgoing, summary)
        return TransferResult.single(outgoing)

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

        obj_val = self._read_name(obj_name, state, summary)
        name_val = self._read_name(name_name, state, summary)
        
        # Get default value if provided
        default_val: Optional[AbsVal] = None
        if default_name and not default_name.startswith("<"):
            default_val = self._read_name(default_name, state, summary)

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
            new_state = self._write_name(target, result_val, state, summary)
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

        obj_val = self._read_name(obj_name, state, summary)
        name_val = self._read_name(name_name, state, summary)
        value_val = self._read_name(value_name, state, summary)

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
            new_state = self._write_name(stmt.get_target(), AbsVal.none_val(), new_state, summary)

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

        obj_val = self._read_name(obj_name, state, summary)
        name_val = self._read_name(name_name, state, summary)

        # Delegate to BuiltinAttributeSemantics
        builtin_semantics = self._get_builtin_attr_semantics(summary)
        result_val = builtin_semantics.handle_hasattr(
            obj_addrs=obj_val.addrs,
            name_val=name_val,
            summary=summary,
            state=state,
        )

        if target:
            new_state = self._write_name(target, result_val, state, summary)
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

        obj_val = self._read_name(obj_name, state, summary)
        iter_sem = self._get_iterator_semantics(summary)
        new_state, result_val = iter_sem.eval_iter(
            obj_val=obj_val,
            state=state,
            summary=summary,
            callsite_id=self._get_callsite_id(),
        )

        if target:
            new_state = self._write_name(target, result_val, new_state, summary)
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

        it_val = self._read_name(it_name, state, summary)
        default_val: Optional[AbsVal] = None
        if len(args) > 1:
            default_name, default_starred = args[1]
            if default_starred or default_name.startswith("<"):
                return self._handle_unknown_call(stmt, state, summary)
            default_val = self._read_name(default_name, state, summary)

        iter_sem = self._get_iterator_semantics(summary)
        new_state, result_val = iter_sem.eval_next(
            it_val=it_val,
            state=state,
            summary=summary,
            callsite_id=self._get_callsite_id(),
            default_val=default_val,
        )

        if target:
            new_state = self._write_name(target, result_val, new_state, summary)
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
            new_state = self._write_name(target, result_val, state, summary)
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
            new_state = self._write_name(target, result_val, state, summary)
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
        arg_val = self._read_name(arg_name, state, summary)
        
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
        
        new_state = self._write_name(target, target_val, state, summary)
        
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
        
        arg_val = self._read_name(arg_name, state, summary)
        target_val = self._get_pta_addrs_for_lval(target, state)
        
        if not target_val.addrs:
            return self._handle_unknown_call(stmt, state, summary)
        
        max_len = getattr(self.budget, "max_tuple_len_exact", 8)
        max_alts = getattr(self.budget, "max_tuple_alts", 4)
        
        disj = materialize_tuple_disj(
            arg_val, state, self.caller_scope, self.call_context,
            max_len=max_len, max_alts=max_alts
        )
        
        new_state = self._write_name(target, target_val, state, summary)
        
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
        summary.mark_incomplete("unknown_call")
        target = stmt.get_target()
        args = stmt.get_args()

        # Record unknown effects on arguments. A captured/global sequence may
        # also escape to unknown code even when it is not a positional argument.
        new_state = self._forget_mutable_sequence_lengths(state)
        for arg_name, is_starred in args:
            if not arg_name.startswith("<"):
                arg_val = self._read_name(arg_name, state, summary)
                if arg_val.addrs:
                    summary.add_unknown_effect(
                        "may_write",
                        frozenset(arg_val.addrs),
                        None,
                    )
                    for obj in arg_val.addrs:
                        new_state = new_state.add_overlay_havoc(obj)

        if target:
            new_state = self._write_name(target, AbsVal.top(), new_state, summary)

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
                alt_state = self._write_name(target, result_val, alt_state, summary)
            
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
        # This interpreter collects body observations without a suspended
        # program counter or tagged resume/throw/close channel. Fallthrough
        # cannot therefore constitute a complete generator transition.
        summary.mark_incomplete("generator_suspension_unmodeled")
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
            ret_val = self._read_name(ret_var, state, summary)
            ret_val = state._reduce_value("$return", ret_val)
            ret_val = self._materialize_scalar_return(ret_val, stmt)
            summary.add_return_value(ret_val, state)
            if ret_val.addr_top:
                summary.mark_incomplete("unknown_return_addresses")
        else:
            summary.add_return_value(self._materialize_scalar_return(AbsVal.none_val(), stmt), state)

        return TransferResult.terminal()

    def _materialize_scalar_return(self, value, stmt):
        """Exact scalar-to-Native projection; full value stays authoritative."""
        from pythonstan.analysis.pointer.kcfa.object import ConstantObject, AllocKind, AllocSite
        from pythonstan.analysis.pointer.kcfa.context import CallStringContext
        constants = []
        strings = value.get_const_strs()
        if strings:
            constants.extend(strings)
        integers = value.get_int_lattice().get_consts()
        if integers:
            constants.extend(integers)
        boolean = value.get_bool_values()
        if boolean.is_definite():
            constants.append(boolean.is_true())
        if value.get_none_lattice().is_none():
            constants.append(None)
        witnesses = frozenset(ConstantObject(self.call_context or CallStringContext(),
                                             AllocSite(stmt, AllocKind.CONSTANT), constant)
                              for constant in constants)
        return AbsVal(addrs=value.addrs | witnesses, components=value.components,
                      addr_top=value.addr_top, registry=value.registry, scalar_kinds=value.scalar_kinds)

    def _transfer_raise(
        self,
        stmt: 'IRRaise',
        state: AIState,
        summary: SummaryAccumulator,
    ) -> TransferResult:
        """Transfer for IRRaise: raise exc."""
        import ast
        exception = stmt.exc
        kind = "Exception"
        saved = getattr(stmt, "saved_exception_var", None)
        if saved is not None:
            kinds = self._read_name(saved, state, summary).get_const_strs()
            if kinds:
                return TransferResult(is_terminal=True, exceptional_states=[(name, state) for name in kinds])
        if exception is None and state.exn is not None:
            kinds = state.exn.get_const_strs()
            if kinds:
                summary.mark_incomplete("unmodeled_dynamic_exception_context")
                return TransferResult(is_terminal=True, exceptional_states=[(name, state) for name in kinds] + [("RuntimeError", state)])
        elif exception is None:
            # Dynamic exception context can be inherited from a calling
            # handler, which this compatibility path does not yet represent.
            summary.mark_incomplete("unmodeled_dynamic_exception_context")
            return TransferResult(is_terminal=True, exceptional_states=[("<unknown>", state)])
        if isinstance(exception, ast.Name):
            value = self._read_name(exception.id, state, summary)
            if exception.id == "@caught_except" and state.exn is not None:
                kinds = state.exn.get_const_strs()
                if kinds and len(kinds) == 1:
                    kind = next(iter(kinds))
            else:
                builtin_kinds = {getattr(obj, "builtin_type", getattr(obj, "builtin_name", None)) for obj in value.addrs}
                builtin_kinds.discard(None)
                if len(builtin_kinds) == 1:
                    kind = next(iter(builtin_kinds))
                elif value.scalar_kinds:
                    kind = "TypeError"
                else:
                    kind = "<unknown>"
                    summary.mark_incomplete("unknown_raised_exception_type")
        elif isinstance(exception, ast.Call) and isinstance(exception.func, ast.Name):
            kind = exception.func.id
        return TransferResult(is_terminal=True, exceptional_states=[(kind, state)])

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
        true_state, false_state = self._refine_branch(refiner, state, test_expr, False)

        definite = self._definite_boolean_guard(state, test_expr)
        branches = []
        if definite is not False:
            branches.append((None, true_state))
        if definite is not True:
            branches.append((target_label, false_state))
        return TransferResult(states=branches)

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
        true_state, false_state = self._refine_branch(refiner, state, test_expr, True)

        definite = self._definite_boolean_guard(state, test_expr)
        branches = []
        if definite is not False:
            branches.append((target_label, true_state))
        if definite is not True:
            branches.append((None, false_state))
        return TransferResult(states=branches)

    def _refine_branch(self, refiner, state, expression, is_jump_if_true):
        predicate = state.get_predicate(expression.id) if isinstance(expression, ast.Name) else None
        if predicate is not None and predicate.kind.startswith("scalar:"):
            if getattr(self.budget, "enable_value_partition", True):
                true_state, false_state = refiner._refine_for_int_comparison(state, predicate.dict_var,
                                                                            predicate.key_literal,
                                                                            predicate.kind.split(":", 1)[1])
                from pythonstan.analysis.pointer.kcfa.object import ConstantObject
                aliases = [name for name, other in state.predicates.items() if other == predicate]
                refined = []
                for truth, candidate in ((True, true_state), (False, false_state)):
                    candidate = candidate.copy()
                    for name in aliases:
                        current = candidate.get_var(name)
                        value = (AbsVal.bool_true() if truth else AbsVal.bool_false()).with_registry(candidate.registry)
                        # Bool is a canonical scalar; its complete value does
                        # not need an allocation-site witness. Retaining a
                        # stale witness here obstructs semantic state order
                        # after exact narrowing without adding identity facts.
                        candidate.env[name] = value
                    refined.append(candidate)
                return tuple(refined)
        return refiner.refine(state, expression, is_jump_if_true=is_jump_if_true)

    def _definite_boolean_guard(self, state: AIState, expr) -> Optional[bool]:
        """Prune only a proved boolean; arbitrary object truth may invoke code.

        Refining an impossible guard to a bottom *value* is insufficient:
        the rest of its environment still causes that edge to be scheduled.
        Avoid representing an unreachable path as an ordinary state.
        """
        if isinstance(expr, ast.Constant) and type(expr.value) is bool:
            return expr.value
        if isinstance(expr, ast.Name):
            return self._read_name(expr.id, state, self._active_summary or SummaryAccumulator()).bool_val
        return None

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

    def _transfer_opaque_ast(
        self,
        stmt: 'IRStatement',
        state: AIState,
        summary: SummaryAccumulator,
    ) -> TransferResult:
        """An unlowered AST statement is not an executable no-op model."""
        summary.mark_incomplete("opaque_ast_statement")
        return TransferResult.single(self._forget_mutable_sequence_lengths(state))

    def _context_special_methods(self, receiver, name, state, summary):
        """Resolve special methods on the type, bypassing instance hooks."""
        from pythonstan.analysis.pointer.kcfa.object import FunctionObject, MethodObject
        from pythonstan.analysis.pointer.kcfa.heap_model import attr
        result = set()
        if receiver.addr_top or not receiver.addrs:
            summary.mark_incomplete("unknown_context_manager_type")
            return result
        for obj in receiver.addrs:
            cls = getattr(obj, "class_obj", None)
            if cls is None:
                summary.mark_incomplete("unsupported_context_manager_type")
                continue
            classes = self.pta_query.get_class_mro(cls) or [cls]
            for candidate_class in classes:
                value, havoc = state.read_field_value(candidate_class, attr(name),
                                                     self.caller_scope, self.call_context)
                if value.addrs:
                    for method in value.addrs:
                        if isinstance(method, (FunctionObject, MethodObject)):
                            result.add(method)
                        else:
                            summary.mark_incomplete("unsupported_special_method_descriptor")
                    break
                if havoc:
                    summary.mark_incomplete("unknown_context_special_lookup")
        return result

    def _invoke_context_methods(self, methods, receiver, arguments, state, summary):
        self._active_state = state
        self._call_exit_states = []
        self._call_normal_exits = []
        value = AbsVal.bottom()
        for method in methods:
            self._invalid_binding = False
            value = value.join(self.invoke_callee(method,
                [("arg" + str(index), item) for index, item in enumerate(arguments)],
                summary, receiver_val=receiver, positional_args=True))
        result_state = state
        if self._call_exit_states:
            outgoing = self._call_exit_states[0]
            for other in self._call_exit_states[1:]:
                outgoing = outgoing.join(other)
            result_state = self._apply_callee_memory(state, outgoing)
        return value, result_state

    @staticmethod
    def _context_slot(entry):
        manager = entry.item.context_expr
        return "$context_exit:" + getattr(manager, "id", str(getattr(manager, "lineno", "unknown")))

    def _transfer_context_enter(self, stmt, state, summary):
        import ast
        if stmt.is_async or not isinstance(stmt.item.context_expr, ast.Name):
            summary.mark_incomplete("unsupported_async_context_manager")
            return self._transfer_opaque_ast(stmt, state, summary)
        receiver = self._read_name(stmt.item.context_expr.id, state, summary)
        exit_methods = self._context_special_methods(receiver, "__exit__", state, summary)
        enter_methods = self._context_special_methods(receiver, "__enter__", state, summary)
        if not enter_methods or not exit_methods:
            summary.mark_incomplete("missing_context_manager_protocol")
            return TransferResult(is_terminal=True, exceptional_states=[("TypeError", state)])
        # __exit__ is captured before __enter__ and the body can mutate its class.
        slot = self._context_slot(stmt)
        captured = state.set_var(slot, AbsVal.from_addrs(frozenset(exit_methods)))
        captured = captured.set_var(slot + ":receiver", receiver)
        value, outgoing = self._invoke_context_methods(enter_methods, receiver, [], captured, summary)
        if isinstance(stmt.item.optional_vars, ast.Name):
            outgoing = self._write_name(stmt.item.optional_vars.id, value, outgoing, summary)
        elif stmt.item.optional_vars is not None:
            summary.mark_incomplete("unsupported_context_target")
        if self._call_normal_exits and not any(self._call_normal_exits):
            return TransferResult.terminal()
        return TransferResult.single(outgoing)

    def _transfer_context_exit(self, stmt, state, summary):
        if stmt.entry.is_async:
            summary.mark_incomplete("unsupported_async_context_manager")
            return self._transfer_opaque_ast(stmt, state, summary)
        slot = self._context_slot(stmt.entry)
        methods = state.get_var(slot).addrs
        receiver = state.get_var(slot + ":receiver")
        if not methods:
            summary.mark_incomplete("missing_captured_context_exit")
            return TransferResult.single(state)
        exceptional = getattr(stmt, "completion_kind", "normal") == "raise"
        none = self._abstract_constant(None)
        args = [AbsVal.top(), AbsVal.top(), AbsVal.top()] if exceptional else [none, none, none]
        value, outgoing = self._invoke_context_methods(methods, receiver, args, state, summary)
        if self._call_normal_exits and not any(self._call_normal_exits):
            return TransferResult.terminal()
        if not exceptional:
            return TransferResult.single(outgoing)
        truth = value.bool_val
        states = []
        if truth is not False:
            suppressed = outgoing.copy()
            suppressed.exn = None
            states.append((stmt.suppressed_label, suppressed))
        if truth is not True:
            states.append((None, outgoing))
        return TransferResult(states=states)

    def _transfer_del(
        self,
        stmt: 'IRDel',
        state: AIState,
        summary: SummaryAccumulator,
    ) -> TransferResult:
        """Transfer for IRDel: del x."""
        state = self._forget_mutable_sequence_lengths(state)
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
        state = self._active_state
        registry = state.registry if state is not None else None
        if value is None:
            result = AbsVal.none_val()
        elif type(value) is bool:
            result = AbsVal.bool_true() if value else AbsVal.bool_false()
        elif type(value) is int:
            result = AbsVal.from_int(value)
        elif type(value) is str:
            result = AbsVal.from_str(value)
        else:
            result = None
        if result is not None:
            return result.with_registry(registry) if registry is not None else result
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
        self, scope_ir, pos_args, kw_args, receiver_val,
    ):
        """Apply the shared binder to fully evaluated abstract values."""
        from pythonstan.analysis.semantics.binding import bind_arguments
        import ast
        signature = scope_ir.args
        state = self._active_state or AIState(pta_query=self.pta_query, budget=self.budget)
        summary = self._active_summary or SummaryAccumulator()
        defaults = []
        positional = [*signature.posonlyargs, *signature.args]
        default_start = len(positional) - len(signature.defaults)
        for offset, expression in enumerate(signature.defaults):
            defaults.append(self._definition_default(scope_ir, positional[default_start + offset].arg,
                                                     expression, state, summary))
        kw_defaults = []
        for parameter, expression in zip(signature.kwonlyargs, signature.kw_defaults):
            if expression is not None:
                kw_defaults.append((parameter.arg, self._definition_default(scope_ir, parameter.arg,
                                                                             expression, state, summary)))
        evaluated = list(pos_args)
        if receiver_val is not None:
            evaluated.insert(0, receiver_val)
        result = bind_arguments(signature, evaluated, tuple(kw_args.items()), defaults, kw_defaults)
        self._invalid_binding = bool(result.errors)
        if result.errors:
            self._binding_errors = getattr(self, "_binding_errors", []) + list(result.errors)
            return []
        bindings = list(result.bindings)
        if signature.vararg is not None:
            value, state = self._collector_value(scope_ir, signature.vararg.arg, result.varargs, None, state)
            bindings.append((signature.vararg.arg, value))
        if signature.kwarg is not None:
            value, state = self._collector_value(scope_ir, signature.kwarg.arg, None, result.kwargs, state)
            bindings.append((signature.kwarg.arg, value))
        self._active_state = state
        # invoke_callee injects the receiver once, preserving descriptor identity.
        receiver_name = self._get_self_param_name(scope_ir) if receiver_val is not None else None
        return [(name, value) for name, value in bindings if name != receiver_name]

    def _definition_default(self, scope_ir, parameter, expression, state, summary):
        import ast
        prefix = "$default:" + scope_ir.get_qualname() + "@"
        found = [value for (owner, name), value in state.shared_bindings.items()
                 if owner.startswith(prefix) and name == parameter]
        if found:
            value = found[0]
            for alternative in found[1:]:
                value = value.join(alternative)
            return value
        if isinstance(expression, ast.Constant):
            return self._abstract_constant(expression.value)
        if isinstance(expression, ast.Name):
            value = self._read_name(expression.id, state, summary)
            if not value.is_bottom():
                # Legacy summaries may start after definition initialization.
                # This fallback is an observation, not proof of definition time.
                if not expression.id.startswith("$"):
                    summary.mark_incomplete("default_definition_snapshot_unavailable")
                return value
        summary.mark_incomplete("unsupported_default_expression")
        return AbsVal.top()

    def _collector_value(self, scope_ir, name, positional, keywords, state):
        from .heap import AIAddr, AbsObj, ObjKind, ContainerSlots, SlotMap, Presence
        from .domains.mapslots import make_str_keysig
        from .domains.ints import IntLattice
        from pythonstan.analysis.pointer.kcfa.object import AbstractObject, AllocSite, AllocKind
        from pythonstan.analysis.pointer.kcfa.context import CallStringContext
        from pythonstan.analysis.pointer.kcfa.heap_model import key, elem
        is_dict = keywords is not None
        obj = AbstractObject(self.call_context or CallStringContext(),
                             AllocSite(self._get_callsite_id() + ":" + scope_ir.get_qualname() + ":" + name,
                                       AllocKind.DICT if is_dict else AllocKind.TUPLE))
        address = AIAddr.from_kcfa_obj(obj)
        if is_dict:
            slots = SlotMap.for_dict(getattr(self.budget, "max_dict_parts", 20))
            for field, value in keywords:
                slots = slots.strong_update(make_str_keysig(field), value, Presence.PRESENT)
            abstract_obj = AbsObj(address, ObjKind.DICT, slots=slots)
        else:
            slots = ContainerSlots.empty_sequence()
            for value in positional:
                slots = slots.append(value)
            slots.length = IntLattice.const(len(positional))
            abstract_obj = AbsObj(address, ObjKind.TUPLE, container_slots=slots)
        result = state.update_obj(address, abstract_obj)
        if not is_dict:
            for index, value in enumerate(positional):
                result = result.write_field_value(obj, key(str(index)), value)
                result = result.write_field_value(obj, elem(), value)
        return AbsVal.from_addrs(frozenset({obj})), result

    def _transfer_definition(self, stmt, state, summary):
        """Capture evaluated defaults at the definition point."""
        import ast
        result = state.copy()
        positional = [*stmt.args.posonlyargs, *stmt.args.args]
        start = len(positional) - len(stmt.args.defaults)
        defaults = [(positional[start + i].arg, expression)
                    for i, expression in enumerate(stmt.args.defaults)]
        defaults.extend((parameter.arg, expression) for parameter, expression
                        in zip(stmt.args.kwonlyargs, stmt.args.kw_defaults) if expression is not None)
        for parameter, expression in defaults:
            if isinstance(expression, ast.Constant):
                value = self._abstract_constant(expression.value)
            elif isinstance(expression, ast.Name):
                value = self._read_name(expression.id, state, summary)
            else:
                summary.mark_incomplete("unsupported_default_expression")
                value = AbsVal.top()
            owner = "$default:" + stmt.get_qualname() + "@" + repr(self.call_context)
            result.shared_bindings[(owner, parameter)] = value
        return TransferResult.single(result)

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
        positional_args: bool = False,
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

        if getattr(self, "_invalid_binding", False) and not positional_args:
            self._call_normal_exits.append(False)
            return AbsVal.bottom()

        summary.add_call(callsite_id or self._get_callsite_id(), frozenset({callee_obj}))
        
        # Check recursion depth budget using self.recursion_depth
        max_depth = self.budget.max_recursion_depth if self.budget else 3
        if self.recursion_depth >= max_depth:
            # Budget exhausted: return conservative result
            summary.mark_incomplete("max_recursion_depth")
            summary.add_unknown_effect("may_call", frozenset({callee_obj}))
            return AbsVal.top()
        
        # Try to get IR scope for the callee
        scope_ir = self._get_callee_scope_ir(callee_obj)
        if scope_ir is None:
            # No IR available: return conservative result
            summary.mark_incomplete("missing_callee_ir")
            summary.add_unknown_effect("may_call", frozenset({callee_obj}))
            return AbsVal.top()
        
        # Build ArgBindings from arg_vals
        arg_bindings: List[ArgBinding] = []
        
        # Inject receiver binding for methods using proper method binding rules
        actual_receiver = self._get_receiver_for_method(callee_obj, scope_ir, receiver_val)
        if positional_args:
            arg_vals = self._build_arg_vals_for_scope(
                scope_ir, [value for _, value in arg_vals], {}, actual_receiver,
            )
            if getattr(self, "_invalid_binding", False):
                self._call_normal_exits.append(False)
                return AbsVal.bottom()
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
        from copy import deepcopy
        callee_config = deepcopy(self.solver_config)
        if callee_config is not None:
            # Block identifiers belong to the caller CFG. Generic policies
            # carry across calls, but those local overrides cannot.
            callee_config.merge_points = set()
            callee_config.loop_iters = {}
        engine = AISummaryEngine(self.pta_query, self.budget, solver_config=callee_config,
                                 work_ledger=self.work_ledger)
        callee_summary = engine.analyze_callee(
            scope_ir=scope_ir,
            call_context=callee_ctx,
            arg_bindings=arg_bindings,
            caller_scope=self.caller_scope,
            recursion_depth=self.recursion_depth + 1,
            caller_state=self._active_state,
        )
        self._call_normal_exits.append(callee_summary.normal_exit)
        if callee_summary.exit_state is not None and callee_summary.normal_exit:
            self._call_exit_states.append(callee_summary.exit_state)
        
        # Merge callee summary's EFFECTS into our accumulator
        # NOTE: Do NOT merge callee_summary.ret into summary.ret_addrs!
        # The callee's return addresses flow into the expression result (AbsVal),
        # not the caller's return values. The caller's returns should only come
        # from the caller's own return statements.
        for write in callee_summary.writes:
            summary.add_write(write.obj_addrs, write.field_key, write.value_addrs)
        for read_obj, read_field in callee_summary.reads:
            summary.add_read(read_obj, read_field)
        for child_callsite, child_callees in callee_summary.calls:
            summary.add_call(child_callsite, child_callees)
        for effect in callee_summary.unknown_effects:
            summary.unknown_effects.append(effect)
        if callee_summary.exceptional_exit_states:
            for exception_type, exception_state in callee_summary.exceptional_exit_states:
                caller = self._active_state or exception_state
                self._pending_exceptions.append((exception_type, self._apply_callee_memory(caller, exception_state)))
        else:
            self._pending_exceptions.extend((exception_type, self._active_state or AIState())
                                            for exception_type in callee_summary.may_raise)
        summary.prefix_exceptions.update(callee_summary.prefix_exceptions)

        if not callee_summary.is_complete:
            for reason in callee_summary.incomplete_reasons or ("incomplete_callee",):
                summary.mark_incomplete(reason)
            summary.add_unknown_effect("may_call", frozenset({callee_obj}))
            known = callee_summary.ret_value or AbsVal.from_addrs(callee_summary.ret)
            return known.join(AbsVal.top())
        
        # Return the callee's return addresses as the expression result
        if callee_summary.ret_value is not None:
            return callee_summary.ret_value
        return AbsVal.from_addrs(callee_summary.ret)

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
            positional = list(getattr(args, 'posonlyargs', ())) + list(getattr(args, 'args', ()))
            if positional:
                return positional[0].arg
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

