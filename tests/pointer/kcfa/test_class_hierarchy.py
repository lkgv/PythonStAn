"""Tests for class hierarchy and MRO computation."""

import pytest
from pythonstan.analysis.pointer.kcfa.class_hierarchy import (
    ClassHierarchyManager, compute_c3_mro, MROError
)
from pythonstan.analysis.pointer.kcfa import AllocSite, AllocKind, AbstractObject
from pythonstan.analysis.pointer.kcfa.context import CallStringContext


class TestClassHierarchyManagerInitialization:
    """Tests for ClassHierarchyManager initialization."""
    
    def test_basic_initialization(self):
        """Test creating class hierarchy manager."""
        hierarchy = ClassHierarchyManager()
        assert hierarchy is not None
        assert hierarchy._bases == {}
        assert hierarchy._subclasses == {}
        assert hierarchy._mro_cache == {}


class TestSimpleInheritance:
    """Tests for simple single inheritance."""
    
    def test_single_inheritance_chain(self):
        """Test MRO for simple inheritance: C -> B -> A."""
        hierarchy = ClassHierarchyManager()
        ctx = CallStringContext((), 2)
        
        # Create class objects
        class_a = AbstractObject(AllocSite('<test>', 1, 0, AllocKind.CLASS, 'A'), ctx)
        class_b = AbstractObject(AllocSite('<test>', 2, 0, AllocKind.CLASS, 'B'), ctx)
        class_c = AbstractObject(AllocSite('<test>', 3, 0, AllocKind.CLASS, 'C'), ctx)
        
        # Build hierarchy: C(B), B(A), A()
        hierarchy.add_class(class_a)
        hierarchy.add_class(class_b, [class_a])
        hierarchy.add_class(class_c, [class_b])
        
        # Get MRO for C
        mro_c = hierarchy.get_mro(class_c)
        
        # Verify: [C, B, A]
        assert mro_c[0] == class_c
        assert mro_c[1] == class_b
        assert mro_c[2] == class_a
    
    def test_no_inheritance(self):
        """Test MRO for class with no explicit base."""
        hierarchy = ClassHierarchyManager()
        ctx = CallStringContext((), 2)
        
        class_c = AbstractObject(AllocSite('<test>', 1, 0, AllocKind.CLASS, 'C'), ctx)
        hierarchy.add_class(class_c)
        
        mro_c = hierarchy.get_mro(class_c)
        
        # Should be just [C]
        assert mro_c == [class_c]


class TestDiamondInheritance:
    """Tests for diamond inheritance pattern."""
    
    def test_simple_diamond(self):
        """Test MRO for diamond: D(B,C), B(A), C(A), A()."""
        hierarchy = ClassHierarchyManager()
        ctx = CallStringContext((), 2)
        
        # Create classes
        class_a = AbstractObject(AllocSite('<test>', 1, 0, AllocKind.CLASS, 'A'), ctx)
        class_b = AbstractObject(AllocSite('<test>', 2, 0, AllocKind.CLASS, 'B'), ctx)
        class_c = AbstractObject(AllocSite('<test>', 3, 0, AllocKind.CLASS, 'C'), ctx)
        class_d = AbstractObject(AllocSite('<test>', 4, 0, AllocKind.CLASS, 'D'), ctx)
        
        # Build diamond
        hierarchy.add_class(class_a)
        hierarchy.add_class(class_b, [class_a])
        hierarchy.add_class(class_c, [class_a])
        hierarchy.add_class(class_d, [class_b, class_c])
        
        mro_d = hierarchy.get_mro(class_d)
        
        # Expected: [D, B, C, A] (C3 linearization)
        assert mro_d[0] == class_d
        assert mro_d[1] == class_b
        assert mro_d[2] == class_c
        assert mro_d[3] == class_a


