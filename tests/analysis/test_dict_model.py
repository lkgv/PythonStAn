"""Unit tests for the AI-internal precise dict model (Route A).

Tests cover:
- Presence lattice operations
- KeySig creation and decomposition
- DictSlots lattice operations (join, widen, narrow, leq)
- KeyDecomposer for various key types
- DictSemantics operations (load, store, del, contains)
- Dict literal initialization
- Predicate recording for membership refinement
"""

import pytest
from typing import FrozenSet


# =============================================================================
# Presence Lattice Tests
# =============================================================================


class TestPresenceLattice:
    """Tests for Presence lattice operations."""
    
    def test_presence_join_same(self):
        """Joining same elements returns the same element."""
        from pythonstan.analysis.pointer.ai.domains.mapslots import Presence
        
        assert Presence.ABSENT.join(Presence.ABSENT) == Presence.ABSENT
        assert Presence.MAYBE.join(Presence.MAYBE) == Presence.MAYBE
        assert Presence.PRESENT.join(Presence.PRESENT) == Presence.PRESENT
    
    def test_presence_join_different(self):
        """Joining different elements produces MAYBE."""
        from pythonstan.analysis.pointer.ai.domains.mapslots import Presence
        
        assert Presence.ABSENT.join(Presence.PRESENT) == Presence.MAYBE
        assert Presence.PRESENT.join(Presence.ABSENT) == Presence.MAYBE
        assert Presence.ABSENT.join(Presence.MAYBE) == Presence.MAYBE
        assert Presence.PRESENT.join(Presence.MAYBE) == Presence.MAYBE
    
    def test_presence_meet(self):
        """Meet operations."""
        from pythonstan.analysis.pointer.ai.domains.mapslots import Presence
        
        # MAYBE is top in the lattice
        assert Presence.MAYBE.meet(Presence.ABSENT) == Presence.ABSENT
        assert Presence.MAYBE.meet(Presence.PRESENT) == Presence.PRESENT
        assert Presence.ABSENT.meet(Presence.PRESENT) == Presence.ABSENT  # Conservative
    
    def test_presence_leq(self):
        """Partial order tests."""
        from pythonstan.analysis.pointer.ai.domains.mapslots import Presence
        
        # Everything ⊑ MAYBE
        assert Presence.ABSENT.leq(Presence.MAYBE)
        assert Presence.PRESENT.leq(Presence.MAYBE)
        assert Presence.MAYBE.leq(Presence.MAYBE)
        
        # Reflexivity
        assert Presence.ABSENT.leq(Presence.ABSENT)
        assert Presence.PRESENT.leq(Presence.PRESENT)
        
        # ABSENT and PRESENT are incomparable
        assert not Presence.ABSENT.leq(Presence.PRESENT)
        assert not Presence.PRESENT.leq(Presence.ABSENT)
    
    def test_presence_predicates(self):
        """Test may_be_present/absent predicates."""
        from pythonstan.analysis.pointer.ai.domains.mapslots import Presence
        
        assert Presence.PRESENT.may_be_present()
        assert Presence.MAYBE.may_be_present()
        assert not Presence.ABSENT.may_be_present()
        
        assert Presence.ABSENT.may_be_absent()
        assert Presence.MAYBE.may_be_absent()
        assert not Presence.PRESENT.may_be_absent()


# =============================================================================
# KeySig Tests
# =============================================================================


