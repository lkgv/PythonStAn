"""Tests for the integer domain with bounded constants, intervals, and parity.

Tests:
- IntLattice creation and predicates
- Lattice operations (join, meet, leq, widen)
- Refinement methods for condition handling
- IntDomain fact production/consumption
- Integration with AbsVal and ConditionRefiner
- Bool domain consumption of int facts
"""

import pytest
from typing import FrozenSet


class TestIntLatticeCreation:
    """Tests for IntLattice factory methods and predicates."""

    def test_bottom_creation(self):
        """Bottom represents no integers."""
        from pythonstan.analysis.pointer.ai.domains.ints import IntLattice

        bottom = IntLattice.bottom()
        assert bottom.is_bottom
        assert not bottom.is_top
        assert bottom.consts == frozenset()
        assert not bottom.is_enumerable()

    def test_top_creation(self):
        """Top represents any integer."""
        from pythonstan.analysis.pointer.ai.domains.ints import IntLattice

        top = IntLattice.top()
        assert top.is_top
        assert not top.is_bottom
        assert not top.is_enumerable()

    def test_const_creation(self):
        """Create single constant integer."""
        from pythonstan.analysis.pointer.ai.domains.ints import IntLattice

        n = IntLattice.const(42)
        assert n.is_const()
        assert n.is_singleton()
        assert n.consts == frozenset({42})
        assert n.is_enumerable()
        assert n.get_singleton() == 42
        assert n.get_consts() == frozenset({42})

    def test_const_zero(self):
        """Create zero constant."""
        from pythonstan.analysis.pointer.ai.domains.ints import IntLattice

        z = IntLattice.zero()
        assert z.is_singleton()
        assert z.get_singleton() == 0
        assert z.is_zero()

    def test_consts_creation(self):
        """Create set of constant integers."""
        from pythonstan.analysis.pointer.ai.domains.ints import IntLattice

        s = IntLattice.from_consts(frozenset({1, 2, 3}))
        assert s.is_const()
        assert s.consts == frozenset({1, 2, 3})
        assert s.is_enumerable()
        assert s.lo == 1
        assert s.hi == 3

    def test_empty_consts_is_bottom(self):
        """Empty constant set is bottom."""
        from pythonstan.analysis.pointer.ai.domains.ints import IntLattice

        s = IntLattice.from_consts(frozenset())
        assert s.is_bottom

    def test_interval_creation(self):
        """Create interval [lo, hi]."""
        from pythonstan.analysis.pointer.ai.domains.ints import IntLattice

        s = IntLattice.from_interval(0, 10)
        assert not s.is_bottom
        assert not s.is_top
        assert s.lo == 0
        assert s.hi == 10

    def test_interval_unbounded_lo(self):
        """Create interval (-∞, hi]."""
        from pythonstan.analysis.pointer.ai.domains.ints import IntLattice

        s = IntLattice.from_interval(None, 10)
        assert not s.is_bottom
        assert not s.is_top
        assert s.lo is None
        assert s.hi == 10

    def test_interval_unbounded_hi(self):
        """Create interval [lo, +∞)."""
        from pythonstan.analysis.pointer.ai.domains.ints import IntLattice

        s = IntLattice.from_interval(0, None)
        assert not s.is_bottom
        assert not s.is_top
        assert s.lo == 0
        assert s.hi is None

    def test_interval_empty_is_bottom(self):
        """Empty interval (lo > hi) is bottom."""
        from pythonstan.analysis.pointer.ai.domains.ints import IntLattice

        s = IntLattice.from_interval(10, 5)
        assert s.is_bottom

    def test_interval_unbounded_is_top(self):
        """Unbounded interval with TOP parity is top."""
        from pythonstan.analysis.pointer.ai.domains.ints import IntLattice, Parity

        s = IntLattice.from_interval(None, None, Parity.TOP)
        assert s.is_top


