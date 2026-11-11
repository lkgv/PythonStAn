"""Tests for AI precision improvements: SlotMap, ClassInfo, Descriptors, KwShape.

These tests verify the new precision features for Python metaprogramming:
- SlotMap: Unified namespace with presence tracking
- ClassInfo: Class namespace, bases, metaclass modeling
- DescriptorSemantics: property/classmethod/staticmethod interpretation
- KwShape: Keyword argument shape modeling
"""

import pytest
from typing import FrozenSet, Optional


# =============================================================================
# SlotMap Tests
# =============================================================================

class TestSlotMap:
    """Tests for SlotMap unified namespace abstraction."""
    
    def test_empty_mapslots(self):
        """Test creating empty SlotMap."""
        from pythonstan.analysis.pointer.ai.domains.mapslots import SlotMap, Presence
        
        slots = SlotMap.bottom()
        assert slots.is_bottom()
        assert len(slots.partitions) == 0
        assert slots.default.pres == Presence.ABSENT
    
    def test_weak_update_creates_partition(self):
        """Test weak_update creates new partition."""
        from pythonstan.analysis.pointer.ai.domains.mapslots import SlotMap, Presence
        from pythonstan.analysis.pointer.ai.value_domain import AbsVal
        
        slots = SlotMap.bottom()
        val = AbsVal.from_str("hello")
        
        updated = slots.weak_update_str("key1", val, Presence.PRESENT)
        
        assert ("str", "key1") in updated.partitions
        assert updated.partitions[("str", "key1")].pres == Presence.PRESENT
        assert not updated.partitions[("str", "key1")].val.is_bottom()
    
    def test_presence_tracking(self):
        """Test presence is tracked correctly."""
        from pythonstan.analysis.pointer.ai.domains.mapslots import SlotMap, Presence
        from pythonstan.analysis.pointer.ai.value_domain import AbsVal
        
        slots = SlotMap.bottom()
        val = AbsVal.from_int(42)
        
        # Add with PRESENT
        slots = slots.weak_update_str("definite", val, Presence.PRESENT)
        # Add with MAYBE
        slots = slots.weak_update_str("uncertain", val, Presence.MAYBE)
        
        assert slots.get_str_presence("definite") == Presence.PRESENT
        assert slots.get_str_presence("uncertain") == Presence.MAYBE
        assert slots.get_str_presence("unknown") == Presence.ABSENT  # default
    
    def test_join_updates_presence(self):
        """Test join makes PRESENT keys MAYBE when only in one side."""
        from pythonstan.analysis.pointer.ai.domains.mapslots import SlotMap, Presence
        from pythonstan.analysis.pointer.ai.value_domain import AbsVal
        
        val = AbsVal.from_str("x")
        
        slots1 = SlotMap.bottom().weak_update_str("a", val, Presence.PRESENT)
        slots2 = SlotMap.bottom().weak_update_str("b", val, Presence.PRESENT)
        
        joined = slots1.join(slots2)
        
        # Keys present in only one side become MAYBE
        assert joined.get_str_presence("a") == Presence.MAYBE
        assert joined.get_str_presence("b") == Presence.MAYBE
    
    def test_assume_contains_refines_presence(self):
        """Test assume_contains strengthens to PRESENT."""
        from pythonstan.analysis.pointer.ai.domains.mapslots import SlotMap, Presence
        from pythonstan.analysis.pointer.ai.value_domain import AbsVal
        
        slots = SlotMap.bottom()
        slots = slots.weak_update_str("maybe_key", AbsVal.from_str("v"), Presence.MAYBE)
        
        refined = slots.assume_contains_str("maybe_key")
        
        assert refined.get_str_presence("maybe_key") == Presence.PRESENT
    
    def test_assume_not_contains_refines_presence(self):
        """Test assume_not_contains strengthens to ABSENT."""
        from pythonstan.analysis.pointer.ai.domains.mapslots import SlotMap, Presence
        from pythonstan.analysis.pointer.ai.value_domain import AbsVal
        
        slots = SlotMap.bottom()
        slots = slots.weak_update_str("maybe_key", AbsVal.from_str("v"), Presence.MAYBE)
        
        refined = slots.assume_not_contains_str("maybe_key")
        
        assert refined.get_str_presence("maybe_key") == Presence.ABSENT
    
    def test_widen_spills_excess_partitions(self):
        """Test widening spills partitions when over budget."""
        from pythonstan.analysis.pointer.ai.domains.mapslots import SlotMap, Presence
        from pythonstan.analysis.pointer.ai.value_domain import AbsVal
        
        slots = SlotMap.bottom(max_partitions=3)
        val = AbsVal.from_str("v")
        
        # Add more keys than max
        for i in range(5):
            slots = slots.weak_update_str(f"key{i}", val, Presence.PRESENT)
        
        # Widen should spill
        widened = slots.widen(slots)
        
        # Should have at most max_partitions tracked
        assert len(widened.partitions) <= 3
        # Default should have absorbed spilled values
        assert not widened.default.val.is_bottom()


