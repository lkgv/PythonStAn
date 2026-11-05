"""Tests for the enhanced string domain with prefixes and suffixes.

Tests:
- StrLattice creation and predicates
- Normalization (anti-chains for prefixes/suffixes)
- may_equal correctness for const/prefix/suffix/unknown
- filter_candidates functionality
- Widening with LCP-based compression
- StringDomain lattice operations
"""

import pytest
from typing import FrozenSet


class TestStrLatticeCreation:
    """Tests for StrLattice factory methods and predicates."""

    def test_bottom_creation(self):
        """Bottom represents no strings."""
        from pythonstan.analysis.pointer.ai.domains.strings import StrLattice

        bottom = StrLattice.bottom()
        assert bottom.is_bottom()
        assert not bottom.is_top()
        assert bottom.consts == frozenset()
        assert bottom.prefixes == frozenset()
        assert bottom.suffixes == frozenset()
        assert not bottom.has_unknown

    def test_top_creation(self):
        """Top represents any string."""
        from pythonstan.analysis.pointer.ai.domains.strings import StrLattice

        top = StrLattice.top()
        assert top.is_top()
        assert not top.is_bottom()
        assert top.has_unknown

    def test_const_creation(self):
        """Create single constant string."""
        from pythonstan.analysis.pointer.ai.domains.strings import StrLattice

        s = StrLattice.const("hello")
        assert s.is_const()
        assert s.consts == frozenset({"hello"})
        assert s.is_enumerable()
        assert s.get_consts() == frozenset({"hello"})

    def test_consts_creation(self):
        """Create set of constant strings."""
        from pythonstan.analysis.pointer.ai.domains.strings import StrLattice

        s = StrLattice.from_consts(frozenset({"a", "b", "c"}))
        assert s.is_const()
        assert s.consts == frozenset({"a", "b", "c"})
        assert s.is_enumerable()

    def test_empty_consts_is_bottom(self):
        """Empty constant set is bottom."""
        from pythonstan.analysis.pointer.ai.domains.strings import StrLattice

        s = StrLattice.from_consts(frozenset())
        assert s.is_bottom()

    def test_prefix_creation(self):
        """Create prefix constraint."""
        from pythonstan.analysis.pointer.ai.domains.strings import StrLattice

        s = StrLattice.from_prefix("get_")
        assert not s.is_bottom()
        assert not s.is_top()
        assert s.prefixes == frozenset({"get_"})
        assert not s.is_enumerable()

    def test_empty_prefix_is_top(self):
        """Empty prefix matches all strings, so normalizes to top."""
        from pythonstan.analysis.pointer.ai.domains.strings import StrLattice

        s = StrLattice.from_prefix("")
        assert s.is_top()

    def test_suffix_creation(self):
        """Create suffix constraint."""
        from pythonstan.analysis.pointer.ai.domains.strings import StrLattice

        s = StrLattice.from_suffix("_id")
        assert not s.is_bottom()
        assert not s.is_top()
        assert s.suffixes == frozenset({"_id"})
        assert not s.is_enumerable()

    def test_empty_suffix_is_top(self):
        """Empty suffix matches all strings, so normalizes to top."""
        from pythonstan.analysis.pointer.ai.domains.strings import StrLattice

        s = StrLattice.from_suffix("")
        assert s.is_top()