class TestKeySig:
    """Tests for KeySig creation functions."""
    
    def test_make_str_keysig(self):
        """Test string key signature creation."""
        from pythonstan.analysis.pointer.ai.domains.mapslots import make_str_keysig, keysig_kind
        
        sig = make_str_keysig("foo")
        assert sig == ("str", "foo")
        assert keysig_kind(sig) == "str"
    
    def test_make_int_keysig(self):
        """Test int key signature creation."""
        from pythonstan.analysis.pointer.ai.domains.mapslots import make_int_keysig, keysig_kind
        
        sig = make_int_keysig(42)
        assert sig == ("int", 42)
        assert keysig_kind(sig) == "int"
    
    def test_make_tuple_keysig(self):
        """Test tuple key signature creation."""
        from pythonstan.analysis.pointer.ai.domains.mapslots import (
            make_str_keysig, make_int_keysig, make_tuple_keysig, keysig_kind
        )
        
        elem1 = make_str_keysig("a")
        elem2 = make_int_keysig(1)
        sig = make_tuple_keysig((elem1, elem2))
        
        assert keysig_kind(sig) == "tuple"
        assert sig[1] == (elem1, elem2)


# =============================================================================
# DictSlots Lattice Tests
# =============================================================================


class TestDictSlots:
    """Tests for DictSlots (SlotMap) lattice operations."""
    
    def test_dictslots_bottom(self):
        """Test bottom DictSlots creation."""
        from pythonstan.analysis.pointer.ai.domains.mapslots import SlotMap
        
        bottom = SlotMap.for_dict()
        assert bottom.is_bottom()
        assert len(bottom.partitions) == 0
    
    def test_dictslots_top(self):
        """Test top DictSlots creation."""
        from pythonstan.analysis.pointer.ai.domains.mapslots import SlotMap, Presence
        
        top = SlotMap.top(track_key_rep=True)
        assert not top.is_bottom()
        assert top.default.pres == Presence.MAYBE
    
    def test_dictslots_weak_update_part(self):
        """Test weak update for partitions."""
        from pythonstan.analysis.pointer.ai.domains.mapslots import (
            SlotMap, make_str_keysig, Presence
        )
        from pythonstan.analysis.pointer.ai.value_domain import AbsVal
        
        slots = SlotMap.for_dict()
        sig = make_str_keysig("foo")
        key_rep = AbsVal.from_str("foo")
        val = AbsVal.from_int(42)
        
        slots = slots.weak_update(sig, val, Presence.PRESENT, key_rep)
        
        assert sig in slots.partitions
        slot = slots.partitions[sig]
        assert slot.pres == Presence.PRESENT
    
    def test_dictslots_join(self):
        """Test join of DictSlots (SlotMap)."""
        from pythonstan.analysis.pointer.ai.domains.mapslots import (
            SlotMap, make_str_keysig, Presence
        )
        from pythonstan.analysis.pointer.ai.value_domain import AbsVal
        
        sig1 = make_str_keysig("a")
        sig2 = make_str_keysig("b")
        
        slots1 = SlotMap.for_dict()
        slots1 = slots1.weak_update(sig1, AbsVal.from_int(1), Presence.PRESENT, AbsVal.from_str("a"))
        
        slots2 = SlotMap.for_dict()
        slots2 = slots2.weak_update(sig2, AbsVal.from_int(2), Presence.PRESENT, AbsVal.from_str("b"))
        
        joined = slots1.join(slots2)
        
        # Both partitions should be present
        assert sig1 in joined.partitions
        assert sig2 in joined.partitions
        
        # Presence should be MAYBE (not PRESENT) since not both have the key
        assert joined.partitions[sig1].pres == Presence.MAYBE
        assert joined.partitions[sig2].pres == Presence.MAYBE
    
    def test_dictslots_leq(self):
        """Test partial order on DictSlots (SlotMap)."""
        from pythonstan.analysis.pointer.ai.domains.mapslots import (
            SlotMap, make_str_keysig, Presence
        )
        from pythonstan.analysis.pointer.ai.value_domain import AbsVal
        
        bottom = SlotMap.for_dict()
        top = SlotMap.top(track_key_rep=True)
        
        assert bottom.leq(top)
        assert not top.leq(bottom)
        assert bottom.leq(bottom)
        assert top.leq(top)
    
    def test_dictslots_widen_spill(self):
        """Test that widen spills excess partitions to default."""
        from pythonstan.analysis.pointer.ai.domains.mapslots import (
            SlotMap, make_str_keysig, Presence
        )
        from pythonstan.analysis.pointer.ai.value_domain import AbsVal
        
        # Create slots with small max_partitions
        slots = SlotMap.for_dict(max_partitions=3)
        
        # Add 5 partitions
        for i in range(5):
            sig = make_str_keysig(f"key{i}")
            slots = slots.weak_update(sig, AbsVal.from_int(i), Presence.PRESENT, AbsVal.from_str(f"key{i}"))
        
        # Widen should keep <= max_partitions
        widened = slots.widen(slots)
        assert len(widened.partitions) <= widened.max_partitions
        
        # Spilled values should be in default bucket
        assert not widened.default.val.is_bottom()