class TestNamespaceSemantics:
    """Tests for NamespaceSemantics service."""
    
    def test_load_from_tracked_key(self):
        """Test loading from a tracked key."""
        from pythonstan.analysis.pointer.ai.domains.mapslots import SlotMap, Presence, SlotMapSemantics
        from pythonstan.analysis.pointer.ai.value_domain import AbsVal
        
        ns = SlotMapSemantics(None, None, None)
        slots = SlotMap.for_namespace()
        val = AbsVal.from_str("hello")
        slots = slots.weak_update_str("attr", val, Presence.PRESENT)
        
        result, may_be_missing = ns.load(slots, "attr")
        
        assert not result.is_bottom()
        assert not may_be_missing  # PRESENT means definitely there
    
    def test_load_from_maybe_key(self):
        """Test loading from a MAYBE key reports may_be_missing."""
        from pythonstan.analysis.pointer.ai.domains.mapslots import SlotMap, Presence, SlotMapSemantics
        from pythonstan.analysis.pointer.ai.value_domain import AbsVal
        
        ns = SlotMapSemantics(None, None, None)
        slots = SlotMap.for_namespace()
        val = AbsVal.from_str("hello")
        slots = slots.weak_update_str("attr", val, Presence.MAYBE)
        
        result, may_be_missing = ns.load(slots, "attr")
        
        assert not result.is_bottom()
        assert may_be_missing  # MAYBE means might be absent


# =============================================================================
# ClassInfo Tests
# =============================================================================

class TestClassInfo:
    """Tests for ClassInfo enhanced class modeling."""
    
    def test_class_info_creation(self):
        """Test creating ClassInfo."""
        from pythonstan.analysis.pointer.ai.domains.classinfo import ClassInfo
        from pythonstan.analysis.pointer.ai.domains.mapslots import SlotMap
        from unittest.mock import MagicMock
        
        mock_obj = MagicMock()
        info = ClassInfo(class_obj=mock_obj)
        
        assert info.class_obj == mock_obj
        assert not info.has_namespace()
        assert not info.has_bases()
        assert not info.has_metaclass()
    
    def test_class_info_with_namespace(self):
        """Test ClassInfo with namespace."""
        from pythonstan.analysis.pointer.ai.domains.classinfo import ClassInfo
        from pythonstan.analysis.pointer.ai.domains.mapslots import SlotMap, Presence
        from pythonstan.analysis.pointer.ai.value_domain import AbsVal
        from unittest.mock import MagicMock
        
        mock_obj = MagicMock()
        ns = SlotMap.for_namespace()
        ns = ns.weak_update_str("method", AbsVal.from_str("func"), Presence.PRESENT)
        
        info = ClassInfo(class_obj=mock_obj, namespace=ns)
        
        assert info.has_namespace()
        method_val = info.get_namespace_value("method")
        assert method_val is not None
        assert not method_val.is_bottom()
    
    def test_class_info_with_metaclass(self):
        """Test ClassInfo with metaclass."""
        from pythonstan.analysis.pointer.ai.domains.classinfo import ClassInfo
        from unittest.mock import MagicMock
        
        mock_obj = MagicMock()
        mock_meta = MagicMock()
        
        info = ClassInfo(
            class_obj=mock_obj,
            metaclass_addrs=frozenset({mock_meta}),
        )
        
        assert info.has_metaclass()
        assert mock_meta in info.metaclass_addrs
    
    def test_class_info_join(self):
        """Test joining ClassInfo."""
        from pythonstan.analysis.pointer.ai.domains.classinfo import ClassInfo
        from pythonstan.analysis.pointer.ai.domains.mapslots import SlotMap, Presence
        from pythonstan.analysis.pointer.ai.value_domain import AbsVal
        from unittest.mock import MagicMock
        
        mock_obj = MagicMock()
        
        ns1 = SlotMap.for_namespace()
        ns1 = ns1.weak_update_str("a", AbsVal.from_str("v1"), Presence.PRESENT)
        info1 = ClassInfo(class_obj=mock_obj, namespace=ns1)
        
        ns2 = SlotMap.for_namespace()
        ns2 = ns2.weak_update_str("b", AbsVal.from_str("v2"), Presence.PRESENT)
        info2 = ClassInfo(class_obj=mock_obj, namespace=ns2)
        
        joined = info1.join(info2)
        
        # Both keys should be present (with MAYBE since only in one side)
        assert joined.has_namespace()
        assert joined.get_namespace_value("a") is not None
        assert joined.get_namespace_value("b") is not None


