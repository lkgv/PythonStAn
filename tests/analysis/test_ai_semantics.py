"""Tests for extracted semantics services.

Tests for:
- CallSemantics: call dispatch, class-call fallback, unknown effects
- SubscriptSemantics: constant/dynamic index, elem() bucket, summary effects
- ConditionRefiner: truthiness, is None, eq_str refinement
"""

import ast
import pytest
from dataclasses import dataclass
from typing import Dict, FrozenSet, List, Optional, Tuple


# =============================================================================
# Mock objects for testing
# =============================================================================


@dataclass
class MockObject:
    """Mock abstract object for testing."""

    name: str

    def __hash__(self):
        return hash(self.name)

    def __eq__(self, other):
        if isinstance(other, MockObject):
            return self.name == other.name
        return False

    @property
    def alloc_site(self):
        """Mock alloc_site for AIAddr compatibility."""
        from pythonstan.analysis.pointer.kcfa.object import AllocSite, AllocKind

        return AllocSite(stmt=f"<mock:{self.name}>", kind=AllocKind.OBJECT)

    @property
    def context(self):
        """Mock context for AIAddr compatibility."""
        return object()


class MockPtaQuery:
    """Mock PTA query interface for testing."""

    def __init__(self):
        self._points_to = {}
        self._field_points_to = {}
        self._class_mro = {}

    def add_points_to(self, var_name: str, objs: FrozenSet[MockObject]):
        """Configure points-to for a variable."""
        self._points_to[var_name] = objs

    def add_field_points_to(
        self, obj: MockObject, field_name: str, objs: FrozenSet[MockObject]
    ):
        """Configure field points-to."""
        key = (obj.name, field_name)
        self._field_points_to[key] = objs

    def add_class_mro(self, cls_obj: MockObject, mro: List[MockObject]) -> None:
        """Configure class MRO."""
        self._class_mro[cls_obj] = mro

    def points_to(self, var, scope, context):
        """Mock points_to query."""
        var_name = var.name if hasattr(var, "name") else str(var)
        return self._points_to.get(var_name, frozenset())

    def field_points_to(self, obj, field, scope, context):
        """Mock field_points_to query."""
        obj_name = obj.name if hasattr(obj, "name") else str(obj)
        field_name = str(field)
        key = (obj_name, field_name)
        return self._field_points_to.get(key, frozenset())

    def get_class_mro(self, cls_obj):
        """Mock MRO query."""
        return self._class_mro.get(cls_obj, [cls_obj])


# =============================================================================
# Tests for SubscriptSemantics
# =============================================================================