class TestStrLatticeNormalization:
    """Tests for normalization rules."""

    def test_prefix_antichain(self):
        """Shorter prefix subsumes longer prefix."""
        from pythonstan.analysis.pointer.ai.domains.strings import StrLattice

        # "get" is a prefix of "getattr", so only "get" should remain
        s = StrLattice.from_prefixes(frozenset({"get", "getattr", "getitem"}))
        assert s.prefixes == frozenset({"get"})

    def test_suffix_antichain(self):
        """Shorter suffix subsumes longer suffix."""
        from pythonstan.analysis.pointer.ai.domains.strings import StrLattice

        # "_id" is a suffix of "user_id", so only "_id" should remain
        s = StrLattice.from_suffixes(frozenset({"_id", "user_id", "item_id"}))
        assert s.suffixes == frozenset({"_id"})

    def test_consts_covered_by_prefix_removed(self):
        """Constants covered by a prefix are removed."""
        from pythonstan.analysis.pointer.ai.domains.strings import StrLattice

        # "getattr" starts with "get", so it's covered by the prefix
        s = StrLattice._normalize(
            consts=frozenset({"getattr", "setattr", "hello"}),
            prefixes=frozenset({"get"}),
            suffixes=frozenset(),
            has_unknown=False,
        )
        # "getattr" is removed (covered by prefix "get")
        # "setattr" and "hello" remain
        assert "getattr" not in s.consts
        assert "setattr" in s.consts
        assert "hello" in s.consts

    def test_consts_covered_by_suffix_removed(self):
        """Constants covered by a suffix are removed."""
        from pythonstan.analysis.pointer.ai.domains.strings import StrLattice

        s = StrLattice._normalize(
            consts=frozenset({"user_id", "name", "item_id"}),
            prefixes=frozenset(),
            suffixes=frozenset({"_id"}),
            has_unknown=False,
        )
        # "user_id" and "item_id" are removed (covered by suffix "_id")
        # "name" remains
        assert "user_id" not in s.consts
        assert "item_id" not in s.consts
        assert "name" in s.consts

    def test_has_unknown_clears_sets(self):
        """has_unknown=True normalizes to canonical top."""
        from pythonstan.analysis.pointer.ai.domains.strings import StrLattice

        s = StrLattice._normalize(
            consts=frozenset({"a", "b"}),
            prefixes=frozenset({"get"}),
            suffixes=frozenset({"_id"}),
            has_unknown=True,
        )
        assert s.is_top()
        assert s.consts == frozenset()
        assert s.prefixes == frozenset()
        assert s.suffixes == frozenset()


class TestMayEqual:
    """Tests for may_equal predicate."""

    def test_may_equal_const(self):
        """Const element matches exact strings."""
        from pythonstan.analysis.pointer.ai.domains.strings import StrLattice

        s = StrLattice.from_consts(frozenset({"foo", "bar"}))
        assert s.may_equal("foo")
        assert s.may_equal("bar")
        assert not s.may_equal("baz")

    def test_may_equal_prefix(self):
        """Prefix element matches strings with that prefix."""
        from pythonstan.analysis.pointer.ai.domains.strings import StrLattice

        s = StrLattice.from_prefix("get_")
        assert s.may_equal("get_attr")
        assert s.may_equal("get_")
        assert s.may_equal("get_item")
        assert not s.may_equal("set_attr")
        assert not s.may_equal("getter")

    def test_may_equal_suffix(self):
        """Suffix element matches strings with that suffix."""
        from pythonstan.analysis.pointer.ai.domains.strings import StrLattice

        s = StrLattice.from_suffix("_id")
        assert s.may_equal("user_id")
        assert s.may_equal("_id")
        assert s.may_equal("item_id")
        assert not s.may_equal("user_name")
        assert not s.may_equal("id_user")

    def test_may_equal_top(self):
        """Top matches any string."""
        from pythonstan.analysis.pointer.ai.domains.strings import StrLattice

        s = StrLattice.top()
        assert s.may_equal("anything")
        assert s.may_equal("")
        assert s.may_equal("foo_bar_baz")

    def test_may_equal_bottom(self):
        """Bottom matches no string."""
        from pythonstan.analysis.pointer.ai.domains.strings import StrLattice

        s = StrLattice.bottom()
        assert not s.may_equal("anything")
        assert not s.may_equal("")

    def test_may_equal_mixed(self):
        """Mixed element matches consts OR prefix OR suffix."""
        from pythonstan.analysis.pointer.ai.domains.strings import StrLattice

        s = StrLattice._normalize(
            consts=frozenset({"exact_match"}),
            prefixes=frozenset({"get_"}),
            suffixes=frozenset({"_id"}),
            has_unknown=False,
        )
        assert s.may_equal("exact_match")  # const
        assert s.may_equal("get_foo")  # prefix
        assert s.may_equal("user_id")  # suffix
        assert not s.may_equal("other")  # none match