class TestIntLatticeParity:
    """Tests for parity tracking."""

    def test_const_even_parity(self):
        """Even constant has EVEN parity."""
        from pythonstan.analysis.pointer.ai.domains.ints import IntLattice, Parity

        n = IntLattice.const(4)
        assert n.parity == Parity.EVEN
        assert n.is_even()
        assert not n.is_odd()

    def test_const_odd_parity(self):
        """Odd constant has ODD parity."""
        from pythonstan.analysis.pointer.ai.domains.ints import IntLattice, Parity

        n = IntLattice.const(3)
        assert n.parity == Parity.ODD
        assert n.is_odd()
        assert not n.is_even()

    def test_mixed_consts_top_parity(self):
        """Mixed even/odd constants have TOP parity."""
        from pythonstan.analysis.pointer.ai.domains.ints import IntLattice, Parity

        n = IntLattice.from_consts(frozenset({2, 3}))
        assert n.parity == Parity.TOP

    def test_all_even_consts(self):
        """All even constants have EVEN parity."""
        from pythonstan.analysis.pointer.ai.domains.ints import IntLattice, Parity

        n = IntLattice.from_consts(frozenset({2, 4, 6}))
        assert n.parity == Parity.EVEN

    def test_all_odd_consts(self):
        """All odd constants have ODD parity."""
        from pythonstan.analysis.pointer.ai.domains.ints import IntLattice, Parity

        n = IntLattice.from_consts(frozenset({1, 3, 5}))
        assert n.parity == Parity.ODD


class TestIntLatticePredicates:
    """Tests for IntLattice predicates."""

    def test_may_be_zero(self):
        """may_be_zero is correct."""
        from pythonstan.analysis.pointer.ai.domains.ints import IntLattice

        assert IntLattice.zero().may_be_zero()
        assert IntLattice.from_consts(frozenset({-1, 0, 1})).may_be_zero()
        assert not IntLattice.const(1).may_be_zero()
        assert not IntLattice.from_consts(frozenset({1, 2, 3})).may_be_zero()
        assert IntLattice.top().may_be_zero()
        assert not IntLattice.bottom().may_be_zero()

    def test_may_be_negative(self):
        """may_be_negative is correct."""
        from pythonstan.analysis.pointer.ai.domains.ints import IntLattice

        assert IntLattice.const(-1).may_be_negative()
        assert IntLattice.from_interval(None, 10).may_be_negative()
        assert not IntLattice.from_interval(0, 10).may_be_negative()
        assert IntLattice.top().may_be_negative()

    def test_may_be_positive(self):
        """may_be_positive is correct."""
        from pythonstan.analysis.pointer.ai.domains.ints import IntLattice

        assert IntLattice.const(1).may_be_positive()
        assert IntLattice.from_interval(-10, None).may_be_positive()
        assert not IntLattice.from_interval(-10, 0).may_be_positive()
        assert IntLattice.top().may_be_positive()

    def test_is_nonneg(self):
        """is_nonneg is correct."""
        from pythonstan.analysis.pointer.ai.domains.ints import IntLattice

        assert IntLattice.nonneg().is_nonneg()
        assert IntLattice.from_consts(frozenset({0, 1, 2})).is_nonneg()
        assert not IntLattice.from_consts(frozenset({-1, 0, 1})).is_nonneg()
        assert not IntLattice.top().is_nonneg()

    def test_is_nonpos(self):
        """is_nonpos is correct."""
        from pythonstan.analysis.pointer.ai.domains.ints import IntLattice

        assert IntLattice.nonpos().is_nonpos()
        assert IntLattice.from_consts(frozenset({-2, -1, 0})).is_nonpos()
        assert not IntLattice.from_consts(frozenset({-1, 0, 1})).is_nonpos()


class TestIntLatticeContainment:
    """Tests for containment checking."""

    def test_may_contain_const(self):
        """may_contain is correct for constants."""
        from pythonstan.analysis.pointer.ai.domains.ints import IntLattice

        s = IntLattice.from_consts(frozenset({1, 2, 3}))
        assert s.may_contain(1)
        assert s.may_contain(2)
        assert s.may_contain(3)
        assert not s.may_contain(0)
        assert not s.may_contain(4)

    def test_may_contain_interval(self):
        """may_contain is correct for intervals."""
        from pythonstan.analysis.pointer.ai.domains.ints import IntLattice

        s = IntLattice.from_interval(0, 10)
        assert s.may_contain(0)
        assert s.may_contain(5)
        assert s.may_contain(10)
        assert not s.may_contain(-1)
        assert not s.may_contain(11)

    def test_may_contain_top(self):
        """Top may contain any integer."""
        from pythonstan.analysis.pointer.ai.domains.ints import IntLattice

        assert IntLattice.top().may_contain(0)
        assert IntLattice.top().may_contain(1000000)
        assert IntLattice.top().may_contain(-1000000)

    def test_may_contain_bottom(self):
        """Bottom may not contain any integer."""
        from pythonstan.analysis.pointer.ai.domains.ints import IntLattice

        assert not IntLattice.bottom().may_contain(0)
        assert not IntLattice.bottom().may_contain(1)