class TestClassInfoCache:
    """Tests for ClassInfoCache."""
    
    def test_cache_creates_info(self):
        """Test cache creates ClassInfo on first access."""
        from pythonstan.analysis.pointer.ai.domains.classinfo import ClassInfoCache
        from unittest.mock import MagicMock
        
        cache = ClassInfoCache(None, None)
        mock_obj = MagicMock()
        mock_obj.alloc_site = "site1"
        mock_obj.context = "ctx1"
        
        info = cache.get_info(mock_obj, None)
        
        assert info.class_obj == mock_obj
    
    def test_cache_returns_cached_info(self):
        """Test cache returns same info on subsequent access."""
        from pythonstan.analysis.pointer.ai.domains.classinfo import ClassInfoCache
        from unittest.mock import MagicMock
        
        cache = ClassInfoCache(None, None)
        mock_obj = MagicMock()
        mock_obj.alloc_site = "site1"
        mock_obj.context = "ctx1"
        
        info1 = cache.get_info(mock_obj, None)
        info2 = cache.get_info(mock_obj, None)
        
        assert info1 is info2  # Same cached object


# =============================================================================
# Descriptor Tests
# =============================================================================

class TestDescriptorTypes:
    """Tests for descriptor type tokens."""
    
    def test_property_ref_creation(self):
        """Test creating PropertyRef."""
        from pythonstan.analysis.pointer.ai.domains.descriptor import PropertyRef, DescriptorType
        from unittest.mock import MagicMock
        
        mock_prop = MagicMock()
        mock_fget = MagicMock()
        mock_fset = MagicMock()
        
        ref = PropertyRef(
            obj=mock_prop,
            fget_addrs=frozenset({mock_fget}),
            fset_addrs=frozenset({mock_fset}),
        )
        
        assert ref.desc_type == DescriptorType.PROPERTY
        assert ref.has_get
        assert ref.has_set
        assert not ref.has_delete
        assert ref.is_data_descriptor  # has fset
    
    def test_classmethod_ref_creation(self):
        """Test creating ClassMethodRef."""
        from pythonstan.analysis.pointer.ai.domains.descriptor import ClassMethodRef, DescriptorType
        from unittest.mock import MagicMock
        
        mock_cm = MagicMock()
        mock_func = MagicMock()
        
        ref = ClassMethodRef(
            obj=mock_cm,
            func_addrs=frozenset({mock_func}),
        )
        
        assert ref.desc_type == DescriptorType.CLASSMETHOD
        assert ref.has_get
        assert not ref.has_set
        assert not ref.is_data_descriptor
    
    def test_staticmethod_ref_creation(self):
        """Test creating StaticMethodRef."""
        from pythonstan.analysis.pointer.ai.domains.descriptor import StaticMethodRef, DescriptorType
        from unittest.mock import MagicMock
        
        mock_sm = MagicMock()
        mock_func = MagicMock()
        
        ref = StaticMethodRef(
            obj=mock_sm,
            func_addrs=frozenset({mock_func}),
        )
        
        assert ref.desc_type == DescriptorType.STATICMETHOD
        assert ref.has_get
        assert not ref.has_set
        assert not ref.is_data_descriptor


class TestDescriptorVal:
    """Tests for DescriptorVal."""
    
    def test_descriptor_val_from_property(self):
        """Test creating DescriptorVal for property."""
        from pythonstan.analysis.pointer.ai.domains.descriptor import DescriptorVal
        from unittest.mock import MagicMock
        
        mock_prop = MagicMock()
        mock_fget = MagicMock()
        
        val = DescriptorVal.from_property(
            mock_prop,
            fget_addrs=frozenset({mock_fget}),
        )
        
        assert not val.is_bottom()
        assert len(val.get_properties()) == 1
    
    def test_descriptor_val_join(self):
        """Test joining DescriptorVal."""
        from pythonstan.analysis.pointer.ai.domains.descriptor import DescriptorVal
        from unittest.mock import MagicMock
        
        mock1 = MagicMock()
        mock2 = MagicMock()
        
        val1 = DescriptorVal.from_classmethod(mock1, frozenset())
        val2 = DescriptorVal.from_staticmethod(mock2, frozenset())
        
        joined = val1.join(val2)
        
        assert len(joined.get_classmethods()) == 1
        assert len(joined.get_staticmethods()) == 1