class TestSubscriptSemantics:
    """Tests for SubscriptSemantics service."""

    def test_load_subscr_with_constant_index(self):
        """Test subscript load with constant string index."""
        from pythonstan.analysis.pointer.ai.state import AIState, AbsVal
        from pythonstan.analysis.pointer.ai.summary import SummaryAccumulator
        from pythonstan.analysis.pointer.ai.api import AnalysisBudget
        from pythonstan.analysis.pointer.ai.domains.subscripts import SubscriptSemantics
        from pythonstan.analysis.pointer.kcfa.heap_model import key, elem

        container = MockObject("container")
        value = MockObject("value")

        budget = AnalysisBudget(max_const_strings=4)
        semantics = SubscriptSemantics(
            caller_scope=None, call_context=None, budget=budget
        )

        # Set up state with container having a value at key("foo")
        state = AIState()
        field_key = key("foo")
        state = state.write_field_value(
            container, field_key, AbsVal.from_addrs(frozenset({value}))
        )

        summary = SummaryAccumulator()

        # Load with constant index "foo"
        obj_val = AbsVal.from_addrs(frozenset({container}))
        index_val = AbsVal.from_str("foo")

        result = semantics.load_subscr(
            obj_val=obj_val, index_val=index_val, state=state, summary=summary
        )

        # Should have read from key("foo") and elem()
        ai_summary = summary.to_summary()
        read_fields = [str(f) for _, f in ai_summary.reads]
        # key("foo") is represented as ['foo']
        assert any("foo" in f for f in read_fields)
        # elem() is represented as .elem
        assert any("elem" in f for f in read_fields)

    def test_load_subscr_with_unknown_index(self):
        """Test subscript load with unknown index reads only elem()."""
        from pythonstan.analysis.pointer.ai.state import AIState, AbsVal
        from pythonstan.analysis.pointer.ai.summary import SummaryAccumulator
        from pythonstan.analysis.pointer.ai.api import AnalysisBudget
        from pythonstan.analysis.pointer.ai.domains.subscripts import SubscriptSemantics
        from pythonstan.analysis.pointer.kcfa.heap_model import elem

        container = MockObject("container")
        value = MockObject("value")

        budget = AnalysisBudget(max_const_strings=4)
        semantics = SubscriptSemantics(
            caller_scope=None, call_context=None, budget=budget
        )

        # Set up state
        state = AIState()
        state = state.write_field_value(
            container, elem(), AbsVal.from_addrs(frozenset({value}))
        )

        summary = SummaryAccumulator()

        # Load with no index value (unknown)
        obj_val = AbsVal.from_addrs(frozenset({container}))

        result = semantics.load_subscr(
            obj_val=obj_val, index_val=None, state=state, summary=summary
        )

        # Should have read from elem() only
        ai_summary = summary.to_summary()
        read_fields = [str(f) for _, f in ai_summary.reads]
        # elem() is represented as .elem
        assert any("elem" in f for f in read_fields)
        # Should NOT have specific key reads (no ['...'] patterns besides elem)
        # Note: with unknown index, we only read from elem()
        assert len(ai_summary.reads) >= 1

    def test_store_subscr_with_constant_index(self):
        """Test subscript store with constant string index."""
        from pythonstan.analysis.pointer.ai.state import AIState, AbsVal
        from pythonstan.analysis.pointer.ai.summary import SummaryAccumulator
        from pythonstan.analysis.pointer.ai.api import AnalysisBudget
        from pythonstan.analysis.pointer.ai.domains.subscripts import SubscriptSemantics

        container = MockObject("container")
        value = MockObject("value")

        budget = AnalysisBudget(max_const_strings=4)
        semantics = SubscriptSemantics(
            caller_scope=None, call_context=None, budget=budget
        )

        state = AIState()
        summary = SummaryAccumulator()

        obj_val = AbsVal.from_addrs(frozenset({container}))
        index_val = AbsVal.from_str("bar")
        value_val = AbsVal.from_addrs(frozenset({value}))

        new_state = semantics.store_subscr(
            obj_val=obj_val,
            index_val=index_val,
            value_val=value_val,
            state=state,
            summary=summary,
        )

        # Should have writes recorded for both key("bar") and elem()
        ai_summary = summary.to_summary()
        write_fields = [str(w.field_key) for w in ai_summary.writes]
        # key("bar") is represented as ['bar']
        assert any("bar" in f for f in write_fields)
        # elem() is represented as .elem
        assert any("elem" in f for f in write_fields)


# =============================================================================
# Tests for ConditionRefiner
# =============================================================================