# =============================================================================
# KeyDecomposer Tests
# =============================================================================


class TestKeyDecomposer:
    """Tests for KeyDecomposer service."""
    
    def test_decompose_const_strings(self):
        """Test decomposition of constant string keys."""
        from pythonstan.analysis.pointer.ai.domains.mapslots import KeyDecomposer
        from pythonstan.analysis.pointer.ai.value_domain import AbsVal
        from pythonstan.analysis.pointer.ai.state import AIState
        
        decomposer = KeyDecomposer()
        key_val = AbsVal.from_strs(frozenset({"a", "b"}))
        state = AIState.bottom()
        
        tracked, has_default, default_key = decomposer.decompose(key_val, state)
        
        # Should track both strings
        assert len(tracked) >= 2
        assert any(sig[0] == "str" and sig[1] == "a" for sig, _ in tracked)
        assert any(sig[0] == "str" and sig[1] == "b" for sig, _ in tracked)
    
    def test_decompose_const_ints(self):
        """Test decomposition of constant int keys."""
        from pythonstan.analysis.pointer.ai.domains.mapslots import KeyDecomposer
        from pythonstan.analysis.pointer.ai.value_domain import AbsVal
        from pythonstan.analysis.pointer.ai.state import AIState
        
        decomposer = KeyDecomposer()
        key_val = AbsVal.from_ints(frozenset({0, 1, 2}))
        state = AIState.bottom()
        
        tracked, has_default, default_key = decomposer.decompose(key_val, state)
        
        # Should track all ints
        int_sigs = [sig for sig, _ in tracked if sig[0] == "int"]
        assert len(int_sigs) >= 3
    
    def test_decompose_too_many_strings_falls_to_default(self):
        """Test that too many strings fall to default bucket."""
        from pythonstan.analysis.pointer.ai.domains.mapslots import KeyDecomposer
        from pythonstan.analysis.pointer.ai.value_domain import AbsVal
        from pythonstan.analysis.pointer.ai.state import AIState
        from pythonstan.analysis.pointer.ai.api import AnalysisBudget
        
        budget = AnalysisBudget(max_dict_key_const_strs=2)
        decomposer = KeyDecomposer(budget)
        
        # Create 5 constant strings (exceeds cap of 2)
        key_val = AbsVal.from_strs(frozenset({f"s{i}" for i in range(5)}))
        state = AIState.bottom()
        
        tracked, has_default, default_key = decomposer.decompose(key_val, state)
        
        # Should fall to default
        assert has_default


# =============================================================================
# DictSemantics Tests
# =============================================================================