class TestDescriptorSemantics:
    """Tests for DescriptorSemantics service."""
    
    def test_interpret_property_get(self):
        """Test interpreting property fget."""
        from pythonstan.analysis.pointer.ai.domains.descriptor import (
            DescriptorSemantics, DescriptorVal, PropertyRef
        )
        from pythonstan.analysis.pointer.ai.value_domain import AbsVal
        from pythonstan.analysis.pointer.ai.summary import SummaryAccumulator
        from unittest.mock import MagicMock
        
        # Mock invoke_callee to return a value
        def mock_invoke(callee, args, summary, receiver):
            return AbsVal.from_str("property_value")
        
        sem = DescriptorSemantics(mock_invoke)
        
        mock_prop = MagicMock()
        mock_fget = MagicMock()
        
        desc_val = DescriptorVal.from_property(
            mock_prop,
            fget_addrs=frozenset({mock_fget}),
        )
        
        receiver = AbsVal.top()
        owner = AbsVal.top()
        summary = SummaryAccumulator()
        
        result = sem.interpret_get(desc_val, receiver, owner, summary)
        
        # Should call fget and return result
        assert not result.is_bottom()
    
    def test_interpret_staticmethod_get(self):
        """Test interpreting staticmethod returns unwrapped function."""
        from pythonstan.analysis.pointer.ai.domains.descriptor import (
            DescriptorSemantics, DescriptorVal, StaticMethodRef
        )
        from pythonstan.analysis.pointer.ai.value_domain import AbsVal
        from pythonstan.analysis.pointer.ai.summary import SummaryAccumulator
        from unittest.mock import MagicMock
        
        def mock_invoke(callee, args, summary, receiver):
            return AbsVal.top()
        
        sem = DescriptorSemantics(mock_invoke)
        
        mock_sm = MagicMock()
        mock_sm.alloc_site = "alloc1"
        mock_sm.context = "ctx1"
        mock_func = MagicMock()
        mock_func.alloc_site = "alloc2"
        mock_func.context = "ctx2"
        
        desc_val = DescriptorVal.from_staticmethod(
            mock_sm,
            func_addrs=frozenset({mock_func}),
        )
        
        summary = SummaryAccumulator()
        
        result = sem.interpret_get(desc_val, None, None, summary)
        
        # Should return function without binding
        assert not result.is_bottom()
        assert mock_func in result.addrs


# =============================================================================
# KwShape Tests
# =============================================================================

class TestKwShape:
    """Tests for KwShape kwargs modeling."""
    
    def test_empty_kwshape(self):
        """Test creating empty KwShape."""
        from pythonstan.analysis.pointer.ai.domains.kwargs import KwShape
        
        shape = KwShape.empty()
        
        assert shape.is_empty()
        assert shape.is_bounded()
        assert len(shape.get_present_keys()) == 0
    
    def test_kwshape_with_key(self):
        """Test adding key to KwShape."""
        from pythonstan.analysis.pointer.ai.domains.kwargs import KwShape
        from pythonstan.analysis.pointer.ai.domains.mapslots import Presence
        from pythonstan.analysis.pointer.ai.value_domain import AbsVal
        
        shape = KwShape.empty()
        shape = shape.with_key("name", AbsVal.from_str("value"), Presence.PRESENT)
        
        assert not shape.is_empty()
        assert shape.is_bounded()
        assert "name" in shape.get_present_keys()
    
    def test_kwshape_with_unknown(self):
        """Test KwShape with unknown keys."""
        from pythonstan.analysis.pointer.ai.domains.kwargs import KwShape
        from pythonstan.analysis.pointer.ai.value_domain import AbsVal
        
        shape = KwShape.empty()
        shape = shape.with_unknown(AbsVal.top())
        
        assert not shape.is_bounded()
        assert shape.has_unknown
    
    def test_kwshape_join(self):
        """Test joining KwShape."""
        from pythonstan.analysis.pointer.ai.domains.kwargs import KwShape
        from pythonstan.analysis.pointer.ai.domains.mapslots import Presence
        from pythonstan.analysis.pointer.ai.value_domain import AbsVal
        
        shape1 = KwShape.empty().with_key("a", AbsVal.from_str("1"), Presence.PRESENT)
        shape2 = KwShape.empty().with_key("b", AbsVal.from_str("2"), Presence.PRESENT)
        
        joined = shape1.join(shape2)
        
        # Both keys should be MAYBE (present in only one side)
        assert "a" in joined.get_present_keys()
        assert "b" in joined.get_present_keys()
        # Both become MAYBE after join
        from pythonstan.analysis.pointer.ai.domains.mapslots import Presence
        assert joined.get_presence("a") == Presence.MAYBE
        assert joined.get_presence("b") == Presence.MAYBE
    
    def test_expand_kwargs(self):
        """Test expanding bounded KwShape."""
        from pythonstan.analysis.pointer.ai.domains.kwargs import KwShape, expand_kwargs
        from pythonstan.analysis.pointer.ai.domains.mapslots import Presence
        from pythonstan.analysis.pointer.ai.value_domain import AbsVal
        
        shape = KwShape.empty()
        shape = shape.with_key("x", AbsVal.from_int(1), Presence.PRESENT)
        shape = shape.with_key("y", AbsVal.from_int(2), Presence.PRESENT)
        
        expanded = expand_kwargs(shape)
        
        assert expanded is not None
        assert "x" in expanded
        assert "y" in expanded
    
    def test_expand_kwargs_unbounded_returns_none(self):
        """Test expand_kwargs returns None for unbounded shape."""
        from pythonstan.analysis.pointer.ai.domains.kwargs import KwShape, expand_kwargs
        from pythonstan.analysis.pointer.ai.value_domain import AbsVal
        
        shape = KwShape.empty().with_unknown(AbsVal.top())
        
        expanded = expand_kwargs(shape)
        
        assert expanded is None