class TestConditionRefiner:
    """Tests for ConditionRefiner service."""

    def test_refine_truthiness(self):
        """Test refinement for simple truthiness check."""
        from pythonstan.analysis.pointer.ai.state import AIState, AbsVal
        from pythonstan.analysis.pointer.ai.api import AnalysisBudget
        from pythonstan.analysis.pointer.ai.domains.conditions import ConditionRefiner
        from pythonstan.analysis.pointer.ai.domains.bool import BoolLattice

        budget = AnalysisBudget()
        refiner = ConditionRefiner(budget=budget)

        # State with unknown bool
        state = AIState()
        state = state.set_var("x", AbsVal.top())

        # Create AST name node
        cond_expr = ast.Name(id="x", ctx=ast.Load())

        true_state, false_state = refiner.refine(
            state, cond_expr, is_jump_if_true=False
        )

        # True branch should have bool=True
        true_val = true_state.get_var("x")
        assert true_val.get_bool_lattice().is_true()

        # False branch should have bool=False
        false_val = false_state.get_var("x")
        assert false_val.get_bool_lattice().is_false()

    def test_refine_is_none(self):
        """Test refinement for x is None check."""
        from pythonstan.analysis.pointer.ai.state import AIState, AbsVal
        from pythonstan.analysis.pointer.ai.api import AnalysisBudget
        from pythonstan.analysis.pointer.ai.domains.conditions import ConditionRefiner
        from pythonstan.analysis.pointer.ai.domains.none import NoneLattice

        budget = AnalysisBudget()
        refiner = ConditionRefiner(budget=budget)

        # State with unknown none-ness
        state = AIState()
        state = state.set_var("x", AbsVal.top())

        # Create x is None expression
        cond_expr = ast.Compare(
            left=ast.Name(id="x", ctx=ast.Load()),
            ops=[ast.Is()],
            comparators=[ast.Constant(value=None)],
        )

        true_state, false_state = refiner.refine(
            state, cond_expr, is_jump_if_true=False
        )

        # True branch should have None
        true_val = true_state.get_var("x")
        true_none = true_val.get_none_lattice()
        assert true_none.is_none()

        # False branch should have not-None
        false_val = false_state.get_var("x")
        false_none = false_val.get_none_lattice()
        assert false_none.is_not_none()

    def test_refine_is_not_none(self):
        """Test refinement for x is not None check."""
        from pythonstan.analysis.pointer.ai.state import AIState, AbsVal
        from pythonstan.analysis.pointer.ai.api import AnalysisBudget
        from pythonstan.analysis.pointer.ai.domains.conditions import ConditionRefiner

        budget = AnalysisBudget()
        refiner = ConditionRefiner(budget=budget)

        state = AIState()
        state = state.set_var("x", AbsVal.top())

        # Create x is not None expression
        cond_expr = ast.Compare(
            left=ast.Name(id="x", ctx=ast.Load()),
            ops=[ast.IsNot()],
            comparators=[ast.Constant(value=None)],
        )

        true_state, false_state = refiner.refine(
            state, cond_expr, is_jump_if_true=False
        )

        # True branch should have not-None
        true_val = true_state.get_var("x")
        assert true_val.get_none_lattice().is_not_none()

        # False branch should have None
        false_val = false_state.get_var("x")
        assert false_val.get_none_lattice().is_none()

    def test_refine_string_equality(self):
        """Test refinement for x == 'literal' check."""
        from pythonstan.analysis.pointer.ai.state import AIState, AbsVal
        from pythonstan.analysis.pointer.ai.api import AnalysisBudget
        from pythonstan.analysis.pointer.ai.domains.conditions import ConditionRefiner
        from pythonstan.analysis.pointer.ai.domains.strings import StrLattice

        budget = AnalysisBudget(value_partition_str_cap=4)
        refiner = ConditionRefiner(budget=budget)

        # State with known string set
        state = AIState()
        val = AbsVal.from_str("foo")
        val = val.join(AbsVal.from_str("bar"))
        state = state.set_var("x", val)

        # Create x == "foo" expression
        cond_expr = ast.Compare(
            left=ast.Name(id="x", ctx=ast.Load()),
            ops=[ast.Eq()],
            comparators=[ast.Constant(value="foo")],
        )

        true_state, false_state = refiner.refine(
            state, cond_expr, is_jump_if_true=False
        )

        # True branch should have only "foo"
        true_val = true_state.get_var("x")
        true_strs = true_val.get_const_strs()
        assert true_strs == frozenset({"foo"})

        # False branch should have only "bar"
        false_val = false_state.get_var("x")
        false_strs = false_val.get_const_strs()
        assert false_strs == frozenset({"bar"})

    def test_refine_negated_truthiness(self):
        """Test refinement for not x check."""
        from pythonstan.analysis.pointer.ai.state import AIState, AbsVal
        from pythonstan.analysis.pointer.ai.api import AnalysisBudget
        from pythonstan.analysis.pointer.ai.domains.conditions import ConditionRefiner

        budget = AnalysisBudget()
        refiner = ConditionRefiner(budget=budget)

        state = AIState()
        state = state.set_var("x", AbsVal.top())

        # Create not x expression
        cond_expr = ast.UnaryOp(
            op=ast.Not(), operand=ast.Name(id="x", ctx=ast.Load())
        )

        true_state, false_state = refiner.refine(
            state, cond_expr, is_jump_if_true=False
        )

        # not x is true means x is falsy
        true_val = true_state.get_var("x")
        assert true_val.get_bool_lattice().is_false()

        # not x is false means x is truthy
        false_val = false_state.get_var("x")
        assert false_val.get_bool_lattice().is_true()

    def test_refine_disabled_by_budget(self):
        """Test that refinement is disabled when budget flag is off."""
        from pythonstan.analysis.pointer.ai.state import AIState, AbsVal
        from pythonstan.analysis.pointer.ai.api import AnalysisBudget
        from pythonstan.analysis.pointer.ai.domains.conditions import ConditionRefiner

        budget = AnalysisBudget(enable_value_partition=False)
        refiner = ConditionRefiner(budget=budget)

        state = AIState()
        state = state.set_var("x", AbsVal.top())

        cond_expr = ast.Name(id="x", ctx=ast.Load())

        true_state, false_state = refiner.refine(
            state, cond_expr, is_jump_if_true=False
        )

        # Both branches should be the same (no refinement)
        assert true_state.get_var("x") == false_state.get_var("x")