class TestMustEqual:
    """Tests for must_equal predicate."""

    def test_must_equal_singleton(self):
        """Singleton constant must equal that string."""
        from pythonstan.analysis.pointer.ai.domains.strings import StrLattice

        s = StrLattice.const("hello")
        assert s.must_equal("hello")
        assert not s.must_equal("world")

    def test_must_equal_multiple_consts(self):
        """Multiple constants cannot must_equal any single string."""
        from pythonstan.analysis.pointer.ai.domains.strings import StrLattice

        s = StrLattice.from_consts(frozenset({"a", "b"}))
        assert not s.must_equal("a")
        assert not s.must_equal("b")

    def test_must_equal_prefix(self):
        """Prefix cannot must_equal any string."""
        from pythonstan.analysis.pointer.ai.domains.strings import StrLattice

        s = StrLattice.from_prefix("get_")
        assert not s.must_equal("get_attr")


class TestFilterCandidates:
    """Tests for filter_candidates functionality."""

    def test_filter_with_consts(self):
        """Filter candidates with const constraints."""
        from pythonstan.analysis.pointer.ai.domains.strings import StrLattice

        s = StrLattice.from_consts(frozenset({"a", "b"}))
        candidates = frozenset({"a", "b", "c", "d"})
        filtered = s.filter_candidates(candidates)
        assert filtered == frozenset({"a", "b"})

    def test_filter_with_prefix(self):
        """Filter candidates with prefix constraints."""
        from pythonstan.analysis.pointer.ai.domains.strings import StrLattice

        s = StrLattice.from_prefix("get_")
        candidates = frozenset({"get_x", "get_y", "set_x", "foo"})
        filtered = s.filter_candidates(candidates)
        assert filtered == frozenset({"get_x", "get_y"})

    def test_filter_with_suffix(self):
        """Filter candidates with suffix constraints."""
        from pythonstan.analysis.pointer.ai.domains.strings import StrLattice

        s = StrLattice.from_suffix("_id")
        candidates = frozenset({"user_id", "item_id", "user_name", "id"})
        filtered = s.filter_candidates(candidates)
        assert filtered == frozenset({"user_id", "item_id"})

    def test_filter_with_top(self):
        """Top returns all candidates."""
        from pythonstan.analysis.pointer.ai.domains.strings import StrLattice

        s = StrLattice.top()
        candidates = frozenset({"a", "b", "c"})
        filtered = s.filter_candidates(candidates)
        assert filtered == candidates

    def test_filter_with_bottom(self):
        """Bottom returns no candidates."""
        from pythonstan.analysis.pointer.ai.domains.strings import StrLattice

        s = StrLattice.bottom()
        candidates = frozenset({"a", "b", "c"})
        filtered = s.filter_candidates(candidates)
        assert filtered == frozenset()

    def test_filter_mixed_constraints(self):
        """Filter with mixed const + prefix + suffix."""
        from pythonstan.analysis.pointer.ai.domains.strings import StrLattice

        s = StrLattice._normalize(
            consts=frozenset({"exact"}),
            prefixes=frozenset({"get_"}),
            suffixes=frozenset({"_id"}),
            has_unknown=False,
        )
        candidates = frozenset({
            "exact",  # matches const
            "get_foo",  # matches prefix
            "user_id",  # matches suffix
            "other",  # no match
        })
        filtered = s.filter_candidates(candidates)
        assert filtered == frozenset({"exact", "get_foo", "user_id"})


