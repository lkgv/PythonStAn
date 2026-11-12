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

from __future__ import annotations

import logging
from collections.abc import Hashable, Sequence
from dataclasses import dataclass, field
from typing import (
    TYPE_CHECKING,
    Protocol,
    override,
    runtime_checkable,
)

from pythonstan.analysis.contracts.budgets import AnalysisBudget
from pythonstan.analysis.contracts.json_data import JsonObject
from pythonstan.analysis.contracts.work import WorkLedgerPort

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from pythonstan.analysis.pointer.ai.solver import SolverConfig
    from pythonstan.analysis.pointer.kcfa.context import NativeContext, OrderedBindingToken, Scope
    from pythonstan.analysis.pointer.kcfa.heap_model import Field
    from pythonstan.analysis.pointer.kcfa.object import AbstractObject
    from pythonstan.analysis.pointer.kcfa.points_to_set import PointsToSet
    from pythonstan.analysis.pointer.kcfa.variable import Variable
    from pythonstan.compat.legacy_ai.state import AbsVal, AIState
    from pythonstan.ir.ir_statements import IRScope

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

    def points_to(self, var: Variable, scope: Scope, context: NativeContext) -> PointsToSet:
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
        obj: AbstractObject,
        field: Field,
        scope: Scope | None,
        context: NativeContext | None,
    ) -> PointsToSet:
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

    def get_class_mro(self, cls_obj: AbstractObject) -> list[AbstractObject]:
        """Get MRO (method resolution order) for a class object.

        Args:
            cls_obj: Class object

        Returns:
            List of class objects in MRO order (most specific first)
        """
        ...

    def may_have_attr(
        self,
        obj: AbstractObject,
        attr_name: str,
        scope: Scope,
        context: NativeContext,
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
        caller_ctx: NativeContext,
        receiver_obj: AbstractObject | None,
        params: tuple[AbstractObject, ...] | OrderedBindingToken | None = None,
    ) -> NativeContext | None:
        """Select context for a call site (kcfa policy aware).

        Returns None if context selection is not available.
        """
        ...

    def get_function_cell_vars(
        self,
        func_obj: AbstractObject,
    ) -> dict[str, frozenset[AbstractObject]]:
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
        func_obj: AbstractObject,
    ) -> dict[str, frozenset[AbstractObject]]:
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

    obj_addrs: frozenset[AbstractObject]
    field_key: Field
    value_addrs: frozenset[AbstractObject]
    value: AbsVal | None = None

    @override
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
    target_addrs: frozenset[AbstractObject]
    field_key: Field | None = None

    @override
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

    ret: frozenset[AbstractObject] = field(default_factory=frozenset)
    ret_value: AbsVal | None = None
    normal_exit: bool = False
    exit_state: AIState | None = None
    prefix_exceptions: frozenset[str] = field(default_factory=frozenset)
    exceptional_exit_states: tuple[tuple[str, AIState], ...] = ()
    writes: tuple[FieldWrite, ...] = field(default_factory=tuple)
    reads: tuple[tuple[frozenset[AbstractObject], Field], ...] = field(default_factory=tuple)
    calls: tuple[tuple[str, frozenset[AbstractObject]], ...] = field(default_factory=tuple)
    unknown_effects: tuple[UnknownEffect, ...] = field(default_factory=tuple)
    may_raise: frozenset[str] = field(default_factory=frozenset)
    is_complete: bool = True
    incomplete_reasons: tuple[str, ...] = field(default_factory=tuple)
    core_statistics: JsonObject = field(default_factory=dict, init=False, compare=False, repr=False)

    def __post_init__(self) -> None:
        # Compatibility constructors may still provide only a nonempty Native
        # projection. It denotes a known normal return, never implicit None.
        if self.ret or self.ret_value is not None and not self.ret_value.is_bottom():
            self.normal_exit = True

    def is_empty(self) -> bool:
        """Check if summary has no effects."""
        return (
            len(self.ret) == 0
            and (self.ret_value is None or self.ret_value.is_bottom())
            and not self.normal_exit
            and not self.prefix_exceptions
            and len(self.writes) == 0
            and len(self.unknown_effects) == 0
            and len(self.reads) == 0
            and len(self.calls) == 0
            and len(self.may_raise) == 0
            and self.is_complete
        )

    def merge(self, other: AISummary) -> AISummary:
        """Merge two summaries (join)."""
        from pythonstan.compat.legacy_ai.value_domain import AbsVal

        left_value = self.ret_value if self.ret_value is not None else AbsVal.from_addrs(self.ret)
        right_value = (
            other.ret_value if other.ret_value is not None else AbsVal.from_addrs(other.ret)
        )
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
            incomplete_reasons=tuple(
                dict.fromkeys(self.incomplete_reasons + other.incomplete_reasons)
            ),
        )

    @override
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
    points_to: frozenset[AbstractObject]
    value: AbsVal | None = None  # When set, used instead of just points_to


# =============================================================================
# AISummaryEngine - Entry point
# =============================================================================


