"""Core API for PTA-driven abstract interpretation.

This module defines:
- PtaQuery: Protocol for AI to query PTA's points-to information
- AISummary: Result of AI analysis (returns, writes, effects)
- AISummaryEngine: Entry point for PTA to invoke AI on a callee/scope

Exception Handling:
- All exceptions are caught at the AISummaryEngine boundary (analyze_callee/analyze_intrinsic).
- When budget.strict_exceptions=True, exceptions are re-raised with full traceback.
- When budget.strict_exceptions=False (default), exceptions are logged with context
  and a conservative summary is returned.
"""

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Dict, FrozenSet, List, Optional, Protocol, Set, Tuple, Union, runtime_checkable

from pythonstan.analysis.contracts.budgets import AnalysisBudget

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from pythonstan.ir.ir_statements import IRFunc, IRScope
    from pythonstan.analysis.pointer.kcfa.object import AbstractObject
    from pythonstan.analysis.pointer.kcfa.context import AbstractContext, Scope
    from pythonstan.analysis.pointer.kcfa.variable import Variable
    from pythonstan.analysis.pointer.kcfa.heap_model import Field
    from pythonstan.analysis.pointer.kcfa.points_to_set import PointsToSet
    from pythonstan.compat.legacy_ai.heap import AIAddr
    from pythonstan.compat.legacy_ai.state import AbsVal
    from pythonstan.analysis.pointer.ai.solver import SolverConfig

__all__ = [
    "PtaQuery",
    "AISummary",
    "AISummaryEngine",
    "FieldWrite",
    "UnknownEffect",
    "ArgBinding",
    "AnalysisBudget",
]


# =============================================================================
# PtaQuery Protocol - How AI queries PTA
# =============================================================================

@runtime_checkable
class PtaQuery(Protocol):
    """Protocol for AI to query PTA's points-to and field information.
    
    This is the minimal interface AI needs from PTA to interpret
    attribute access, subscript operations, and call resolution.
    """

    def points_to(self, var: 'Variable', scope: 'Scope', context: 'AbstractContext') -> 'PointsToSet':
        """Get points-to set for a variable in given scope/context.
        
        Args:
            var: Variable to query
            scope: Scope containing the variable
            context: Analysis context
            
        Returns:
            PointsToSet of abstract objects the variable may point to
        """
        ...

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
        ...

    def get_class_mro(self, cls_obj: 'AbstractObject') -> List['AbstractObject']:
        """Get MRO (method resolution order) for a class object.
        
        Args:
            cls_obj: Class object
            
        Returns:
            List of class objects in MRO order (most specific first)
        """
        ...

    def may_have_attr(
        self,
        obj: 'AbstractObject',
        attr_name: str,
        scope: 'Scope',
        context: 'AbstractContext',
    ) -> bool:
        """Check if an object may have an attribute (for descriptor checks).
        
        Args:
            obj: Object to check
            attr_name: Attribute name (e.g., "__get__", "__set__")
            scope: Current scope
            context: Analysis context
            
        Returns:
            True if the object may have the attribute
        """
        ...

    def select_call_context(
        self,
        callsite_id: str,
        argc: int,
        caller_ctx: 'AbstractContext',
        receiver_obj: Optional['AbstractObject'],
        params: Optional[Tuple['AbstractObject', ...]] = None,
    ) -> Optional['AbstractContext']:
        """Select context for a call site (kcfa policy aware).
        
        Returns None if context selection is not available.
        """
        ...

    def get_function_cell_vars(
        self,
        func_obj: 'AbstractObject',
    ) -> Dict[str, FrozenSet['AbstractObject']]:
        """Get cell variable bindings captured by a function object.
        
        Cell variables are variables from enclosing scopes that are captured
        by the function for use in its body (closure variables).
        
        Args:
            func_obj: Function object to query
            
        Returns:
            Dict mapping cell variable names to their captured points-to sets
        """
        ...

    def get_function_global_vars(
        self,
        func_obj: 'AbstractObject',
    ) -> Dict[str, FrozenSet['AbstractObject']]:
        """Get global variable bindings accessible by a function object.
        
        Global variables are module-level names that the function may read/write.
        
        Args:
            func_obj: Function object to query
            
        Returns:
            Dict mapping global variable names to their points-to sets
        """
        ...


# =============================================================================
# Summary dataclasses - What AI returns to PTA
# =============================================================================

