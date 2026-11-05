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
from typing import (
    TYPE_CHECKING,
    Any,
    Dict,
    FrozenSet,
    List,
    Optional,
    Protocol,
    Set,
    Tuple,
    Union,
    runtime_checkable,
)

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from pythonstan.ir.ir_statements import IRFunc, IRScope
    from pythonstan.analysis.pointer.kcfa.object import AbstractObject
    from pythonstan.analysis.pointer.kcfa.context import AbstractContext, Scope
    from pythonstan.analysis.pointer.kcfa.variable import Variable
    from pythonstan.analysis.pointer.kcfa.heap_model import Field
    from pythonstan.analysis.pointer.kcfa.points_to_set import PointsToSet
    from .heap import AIAddr
    from .state import AbsVal

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
    """
    ret: FrozenSet['AbstractObject'] = field(default_factory=frozenset)
    writes: Tuple[FieldWrite, ...] = field(default_factory=tuple)
    reads: Tuple[Tuple[FrozenSet['AbstractObject'], 'Field'], ...] = field(default_factory=tuple)
    calls: Tuple[Tuple[str, FrozenSet['AbstractObject']], ...] = field(default_factory=tuple)
    unknown_effects: Tuple[UnknownEffect, ...] = field(default_factory=tuple)
    may_raise: FrozenSet[str] = field(default_factory=frozenset)

    def is_empty(self) -> bool:
        """Check if summary has no effects."""
        return (
            len(self.ret) == 0 and
            len(self.writes) == 0 and
            len(self.unknown_effects) == 0
        )

    def merge(self, other: 'AISummary') -> 'AISummary':
        """Merge two summaries (join)."""
        return AISummary(
            ret=self.ret | other.ret,
            writes=self.writes + other.writes,
            reads=self.reads + other.reads,
            calls=self.calls + other.calls,
            unknown_effects=self.unknown_effects + other.unknown_effects,
            may_raise=self.may_raise | other.may_raise,
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


@dataclass
class AnalysisBudget:
    """Budget limits for AI analysis to ensure termination.
    
    Attributes:
        max_iterations: Maximum fixpoint iterations
        max_stmt_visits: Maximum statement visits per block
        max_recursion_depth: Maximum recursive AI calls
        strict_exceptions: Re-raise internal exceptions instead of masking
        max_addr_set_size: Cap for address set widening
        max_field_keys: Cap for field store key tracking
        max_const_strings: Cap for string domain const set
        max_ho_tokens: Cap for higher-order token set
        enable_narrowing: Whether to run narrowing pass
        narrowing_iterations: Number of narrowing iterations
        
        Value Partitioning:
        enable_value_partition: Whether to refine states on branches
        max_value_partitions: Max partitions per variable before merging
        value_partition_str_cap: Max string constants to enumerate for partitioning
        
        Tuple/Int Indexing:
        max_const_ints: Max int constants to enumerate for subscript access
        max_tuple_len_exact: Max tuple length to materialize exactly
        max_tuple_alts: Max tuple shape alternatives before merging
        max_star_expand_len: Max tuple length for *args expansion
        max_star_expand_alts: Max tuple alternatives for *args expansion
        
        Dict Modeling (Route A):
        max_dict_parts: Max key partitions per dict before spilling to default
        max_dict_key_const_strs: Max constant strings to track as distinct keys
        max_dict_key_const_ints: Max constant ints to track as distinct keys
        max_dict_key_tuple_len: Max tuple length for tuple key decomposition
        max_dict_key_tuple_alts: Max tuple alternatives for tuple keys
        enable_dict_identity_keys: Enable identity-based key partitioning
        max_dict_identity_keys: Max identity keys before spilling to default
    """
    max_iterations: int = 100
    max_stmt_visits: int = 50
    max_recursion_depth: int = 3
    strict_exceptions: bool = False
    max_addr_set_size: int = 50
    max_field_keys: int = 20
    max_const_strings: int = 10
    max_ho_tokens: int = 20
    enable_narrowing: bool = True
    narrowing_iterations: int = 2
    
    # Value partitioning settings
    enable_value_partition: bool = True  # Enable branch refinement (on by default)
    max_value_partitions: int = 4  # Max partitions per variable
    value_partition_str_cap: int = 4  # Max strings to enumerate for partitioning
    
    # Tuple/int indexing settings
    max_const_ints: int = 4  # Max int constants for subscript (e.g., t[0], t[1])
    max_tuple_len_exact: int = 8  # Max tuple length to materialize exactly
    max_tuple_alts: int = 4  # Max tuple shape alternatives before merging
    max_star_expand_len: int = 8  # Max tuple length for *args expansion
    max_star_expand_alts: int = 4  # Max tuple alternatives for *args expansion
    
    # Dict modeling settings (Route A)
    max_dict_parts: int = 20  # Max key partitions per dict
    max_dict_key_const_strs: int = 10  # Max constant string keys to track
    max_dict_key_const_ints: int = 10  # Max constant int keys to track
    max_dict_key_tuple_len: int = 4  # Max tuple length for tuple key decomposition
    max_dict_key_tuple_alts: int = 2  # Max tuple alternatives for tuple keys
    enable_dict_identity_keys: bool = True  # Enable identity key partitioning
    max_dict_identity_keys: int = 10  # Max identity keys


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
    ):
        """Initialize the AI engine.
        
        Args:
            pta_query: Interface to query PTA
            budget: Analysis budget limits
        """
        from .value_domain import configure_registry

        self.pta_query = pta_query
        self.budget = budget or AnalysisBudget()
        configure_registry(max_ho_tokens=self.budget.max_ho_tokens)
        self._summary_cache: Dict[Tuple[Any, ...], AISummary] = {}

    def analyze_callee(
        self,
        scope_ir: 'IRScope',
        call_context: 'AbstractContext',
        arg_bindings: List[ArgBinding],
        caller_scope: Optional['Scope'] = None,
        recursion_depth: int = 0,
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
        from .solver import AISolver
        from .state import AIState

        # Compute cache key
        cache_key = self._make_cache_key(scope_ir, call_context, arg_bindings)
        if cache_key in self._summary_cache:
            return self._summary_cache[cache_key]

        try:
            # Create initial state from arg bindings
            initial_state = AIState.from_arg_bindings(
                arg_bindings,
                self.pta_query,
                self.budget,
            )

            # Run the solver
            solver = AISolver(
                scope_ir=scope_ir,
                initial_state=initial_state,
                pta_query=self.pta_query,
                budget=self.budget,
                caller_scope=caller_scope,
                call_context=call_context,
                recursion_depth=recursion_depth,
            )
            summary = solver.solve()

            # Cache and return
            self._summary_cache[cache_key] = summary
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
        return (id(scope_ir), call_context, bindings_key)
    
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
        """Return a conservative summary when analysis fails."""
        return AISummary(
            ret=frozenset(),
            writes=(),
            reads=(),
            calls=(),
            unknown_effects=(),
            may_raise=frozenset({"Exception"}),
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
        from .state import AIState, AbsVal
        from .summary import SummaryAccumulator
        from .interpreter import IRInterpreter

        try:
            # Create initial state from arg bindings
            initial_state = AIState.from_arg_bindings(
                arg_bindings,
                self.pta_query,
                self.budget,
            )

            # Create summary accumulator
            summary_acc = SummaryAccumulator()

            # Create interpreter
            interpreter = IRInterpreter(
                pta_query=self.pta_query,
                budget=self.budget,
                scope_ir=None,
                caller_scope=caller_scope,
                call_context=call_context,
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

            # Dispatch to appropriate handler
            if builtin_name == "getattr":
                result = interpreter._handle_getattr(stub, initial_state, summary_acc)
            elif builtin_name == "setattr":
                result = interpreter._handle_setattr(stub, initial_state, summary_acc)
            elif builtin_name == "hasattr":
                result = interpreter._handle_hasattr(stub, initial_state, summary_acc)
            else:
                # Unknown builtin, return empty summary
                return AISummary()

            # Extract return value addresses from the result state
            ret_addrs: FrozenSet['AbstractObject'] = frozenset()
            if target_name and result.states:
                # Get the first (and typically only) result state
                _, final_state = result.states[0]
                target_val = final_state.get_var(target_name)
                ret_addrs = target_val.addrs

            # Build and return AISummary
            ai_summary = summary_acc.to_summary()
            
            # Merge in return addresses
            if ret_addrs:
                ai_summary = AISummary(
                    ret=ai_summary.ret | ret_addrs,
                    writes=ai_summary.writes,
                    reads=ai_summary.reads,
                    calls=ai_summary.calls,
                    unknown_effects=ai_summary.unknown_effects,
                    may_raise=ai_summary.may_raise,
                )

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