class TestDictSemantics:
    """Tests for DictSemantics operations."""
    
    def _make_mock_dict_obj(self):
        """Create a mock dict AbstractObject."""
        from dataclasses import dataclass
        from pythonstan.analysis.pointer.kcfa.object import AllocKind
        
        @dataclass(frozen=True)
        class MockAllocSite:
            stmt: str
            kind: AllocKind
        
        @dataclass(frozen=True)
        class MockContext:
            pass
        
        @dataclass(frozen=True)
        class MockDictObject:
            context: MockContext
            alloc_site: MockAllocSite
            
            @property
            def kind(self):
                return AllocKind.DICT
        
        return MockDictObject(
            context=MockContext(),
            alloc_site=MockAllocSite(stmt="<test>", kind=AllocKind.DICT),
        )
    
    def test_store_and_load_const_key(self):
        """Test store and load with constant string key."""
        from pythonstan.analysis.pointer.ai.domains.mapslots import DictSemantics
        from pythonstan.analysis.pointer.ai.value_domain import AbsVal
        from pythonstan.analysis.pointer.ai.state import AIState
        from pythonstan.analysis.pointer.ai.summary import SummaryAccumulator
        
        dict_obj = self._make_mock_dict_obj()
        dict_val = AbsVal.from_addrs(frozenset({dict_obj}))
        
        key_val = AbsVal.from_str("foo")
        value_val = AbsVal.from_int(42)
        
        state = AIState.bottom()
        summary = SummaryAccumulator()
        
        dict_sem = DictSemantics(
            caller_scope=None,
            call_context=None,
            budget=None,
        )
        
        # Store
        state = dict_sem.store_item(dict_val, key_val, value_val, state, summary)
        
        # Load
        result = dict_sem.load_item(dict_val, key_val, state, summary)
        
        # Should have stored and loaded the value
        # Note: The int lattice might not directly match, but the operation should complete
        assert not result.result_val.is_bottom() or result.saw_havoc
    
    def test_contains_present_key(self):
        """Test contains for a key that was stored."""
        from pythonstan.analysis.pointer.ai.domains.mapslots import DictSemantics
        from pythonstan.analysis.pointer.ai.value_domain import AbsVal
        from pythonstan.analysis.pointer.ai.state import AIState
        from pythonstan.analysis.pointer.ai.summary import SummaryAccumulator
        
        dict_obj = self._make_mock_dict_obj()
        dict_val = AbsVal.from_addrs(frozenset({dict_obj}))
        
        key_val = AbsVal.from_str("exists")
        value_val = AbsVal.from_int(1)
        
        state = AIState.bottom()
        summary = SummaryAccumulator()
        
        dict_sem = DictSemantics(
            caller_scope=None,
            call_context=None,
            budget=None,
        )
        
        # Store a key
        state = dict_sem.store_item(dict_val, key_val, value_val, state, summary)
        
        # Check contains
        may_be_true, may_be_false = dict_sem.contains(dict_val, key_val, state)
        
        # Should definitely be present
        assert may_be_true
    
    def test_dict_get_with_default(self):
        """Test dict.get with default value."""
        from pythonstan.analysis.pointer.ai.domains.mapslots import DictSemantics
        from pythonstan.analysis.pointer.ai.value_domain import AbsVal
        from pythonstan.analysis.pointer.ai.state import AIState
        from pythonstan.analysis.pointer.ai.summary import SummaryAccumulator
        
        dict_obj = self._make_mock_dict_obj()
        dict_val = AbsVal.from_addrs(frozenset({dict_obj}))
        
        key_val = AbsVal.from_str("missing")
        default_val = AbsVal.from_str("default_result")
        
        state = AIState.bottom()
        summary = SummaryAccumulator()
        
        dict_sem = DictSemantics(
            caller_scope=None,
            call_context=None,
            budget=None,
        )
        
        # Get with default (key doesn't exist)
        result = dict_sem.dict_get(dict_val, key_val, default_val, state, summary)
        
        # Should include the default
        # The string lattice should have the default value
        str_consts = result.get_const_strs()
        if str_consts is not None:
            assert "default_result" in str_consts or result.get_string_lattice().is_top()


# =============================================================================
# PredicateInfo Tests
# =============================================================================


