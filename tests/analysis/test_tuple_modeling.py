"""Tests for tuple modeling in abstract interpretation.

Tests:
- TupleExact/TupleSummary/TupleDisj data structures
- Tuple materialization from AI state
- SubscriptSemantics int index support
- list(tuple) intrinsic for unpacking precision
- *args expansion
"""

import pytest
from typing import FrozenSet


class MockAllocSite:
    """Mock alloc site for testing."""
    
    def __init__(self, name: str):
        self.name = name
        self.stmt = None
        self.kind = "mock"
    
    def __hash__(self):
        return hash(self.name)
    
    def __eq__(self, other):
        if isinstance(other, MockAllocSite):
            return self.name == other.name
        return False


class MockContext:
    """Mock context for testing."""
    
    def __init__(self, name: str = "mock_ctx"):
        self.name = name
    
    def __hash__(self):
        return hash(self.name)
    
    def __eq__(self, other):
        if isinstance(other, MockContext):
            return self.name == other.name
        return False


class MockAbstractObject:
    """Mock AbstractObject for testing without full KCFA infrastructure."""
    
    def __init__(self, name: str):
        self.name = name
        self.alloc_site = MockAllocSite(name)
        self.context = MockContext(name)
    
    def __hash__(self):
        return hash(self.name)
    
    def __eq__(self, other):
        if isinstance(other, MockAbstractObject):
            return self.name == other.name
        return False
    
    def __str__(self):
        return f"MockObj({self.name})"
    
    def __repr__(self):
        return self.__str__()


class TestTupleExact:
    """Tests for TupleExact data structure."""

    def test_tuple_exact_creation(self):
        """Create a TupleExact with elements."""
        from pythonstan.analysis.pointer.ai.domains.tuples import TupleExact
        from pythonstan.analysis.pointer.ai.value_domain import AbsVal
        
        elems = (
            AbsVal.from_int(1),
            AbsVal.from_str("hello"),
            AbsVal.none_val(),
        )
        te = TupleExact(elems=elems)
        
        assert te.length == 3
        assert te.get(0) == AbsVal.from_int(1)
        assert te.get(1) == AbsVal.from_str("hello")
        assert te.get(2).get_none_lattice().is_none()
        assert te.get(3) is None  # Out of bounds

    def test_tuple_exact_join_same_length(self):
        """Join two TupleExact of same length."""
        from pythonstan.analysis.pointer.ai.domains.tuples import TupleExact
        from pythonstan.analysis.pointer.ai.value_domain import AbsVal
        
        te1 = TupleExact(elems=(AbsVal.from_int(1), AbsVal.from_int(2)))
        te2 = TupleExact(elems=(AbsVal.from_int(3), AbsVal.from_int(4)))
        
        joined = te1.join(te2)
        assert joined is not None
        assert joined.length == 2
        
        # Elements should be joined
        elem0 = joined.get(0)
        assert elem0 is not None
        int_lattice = elem0.get_int_lattice()
        consts = int_lattice.get_consts()
        assert consts is not None
        assert 1 in consts
        assert 3 in consts

    def test_tuple_exact_join_different_length(self):
        """Join two TupleExact of different lengths returns None."""
        from pythonstan.analysis.pointer.ai.domains.tuples import TupleExact
        from pythonstan.analysis.pointer.ai.value_domain import AbsVal
        
        te1 = TupleExact(elems=(AbsVal.from_int(1),))
        te2 = TupleExact(elems=(AbsVal.from_int(1), AbsVal.from_int(2)))
        
        assert te1.join(te2) is None