class TestIntDomainJoin:
    """Tests for IntDomain join operation."""

    def test_join_bottom_identity(self):
        """Bottom is identity for join."""
        from pythonstan.analysis.pointer.ai.domains.ints import IntDomain, IntLattice

        domain = IntDomain()
        n = IntLattice.const(5)
        result = domain.join(IntLattice.bottom(), n)
        assert result == n

    def test_join_top_absorbs(self):
        """Top absorbs in join."""
        from pythonstan.analysis.pointer.ai.domains.ints import IntDomain, IntLattice

        domain = IntDomain()
        n = IntLattice.const(5)
        result = domain.join(IntLattice.top(), n)
        assert result.is_top

    def test_join_consts(self):
        """Join unions constant sets."""
        from pythonstan.analysis.pointer.ai.domains.ints import IntDomain, IntLattice

        domain = IntDomain()
        s1 = IntLattice.from_consts(frozenset({1, 2}))
        s2 = IntLattice.from_consts(frozenset({2, 3}))
        result = domain.join(s1, s2)
        assert result.consts == frozenset({1, 2, 3})

    def test_join_intervals(self):
        """Join takes hull of intervals."""
        from pythonstan.analysis.pointer.ai.domains.ints import IntDomain, IntLattice

        domain = IntDomain()
        s1 = IntLattice.from_interval(0, 5)
        s2 = IntLattice.from_interval(3, 10)
        result = domain.join(s1, s2)
        assert result.lo == 0
        assert result.hi == 10


class TestIntDomainMeet:
    """Tests for IntDomain meet operation."""

    def test_meet_top_identity(self):
        """Top is identity for meet."""
        from pythonstan.analysis.pointer.ai.domains.ints import IntDomain, IntLattice

        domain = IntDomain()
        n = IntLattice.const(5)
        result = domain.meet(IntLattice.top(), n)
        assert result == n

    def test_meet_bottom_absorbs(self):
        """Bottom absorbs in meet."""
        from pythonstan.analysis.pointer.ai.domains.ints import IntDomain, IntLattice

        domain = IntDomain()
        n = IntLattice.const(5)
        result = domain.meet(IntLattice.bottom(), n)
        assert result.is_bottom

    def test_meet_consts_intersection(self):
        """Meet intersects constant sets."""
        from pythonstan.analysis.pointer.ai.domains.ints import IntDomain, IntLattice

        domain = IntDomain()
        s1 = IntLattice.from_consts(frozenset({1, 2, 3}))
        s2 = IntLattice.from_consts(frozenset({2, 3, 4}))
        result = domain.meet(s1, s2)
        assert result.consts == frozenset({2, 3})

    def test_meet_interval_intersection(self):
        """Meet takes intersection of intervals."""
        from pythonstan.analysis.pointer.ai.domains.ints import IntDomain, IntLattice

        domain = IntDomain()
        s1 = IntLattice.from_interval(0, 10)
        s2 = IntLattice.from_interval(5, 15)
        result = domain.meet(s1, s2)
        assert result.lo == 5
        assert result.hi == 10

    def test_meet_disjoint_intervals_bottom(self):
        """Disjoint intervals meet to bottom."""
        from pythonstan.analysis.pointer.ai.domains.ints import IntDomain, IntLattice

        domain = IntDomain()
        s1 = IntLattice.from_interval(0, 5)
        s2 = IntLattice.from_interval(10, 15)
        result = domain.meet(s1, s2)
        assert result.is_bottom