class TestKwShapeDisj:
    """Tests for KwShapeDisj."""
    
    def test_kwshape_disj_single(self):
        """Test creating single-alternative KwShapeDisj."""
        from pythonstan.analysis.pointer.ai.domains.kwargs import KwShape, KwShapeDisj
        
        shape = KwShape.empty()
        disj = KwShapeDisj.single(shape)
        
        assert disj.is_single()
        assert len(disj.alts) == 1
    
    def test_kwshape_disj_join(self):
        """Test joining KwShapeDisj."""
        from pythonstan.analysis.pointer.ai.domains.kwargs import KwShape, KwShapeDisj
        from pythonstan.analysis.pointer.ai.domains.mapslots import Presence
        from pythonstan.analysis.pointer.ai.value_domain import AbsVal
        
        shape1 = KwShape.empty().with_key("a", AbsVal.from_str("1"), Presence.PRESENT)
        shape2 = KwShape.empty().with_key("b", AbsVal.from_str("2"), Presence.PRESENT)
        
        disj1 = KwShapeDisj.single(shape1)
        disj2 = KwShapeDisj.single(shape2)
        
        joined = disj1.join(disj2)
        
        # Should have 2 alternatives
        assert len(joined.alts) == 2
        
        # All possible keys should include both
        all_keys = joined.get_all_possible_keys()
        assert "a" in all_keys
        assert "b" in all_keys


class TestArgKwShape:
    """Tests for ArgKwShape combined shape."""
    
    def test_argkw_shape_creation(self):
        """Test creating ArgKwShape."""
        from pythonstan.analysis.pointer.ai.domains.kwargs import ArgKwShape, KwShapeDisj, KwShape
        from pythonstan.analysis.pointer.ai.domains.tuples import TupleDisj, TupleExact
        from pythonstan.analysis.pointer.ai.value_domain import AbsVal
        
        args = TupleDisj.from_exact(TupleExact(elems=(
            AbsVal.from_int(1),
            AbsVal.from_int(2),
        )))
        kwargs = KwShapeDisj.single(KwShape.empty())
        
        shape = ArgKwShape(args=args, kwargs=kwargs)
        
        assert shape.has_args()
        assert shape.has_kwargs()


# =============================================================================
# Integration Tests
# =============================================================================

class TestPrecisionIntegration:
    """Integration tests for precision improvements."""
    
    def test_mapslots_with_string_lattice_filtering(self):
        """Test SlotMap key filtering with string lattice."""
        from pythonstan.analysis.pointer.ai.domains.mapslots import SlotMap, Presence
        from pythonstan.analysis.pointer.ai.domains.strings import StrLattice
        from pythonstan.analysis.pointer.ai.value_domain import AbsVal
        from pythonstan.analysis.pointer.ai.domains.mapslots import SlotMap, Presence, make_str_keysig
        
        slots = SlotMap.for_namespace()
        slots = slots.weak_update_str("prefix_a", AbsVal.from_int(1), Presence.PRESENT)
        slots = slots.weak_update_str("prefix_b", AbsVal.from_int(2), Presence.PRESENT)
        slots = slots.weak_update_str("other", AbsVal.from_int(3), Presence.PRESENT)
        
        # Filter with prefix constraint using SlotMap's tracked string keys
        prefix_lattice = StrLattice.from_prefix("prefix_")
        tracked_strs = slots.get_all_str_keys()
        
        # Filter tracked keys using the prefix lattice
        matching = frozenset(k for k in tracked_strs if prefix_lattice.may_equal(k))
        
        assert "prefix_a" in matching
        assert "prefix_b" in matching
        assert "other" not in matching
    
    def test_descriptor_data_precedence(self):
        """Test data descriptor takes precedence in attribute lookup."""
        from pythonstan.analysis.pointer.ai.domains.descriptor import (
            DescriptorVal, PropertyRef, DescriptorRef
        )
        from unittest.mock import MagicMock
        
        # Property is a data descriptor (has fset)
        mock_prop = MagicMock()
        mock_fget = MagicMock()
        mock_fset = MagicMock()
        
        prop_val = DescriptorVal.from_property(
            mock_prop,
            fget_addrs=frozenset({mock_fget}),
            fset_addrs=frozenset({mock_fset}),
        )
        
        # Check data descriptor detection
        assert prop_val.has_data_descriptor()
        
        # Generic descriptor without __set__ is non-data
        generic_ref = DescriptorRef(
            obj=MagicMock(),
            has_get=True,
            has_set=False,
            has_delete=False,
        )
        generic_val = DescriptorVal.from_tokens(frozenset({generic_ref}))
        
        assert not generic_val.has_data_descriptor()