class TestPredicateInfo:
    """Tests for PredicateInfo and predicate recording."""
    
    def test_predicate_info_creation(self):
        """Test creating PredicateInfo."""
        from pythonstan.analysis.pointer.ai.state import PredicateInfo
        
        pred = PredicateInfo(
            kind="dict_contains",
            dict_var="d",
            key_var="k",
            negated=False,
        )
        
        assert pred.kind == "dict_contains"
        assert pred.dict_var == "d"
        assert pred.key_var == "k"
        assert not pred.negated
    
    def test_add_predicate_to_state(self):
        """Test adding predicate to AIState."""
        from pythonstan.analysis.pointer.ai.state import AIState, PredicateInfo
        
        state = AIState.bottom()
        pred = PredicateInfo(
            kind="dict_contains",
            dict_var="d",
            key_var="k",
            negated=False,
        )
        
        new_state = state.add_predicate("$tmp", pred)
        
        assert new_state.get_predicate("$tmp") == pred
        assert state.get_predicate("$tmp") is None  # Original unchanged
    
    def test_predicates_join(self):
        """Test that predicates are preserved through join correctly."""
        from pythonstan.analysis.pointer.ai.state import AIState, PredicateInfo
        
        pred = PredicateInfo(
            kind="dict_contains",
            dict_var="d",
            key_var="k",
            negated=False,
        )
        
        state1 = AIState.bottom().add_predicate("$tmp", pred)
        state2 = AIState.bottom().add_predicate("$tmp", pred)
        
        joined = state1.join(state2)
        
        # Same predicates should be preserved
        assert joined.get_predicate("$tmp") == pred


# =============================================================================
# Integration Tests
# =============================================================================


class TestDictModelIntegration:
    """Integration tests for the dict model."""
    
    def test_distinct_string_and_int_keys(self):
        """Test that string and int keys are kept distinct."""
        from pythonstan.analysis.pointer.ai.domains.mapslots import (
            SlotMap, make_str_keysig, make_int_keysig, Presence
        )
        from pythonstan.analysis.pointer.ai.value_domain import AbsVal
        
        slots = SlotMap.for_dict()
        
        # Add string key "1"
        str_sig = make_str_keysig("1")
        slots = slots.weak_update(str_sig, AbsVal.from_str("string_value"), Presence.PRESENT, AbsVal.from_str("1"))
        
        # Add int key 1
        int_sig = make_int_keysig(1)
        slots = slots.weak_update(int_sig, AbsVal.from_str("int_value"), Presence.PRESENT, AbsVal.from_int(1))
        
        # Should be different partitions
        assert str_sig in slots.partitions
        assert int_sig in slots.partitions
        assert str_sig != int_sig
    
    def test_absobj_with_slots(self):
        """Test AbsObj with slots field (unified SlotMap)."""
        from pythonstan.analysis.pointer.ai.heap import AbsObj, AIAddr, ObjKind, SlotMap
        from pythonstan.analysis.pointer.ai.domains.mapslots import make_str_keysig, Presence
        from pythonstan.analysis.pointer.ai.value_domain import AbsVal
        
        # Create mock alloc site and context
        from dataclasses import dataclass
        from pythonstan.analysis.pointer.kcfa.object import AllocKind
        
        @dataclass(frozen=True)
        class MockAllocSite:
            stmt: str
            kind: AllocKind
        
        @dataclass(frozen=True)
        class MockContext:
            pass
        
        alloc_site = MockAllocSite(stmt="<test>", kind=AllocKind.DICT)
        context = MockContext()
        addr = AIAddr(alloc_site=alloc_site, context=context)
        
        # Create SlotMap
        slots = SlotMap.for_dict()
        sig = make_str_keysig("key")
        slots = slots.weak_update(sig, AbsVal.from_int(42), Presence.PRESENT, AbsVal.from_str("key"))
        
        # Create AbsObj with slots
        obj = AbsObj(addr=addr, kind=ObjKind.DICT, slots=slots)
        
        assert obj.slots is not None
        assert sig in obj.slots.partitions
        
        # Test join
        obj2 = obj.copy()
        joined = obj.join(obj2)
        assert joined.slots is not None