class TestIntDomainLeq:
    """Tests for IntDomain partial order."""

    def test_leq_bottom_least(self):
        """Bottom is less than everything."""
        from pythonstan.analysis.pointer.ai.domains.ints import IntDomain, IntLattice

        domain = IntDomain()
        assert domain.leq(IntLattice.bottom(), IntLattice.top())
        assert domain.leq(IntLattice.bottom(), IntLattice.const(5))

    def test_leq_top_greatest(self):
        """Everything is less than top."""
        from pythonstan.analysis.pointer.ai.domains.ints import IntDomain, IntLattice

        domain = IntDomain()
        assert domain.leq(IntLattice.const(5), IntLattice.top())
        assert domain.leq(IntLattice.from_interval(0, 10), IntLattice.top())

    def test_leq_subset_consts(self):
        """Subset of constants is less than superset."""
        from pythonstan.analysis.pointer.ai.domains.ints import IntDomain, IntLattice

        domain = IntDomain()
        s1 = IntLattice.from_consts(frozenset({1}))
        s2 = IntLattice.from_consts(frozenset({1, 2}))
        assert domain.leq(s1, s2)
        assert not domain.leq(s2, s1)

    def test_leq_interval_contained(self):
        """Smaller interval is less than larger interval."""
        from pythonstan.analysis.pointer.ai.domains.ints import IntDomain, IntLattice

        domain = IntDomain()
        s1 = IntLattice.from_interval(2, 5)
        s2 = IntLattice.from_interval(0, 10)
        assert domain.leq(s1, s2)
        assert not domain.leq(s2, s1)


class TestIntDomainWiden:
    """Tests for widening."""

    def test_widen_under_cap(self):
        """An unstable bound widens even below the finite-constant cap."""
        from pythonstan.analysis.pointer.ai.domains.ints import IntDomain, IntLattice

        domain = IntDomain(max_consts=10)
        s1 = IntLattice.from_consts(frozenset({1, 2}))
        s2 = IntLattice.from_consts(frozenset({3, 4}))
        result = domain.widen(s1, s2)
        # Frozen A12 semantics distinguish an exact finite set from an interval.
        # The previous test kept {1,2,3,4} while hi had widened to infinity,
        # allowing consumers to enumerate an unbounded result incompletely.
        assert result.consts == frozenset()
        assert not result.is_enumerable()
        assert result.get_consts() is None
        assert result.lo == 1 and result.hi is None
        assert not result.is_top  # Keep the useful lower bound, rather than Top.
        assert domain.leq(s1, result) and domain.leq(s2, result)

    def test_widen_exceeds_const_cap(self):
        """Widening exceeding const cap compresses to interval."""
        from pythonstan.analysis.pointer.ai.domains.ints import IntDomain, IntLattice

        domain = IntDomain(max_consts=3)
        s1 = IntLattice.from_consts(frozenset({1, 2, 3}))
        s2 = IntLattice.from_consts(frozenset({4, 5}))
        result = domain.widen(s1, s2)
        # Should have compressed to interval or widened to top
        assert not result.is_enumerable() or len(result.consts) <= 3 or result.is_top
        # Bounds should cover 1-5 (or be unbounded/top)
        if not result.is_top:
            # lo may be None (widened to -∞) or <= 1
            assert result.lo is None or result.lo <= 1
            # hi may be None (widened to +∞) or >= 5
            assert result.hi is None or result.hi >= 5

    def test_widen_bounds_increase(self):
        """Widening with increasing bounds widens to infinity."""
        from pythonstan.analysis.pointer.ai.domains.ints import IntDomain, IntLattice

        domain = IntDomain(max_consts=10)
        s1 = IntLattice.from_interval(0, 5)
        s2 = IntLattice.from_interval(0, 10)  # hi increased
        result = domain.widen(s1, s2)
        # hi should widen to +∞
        assert result.hi is None

    def test_widen_monotone(self):
        """Widening is monotone: old ⊑ widen(old, new)."""
        from pythonstan.analysis.pointer.ai.domains.ints import IntDomain, IntLattice

        domain = IntDomain(max_consts=5)
        s1 = IntLattice.from_consts(frozenset({1, 2}))
        s2 = IntLattice.from_consts(frozenset({3, 4}))
        result = domain.widen(s1, s2)
        assert domain.leq(s1, result)