# =============================================================================
# AttributeSemantics Integration Tests
# =============================================================================

class TestAttributeSemanticsIntegration:
    """Integration tests for AttributeSemantics with ClassInfo and DescriptorSemantics."""
    
    def test_classify_as_descriptor_empty(self):
        """Test _classify_as_descriptor with empty value."""
        from pythonstan.analysis.pointer.ai.domains.attributes import AttributeSemantics
        from pythonstan.analysis.pointer.ai.value_domain import AbsVal
        from unittest.mock import MagicMock
        
        def mock_invoke(callee, args, summary, receiver):
            return AbsVal.top()
        
        sem = AttributeSemantics(
            pta_query=None,
            caller_scope=None,
            call_context=None,
            invoke_callee=mock_invoke,
        )
        
        # Empty value should return bottom descriptor
        empty_val = AbsVal.bottom()
        desc_val = sem._classify_as_descriptor(empty_val, None)
        
        assert desc_val.is_bottom()
    
    def test_classify_as_descriptor_method_with_getter(self):
        """Test _classify_as_descriptor recognizes property from IR flags."""
        from pythonstan.analysis.pointer.ai.domains.attributes import AttributeSemantics
        from pythonstan.analysis.pointer.ai.domains.descriptor import PropertyRef
        from pythonstan.analysis.pointer.ai.value_domain import AbsVal
        from unittest.mock import MagicMock
        
        def mock_invoke(callee, args, summary, receiver):
            return AbsVal.top()
        
        sem = AttributeSemantics(
            pta_query=None,
            caller_scope=None,
            call_context=None,
            invoke_callee=mock_invoke,
        )
        
        # Create mock object with is_getter=True
        mock_obj = MagicMock()
        mock_ir = MagicMock()
        mock_ir.is_getter = True
        mock_ir.is_class_method = False
        mock_ir.is_static_method = False
        mock_obj.ir = mock_ir
        
        val = AbsVal.from_addrs(frozenset({mock_obj}))
        desc_val = sem._classify_as_descriptor(val, None)
        
        # Should recognize as property
        assert not desc_val.is_bottom()
        props = desc_val.get_properties()
        assert len(props) == 1
        assert props[0].fget_addrs == frozenset({mock_obj})
    
    def test_classify_as_descriptor_classmethod(self):
        """Test _classify_as_descriptor recognizes classmethod from IR flags."""
        from pythonstan.analysis.pointer.ai.domains.attributes import AttributeSemantics
        from pythonstan.analysis.pointer.ai.domains.descriptor import ClassMethodRef
        from pythonstan.analysis.pointer.ai.value_domain import AbsVal
        from unittest.mock import MagicMock
        
        def mock_invoke(callee, args, summary, receiver):
            return AbsVal.top()
        
        sem = AttributeSemantics(
            pta_query=None,
            caller_scope=None,
            call_context=None,
            invoke_callee=mock_invoke,
        )
        
        # Create mock object with is_class_method=True
        mock_obj = MagicMock()
        mock_ir = MagicMock()
        mock_ir.is_getter = False
        mock_ir.is_class_method = True
        mock_ir.is_static_method = False
        mock_obj.ir = mock_ir
        
        val = AbsVal.from_addrs(frozenset({mock_obj}))
        desc_val = sem._classify_as_descriptor(val, None)
        
        # Should recognize as classmethod
        assert not desc_val.is_bottom()
        cms = desc_val.get_classmethods()
        assert len(cms) == 1
        assert cms[0].func_addrs == frozenset({mock_obj})
    
    def test_classify_as_descriptor_staticmethod(self):
        """Test _classify_as_descriptor recognizes staticmethod from IR flags."""
        from pythonstan.analysis.pointer.ai.domains.attributes import AttributeSemantics
        from pythonstan.analysis.pointer.ai.domains.descriptor import StaticMethodRef
        from pythonstan.analysis.pointer.ai.value_domain import AbsVal
        from unittest.mock import MagicMock
        
        def mock_invoke(callee, args, summary, receiver):
            return AbsVal.top()
        
        sem = AttributeSemantics(
            pta_query=None,
            caller_scope=None,
            call_context=None,
            invoke_callee=mock_invoke,
        )
        
        # Create mock object with is_static_method=True
        mock_obj = MagicMock()
        mock_ir = MagicMock()
        mock_ir.is_getter = False
        mock_ir.is_class_method = False
        mock_ir.is_static_method = True
        mock_obj.ir = mock_ir
        
        val = AbsVal.from_addrs(frozenset({mock_obj}))
        desc_val = sem._classify_as_descriptor(val, None)
        
        # Should recognize as staticmethod
        assert not desc_val.is_bottom()
        sms = desc_val.get_staticmethods()
        assert len(sms) == 1
        assert sms[0].func_addrs == frozenset({mock_obj})
    
    def test_create_descriptor_val_for_method_property(self):
        """Test create_descriptor_val_for_method with property decorator."""
        from pythonstan.analysis.pointer.ai.domains.descriptor import (
            create_descriptor_val_for_method,
            DescriptorType,
        )
        from unittest.mock import MagicMock
        
        mock_obj = MagicMock()
        mock_ir = MagicMock()
        mock_ir.is_getter = True
        mock_ir.is_class_method = False
        mock_ir.is_static_method = False
        mock_obj.ir = mock_ir
        
        desc_val = create_descriptor_val_for_method(mock_obj, None)
        
        assert desc_val is not None
        assert not desc_val.is_bottom()
        props = desc_val.get_properties()
        assert len(props) == 1
        assert props[0].desc_type == DescriptorType.PROPERTY
    
    def test_create_descriptor_val_for_method_classmethod(self):
        """Test create_descriptor_val_for_method with classmethod decorator."""
        from pythonstan.analysis.pointer.ai.domains.descriptor import (
            create_descriptor_val_for_method,
            DescriptorType,
        )
        from unittest.mock import MagicMock
        
        mock_obj = MagicMock()
        mock_ir = MagicMock()
        mock_ir.is_getter = False
        mock_ir.is_class_method = True
        mock_ir.is_static_method = False
        mock_obj.ir = mock_ir
        
        desc_val = create_descriptor_val_for_method(mock_obj, None)
        
        assert desc_val is not None
        assert not desc_val.is_bottom()
        cms = desc_val.get_classmethods()
        assert len(cms) == 1
        assert cms[0].desc_type == DescriptorType.CLASSMETHOD
    
    def test_create_descriptor_val_for_method_staticmethod(self):
        """Test create_descriptor_val_for_method with staticmethod decorator."""
        from pythonstan.analysis.pointer.ai.domains.descriptor import (
            create_descriptor_val_for_method,
            DescriptorType,
        )
        from unittest.mock import MagicMock
        
        mock_obj = MagicMock()
        mock_ir = MagicMock()
        mock_ir.is_getter = False
        mock_ir.is_class_method = False
        mock_ir.is_static_method = True
        mock_obj.ir = mock_ir
        
        desc_val = create_descriptor_val_for_method(mock_obj, None)
        
        assert desc_val is not None
        assert not desc_val.is_bottom()
        sms = desc_val.get_staticmethods()
        assert len(sms) == 1
        assert sms[0].desc_type == DescriptorType.STATICMETHOD
    
    def test_create_descriptor_val_for_method_regular(self):
        """Test create_descriptor_val_for_method with regular method (no decorator)."""
        from pythonstan.analysis.pointer.ai.domains.descriptor import (
            create_descriptor_val_for_method,
        )
        from unittest.mock import MagicMock
        
        mock_obj = MagicMock()
        mock_ir = MagicMock()
        mock_ir.is_getter = False
        mock_ir.is_class_method = False
        mock_ir.is_static_method = False
        mock_obj.ir = mock_ir
        
        desc_val = create_descriptor_val_for_method(mock_obj, None)
        
        # Regular method should return None (not a special descriptor)
        assert desc_val is None
    
    def test_is_known_descriptor_method(self):
        """Test is_known_descriptor_method helper."""
        from pythonstan.analysis.pointer.ai.domains.descriptor import is_known_descriptor_method
        from unittest.mock import MagicMock
        
        # Property
        prop_obj = MagicMock()
        prop_ir = MagicMock()
        prop_ir.is_getter = True
        prop_ir.is_class_method = False
        prop_ir.is_static_method = False
        prop_obj.ir = prop_ir
        assert is_known_descriptor_method(prop_obj)
        
        # Classmethod
        cm_obj = MagicMock()
        cm_ir = MagicMock()
        cm_ir.is_getter = False
        cm_ir.is_class_method = True
        cm_ir.is_static_method = False
        cm_obj.ir = cm_ir
        assert is_known_descriptor_method(cm_obj)
        
        # Staticmethod
        sm_obj = MagicMock()
        sm_ir = MagicMock()
        sm_ir.is_getter = False
        sm_ir.is_class_method = False
        sm_ir.is_static_method = True
        sm_obj.ir = sm_ir
        assert is_known_descriptor_method(sm_obj)
        
        # Regular method
        reg_obj = MagicMock()
        reg_ir = MagicMock()
        reg_ir.is_getter = False
        reg_ir.is_class_method = False
        reg_ir.is_static_method = False
        reg_obj.ir = reg_ir
        assert not is_known_descriptor_method(reg_obj)
        
        # No IR
        no_ir_obj = MagicMock(spec=[])  # No ir attribute
        assert not is_known_descriptor_method(no_ir_obj)
    
    def test_descriptor_semantics_interpret_get_property(self):
        """Test DescriptorSemantics.interpret_get calls property fget."""
        from pythonstan.analysis.pointer.ai.domains.descriptor import (
            DescriptorSemantics, DescriptorVal
        )
        from pythonstan.analysis.pointer.ai.value_domain import AbsVal
        from pythonstan.analysis.pointer.ai.summary import SummaryAccumulator
        from unittest.mock import MagicMock
        
        calls = []
        def mock_invoke(callee, args, summary, receiver):
            calls.append((callee, args, receiver))
            return AbsVal.from_str("property_result")
        
        sem = DescriptorSemantics(mock_invoke)
        
        mock_prop = MagicMock()
        mock_fget = MagicMock()
        
        desc_val = DescriptorVal.from_property(
            mock_prop,
            fget_addrs=frozenset({mock_fget}),
        )
        
        receiver = AbsVal.from_addrs(frozenset({MagicMock()}))
        owner = AbsVal.from_addrs(frozenset({MagicMock()}))
        summary = SummaryAccumulator()
        
        result = sem.interpret_get(desc_val, receiver, owner, summary)
        
        # Should have called fget
        assert len(calls) == 1
        assert calls[0][0] == mock_fget
        assert not result.is_bottom()
    
    def test_descriptor_semantics_interpret_get_classmethod(self):
        """Test DescriptorSemantics.interpret_get binds classmethod to class."""
        from pythonstan.analysis.pointer.ai.domains.descriptor import (
            DescriptorSemantics, DescriptorVal
        )
        from pythonstan.analysis.pointer.ai.domains.callable import BoundMethodRef
        from pythonstan.analysis.pointer.ai.value_domain import AbsVal
        from pythonstan.analysis.pointer.ai.summary import SummaryAccumulator
        from unittest.mock import MagicMock
        
        def mock_invoke(callee, args, summary, receiver):
            return AbsVal.top()
        
        sem = DescriptorSemantics(mock_invoke)
        
        mock_cm = MagicMock()
        mock_cm.alloc_site = "cm_alloc"
        mock_cm.context = "cm_ctx"
        mock_func = MagicMock()
        mock_func.alloc_site = "func_alloc"
        mock_func.context = "func_ctx"
        
        desc_val = DescriptorVal.from_classmethod(
            mock_cm,
            func_addrs=frozenset({mock_func}),
        )
        
        mock_class = MagicMock()
        receiver = AbsVal.from_addrs(frozenset({MagicMock()}))  # Instance
        owner = AbsVal.from_addrs(frozenset({mock_class}))  # Class
        summary = SummaryAccumulator()
        
        result = sem.interpret_get(desc_val, receiver, owner, summary)
        
        # Should return bound method with class as receiver
        assert not result.is_bottom()
        callable_val = result.get_component("callable")
        assert callable_val is not None
        # Check for BoundMethodRef in tokens
        bound_methods = [t for t in callable_val.tokens if isinstance(t, BoundMethodRef)]
        assert len(bound_methods) > 0
    
    def test_descriptor_semantics_interpret_get_staticmethod(self):
        """Test DescriptorSemantics.interpret_get returns unwrapped function."""
        from pythonstan.analysis.pointer.ai.domains.descriptor import (
            DescriptorSemantics, DescriptorVal
        )
        from pythonstan.analysis.pointer.ai.domains.callable import FunctionRef
        from pythonstan.analysis.pointer.ai.value_domain import AbsVal
        from pythonstan.analysis.pointer.ai.summary import SummaryAccumulator
        from unittest.mock import MagicMock
        
        def mock_invoke(callee, args, summary, receiver):
            return AbsVal.top()
        
        sem = DescriptorSemantics(mock_invoke)
        
        mock_sm = MagicMock()
        mock_sm.alloc_site = "sm_alloc"
        mock_sm.context = "sm_ctx"
        mock_func = MagicMock()
        mock_func.alloc_site = "func_alloc"
        mock_func.context = "func_ctx"
        
        desc_val = DescriptorVal.from_staticmethod(
            mock_sm,
            func_addrs=frozenset({mock_func}),
        )
        
        summary = SummaryAccumulator()
        
        result = sem.interpret_get(desc_val, None, None, summary)
        
        # Should return function without binding
        assert not result.is_bottom()
        assert mock_func in result.addrs
        callable_val = result.get_component("callable")
        assert callable_val is not None
        # Check for FunctionRef (not BoundMethodRef) in tokens
        func_refs = [t for t in callable_val.tokens if isinstance(t, FunctionRef)]
        assert len(func_refs) > 0
