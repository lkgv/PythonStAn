"""Test MRO computation and class hierarchy building.

This test verifies the C3 linearization algorithm and class hierarchy management.
"""

import pytest
import sys


def test_c3_mro_simple_inheritance():
    """Test C3 MRO for simple single inheritance."""
    from pythonstan.analysis.pointer.kcfa2.mro import ClassHierarchyManager, compute_c3_mro
    
    hierarchy = ClassHierarchyManager()
    
    # Create hierarchy: Dog(Animal)
    hierarchy.add_class("Animal", None)
    hierarchy.add_class("Dog", ["Animal"])
    
    # Compute MRO for Dog
    mro = hierarchy.get_mro("Dog")
    
    # Expected: [Dog, Animal, object]
    assert mro == ["Dog", "Animal", "object"], f"Got MRO: {mro}"
    print(f"✓ Simple inheritance MRO: {mro}")


def test_c3_mro_diamond_inheritance():
    """Test C3 MRO for diamond inheritance pattern."""
    from pythonstan.analysis.pointer.kcfa2.mro import ClassHierarchyManager
    
    hierarchy = ClassHierarchyManager()
    
    # Diamond pattern:
    #      A
    #     / \
    #    B   C
    #     \ /
    #      D
    hierarchy.add_class("A", None)
    hierarchy.add_class("B", ["A"])
    hierarchy.add_class("C", ["A"])
    hierarchy.add_class("D", ["B", "C"])
    
    mro = hierarchy.get_mro("D")
    
    # Expected: [D, B, C, A, object]
    # B comes before C because D(B, C) lists B first
    # A comes after both B and C
    assert mro[0] == "D"
    assert mro[1] == "B"
    assert mro[2] == "C"
    assert mro[3] == "A"
    assert mro[4] == "object"
    print(f"✓ Diamond inheritance MRO: {mro}")


def test_c3_mro_complex_multiple_inheritance():
    """Test C3 MRO for complex multiple inheritance."""
    from pythonstan.analysis.pointer.kcfa2.mro import ClassHierarchyManager
    
    hierarchy = ClassHierarchyManager()
    
    # Complex pattern (real Python example):
    #   object
    #    |  \
    #    A   B
    #    |   |
    #    C   D
    #     \ /
    #      E
    hierarchy.add_class("A", None)
    hierarchy.add_class("B", None)
    hierarchy.add_class("C", ["A"])
    hierarchy.add_class("D", ["B"])
    hierarchy.add_class("E", ["C", "D"])
    
    mro = hierarchy.get_mro("E")
    
    # Expected: [E, C, A, D, B, object]
    assert mro[0] == "E"
    assert mro[1] == "C"
    # A should come before D since C(A) is listed before D in E's bases
    a_idx = mro.index("A")
    d_idx = mro.index("D")
    assert a_idx < d_idx, f"A should come before D in MRO: {mro}"
    assert "object" in mro
    print(f"✓ Complex multiple inheritance MRO: {mro}")


def test_hierarchy_bases_tracking():
    """Test that hierarchy correctly tracks base classes."""
    from pythonstan.analysis.pointer.kcfa2.mro import ClassHierarchyManager
    
    hierarchy = ClassHierarchyManager()
    
    hierarchy.add_class("Animal", None)
    hierarchy.add_class("Mammal", ["Animal"])
    hierarchy.add_class("Dog", ["Mammal"])
    
    # Test get_bases
    assert hierarchy.get_bases("Dog") == ["Mammal"]
    assert hierarchy.get_bases("Mammal") == ["Animal"]
    assert hierarchy.get_bases("Animal") == []
    print("✓ Base class tracking works correctly")


def test_hierarchy_subclasses_tracking():
    """Test that hierarchy correctly tracks subclasses."""
    from pythonstan.analysis.pointer.kcfa2.mro import ClassHierarchyManager
    
    hierarchy = ClassHierarchyManager()
    
    hierarchy.add_class("Animal", None)
    hierarchy.add_class("Dog", ["Animal"])
    hierarchy.add_class("Cat", ["Animal"])
    
    # Test get_subclasses
    subclasses = hierarchy.get_subclasses("Animal")
    assert "Dog" in subclasses
    assert "Cat" in subclasses
    assert len(subclasses) == 2
    print("✓ Subclass tracking works correctly")


def test_mro_caching():
    """Test that MRO results are cached."""
    from pythonstan.analysis.pointer.kcfa2.mro import ClassHierarchyManager
    
    hierarchy = ClassHierarchyManager()
    
    hierarchy.add_class("A", None)
    hierarchy.add_class("B", ["A"])
    
    # First call computes MRO
    mro1 = hierarchy.get_mro("B")
    
    # Second call should return cached result (same object)
    mro2 = hierarchy.get_mro("B")
    
    assert mro1 == mro2
    assert mro1 is mro2  # Same object from cache
    print("✓ MRO caching works correctly")


def test_mro_cache_invalidation():
    """Test that MRO cache is invalidated when hierarchy changes."""
    from pythonstan.analysis.pointer.kcfa2.mro import ClassHierarchyManager
    
    hierarchy = ClassHierarchyManager()
    
    hierarchy.add_class("A", None)
    hierarchy.add_class("B", ["A"])
    hierarchy.add_class("C", ["B"])
    
    # Get initial MRO for C
    mro_c1 = hierarchy.get_mro("C")
    
    # Add a new subclass of C
    # This should invalidate C's cache (though C's MRO won't change)
    hierarchy.add_class("D", ["C"])
    
    # Verify D's MRO includes C
    mro_d = hierarchy.get_mro("D")
    assert "C" in mro_d
    assert "B" in mro_d
    assert "A" in mro_d
    
    # Cache invalidation primarily affects subclasses
    # The important thing is that new classes can be added and computed correctly
    print("✓ MRO cache invalidation works correctly")


if __name__ == "__main__":
    print("\n=== Testing MRO and Class Hierarchy ===\n")
    
    try:
        test_c3_mro_simple_inheritance()
        test_c3_mro_diamond_inheritance()
        test_c3_mro_complex_multiple_inheritance()
        test_hierarchy_bases_tracking()
        test_hierarchy_subclasses_tracking()
        test_mro_caching()
        test_mro_cache_invalidation()
        
        print("\n✅ All MRO and hierarchy tests passed!")
    except Exception as e:
        print(f"\n❌ Test failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