class TestMultipleInheritance:
    """Tests for multiple inheritance."""
    
    def test_simple_multiple_inheritance(self):
        """Test MRO for class inheriting from multiple bases."""
        hierarchy = ClassHierarchyManager()
        ctx = CallStringContext((), 2)
        
        class_a = AbstractObject(AllocSite('<test>', 1, 0, AllocKind.CLASS, 'A'), ctx)
        class_b = AbstractObject(AllocSite('<test>', 2, 0, AllocKind.CLASS, 'B'), ctx)
        class_c = AbstractObject(AllocSite('<test>', 3, 0, AllocKind.CLASS, 'C'), ctx)
        
        hierarchy.add_class(class_a)
        hierarchy.add_class(class_b)
        hierarchy.add_class(class_c, [class_a, class_b])
        
        mro_c = hierarchy.get_mro(class_c)
        
        # Expected: [C, A, B]
        assert mro_c[0] == class_c
        assert mro_c[1] == class_a
        assert mro_c[2] == class_b
    
    def test_mro_order_matters(self):
        """Test that order of bases affects MRO."""
        hierarchy1 = ClassHierarchyManager()
        hierarchy2 = ClassHierarchyManager()
        ctx = CallStringContext((), 2)
        
        a1 = AbstractObject(AllocSite('<test>', 1, 0, AllocKind.CLASS, 'A'), ctx)
        b1 = AbstractObject(AllocSite('<test>', 2, 0, AllocKind.CLASS, 'B'), ctx)
        c1 = AbstractObject(AllocSite('<test>', 3, 0, AllocKind.CLASS, 'C'), ctx)
        
        a2 = AbstractObject(AllocSite('<test>', 1, 0, AllocKind.CLASS, 'A'), ctx)
        b2 = AbstractObject(AllocSite('<test>', 2, 0, AllocKind.CLASS, 'B'), ctx)
        c2 = AbstractObject(AllocSite('<test>', 3, 0, AllocKind.CLASS, 'C'), ctx)
        
        hierarchy1.add_class(a1)
        hierarchy1.add_class(b1)
        hierarchy1.add_class(c1, [a1, b1])  # C(A, B)
        
        hierarchy2.add_class(a2)
        hierarchy2.add_class(b2)
        hierarchy2.add_class(c2, [b2, a2])  # C(B, A)
        
        mro1 = hierarchy1.get_mro(c1)
        mro2 = hierarchy2.get_mro(c2)
        
        # Order should differ
        assert mro1[1] == a1  # A first
        assert mro2[1] == b2  # B first


class TestMROCache:
    """Tests for MRO caching."""
    
    def test_mro_cached(self):
        """Test that MRO is cached after first computation."""
        hierarchy = ClassHierarchyManager()
        ctx = CallStringContext((), 2)
        
        class_a = AbstractObject(AllocSite('<test>', 1, 0, AllocKind.CLASS, 'A'), ctx)
        hierarchy.add_class(class_a)
        
        # First computation
        mro1 = hierarchy.get_mro(class_a)
        
        # Second computation should use cache
        mro2 = hierarchy.get_mro(class_a)
        
        assert mro1 is mro2  # Same list object (cached)
    
    def test_mro_cache_invalidation(self):
        """Test that cache is invalidated when hierarchy changes."""
        hierarchy = ClassHierarchyManager()
        ctx = CallStringContext((), 2)
        
        class_a = AbstractObject(AllocSite('<test>', 1, 0, AllocKind.CLASS, 'A'), ctx)
        class_b = AbstractObject(AllocSite('<test>', 2, 0, AllocKind.CLASS, 'B'), ctx)
        
        hierarchy.add_class(class_a)
        mro_a_before = hierarchy.get_mro(class_a)
        
        # Add subclass - should invalidate A's cache
        hierarchy.add_class(class_b, [class_a])
        
        # Verify A is in cache initially
        assert class_a in hierarchy._mro_cache
        
        # Update A's bases - should invalidate
        hierarchy.update_bases(class_a, [])
        assert class_a not in hierarchy._mro_cache