class AISummaryEngine:
    """Source-backed compatibility facade over the sole shared abstract driver.

    Each invocation has fresh mutable state.  ``_summary_cache`` remains an
    inspectable compatibility field, but no caller/heap-dependent result is
    reused without a complete dependency protocol.
    """

    def __init__(
        self,
        pta_query: PtaQuery | None,
        budget: AnalysisBudget | None = None,
        solver_config: SolverConfig | None = None,
        work_ledger: WorkLedgerPort | None = None,
    ) -> None:
        from copy import deepcopy

        self.pta_query = pta_query
        self.budget = deepcopy(budget) if budget is not None else AnalysisBudget()
        self.solver_config = deepcopy(solver_config)
        from pythonstan.compat.shared_ai import options

        # Configuration errors precede driver construction and are independent
        # of the host runtime exception fallback policy.
        options(self.budget, self.solver_config)
        self.work_ledger = work_ledger
        self._summary_cache: dict[Hashable, AISummary] = {}

    def analyze_callee(
        self,
        scope_ir: IRScope,
        call_context: NativeContext | None,
        arg_bindings: list[ArgBinding],
        caller_scope: Scope | None = None,
        recursion_depth: int = 0,
        caller_state: AIState | None = None,
    ) -> AISummary:
        from pythonstan.compat.legacy_ai.state import AIState
        from pythonstan.compat.shared_ai import execute_scope

        initial = AIState.from_arg_bindings(arg_bindings, self.pta_query, self.budget)
        if caller_state is not None:
            parameters = initial.env
            initial = caller_state.copy()
            initial.env = parameters
            initial.pta_query = self.pta_query
            initial.budget = self.budget
        try:
            result, _, _, _ = execute_scope(
                scope_ir,
                initial,
                self.pta_query,
                self.budget,
                caller_scope,
                call_context,
                self.solver_config,
                self.work_ledger,
            )
            return result
        except Exception as error:
            if self.budget.strict_exceptions:
                raise
            logger.warning(
                "Shared AI analysis failed for %s: %s",
                getattr(scope_ir, "qualname", "<unknown>"),
                error,
                exc_info=True,
            )
            return self._conservative_summary(
                initial, call_context, reason=type(error).__name__ + ":" + str(error)
            )

    def analyze_intrinsic(
        self,
        builtin_name: str,
        call_context: NativeContext | None,
        arg_bindings: Sequence[ArgBinding],
        caller_scope: Scope | None = None,
        target_name: str | None = None,
    ) -> AISummary:
        from pythonstan.compat.legacy_ai.state import AIState
        from pythonstan.compat.shared_ai import execute_intrinsic

        try:
            return execute_intrinsic(
                builtin_name,
                arg_bindings,
                self.pta_query,
                self.budget,
                caller_scope,
                call_context,
                self.solver_config,
                self.work_ledger,
            )
        except Exception as error:
            if self.budget.strict_exceptions:
                raise
            logger.warning("Shared intrinsic failed for %s: %s", builtin_name, error, exc_info=True)
            return self._conservative_summary(
                AIState.from_arg_bindings(list(arg_bindings), self.pta_query, self.budget),
                call_context,
                reason=type(error).__name__ + ":" + str(error),
            )

    def invalidate_cache(self) -> None:
        self._summary_cache.clear()

    def _conservative_summary(
        self,
        initial: AIState | None = None,
        context: NativeContext | None = None,
        reason: str = "analysis_error",
    ) -> AISummary:
        from pythonstan.analysis.pointer.kcfa.context import CallStringContext
        from pythonstan.analysis.pointer.kcfa.object import AllocKind, AllocSite, UnknownValue
        from pythonstan.compat.legacy_ai.state import AIState
        from pythonstan.compat.legacy_ai.value_domain import AbsVal

        initial = (initial or AIState(pta_query=self.pta_query, budget=self.budget)).copy()
        targets = set(initial.overlay_havoc)
        for value in (
            *initial.env.values(),
            *initial.overlay.values(),
            *initial.shared_bindings.values(),
        ):
            targets.update(value.addrs)
        initial.overlay_havoc |= targets
        initial.shared_bindings = {
            key: AbsVal.top(initial.registry) for key in initial.shared_bindings
        }
        unknown = UnknownValue(
            context or CallStringContext(), AllocSite("shared-unavailable", AllocKind.UNKNOWN)
        )
        value = AbsVal.top(initial.registry).with_addrs(frozenset((unknown,)))
        return AISummary(
            ret=value.addrs,
            ret_value=value,
            normal_exit=True,
            exit_state=initial,
            unknown_effects=tuple(
                UnknownEffect(kind, frozenset(targets))
                for kind in ("may_read", "may_write", "may_call")
            ),
            may_raise=frozenset(("BaseException",)),
            prefix_exceptions=frozenset(("BaseException",)),
            exceptional_exit_states=(("BaseException", initial.copy()),),
            is_complete=False,
            incomplete_reasons=(reason,),
        )