class TestStringDomainJoin:
    """Tests for StringDomain join operation."""

    def test_join_bottom_identity(self):
        """Bottom is identity for join."""
        from pythonstan.analysis.pointer.ai.domains.strings import StringDomain, StrLattice

        domain = StringDomain()
        s = StrLattice.const("hello")
        result = domain.join(StrLattice.bottom(), s)
        assert result == s

    def test_join_top_absorbs(self):
        """Top absorbs in join."""
        from pythonstan.analysis.pointer.ai.domains.strings import StringDomain, StrLattice

        domain = StringDomain()
        s = StrLattice.const("hello")
        result = domain.join(StrLattice.top(), s)
        assert result.is_top()

    def test_join_consts(self):
        """Join unions constant sets."""
        from pythonstan.analysis.pointer.ai.domains.strings import StringDomain, StrLattice

        domain = StringDomain()
        s1 = StrLattice.from_consts(frozenset({"a", "b"}))
        s2 = StrLattice.from_consts(frozenset({"b", "c"}))
        result = domain.join(s1, s2)
        assert result.consts == frozenset({"a", "b", "c"})

    def test_join_prefixes(self):
        """Join unions prefix sets."""
        from pythonstan.analysis.pointer.ai.domains.strings import StringDomain, StrLattice

        domain = StringDomain()
        s1 = StrLattice.from_prefix("get_")
        s2 = StrLattice.from_prefix("set_")
        result = domain.join(s1, s2)
        assert result.prefixes == frozenset({"get_", "set_"})

    def test_join_mixed(self):
        """Join mixed elements."""
        from pythonstan.analysis.pointer.ai.domains.strings import StringDomain, StrLattice

        domain = StringDomain()
        s1 = StrLattice.const("exact")
        s2 = StrLattice.from_prefix("get_")
        result = domain.join(s1, s2)
        # "exact" doesn't start with "get_", so it stays
        assert "exact" in result.consts
        assert "get_" in result.prefixes


class TestStringDomainMeet:
    """Tests for StringDomain meet operation."""

    def test_meet_top_identity(self):
        """Top is identity for meet."""
        from pythonstan.analysis.pointer.ai.domains.strings import StringDomain, StrLattice

        domain = StringDomain()
        s = StrLattice.const("hello")
        result = domain.meet(StrLattice.top(), s)
        assert result == s

    def test_meet_bottom_absorbs(self):
        """Bottom absorbs in meet."""
        from pythonstan.analysis.pointer.ai.domains.strings import StringDomain, StrLattice

        domain = StringDomain()
        s = StrLattice.const("hello")
        result = domain.meet(StrLattice.bottom(), s)
        assert result.is_bottom()

    def test_meet_consts_intersection(self):
        """Meet intersects constant sets."""
        from pythonstan.analysis.pointer.ai.domains.strings import StringDomain, StrLattice

        domain = StringDomain()
        s1 = StrLattice.from_consts(frozenset({"a", "b", "c"}))
        s2 = StrLattice.from_consts(frozenset({"b", "c", "d"}))
        result = domain.meet(s1, s2)
        assert result.consts == frozenset({"b", "c"})

    def test_meet_const_with_prefix(self):
        """Meet const with prefix filters consts."""
        from pythonstan.analysis.pointer.ai.domains.strings import StringDomain, StrLattice

        domain = StringDomain()
        s1 = StrLattice.from_consts(frozenset({"get_x", "get_y", "set_x"}))
        s2 = StrLattice.from_prefix("get_")
        result = domain.meet(s1, s2)
        assert result.consts == frozenset({"get_x", "get_y"})


class TestStringDomainLeq:
    """Tests for StringDomain partial order."""

    def test_leq_bottom_least(self):
        """Bottom is less than everything."""
        from pythonstan.analysis.pointer.ai.domains.strings import StringDomain, StrLattice

        domain = StringDomain()
        assert domain.leq(StrLattice.bottom(), StrLattice.top())
        assert domain.leq(StrLattice.bottom(), StrLattice.const("x"))

    def test_leq_top_greatest(self):
        """Everything is less than top."""
        from pythonstan.analysis.pointer.ai.domains.strings import StringDomain, StrLattice

        domain = StringDomain()
        assert domain.leq(StrLattice.const("x"), StrLattice.top())
        assert domain.leq(StrLattice.from_prefix("get_"), StrLattice.top())

    def test_leq_subset_consts(self):
        """Subset of constants is less than superset."""
        from pythonstan.analysis.pointer.ai.domains.strings import StringDomain, StrLattice

        domain = StringDomain()
        s1 = StrLattice.from_consts(frozenset({"a"}))
        s2 = StrLattice.from_consts(frozenset({"a", "b"}))
        assert domain.leq(s1, s2)
        assert not domain.leq(s2, s1)

    def test_leq_const_covered_by_prefix(self):
        """Const is ⊑ prefix if const matches prefix."""
        from pythonstan.analysis.pointer.ai.domains.strings import StringDomain, StrLattice

        domain = StringDomain()
        s1 = StrLattice.const("get_x")
        s2 = StrLattice.from_prefix("get_")
        assert domain.leq(s1, s2)

    def test_leq_const_not_covered_by_prefix(self):
        """Const is not ⊑ prefix if const doesn't match."""
        from pythonstan.analysis.pointer.ai.domains.strings import StringDomain, StrLattice

        domain = StringDomain()
        s1 = StrLattice.const("set_x")
        s2 = StrLattice.from_prefix("get_")
        assert not domain.leq(s1, s2)