class TestTupleSummary:
    """Tests for TupleSummary data structure."""

    def test_tuple_summary_creation(self):
        """Create a TupleSummary with prefix and tail."""
        from pythonstan.analysis.pointer.ai.domains.tuples import TupleSummary
        from pythonstan.analysis.pointer.ai.value_domain import AbsVal
        
        prefix = (AbsVal.from_int(1), AbsVal.from_int(2))
        elem = AbsVal.top()
        ts = TupleSummary(prefix=prefix, elem=elem, min_len=2, max_len=10)
        
        assert ts.prefix_len == 2
        assert ts.get(0) == AbsVal.from_int(1)
        assert ts.get(1) == AbsVal.from_int(2)
        # Tail elements return elem
        elem2 = ts.get(2)
        assert elem2 is not None
        assert ts.get(100) is None  # Beyond max_len

    def test_tuple_summary_join(self):
        """Join two TupleSummary."""
        from pythonstan.analysis.pointer.ai.domains.tuples import TupleSummary
        from pythonstan.analysis.pointer.ai.value_domain import AbsVal
        
        ts1 = TupleSummary(
            prefix=(AbsVal.from_int(1), AbsVal.from_int(2)),
            elem=AbsVal.from_int(3),
            min_len=2,
            max_len=5,
        )
        ts2 = TupleSummary(
            prefix=(AbsVal.from_int(10), AbsVal.from_int(20), AbsVal.from_int(30)),
            elem=AbsVal.from_int(40),
            min_len=3,
            max_len=8,
        )
        
        joined = ts1.join(ts2)
        
        # Common prefix length is min(2, 3) = 2
        assert joined.prefix_len == 2
        assert joined.min_len == 2
        assert joined.max_len == 8


class TestTupleDisj:
    """Tests for TupleDisj data structure."""

    def test_tuple_disj_empty(self):
        """Empty disjunction."""
        from pythonstan.analysis.pointer.ai.domains.tuples import TupleDisj
        
        disj = TupleDisj.empty()
        assert disj.is_empty()
        assert disj.num_alts == 0

    def test_tuple_disj_from_exact(self):
        """Create disjunction from single exact tuple."""
        from pythonstan.analysis.pointer.ai.domains.tuples import TupleExact, TupleDisj
        from pythonstan.analysis.pointer.ai.value_domain import AbsVal
        
        te = TupleExact(elems=(AbsVal.from_int(1), AbsVal.from_int(2)))
        disj = TupleDisj.from_exact(te)
        
        assert not disj.is_empty()
        assert disj.is_exact_single()
        assert disj.num_alts == 1

    def test_tuple_disj_get_joined(self):
        """Get element joined across alternatives."""
        from pythonstan.analysis.pointer.ai.domains.tuples import TupleExact, TupleDisj
        from pythonstan.analysis.pointer.ai.value_domain import AbsVal
        
        te1 = TupleExact(elems=(AbsVal.from_int(1), AbsVal.from_int(2)))
        te2 = TupleExact(elems=(AbsVal.from_int(10), AbsVal.from_int(20)))
        
        disj = TupleDisj(alts=(te1, te2))
        
        elem0 = disj.get(0)
        assert elem0 is not None
        int_lattice = elem0.get_int_lattice()
        consts = int_lattice.get_consts()
        assert consts is not None
        assert 1 in consts
        assert 10 in consts

    def test_tuple_disj_join_cap(self):
        """Join disjunctions respecting cap."""
        from pythonstan.analysis.pointer.ai.domains.tuples import TupleExact, TupleDisj
        from pythonstan.analysis.pointer.ai.value_domain import AbsVal
        
        te1 = TupleExact(elems=(AbsVal.from_int(1),))
        te2 = TupleExact(elems=(AbsVal.from_int(2),))
        te3 = TupleExact(elems=(AbsVal.from_int(3),))
        te4 = TupleExact(elems=(AbsVal.from_int(4),))
        te5 = TupleExact(elems=(AbsVal.from_int(5),))
        
        disj1 = TupleDisj(alts=(te1, te2))
        disj2 = TupleDisj(alts=(te3, te4, te5))
        
        # Join with cap of 4
        joined = disj1.join(disj2, max_alts=4)
        
        # Should stay within cap
        assert joined.num_alts <= 4