class TestIntLatticeRefinement:
    """Tests for refinement methods used in condition handling."""

    def test_refine_eq(self):
        """refine_eq narrows to singleton."""
        from pythonstan.analysis.pointer.ai.domains.ints import IntLattice

        s = IntLattice.from_consts(frozenset({1, 2, 3}))
        result = s.refine_eq(2)
        assert result.is_singleton()
        assert result.get_singleton() == 2

    def test_refine_eq_not_contained(self):
        """refine_eq with non-contained value is bottom."""
        from pythonstan.analysis.pointer.ai.domains.ints import IntLattice

        s = IntLattice.from_consts(frozenset({1, 2, 3}))
        result = s.refine_eq(5)
        assert result.is_bottom

    def test_refine_neq(self):
        """refine_neq removes the value."""
        from pythonstan.analysis.pointer.ai.domains.ints import IntLattice

        s = IntLattice.from_consts(frozenset({1, 2, 3}))
        result = s.refine_neq(2)
        assert result.consts == frozenset({1, 3})

    def test_refine_lt(self):
        """refine_lt narrows to x < n."""
        from pythonstan.analysis.pointer.ai.domains.ints import IntLattice

        s = IntLattice.from_consts(frozenset({1, 2, 3, 4, 5}))
        result = s.refine_lt(3)
        assert result.consts == frozenset({1, 2})

    def test_refine_le(self):
        """refine_le narrows to x <= n."""
        from pythonstan.analysis.pointer.ai.domains.ints import IntLattice

        s = IntLattice.from_consts(frozenset({1, 2, 3, 4, 5}))
        result = s.refine_le(3)
        assert result.consts == frozenset({1, 2, 3})

    def test_refine_gt(self):
        """refine_gt narrows to x > n."""
        from pythonstan.analysis.pointer.ai.domains.ints import IntLattice

        s = IntLattice.from_consts(frozenset({1, 2, 3, 4, 5}))
        result = s.refine_gt(3)
        assert result.consts == frozenset({4, 5})

    def test_refine_ge(self):
        """refine_ge narrows to x >= n."""
        from pythonstan.analysis.pointer.ai.domains.ints import IntLattice

        s = IntLattice.from_consts(frozenset({1, 2, 3, 4, 5}))
        result = s.refine_ge(3)
        assert result.consts == frozenset({3, 4, 5})

    def test_refine_lt_on_top(self):
        """refine_lt on top gives unbounded interval."""
        from pythonstan.analysis.pointer.ai.domains.ints import IntLattice

        result = IntLattice.top().refine_lt(10)
        assert not result.is_top
        assert result.hi == 9
        assert result.lo is None

    def test_refine_ge_on_interval(self):
        """refine_ge on interval narrows lower bound."""
        from pythonstan.analysis.pointer.ai.domains.ints import IntLattice

        s = IntLattice.from_interval(0, 10)
        result = s.refine_ge(5)
        assert result.lo == 5
        assert result.hi == 10