@dataclass(frozen=True)
class FieldWrite:
    """Represents a may-write to an object field.
    
    Semantics: field `obj_addrs.field_key` may contain `value_addrs`.
    
    Attributes:
        obj_addrs: Set of abstract objects being written to
        field_key: Field being written (attr/key/elem/unknown)
        value_addrs: Set of abstract objects being written
    """
    obj_addrs: FrozenSet['AbstractObject']
    field_key: 'Field'
    value_addrs: FrozenSet['AbstractObject']
    value: Optional['AbsVal'] = None

    def __str__(self) -> str:
        objs = ", ".join(str(o) for o in self.obj_addrs)
        vals = ", ".join(str(v) for v in self.value_addrs)
        return f"Write({{{objs}}}.{self.field_key} <- {{{vals}}})"


@dataclass(frozen=True)
class UnknownEffect:
    """Represents a scoped havoc effect from unknown/foreign calls.
    
    Used when AI cannot precisely model a call but needs to stay sound.
    Instead of global havoc, we scope the effect to specific regions.
    
    Attributes:
        kind: Type of effect (may_write, may_read, may_call)
        target_addrs: Objects affected
        field_key: Optional field affected (None = all fields)
    """
    kind: str  # "may_write", "may_read", "may_call"
    target_addrs: FrozenSet['AbstractObject']
    field_key: Optional['Field'] = None

    def __str__(self) -> str:
        targets = ", ".join(str(t) for t in self.target_addrs)
        field_str = f".{self.field_key}" if self.field_key else ".*"
        return f"{self.kind}({{{targets}}}{field_str})"


@dataclass
class AISummary:
    """Summary of AI analysis for a callee/scope.
    
    This is what AI returns to PTA. PTA compiles this into
    constraints/worklist seeds.
    
    Attributes:
        ret: Return value as set of abstract objects
        writes: Field writes (may-write effects)
        reads: Field reads (optional, for caching/invalidation)
        calls: Additional call edges discovered (optional)
        unknown_effects: Scoped havoc effects for unknown/foreign
        may_raise: Exception types that may be raised (optional)
        is_complete: Whether analysis finished within its supported transfer
            model and budget. This is not a soundness certificate for Python.
        incomplete_reasons: Why the observed facts may omit further effects.
    """
    ret: FrozenSet['AbstractObject'] = field(default_factory=frozenset)
    ret_value: Optional['AbsVal'] = None
    normal_exit: bool = False
    exit_state: Optional['AIState'] = None
    prefix_exceptions: FrozenSet[str] = field(default_factory=frozenset)
    exceptional_exit_states: Tuple[Tuple[str, 'AIState'], ...] = ()
    writes: Tuple[FieldWrite, ...] = field(default_factory=tuple)
    reads: Tuple[Tuple[FrozenSet['AbstractObject'], 'Field'], ...] = field(default_factory=tuple)
    calls: Tuple[Tuple[str, FrozenSet['AbstractObject']], ...] = field(default_factory=tuple)
    unknown_effects: Tuple[UnknownEffect, ...] = field(default_factory=tuple)
    may_raise: FrozenSet[str] = field(default_factory=frozenset)
    is_complete: bool = True
    incomplete_reasons: Tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        # Compatibility constructors may still provide only a nonempty Native
        # projection. It denotes a known normal return, never implicit None.
        if self.ret or self.ret_value is not None and not self.ret_value.is_bottom():
            self.normal_exit = True

    def is_empty(self) -> bool:
        """Check if summary has no effects."""
        return (
            len(self.ret) == 0 and
            (self.ret_value is None or self.ret_value.is_bottom()) and
            not self.normal_exit and
            not self.prefix_exceptions and
            len(self.writes) == 0 and
            len(self.unknown_effects) == 0 and
            len(self.reads) == 0 and
            len(self.calls) == 0 and
            len(self.may_raise) == 0 and
            self.is_complete
        )

    def merge(self, other: 'AISummary') -> 'AISummary':
        """Merge two summaries (join)."""
        from pythonstan.compat.legacy_ai.value_domain import AbsVal
        left_value = self.ret_value if self.ret_value is not None else AbsVal.from_addrs(self.ret)
        right_value = other.ret_value if other.ret_value is not None else AbsVal.from_addrs(other.ret)
        exit_state = self.exit_state
        if exit_state is None:
            exit_state = other.exit_state
        elif other.exit_state is not None:
            exit_state = exit_state.join(other.exit_state)
        return AISummary(
            ret=self.ret | other.ret,
            ret_value=left_value.join(right_value),
            normal_exit=self.normal_exit or other.normal_exit,
            exit_state=exit_state,
            prefix_exceptions=self.prefix_exceptions | other.prefix_exceptions,
            exceptional_exit_states=self.exceptional_exit_states + other.exceptional_exit_states,
            writes=self.writes + other.writes,
            reads=self.reads + other.reads,
            calls=self.calls + other.calls,
            unknown_effects=self.unknown_effects + other.unknown_effects,
            may_raise=self.may_raise | other.may_raise,
            is_complete=self.is_complete and other.is_complete,
            incomplete_reasons=tuple(dict.fromkeys(
                self.incomplete_reasons + other.incomplete_reasons
            )),
        )

    def __str__(self) -> str:
        parts = []
        if self.ret:
            parts.append(f"ret={{{', '.join(str(r) for r in self.ret)}}}")
        if self.writes:
            parts.append(f"writes=[{', '.join(str(w) for w in self.writes)}]")
        if self.unknown_effects:
            parts.append(f"effects=[{', '.join(str(e) for e in self.unknown_effects)}]")
        return f"AISummary({'; '.join(parts)})"