class TestSubscriptSemanticsIntIndex:
    """Tests for SubscriptSemantics int index support."""

    def test_load_subscr_int_index(self):
        """Load with constant int index uses key(str(i))."""
        from pythonstan.analysis.pointer.ai.domains.subscripts import SubscriptSemantics
        from pythonstan.analysis.pointer.ai.state import AIState, AbsVal
        from pythonstan.analysis.pointer.ai.summary import SummaryAccumulator
        from pythonstan.analysis.pointer.ai.api import AnalysisBudget
        from pythonstan.analysis.pointer.kcfa.heap_model import key
        
        budget = AnalysisBudget()
        sem = SubscriptSemantics(caller_scope=None, call_context=None, budget=budget)
        
        # Create a mock object and state with overlay data
        obj = MockAbstractObject("tuple_obj")
        state = AIState()
        
        # Write to key("0") and key("1")
        state = state.add_overlay_write(obj, key("0"), AbsVal.from_int(100))
        state = state.add_overlay_write(obj, key("1"), AbsVal.from_int(200))
        
        summary = SummaryAccumulator()
        obj_val = AbsVal.from_addrs(frozenset({obj}))
        
        # Load with int index 0
        index_val = AbsVal.from_int(0)
        result = sem.load_subscr(obj_val, index_val, state, summary)
        
        # Should include value from key("0")
        int_lattice = result.result_val.get_int_lattice()
        consts = int_lattice.get_consts()
        assert consts is not None
        assert 100 in consts

    def test_store_subscr_int_index(self):
        """Store with constant int index writes to key(str(i))."""
        from pythonstan.analysis.pointer.ai.domains.subscripts import SubscriptSemantics
        from pythonstan.analysis.pointer.ai.state import AIState, AbsVal
        from pythonstan.analysis.pointer.ai.summary import SummaryAccumulator
        from pythonstan.analysis.pointer.ai.api import AnalysisBudget
        from pythonstan.analysis.pointer.kcfa.heap_model import key
        
        budget = AnalysisBudget()
        sem = SubscriptSemantics(caller_scope=None, call_context=None, budget=budget)
        
        obj = MockAbstractObject("list_obj")
        state = AIState()
        summary = SummaryAccumulator()
        
        obj_val = AbsVal.from_addrs(frozenset({obj}))
        index_val = AbsVal.from_int(0)
        value_val = AbsVal.from_int(42)
        
        new_state = sem.store_subscr(obj_val, index_val, value_val, state, summary)
        
        # Check that value was written to key("0")
        written_val = new_state.overlay.get((obj, key("0")))
        assert written_val is not None
        int_lattice = written_val.get_int_lattice()
        consts = int_lattice.get_consts()
        assert consts is not None
        assert 42 in consts


class TestTupleMaterialization:
    """Tests for tuple materialization from AI state."""

    def test_materialize_tuple_shape_exact(self):
        """Materialize exact tuple shape from overlay."""
        from pythonstan.analysis.pointer.ai.domains.tuples import materialize_tuple_shape, TupleExact
        from pythonstan.analysis.pointer.ai.state import AIState, AbsVal
        from pythonstan.analysis.pointer.kcfa.heap_model import key
        
        obj = MockAbstractObject("tuple_obj")
        state = AIState()
        
        # Write elements at key("0"), key("1"), key("2")
        state = state.add_overlay_write(obj, key("0"), AbsVal.from_int(10))
        state = state.add_overlay_write(obj, key("1"), AbsVal.from_int(20))
        state = state.add_overlay_write(obj, key("2"), AbsVal.from_int(30))
        
        shape = materialize_tuple_shape(obj, state, None, None, max_len=8)
        
        assert shape is not None
        assert isinstance(shape, TupleExact)
        assert shape.length == 3
        
        elem0 = shape.get(0)
        assert elem0 is not None
        int_lattice = elem0.get_int_lattice()
        consts = int_lattice.get_consts()
        assert consts is not None
        assert 10 in consts

    def test_materialize_tuple_disj(self):
        """Materialize tuple disjunction from multiple objects."""
        from pythonstan.analysis.pointer.ai.domains.tuples import materialize_tuple_disj
        from pythonstan.analysis.pointer.ai.state import AIState, AbsVal
        from pythonstan.analysis.pointer.kcfa.heap_model import key
        
        obj1 = MockAbstractObject("tuple1")
        obj2 = MockAbstractObject("tuple2")
        state = AIState()
        
        # obj1 has 2 elements
        state = state.add_overlay_write(obj1, key("0"), AbsVal.from_int(1))
        state = state.add_overlay_write(obj1, key("1"), AbsVal.from_int(2))
        
        # obj2 has 3 elements
        state = state.add_overlay_write(obj2, key("0"), AbsVal.from_int(10))
        state = state.add_overlay_write(obj2, key("1"), AbsVal.from_int(20))
        state = state.add_overlay_write(obj2, key("2"), AbsVal.from_int(30))
        
        val = AbsVal.from_addrs(frozenset({obj1, obj2}))
        disj = materialize_tuple_disj(val, state, None, None, max_len=8, max_alts=4)
        
        assert not disj.is_empty()
        assert disj.num_alts == 2
        
        lengths = disj.get_all_lengths()
        assert 2 in lengths
        assert 3 in lengths