class TestIntDomainFacts:
    """Tests for fact production and consumption."""

    def test_produce_facts_singleton(self):
        """Singleton produces is_singleton_int fact."""
        from pythonstan.analysis.pointer.ai.domains.ints import IntDomain, IntLattice

        domain = IntDomain()
        facts = domain.produce_facts("x", IntLattice.const(42))
        kinds = {f.kind for f in facts}
        assert "is_singleton_int" in kinds
        singleton_fact = next(f for f in facts if f.kind == "is_singleton_int")
        assert singleton_fact.data == 42

    def test_produce_facts_zero(self):
        """Zero produces is_zero fact."""
        from pythonstan.analysis.pointer.ai.domains.ints import IntDomain, IntLattice

        domain = IntDomain()
        facts = domain.produce_facts("x", IntLattice.zero())
        kinds = {f.kind for f in facts}
        assert "is_zero" in kinds

    def test_produce_facts_nonzero(self):
        """Non-zero singleton produces is_nonzero fact."""
        from pythonstan.analysis.pointer.ai.domains.ints import IntDomain, IntLattice

        domain = IntDomain()
        facts = domain.produce_facts("x", IntLattice.const(5))
        kinds = {f.kind for f in facts}
        assert "is_nonzero" in kinds

    def test_produce_facts_const_set(self):
        """Const set produces is_const_int_set fact."""
        from pythonstan.analysis.pointer.ai.domains.ints import IntDomain, IntLattice

        domain = IntDomain()
        facts = domain.produce_facts("x", IntLattice.from_consts(frozenset({1, 2, 3})))
        kinds = {f.kind for f in facts}
        assert "is_const_int_set" in kinds

    def test_produce_facts_nonneg(self):
        """Non-negative produces is_nonneg fact."""
        from pythonstan.analysis.pointer.ai.domains.ints import IntDomain, IntLattice

        domain = IntDomain()
        facts = domain.produce_facts("x", IntLattice.from_consts(frozenset({0, 1, 2})))
        kinds = {f.kind for f in facts}
        assert "is_nonneg" in kinds

    def test_produce_facts_parity(self):
        """Even/odd produces parity facts."""
        from pythonstan.analysis.pointer.ai.domains.ints import IntDomain, IntLattice

        domain = IntDomain()
        even_facts = domain.produce_facts("x", IntLattice.from_consts(frozenset({2, 4})))
        assert "is_even" in {f.kind for f in even_facts}

        odd_facts = domain.produce_facts("x", IntLattice.from_consts(frozenset({1, 3})))
        assert "is_odd" in {f.kind for f in odd_facts}

    def test_consume_fact_singleton(self):
        """Consuming is_singleton_int refines to singleton."""
        from pythonstan.analysis.pointer.ai.domains.ints import IntDomain, IntLattice
        from pythonstan.analysis.pointer.ai.domains.base import Fact

        domain = IntDomain()
        elem = IntLattice.top()
        fact = Fact("is_singleton_int", "x", 42)
        result = domain.consume_fact(elem, fact)
        assert result.is_singleton()
        assert result.get_singleton() == 42

    def test_consume_fact_nonneg(self):
        """Consuming is_nonneg refines interval."""
        from pythonstan.analysis.pointer.ai.domains.ints import IntDomain, IntLattice
        from pythonstan.analysis.pointer.ai.domains.base import Fact

        domain = IntDomain()
        elem = IntLattice.top()
        fact = Fact("is_nonneg", "x", None)
        result = domain.consume_fact(elem, fact)
        assert result.lo == 0
        assert result.hi is None


class TestIntDomainAbstractConst:
    """Tests for abstract_const."""

    def test_abstract_int(self):
        """Abstract an integer constant."""
        from pythonstan.analysis.pointer.ai.domains.ints import IntDomain

        domain = IntDomain()
        result = domain.abstract_const(42)
        assert result is not None
        assert result.consts == frozenset({42})

    def test_abstract_bool_returns_none(self):
        """Bool is NOT abstracted as int (handled by bool domain)."""
        from pythonstan.analysis.pointer.ai.domains.ints import IntDomain

        domain = IntDomain()
        assert domain.abstract_const(True) is None
        assert domain.abstract_const(False) is None

    def test_abstract_non_int(self):
        """Non-int returns None."""
        from pythonstan.analysis.pointer.ai.domains.ints import IntDomain

        domain = IntDomain()
        assert domain.abstract_const("hello") is None
        assert domain.abstract_const(3.14) is None
        assert domain.abstract_const(None) is None


class TestAbsValIntHelpers:
    """Tests for AbsVal int helper methods."""

    def test_from_int(self):
        """from_int creates singleton int value."""
        from pythonstan.analysis.pointer.ai.state import AbsVal

        v = AbsVal.from_int(42)
        int_lattice = v.get_int_lattice()
        assert int_lattice.is_singleton()
        assert int_lattice.get_singleton() == 42

    def test_from_ints(self):
        """from_ints creates const set int value."""
        from pythonstan.analysis.pointer.ai.state import AbsVal

        v = AbsVal.from_ints(frozenset({1, 2, 3}))
        int_lattice = v.get_int_lattice()
        assert int_lattice.is_enumerable()
        assert int_lattice.consts == frozenset({1, 2, 3})

    def test_get_int_lattice_top(self):
        """get_int_lattice returns top for top value."""
        from pythonstan.analysis.pointer.ai.state import AbsVal

        v = AbsVal.top()
        int_lattice = v.get_int_lattice()
        assert int_lattice.is_top