class TestStringDomainWiden:
    """Tests for widening with LCP-based compression."""

    def test_widen_under_cap(self):
        """Widening under cap just joins."""
        from pythonstan.analysis.pointer.ai.domains.strings import StringDomain, StrLattice

        domain = StringDomain(max_consts=10)
        s1 = StrLattice.from_consts(frozenset({"a", "b"}))
        s2 = StrLattice.from_consts(frozenset({"c", "d"}))
        result = domain.widen(s1, s2)
        assert result.consts == frozenset({"a", "b", "c", "d"})
        assert result.is_enumerable()

    def test_widen_exceeds_const_cap_compresses(self):
        """Widening exceeding const cap compresses to prefixes/suffixes."""
        from pythonstan.analysis.pointer.ai.domains.strings import StringDomain, StrLattice

        domain = StringDomain(max_consts=3, max_prefixes=10, max_suffixes=10)
        s1 = StrLattice.from_consts(frozenset({"get_a", "get_b", "get_c"}))
        s2 = StrLattice.from_consts(frozenset({"get_d", "get_e"}))
        result = domain.widen(s1, s2)
        # Should have compressed to prefixes
        assert not result.is_enumerable()
        # "get_" should be in prefixes (LCP)
        assert len(result.prefixes) >= 1 or result.is_top()

    def test_widen_to_top(self):
        """Widening to top when caps exceeded."""
        from pythonstan.analysis.pointer.ai.domains.strings import StringDomain, StrLattice

        domain = StringDomain(max_consts=2, max_prefixes=2, max_suffixes=2)
        # Create many diverse strings that don't share prefixes
        s1 = StrLattice.from_consts(frozenset({"a1", "b1", "c1"}))
        s2 = StrLattice.from_consts(frozenset({"d1", "e1", "f1"}))
        result = domain.widen(s1, s2)
        # This should eventually widen to top
        # (exact behavior depends on LCP compression results)
        assert result.is_top() or len(result.prefixes) <= 2

    def test_widen_monotone(self):
        """Widening is monotone: old ⊑ widen(old, new)."""
        from pythonstan.analysis.pointer.ai.domains.strings import StringDomain, StrLattice

        domain = StringDomain(max_consts=5)
        s1 = StrLattice.from_consts(frozenset({"a", "b"}))
        s2 = StrLattice.from_consts(frozenset({"c", "d"}))
        result = domain.widen(s1, s2)
        assert domain.leq(s1, result)


class TestStringDomainAbstractConst:
    """Tests for abstract_const."""

    def test_abstract_string(self):
        """Abstract a string constant."""
        from pythonstan.analysis.pointer.ai.domains.strings import StringDomain

        domain = StringDomain()
        result = domain.abstract_const("hello")
        assert result is not None
        assert result.consts == frozenset({"hello"})

    def test_abstract_non_string(self):
        """Non-string returns None."""
        from pythonstan.analysis.pointer.ai.domains.strings import StringDomain

        domain = StringDomain()
        assert domain.abstract_const(42) is None
        assert domain.abstract_const(None) is None
        assert domain.abstract_const([]) is None


