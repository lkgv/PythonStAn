"""Condition refinement semantics for abstract interpretation.

This module encapsulates branch condition refinement (value partitioning):
- Parsing condition expressions to extract refinement patterns
- Refining abstract states based on branch outcomes
- Supporting truthiness, None-checks, string equality, and integer comparisons

The interpreter delegates to ConditionRefiner to keep condition logic
in the domains layer rather than the interpreter.
"""

import ast
from typing import (
    TYPE_CHECKING,
    Optional,
    Tuple,
    Union,
)

if TYPE_CHECKING:
    from ..state import AIState, PredicateInfo
    from ..api import AnalysisBudget

__all__ = ["ConditionRefiner"]


class ConditionRefiner:
    """Encapsulates condition refinement for value partitioning.

    This class owns the logic for:
    - Parsing condition expressions (truthiness, is_none, eq_str, int comparisons)
    - Refining abstract states on branch outcomes
    - Applying caps for string/int equality partitioning

    Policy:
    - Truthiness refines bool and none domains
    - is None / is not None refines none domain (and bool for None)
    - eq/neq str refines string domain (within cap)
    - Integer comparisons (==, !=, <, <=, >, >=) refine int domain
    """

    def __init__(self, budget: "AnalysisBudget"):
        """Initialize condition refiner.

        Args:
            budget: Analysis budget for caps and feature flags
        """
        self.budget = budget

    def refine(
        self,
        state: "AIState",
        cond_expr: Optional[ast.expr],
        *,
        is_jump_if_true: bool,
    ) -> Tuple["AIState", "AIState"]:
        """Refine states based on condition expression for value partitioning.

        Args:
            state: Current abstract state
            cond_expr: AST expression for the condition (may be None)
            is_jump_if_true: True if this is JumpIfTrue, False for JumpIfFalse

        Returns:
            Tuple of (true_state, false_state) where:
            - true_state: refined state when condition is truthy
            - false_state: refined state when condition is falsy
        """
        if not getattr(self.budget, "enable_value_partition", True):
            return state, state

        info = self._parse_condition_expr(cond_expr)
        if info is None:
            # Check if condition is a TAC temporary with a recorded predicate
            cond_id = None
            if isinstance(cond_expr, ast.Name):
                cond_id = cond_expr.id
            elif hasattr(cond_expr, "id") and cond_expr.id:
                cond_id = cond_expr.id
            
            if cond_id is not None:
                pred = state.get_predicate(cond_id)
                if pred is not None:
                    return self._refine_for_dict_contains(state, pred)
                # Fall back to legacy truthiness behavior
                return self._refine_for_truthy(state, cond_id)
            return state, state

        var_name, kind, literal, negated = info
        true_state: "AIState"
        false_state: "AIState"

        if kind == "truthy":
            # Check for predicate-based refinement first
            pred = state.get_predicate(var_name)
            if pred is not None:
                true_state, false_state = self._refine_for_dict_contains(state, pred)
            else:
                true_state, false_state = self._refine_for_truthy(state, var_name)
        elif kind in {"is_none", "is_not_none"}:
            true_state, false_state = self._refine_for_none_check(
                state, var_name, is_none=(kind == "is_none")
            )
        elif kind in {"eq_str", "neq_str"}:
            true_state, false_state = self._refine_for_str_equality(
                state, var_name, literal, is_eq=(kind == "eq_str")
            )
        elif kind in {"eq_int", "neq_int", "lt_int", "le_int", "gt_int", "ge_int"}:
            true_state, false_state = self._refine_for_int_comparison(
                state, var_name, literal, kind
            )
        else:
            return state, state

        # Apply unary-not inversion when applicable
        if negated:
            return false_state, true_state
        return true_state, false_state

    def _parse_condition_expr(
        self, expr: Optional[ast.expr]
    ) -> Optional[Tuple[str, str, Optional[Union[str, int]], bool]]:
        """Extract simple condition patterns for refinement.

        Returns a tuple (var_name, kind, literal, negated) where:
        - kind in {"truthy", "is_none", "is_not_none", "eq_str", "neq_str",
                   "eq_int", "neq_int", "lt_int", "le_int", "gt_int", "ge_int"}
        - literal is used for string/int comparisons
        - negated indicates the condition was wrapped in a unary `not`
        """
        if expr is None:
            return None

        # Name: truthiness
        if isinstance(expr, ast.Name):
            return (expr.id, "truthy", None, False)

        # not x: negated truthiness
        if isinstance(expr, ast.UnaryOp) and isinstance(expr.op, ast.Not):
            if isinstance(expr.operand, ast.Name):
                return (expr.operand.id, "truthy", None, True)

        # Comparisons
        if (
            isinstance(expr, ast.Compare)
            and len(expr.ops) == 1
            and len(expr.comparators) == 1
        ):
            left = expr.left
            comp = expr.comparators[0]
            op = expr.ops[0]

            if isinstance(left, ast.Name):
                var = left.id

                # x is None
                if (
                    isinstance(op, ast.Is)
                    and isinstance(comp, ast.Constant)
                    and comp.value is None
                ):
                    return (var, "is_none", None, False)

                # x is not None
                if (
                    isinstance(op, ast.IsNot)
                    and isinstance(comp, ast.Constant)
                    and comp.value is None
                ):
                    return (var, "is_not_none", None, False)

                # x == "literal"
                if (
                    isinstance(op, ast.Eq)
                    and isinstance(comp, ast.Constant)
                    and isinstance(comp.value, str)
                ):
                    return (var, "eq_str", comp.value, False)

                # x != "literal"
                if (
                    isinstance(op, ast.NotEq)
                    and isinstance(comp, ast.Constant)
                    and isinstance(comp.value, str)
                ):
                    return (var, "neq_str", comp.value, False)

                # Integer comparisons (exclude bool since bool is subclass of int)
                if isinstance(comp, ast.Constant) and isinstance(comp.value, int):
                    if isinstance(comp.value, bool):
                        # Skip bool constants for int comparisons
                        pass
                    elif isinstance(op, ast.Eq):
                        return (var, "eq_int", comp.value, False)
                    elif isinstance(op, ast.NotEq):
                        return (var, "neq_int", comp.value, False)
                    elif isinstance(op, ast.Lt):
                        return (var, "lt_int", comp.value, False)
                    elif isinstance(op, ast.LtE):
                        return (var, "le_int", comp.value, False)
                    elif isinstance(op, ast.Gt):
                        return (var, "gt_int", comp.value, False)
                    elif isinstance(op, ast.GtE):
                        return (var, "ge_int", comp.value, False)

        return None

    def _refine_for_truthy(
        self,
        state: "AIState",
        cond_name: str,
    ) -> Tuple["AIState", "AIState"]:
        """Refine states for basic truthiness on a single variable."""
        from .bool import BoolLattice
        from .none import NoneLattice
        from ..state import BOOL_DOMAIN, NONE_DOMAIN

        cond_val = state.get_var(cond_name)
        bool_elem = cond_val.get_bool_lattice()

        if bool_elem.is_true():
            return state, state
        elif bool_elem.is_false():
            return state, state
        else:
            true_val = cond_val.with_component(BOOL_DOMAIN, BoolLattice.true_())
            true_val = true_val.with_component(NONE_DOMAIN, NoneLattice.not_none())
            false_val = cond_val.with_component(BOOL_DOMAIN, BoolLattice.false_())
            true_state = state.set_var(cond_name, true_val)
            false_state = state.set_var(cond_name, false_val)

        return true_state, false_state

    def _refine_for_none_check(
        self,
        state: "AIState",
        var_name: str,
        is_none: bool,
    ) -> Tuple["AIState", "AIState"]:
        """Refine states for x is None / x is not None."""
        from .bool import BoolLattice
        from .none import NoneLattice
        from ..state import BOOL_DOMAIN, NONE_DOMAIN

        cond_val = state.get_var(var_name)
        true_none_elem = NoneLattice.none_() if is_none else NoneLattice.not_none()
        false_none_elem = NoneLattice.not_none() if is_none else NoneLattice.none_()

        true_val = cond_val.with_component(NONE_DOMAIN, true_none_elem)
        false_val = cond_val.with_component(NONE_DOMAIN, false_none_elem)

        # None is falsy; pin boolean lattice when branch implies None
        if is_none:
            true_val = true_val.with_component(BOOL_DOMAIN, BoolLattice.false_())
        else:
            false_val = false_val.with_component(BOOL_DOMAIN, BoolLattice.false_())

        true_state = state.set_var(var_name, true_val)
        false_state = state.set_var(var_name, false_val)
        return true_state, false_state

    def _refine_for_str_equality(
        self,
        state: "AIState",
        var_name: str,
        literal: Optional[str],
        is_eq: bool,
    ) -> Tuple["AIState", "AIState"]:
        """Refine states for x == "literal" / x != "literal"."""
        from .strings import StrLattice
        from ..state import STRING_DOMAIN

        cond_val = state.get_var(var_name)
        str_elem = cond_val.get_string_lattice()
        cap = getattr(self.budget, "value_partition_str_cap", 4)

        # Only refine enumerable constants within cap
        if not str_elem.is_enumerable() or len(str_elem.consts) > cap:
            return state, state

        literal_set = frozenset({literal}) if literal is not None else frozenset()

        if is_eq:
            true_strs = str_elem.consts & literal_set
            false_strs = str_elem.consts - literal_set
        else:
            true_strs = str_elem.consts - literal_set
            false_strs = str_elem.consts & literal_set

        true_val = cond_val.with_component(STRING_DOMAIN, StrLattice.from_consts(true_strs))
        false_component = (
            StrLattice.from_consts(false_strs) if false_strs else str_elem
        )
        false_val = cond_val.with_component(STRING_DOMAIN, false_component)

        true_state = state.set_var(var_name, true_val)
        false_state = state.set_var(var_name, false_val)
        return true_state, false_state

    def _refine_for_int_comparison(
        self,
        state: "AIState",
        var_name: str,
        literal: Optional[Union[str, int]],
        kind: str,
    ) -> Tuple["AIState", "AIState"]:
        """Refine states for integer comparisons.

        Handles: x == n, x != n, x < n, x <= n, x > n, x >= n

        Args:
            state: Current abstract state
            var_name: Variable being compared
            literal: Integer constant being compared to
            kind: One of eq_int, neq_int, lt_int, le_int, gt_int, ge_int

        Returns:
            (true_state, false_state) with refined int domains
        """
        from ..state import INT_DOMAIN

        if literal is None or not isinstance(literal, int):
            return state, state

        n = literal
        cond_val = state.get_var(var_name)
        int_elem = cond_val.get_int_lattice()

        # Get cap for int partitioning
        cap = getattr(self.budget, "value_partition_int_cap", 10)

        # For enumerable sets exceeding cap, skip refinement
        if int_elem.is_enumerable() and len(int_elem.consts) > cap:
            return state, state

        # Compute refined values for true and false branches
        if kind == "eq_int":
            # x == n: true -> {n}, false -> x != n
            true_elem = int_elem.refine_eq(n)
            false_elem = int_elem.refine_neq(n)
        elif kind == "neq_int":
            # x != n: true -> x != n, false -> {n}
            true_elem = int_elem.refine_neq(n)
            false_elem = int_elem.refine_eq(n)
        elif kind == "lt_int":
            # x < n: true -> x < n, false -> x >= n
            true_elem = int_elem.refine_lt(n)
            false_elem = int_elem.refine_ge(n)
        elif kind == "le_int":
            # x <= n: true -> x <= n, false -> x > n
            true_elem = int_elem.refine_le(n)
            false_elem = int_elem.refine_gt(n)
        elif kind == "gt_int":
            # x > n: true -> x > n, false -> x <= n
            true_elem = int_elem.refine_gt(n)
            false_elem = int_elem.refine_le(n)
        elif kind == "ge_int":
            # x >= n: true -> x >= n, false -> x < n
            true_elem = int_elem.refine_ge(n)
            false_elem = int_elem.refine_lt(n)
        else:
            return state, state

        true_val = cond_val.with_component(INT_DOMAIN, true_elem)
        false_val = cond_val.with_component(INT_DOMAIN, false_elem)

        true_state = state.set_var(var_name, true_val)
        false_state = state.set_var(var_name, false_val)
        return true_state, false_state

    def _refine_for_dict_contains(
        self,
        state: "AIState",
        pred: "PredicateInfo",
    ) -> Tuple["AIState", "AIState"]:
        """Refine states for dict membership check (k in d).
        
        Uses recorded predicate info to refine dict presence via DictSemantics.
        
        Args:
            state: Current abstract state
            pred: Predicate info with dict_var, key_var, key_literal
            
        Returns:
            (true_state, false_state) tuple
        """
        from .mapslots import DictSemantics
        from ..value_domain import AbsVal

        if pred.kind not in {"dict_contains", "dict_not_contains"}:
            return state, state
        
        # Get dict value
        dict_val = state.get_var(pred.dict_var)
        if dict_val.is_bottom() or not dict_val.addrs:
            return state, state
        
        # Get key value
        if pred.key_var is not None:
            key_val = state.get_var(pred.key_var)
        elif pred.key_literal is not None:
            if isinstance(pred.key_literal, str):
                key_val = AbsVal.from_str(pred.key_literal)
            elif type(pred.key_literal) is int:
                key_val = AbsVal.from_int(pred.key_literal)
            else:
                key_val = AbsVal.top()
        else:
            return state, state
        
        # Create DictSemantics for refinement
        dict_sem = DictSemantics(
            caller_scope=None,
            call_context=None,
            budget=self.budget,
        )
        
        # Refine for true and false branches
        if pred.kind == "dict_contains":
            true_state = dict_sem.assume_contains(dict_val, key_val, state)
            false_state = dict_sem.assume_not_contains(dict_val, key_val, state)
        else:  # dict_not_contains
            true_state = dict_sem.assume_not_contains(dict_val, key_val, state)
            false_state = dict_sem.assume_contains(dict_val, key_val, state)
        
        # If negated in predicate, swap
        if pred.negated:
            return false_state, true_state
        
        return true_state, false_state