class TestInterpreterIntConstants:
    """Tests for interpreter handling of int constants."""

    def test_abstract_constant_int(self):
        """Interpreter abstracts int constants properly."""
        from pythonstan.analysis.pointer.ai.interpreter import IRInterpreter
        from pythonstan.analysis.pointer.ai.api import AnalysisBudget

        # Create a minimal interpreter (pta_query can be None for this test)
        interp = IRInterpreter(pta_query=None, budget=AnalysisBudget())  # type: ignore

        val = interp._abstract_constant(42)
        int_lattice = val.get_int_lattice()
        assert int_lattice.is_singleton()
        assert int_lattice.get_singleton() == 42

    def test_abstract_constant_bool_not_int(self):
        """Bool constants go to bool domain, not int domain."""
        from pythonstan.analysis.pointer.ai.interpreter import IRInterpreter
        from pythonstan.analysis.pointer.ai.api import AnalysisBudget

        interp = IRInterpreter(pta_query=None, budget=AnalysisBudget())  # type: ignore

        val_true = interp._abstract_constant(True)
        val_false = interp._abstract_constant(False)

        # Bool should be set
        assert val_true.get_bool_lattice().is_true()
        assert val_false.get_bool_lattice().is_false()

        # Int should be top (not abstracted as int)
        assert val_true.get_int_lattice().is_top
        assert val_false.get_int_lattice().is_top


class TestConditionRefinerInt:
    """Tests for ConditionRefiner handling of integer comparisons."""

    def test_refine_eq_int(self):
        """Condition x == 0 refines to singleton on true branch."""
        from pythonstan.analysis.pointer.ai.domains.conditions import ConditionRefiner
        from pythonstan.analysis.pointer.ai.state import AIState, AbsVal
        from pythonstan.analysis.pointer.ai.api import AnalysisBudget
        import ast

        budget = AnalysisBudget()
        refiner = ConditionRefiner(budget)

        # Create state where x is {0, 1, 2}
        state = AIState()
        state.env["x"] = AbsVal.from_ints(frozenset({0, 1, 2}))

        # Condition: x == 0
        cond = ast.Compare(
            left=ast.Name(id="x", ctx=ast.Load()),
            ops=[ast.Eq()],
            comparators=[ast.Constant(value=0)],
        )

        true_state, false_state = refiner.refine(state, cond, is_jump_if_true=False)

        # True branch: x == 0
        true_int = true_state.get_var("x").get_int_lattice()
        assert true_int.is_singleton()
        assert true_int.get_singleton() == 0

        # False branch: x != 0
        false_int = false_state.get_var("x").get_int_lattice()
        assert false_int.consts == frozenset({1, 2})

    def test_refine_lt_int(self):
        """Condition x < 3 refines ranges on both branches."""
        from pythonstan.analysis.pointer.ai.domains.conditions import ConditionRefiner
        from pythonstan.analysis.pointer.ai.state import AIState, AbsVal
        from pythonstan.analysis.pointer.ai.api import AnalysisBudget
        import ast

        budget = AnalysisBudget()
        refiner = ConditionRefiner(budget)

        # Create state where x is {0, 1, 2, 3, 4}
        state = AIState()
        state.env["x"] = AbsVal.from_ints(frozenset({0, 1, 2, 3, 4}))

        # Condition: x < 3
        cond = ast.Compare(
            left=ast.Name(id="x", ctx=ast.Load()),
            ops=[ast.Lt()],
            comparators=[ast.Constant(value=3)],
        )

        true_state, false_state = refiner.refine(state, cond, is_jump_if_true=False)

        # True branch: x < 3 -> {0, 1, 2}
        true_int = true_state.get_var("x").get_int_lattice()
        assert true_int.consts == frozenset({0, 1, 2})

        # False branch: x >= 3 -> {3, 4}
        false_int = false_state.get_var("x").get_int_lattice()
        assert false_int.consts == frozenset({3, 4})

    def test_refine_ge_int(self):
        """Condition x >= 2 refines ranges on both branches."""
        from pythonstan.analysis.pointer.ai.domains.conditions import ConditionRefiner
        from pythonstan.analysis.pointer.ai.state import AIState, AbsVal
        from pythonstan.analysis.pointer.ai.api import AnalysisBudget
        import ast

        budget = AnalysisBudget()
        refiner = ConditionRefiner(budget)

        # Create state where x is {0, 1, 2, 3}
        state = AIState()
        state.env["x"] = AbsVal.from_ints(frozenset({0, 1, 2, 3}))

        # Condition: x >= 2
        cond = ast.Compare(
            left=ast.Name(id="x", ctx=ast.Load()),
            ops=[ast.GtE()],
            comparators=[ast.Constant(value=2)],
        )

        true_state, false_state = refiner.refine(state, cond, is_jump_if_true=False)

        # True branch: x >= 2 -> {2, 3}
        true_int = true_state.get_var("x").get_int_lattice()
        assert true_int.consts == frozenset({2, 3})

        # False branch: x < 2 -> {0, 1}
        false_int = false_state.get_var("x").get_int_lattice()
        assert false_int.consts == frozenset({0, 1})