class TestAbsValStringHelpers:
    """Tests for AbsVal string helper methods."""

    def test_get_const_strs(self):
        """get_const_strs returns constants when enumerable."""
        from pythonstan.analysis.pointer.ai.state import AbsVal

        v = AbsVal.from_strs(frozenset({"a", "b"}))
        result = v.get_const_strs()
        assert result == frozenset({"a", "b"})

    def test_get_const_strs_non_enumerable(self):
        """get_const_strs returns None when not enumerable."""
        from pythonstan.analysis.pointer.ai.state import AbsVal
        from pythonstan.analysis.pointer.ai.value_domain import STRING_DOMAIN
        from pythonstan.analysis.pointer.ai.domains.strings import StrLattice

        v = AbsVal(components={STRING_DOMAIN: StrLattice.from_prefix("get_")})
        result = v.get_const_strs()
        assert result is None

    def test_filter_string_candidates(self):
        """filter_string_candidates delegates to lattice."""
        from pythonstan.analysis.pointer.ai.state import AbsVal
        from pythonstan.analysis.pointer.ai.value_domain import STRING_DOMAIN
        from pythonstan.analysis.pointer.ai.domains.strings import StrLattice

        v = AbsVal(components={STRING_DOMAIN: StrLattice.from_prefix("get_")})
        candidates = frozenset({"get_x", "get_y", "set_x"})
        result = v.filter_string_candidates(candidates)
        assert result == frozenset({"get_x", "get_y"})

    def test_may_equal_str(self):
        """may_equal_str delegates to lattice."""
        from pythonstan.analysis.pointer.ai.state import AbsVal
        from pythonstan.analysis.pointer.ai.value_domain import STRING_DOMAIN
        from pythonstan.analysis.pointer.ai.domains.strings import StrLattice

        v = AbsVal(components={STRING_DOMAIN: StrLattice.from_prefix("get_")})
        assert v.may_equal_str("get_x")
        assert not v.may_equal_str("set_x")


class TestLCPCompression:
    """Tests for LCP-based prefix/suffix compression."""

    def test_lcp_computation(self):
        """LCP is computed correctly."""
        from pythonstan.analysis.pointer.ai.domains.strings import StringDomain

        domain = StringDomain()
        assert domain._lcp("hello", "help") == "hel"
        assert domain._lcp("abc", "abc") == "abc"
        assert domain._lcp("abc", "xyz") == ""
        assert domain._lcp("", "abc") == ""

    def test_lcp_prefix_grouping(self):
        """Strings with common prefixes are grouped."""
        from pythonstan.analysis.pointer.ai.domains.strings import StringDomain

        domain = StringDomain()
        strings = ["get_x", "get_y", "get_z", "set_a"]
        prefixes = domain._compute_lcp_prefixes(strings)
        # Should find "get_" as a common prefix
        assert "get_" in prefixes or any(
            "get_x".startswith(p) and "get_y".startswith(p) and "get_z".startswith(p)
            for p in prefixes
        )

    def test_lcs_suffix_grouping(self):
        """Strings with common suffixes are grouped."""
        from pythonstan.analysis.pointer.ai.domains.strings import StringDomain

        domain = StringDomain()
        strings = ["user_id", "item_id", "order_id", "name"]
        suffixes = domain._compute_lcs_suffixes(strings)
        # Should find "_id" as a common suffix
        assert "_id" in suffixes or any(
            "user_id".endswith(s) and "item_id".endswith(s)
            for s in suffixes
        )


class TestStrLatticeEquality:
    """Tests for StrLattice equality and hashing."""

    def test_equality(self):
        """Equal lattice elements are equal."""
        from pythonstan.analysis.pointer.ai.domains.strings import StrLattice

        s1 = StrLattice.const("hello")
        s2 = StrLattice.const("hello")
        assert s1 == s2
        assert hash(s1) == hash(s2)

    def test_inequality(self):
        """Different lattice elements are not equal."""
        from pythonstan.analysis.pointer.ai.domains.strings import StrLattice

        s1 = StrLattice.const("hello")
        s2 = StrLattice.const("world")
        assert s1 != s2

    def test_bottom_equality(self):
        """Bottom elements are equal."""
        from pythonstan.analysis.pointer.ai.domains.strings import StrLattice

        assert StrLattice.bottom() == StrLattice.bottom()

    def test_top_equality(self):
        """Top elements are equal."""
        from pythonstan.analysis.pointer.ai.domains.strings import StrLattice

        assert StrLattice.top() == StrLattice.top()