class TestExpandStarredArg:
    """Tests for *args expansion."""

    def test_expand_starred_single_exact(self):
        """Expand starred arg with single exact tuple."""
        from pythonstan.analysis.pointer.ai.domains.tuples import expand_starred_arg
        from pythonstan.analysis.pointer.ai.state import AIState, AbsVal
        from pythonstan.analysis.pointer.kcfa.heap_model import key
        
        obj = MockAbstractObject("tuple_obj")
        state = AIState()
        
        state = state.add_overlay_write(obj, key("0"), AbsVal.from_int(1))
        state = state.add_overlay_write(obj, key("1"), AbsVal.from_int(2))
        state = state.add_overlay_write(obj, key("2"), AbsVal.from_int(3))
        
        val = AbsVal.from_addrs(frozenset({obj}))
        result = expand_starred_arg(val, state, None, None, max_expand_len=8, max_expand_alts=4)
        
        assert result is not None
        assert len(result) == 1
        
        elems, length = result[0]
        assert length == 3
        assert len(elems) == 3

    def test_expand_starred_multiple_alts(self):
        """Expand starred arg with multiple tuple alternatives."""
        from pythonstan.analysis.pointer.ai.domains.tuples import expand_starred_arg
        from pythonstan.analysis.pointer.ai.state import AIState, AbsVal
        from pythonstan.analysis.pointer.kcfa.heap_model import key
        
        obj1 = MockAbstractObject("tuple1")
        obj2 = MockAbstractObject("tuple2")
        state = AIState()
        
        # Two tuples of different lengths
        state = state.add_overlay_write(obj1, key("0"), AbsVal.from_int(1))
        state = state.add_overlay_write(obj1, key("1"), AbsVal.from_int(2))
        
        state = state.add_overlay_write(obj2, key("0"), AbsVal.from_int(10))
        state = state.add_overlay_write(obj2, key("1"), AbsVal.from_int(20))
        state = state.add_overlay_write(obj2, key("2"), AbsVal.from_int(30))
        
        val = AbsVal.from_addrs(frozenset({obj1, obj2}))
        result = expand_starred_arg(val, state, None, None, max_expand_len=8, max_expand_alts=4)
        
        assert result is not None
        assert len(result) == 2
        
        lengths = {r[1] for r in result}
        assert 2 in lengths
        assert 3 in lengths

    def test_expand_starred_too_long_returns_none(self):
        """Expansion returns None for tuples exceeding max length."""
        from pythonstan.analysis.pointer.ai.domains.tuples import expand_starred_arg
        from pythonstan.analysis.pointer.ai.state import AIState, AbsVal
        from pythonstan.analysis.pointer.kcfa.heap_model import key
        
        obj = MockAbstractObject("tuple_obj")
        state = AIState()
        
        # Create a 10-element tuple
        for i in range(10):
            state = state.add_overlay_write(obj, key(str(i)), AbsVal.from_int(i))
        
        val = AbsVal.from_addrs(frozenset({obj}))
        
        # With max_expand_len=4, should return None
        result = expand_starred_arg(val, state, None, None, max_expand_len=4, max_expand_alts=4)
        
        assert result is None


class TestAnalysisBudgetTupleSettings:
    """Tests for AnalysisBudget tuple-related settings."""

    def test_default_tuple_settings(self):
        """Check default tuple-related budget settings."""
        from pythonstan.analysis.pointer.ai.api import AnalysisBudget
        
        budget = AnalysisBudget()
        
        assert budget.max_const_ints == 4
        assert budget.max_tuple_len_exact == 8
        assert budget.max_tuple_alts == 4
        assert budget.max_star_expand_len == 8
        assert budget.max_star_expand_alts == 4

    def test_custom_tuple_settings(self):
        """Check custom tuple-related budget settings."""
        from pythonstan.analysis.pointer.ai.api import AnalysisBudget
        
        budget = AnalysisBudget(
            max_const_ints=8,
            max_tuple_len_exact=16,
            max_tuple_alts=8,
            max_star_expand_len=12,
            max_star_expand_alts=6,
        )
        
        assert budget.max_const_ints == 8
        assert budget.max_tuple_len_exact == 16
        assert budget.max_tuple_alts == 8
        assert budget.max_star_expand_len == 12
        assert budget.max_star_expand_alts == 6