class TestBoolDomainIntFacts:
    """Tests for BoolDomain consuming int facts."""

    def test_consume_is_zero_false(self):
        """is_zero fact refines bool to False."""
        from pythonstan.analysis.pointer.ai.domains.bool import BoolDomain, BoolLattice
        from pythonstan.analysis.pointer.ai.domains.base import Fact

        domain = BoolDomain()
        elem = BoolLattice.top()
        fact = Fact("is_zero", "x", None)
        result = domain.consume_fact(elem, fact)
        assert result.is_false()

    def test_consume_is_nonzero_true(self):
        """is_nonzero fact refines bool to True."""
        from pythonstan.analysis.pointer.ai.domains.bool import BoolDomain, BoolLattice
        from pythonstan.analysis.pointer.ai.domains.base import Fact

        domain = BoolDomain()
        elem = BoolLattice.top()
        fact = Fact("is_nonzero", "x", None)
        result = domain.consume_fact(elem, fact)
        assert result.is_true()

    def test_consume_singleton_int_zero(self):
        """is_singleton_int with value 0 refines bool to False."""
        from pythonstan.analysis.pointer.ai.domains.bool import BoolDomain, BoolLattice
        from pythonstan.analysis.pointer.ai.domains.base import Fact

        domain = BoolDomain()
        elem = BoolLattice.top()
        fact = Fact("is_singleton_int", "x", 0)
        result = domain.consume_fact(elem, fact)
        assert result.is_false()

    def test_consume_singleton_int_nonzero(self):
        """is_singleton_int with non-zero value refines bool to True."""
        from pythonstan.analysis.pointer.ai.domains.bool import BoolDomain, BoolLattice
        from pythonstan.analysis.pointer.ai.domains.base import Fact

        domain = BoolDomain()
        elem = BoolLattice.top()
        fact = Fact("is_singleton_int", "x", 42)
        result = domain.consume_fact(elem, fact)
        assert result.is_true()


class TestIntLatticeEquality:
    """Tests for IntLattice equality and hashing."""

    def test_equality(self):
        """Equal lattice elements are equal."""
        from pythonstan.analysis.pointer.ai.domains.ints import IntLattice

        s1 = IntLattice.const(42)
        s2 = IntLattice.const(42)
        assert s1 == s2
        assert hash(s1) == hash(s2)

    def test_inequality(self):
        """Different lattice elements are not equal."""
        from pythonstan.analysis.pointer.ai.domains.ints import IntLattice

        s1 = IntLattice.const(42)
        s2 = IntLattice.const(43)
        assert s1 != s2

    def test_bottom_equality(self):
        """Bottom elements are equal."""
        from pythonstan.analysis.pointer.ai.domains.ints import IntLattice

        assert IntLattice.bottom() == IntLattice.bottom()

    def test_top_equality(self):
        """Top elements are equal."""
        from pythonstan.analysis.pointer.ai.domains.ints import IntLattice

        assert IntLattice.top() == IntLattice.top()


class TestIntLatticeStr:
    """Tests for IntLattice string representation."""

    def test_str_bottom(self):
        """Bottom has correct string representation."""
        from pythonstan.analysis.pointer.ai.domains.ints import IntLattice

        assert str(IntLattice.bottom()) == "⊥_int"

    def test_str_top(self):
        """Top has correct string representation."""
        from pythonstan.analysis.pointer.ai.domains.ints import IntLattice

        assert str(IntLattice.top()) == "⊤_int"

    def test_str_singleton(self):
        """Singleton has correct string representation."""
        from pythonstan.analysis.pointer.ai.domains.ints import IntLattice

        s = str(IntLattice.const(42))
        assert "42" in s

    def test_str_consts(self):
        """Const set has correct string representation."""
        from pythonstan.analysis.pointer.ai.domains.ints import IntLattice

        s = str(IntLattice.from_consts(frozenset({1, 2, 3})))
        assert "1" in s
        assert "2" in s
        assert "3" in s