# =============================================================================
# Input bindings and budget
# =============================================================================

@dataclass
class ArgBinding:
    """Binding of a formal parameter to input points-to set and optional domain value.
    
    When `value` is provided, it takes precedence over `points_to` for creating
    the initial AIState. This allows passing domain facts (e.g., constant strings
    for attribute names in getattr/setattr).
    
    Attributes:
        param_name: Formal parameter name
        points_to: Points-to set from caller arguments (fallback if value is None)
        value: Optional full AbsVal including domain components
    """
    param_name: str
    points_to: FrozenSet['AbstractObject']
    value: Optional['AbsVal'] = None  # When set, used instead of just points_to




# =============================================================================
# AISummaryEngine - Entry point
# =============================================================================

class AISummaryEngine:
    """Entry point for PTA to invoke AI analysis on a callee/scope.
    
    Usage:
        engine = AISummaryEngine(pta_query, budget)
        summary = engine.analyze_callee(scope_ir, call_context, arg_bindings)
    
    The engine maintains a summary cache keyed by (callee, context, input_fingerprint).
    """

    def __init__(
        self,
        pta_query: PtaQuery,
        budget: Optional[AnalysisBudget] = None,
        solver_config: Optional['SolverConfig'] = None,
        work_ledger: Optional[Any] = None,
    ):
        """Initialize the AI engine.
        
        Args:
            pta_query: Interface to query PTA
            budget: Analysis budget limits
        """
        from copy import deepcopy

        self.pta_query = pta_query
        self.budget = deepcopy(budget) if budget is not None else AnalysisBudget()
        self.solver_config = deepcopy(solver_config)
        self.work_ledger = work_ledger
        self._summary_cache: Dict[Tuple[Any, ...], AISummary] = {}

    def analyze_callee(
        self,
        scope_ir: 'IRScope',
        call_context: 'AbstractContext',
        arg_bindings: List[ArgBinding],
        caller_scope: Optional['Scope'] = None,
        recursion_depth: int = 0,
        caller_state: Optional['AIState'] = None,
    ) -> AISummary:
        """Analyze a callee scope and produce a summary.
        
        This is the main entry point for PTA to invoke AI.
        
        Exception Handling:
        - All exceptions are caught at this boundary.
        - When budget.strict_exceptions=True, exceptions are re-raised.
        - Otherwise, exceptions are logged and a conservative summary is returned.
        
        Args:
            scope_ir: IR of the callee (IRFunc, IRClass, etc.)
            call_context: Context at the call site
            arg_bindings: Parameter bindings from caller arguments
            caller_scope: Optional caller scope for context
            recursion_depth: Current recursion depth for nested AI calls
            
        Returns:
            AISummary with returns, writes, and effects
        """
        from pythonstan.analysis.pointer.ai.solver import AISolver
        from pythonstan.compat.legacy_ai.state import AIState
        from copy import deepcopy

        # A mutable PTA without an explicit snapshot token cannot validate
        # cached reads, closure cells or MRO dependencies. Recompute in that
        # case; argument equality alone is not an invalidation mechanism.
        from dataclasses import replace
        cacheable, dependency_token = self._cache_dependency_token()
        # Caller-local heap/cells are not represented by the PTA cache token.
        # Until their complete dependency versions are part of this cache,
        # bypass reuse whenever a complete incoming state is supplied.
        if caller_state is not None:
            cacheable = False
        cache_key = self._make_cache_key(
            scope_ir, call_context, arg_bindings, caller_scope, recursion_depth,
            dependency_token,
        )
        if cacheable and cache_key in self._summary_cache:
            return replace(self._summary_cache[cache_key])

        try:
            # Create initial state from arg bindings
            initial_state = AIState.from_arg_bindings(
                arg_bindings,
                self.pta_query,
                self.budget,
            )
            if caller_state is not None:
                parameter_env = initial_state.env
                initial_state = caller_state.copy()
                initial_state.env = parameter_env
                initial_state.pta_query = self.pta_query
                initial_state.budget = self.budget
                initial_state.exn = None

            # Run the solver
            solver = AISolver(
                scope_ir=scope_ir,
                initial_state=initial_state,
                pta_query=self.pta_query,
                budget=self.budget,
                caller_scope=caller_scope,
                call_context=call_context,
                recursion_depth=recursion_depth,
                config=deepcopy(self.solver_config),
                work_ledger=self.work_ledger,
            )
            summary = solver.solve()

            # A partial observation is not a reusable callee summary.
            if cacheable and summary.is_complete:
                self._summary_cache[cache_key] = replace(summary)
            return summary
        except Exception as e:
            # Exception boundary: decide whether to re-raise or return conservative
            if self.budget.strict_exceptions:
                raise
            # Log with context for debugging
            scope_name = "<unknown>"
            try:
                if hasattr(scope_ir, "get_qualname"):
                    scope_name = scope_ir.get_qualname()
                elif hasattr(scope_ir, "name"):
                    scope_name = scope_ir.name
            except Exception:
                pass
            logger.warning(
                "AI analysis failed for scope %s (context=%s, depth=%d): %s",
                scope_name, call_context, recursion_depth, e,
                exc_info=True,
            )
            return self._conservative_summary()

    def _make_cache_key(
        self,
        scope_ir: 'IRScope',
        call_context: 'AbstractContext',
        arg_bindings: List[ArgBinding],
        caller_scope: Optional['Scope'] = None,
        recursion_depth: int = 0,
        dependency_token: Any = None,
    ) -> Tuple[Any, ...]:
        """Create a cache key for memoization.
        
        Includes fingerprint of ArgBinding.value when present to ensure
        different constant values don't reuse summaries.
        """
        # Use frozensets for arg bindings to make them hashable
        bindings_key = tuple(
            self._make_binding_key(b)
            for b in sorted(arg_bindings, key=lambda b: b.param_name)
        )
        budget_key = tuple(sorted(vars(self.budget).items()))
        return (id(scope_ir), call_context, bindings_key, id(caller_scope),
                recursion_depth, budget_key, repr(self.solver_config), dependency_token)

    def _cache_dependency_token(self) -> Tuple[bool, Any]:
        """Require a query-provided immutable token covering all dependencies."""
        if self.pta_query is None:
            return True, None
        token_provider = getattr(self.pta_query, "analysis_cache_token", None)
        if token_provider is None:
            return False, None
        try:
            token = token_provider() if callable(token_provider) else token_provider
            hash(token)
        except Exception:
            return False, None
        return True, (id(self.pta_query), token)
    
    def _make_binding_key(self, binding: ArgBinding) -> Tuple[Any, ...]:
        """Create a hashable key for a single ArgBinding.
        
        Includes the full AbsVal fingerprint when value is present.
        """
        base_key: Tuple[Any, ...] = (binding.param_name, binding.points_to)
        
        if binding.value is not None:
            # Include the full AbsVal fingerprint in the key
            return base_key + (binding.value.fingerprint(),)
        
        return base_key

    def invalidate_cache(self) -> None:
        """Clear the summary cache."""
        self._summary_cache.clear()

    def _conservative_summary(self) -> AISummary:
        """Report an unavailable result; empty address sets do not mean top."""
        return AISummary(
            ret=frozenset(),
            writes=(),
            reads=(),
            calls=(),
            unknown_effects=(),
            may_raise=frozenset({"Exception"}),
            is_complete=False,
            incomplete_reasons=("analysis_error",),
        )

    def analyze_intrinsic(
        self,
        builtin_name: str,
        call_context: 'AbstractContext',
        arg_bindings: List[ArgBinding],
        caller_scope: Optional['Scope'] = None,
        target_name: Optional[str] = None,
    ) -> AISummary:
        """Analyze a single builtin intrinsic call (no CFG needed).
        
        This is used for getattr/setattr/hasattr where we can directly invoke
        the AI interpreter's builtin handler without building a full CFG.
        
        Exception Handling:
        - All exceptions are caught at this boundary.
        - When budget.strict_exceptions=True, exceptions are re-raised.
        - Otherwise, exceptions are logged and a conservative summary is returned.
        
        Args:
            builtin_name: Name of builtin (getattr, setattr, hasattr)
            call_context: Context at the call site
            arg_bindings: Argument bindings (obj, name, value/default)
            caller_scope: Caller's scope for field queries
            target_name: Name of target variable for return value (or None)
            
        Returns:
            AISummary with returns and writes
        """
        from pythonstan.compat.legacy_ai.state import AIState, AbsVal
        from pythonstan.compat.legacy_ai.summary import SummaryAccumulator
        from pythonstan.analysis.pointer.ai.interpreter import IRInterpreter

        try:
            # Create initial state from arg bindings
            initial_state = AIState.from_arg_bindings(
                arg_bindings,
                self.pta_query,
                self.budget,
            )

            # Create summary accumulator
            summary_acc = SummaryAccumulator()
            if any(value.addr_top for value in initial_state.env.values()):
                summary_acc.mark_incomplete("unknown_address_input")

            # Create interpreter
            interpreter = IRInterpreter(
                pta_query=self.pta_query,
                budget=self.budget,
                scope_ir=None,
                caller_scope=caller_scope,
                call_context=call_context,
                solver_config=self.solver_config,
                work_ledger=self.work_ledger,
            )

            # Create a stub IRCall-like object for the interpreter
            class IntrinsicCallStub:
                """Minimal stub mimicking IRCall interface for builtin handlers."""
                
                def __init__(stub_self, target: Optional[str], func_name: str, bindings: List[ArgBinding]):
                    stub_self._target = target
                    stub_self._func_name = func_name
                    stub_self._bindings = bindings
                
                def get_target(stub_self) -> Optional[str]:
                    return stub_self._target
                
                def get_func_name(stub_self) -> str:
                    return stub_self._func_name
                
                def get_args(stub_self) -> List[Tuple[str, bool]]:
                    # Return (param_name, is_starred) pairs
                    return [(b.param_name, False) for b in stub_self._bindings]
                
                def get_keywords(stub_self) -> List:
                    return []

            stub = IntrinsicCallStub(target_name, builtin_name, arg_bindings)

            # The direct intrinsic dispatch is real abstract work even though
            # it does not enter AISolver.transfer. Nested source calls share
            # this exact run ledger and charge their own statement transfers.
            if self.work_ledger is not None and self.work_ledger.consume(kind="abstract_transfer") is False:
                return AISummary(is_complete=False, incomplete_reasons=("shared_work_budget",))

            # Dispatch to appropriate handler
            if builtin_name == "getattr":
                result = interpreter._handle_getattr(stub, initial_state, summary_acc)
            elif builtin_name == "setattr":
                result = interpreter._handle_setattr(stub, initial_state, summary_acc)
            elif builtin_name == "hasattr":
                result = interpreter._handle_hasattr(stub, initial_state, summary_acc)
            else:
                return AISummary(
                    is_complete=False,
                    incomplete_reasons=("unsupported_intrinsic",),
                )

            # Extract return value addresses from the result state
            ret_addrs: FrozenSet['AbstractObject'] = frozenset()
            target_val = None
            final_state = None
            if target_name and result.states:
                # Get the first (and typically only) result state
                _, final_state = result.states[0]
                target_val = final_state.get_var(target_name)
                target_val = interpreter._materialize_scalar_return(target_val, "intrinsic:" + builtin_name)
                ret_addrs = target_val.addrs
                if target_val.addr_top:
                    summary_acc.mark_incomplete("unknown_return_addresses")

            # Build and return AISummary
            ai_summary = summary_acc.to_summary()
            
            # Merge in return addresses
            if target_val is not None:
                from dataclasses import replace
                ai_summary = replace(ai_summary, ret=ai_summary.ret | ret_addrs,
                                     ret_value=target_val, normal_exit=True,
                                     exit_state=final_state.copy())

            return ai_summary
        except Exception as e:
            # Exception boundary: decide whether to re-raise or return conservative
            if self.budget.strict_exceptions:
                raise
            logger.warning(
                "AI intrinsic analysis failed for %s (context=%s): %s",
                builtin_name, call_context, e,
                exc_info=True,
            )
            return self._conservative_summary()