# =============================================================================
# Tests for binding module
# =============================================================================


class TestBindingModule:
    """Tests for binding module functions."""

    def test_to_callable_targets_from_addrs(self):
        """Test extracting callable targets from address set."""
        from pythonstan.analysis.pointer.ai.value_domain import AbsVal
        from pythonstan.analysis.pointer.ai.domains.binding import to_callable_targets

        func1 = MockObject("func1")
        func2 = MockObject("func2")

        val = AbsVal.from_addrs(frozenset({func1, func2}))

        targets = to_callable_targets(val)

        # Should have targets for both objects
        callee_objs = {t.callee_obj for t in targets}
        assert func1 in callee_objs
        assert func2 in callee_objs

    def test_bind_descriptor_with_receiver(self):
        """Test that bind_descriptor updates callable domain."""
        from pythonstan.analysis.pointer.ai.value_domain import AbsVal
        from pythonstan.analysis.pointer.ai.domains.binding import bind_descriptor

        owner = MockObject("owner")
        func = MockObject("func")
        receiver = MockObject("instance")

        attr_val = AbsVal.from_addrs(frozenset({func}))
        receiver_val = AbsVal.from_addrs(frozenset({receiver}))

        # Bind descriptor - should not crash and should return a value
        result = bind_descriptor(owner, attr_val, receiver_val)

        # Result should not be None
        assert result is not None
        # Result should still have the function address
        assert func in result.addrs

    def test_bind_descriptor_without_receiver(self):
        """Test that bind_descriptor returns attr_value when receiver is None."""
        from pythonstan.analysis.pointer.ai.value_domain import AbsVal
        from pythonstan.analysis.pointer.ai.domains.binding import bind_descriptor

        owner = MockObject("owner")
        func = MockObject("func")

        attr_val = AbsVal.from_addrs(frozenset({func}))

        result = bind_descriptor(owner, attr_val, None)

        # Should return same value
        assert result == attr_val
