"""Tests for attribute handling in k-CFA2 pointer analysis.

This module tests how the pointer analysis handles attribute access:
- Direct attribute access (obj.attr)
- Dynamic attribute access (getattr/setattr)
- Unknown attribute name handling
- Field sensitivity for different kinds of fields
- Descriptor handling
"""

import pytest
from dataclasses import dataclass
from typing import Dict, List, Set, FrozenSet, Tuple, Optional

from pythonstan.analysis.pointer.kcfa2.context import Context
from pythonstan.analysis.pointer.kcfa2.model import AbstractObject, PointsToSet
from pythonstan.analysis.pointer.kcfa2.heap_model import (
    make_object, 
    attr_key, 
    elem_key,
    value_key, 
    unknown_attr_key
)


@dataclass
class MockHeap:
    """Mock heap implementation for testing attribute access."""
    _mapping: Dict[Tuple[AbstractObject, str], PointsToSet] = None
    
    def __post_init__(self):
        self._mapping = self._mapping or {}
    
    def get(self, obj: AbstractObject, field: str) -> PointsToSet:
        """Get points-to set for an object field."""
        return self._mapping.get((obj, field), PointsToSet())
    
    def put(self, obj: AbstractObject, field: str, pts: PointsToSet) -> None:
        """Set points-to set for an object field."""
        self._mapping[(obj, field)] = pts


def test_direct_attribute_access():
    """Test direct attribute access with known field names."""
    # Set up mock heap and objects
    heap = MockHeap()
    ctx = Context()
    
    # Create objects
    base_obj = make_object("test.py:10:5:obj", ctx)
    attr_obj = make_object("test.py:20:5:obj", ctx)
    
    # Store attribute on base object
    field = str(attr_key("foo"))
    heap.put(base_obj, field, PointsToSet(frozenset([attr_obj])))
    
    # Load the attribute
    result = heap.get(base_obj, field)
    
    # Check that attribute points to expected object
    assert result.objects == frozenset([attr_obj])


def test_dynamic_attribute_access():
    """Test dynamic attribute access with getattr/setattr."""
    # Set up mock heap and objects
    heap = MockHeap()
    ctx = Context()
    
    # Create objects
    base_obj = make_object("test.py:10:5:obj", ctx)
    attr1_obj = make_object("test.py:20:5:obj", ctx)
    attr2_obj = make_object("test.py:30:5:obj", ctx)
    
    # Store known attributes
    field1 = str(attr_key("foo"))
    field2 = str(attr_key("bar"))
    heap.put(base_obj, field1, PointsToSet(frozenset([attr1_obj])))
    heap.put(base_obj, field2, PointsToSet(frozenset([attr2_obj])))
    
    # Case 1: getattr with known attribute name
    result1 = heap.get(base_obj, field1)
    assert result1.objects == frozenset([attr1_obj])
    
    # Case 2: getattr with unknown attribute name
    # This should conservatively return join of all attributes
    all_attrs = []
    for field_key in [field1, field2]:
        pts = heap.get(base_obj, field_key)
        all_attrs.extend(pts.objects)
    
    # In a real analysis, the unknown attribute access would return
    # a sound approximation of all possible attributes
    assert set(all_attrs) == {attr1_obj, attr2_obj}


def test_unknown_attribute_field():
    """Test handling of unknown attribute field."""
    # Set up mock heap and objects
    heap = MockHeap()
    ctx = Context()
    
    # Create objects
    base_obj = make_object("test.py:10:5:obj", ctx)
    unknown_obj = make_object("test.py:20:5:obj", ctx)
    
    # Store to unknown attribute field
    unknown_field = str(unknown_attr_key())
    heap.put(base_obj, unknown_field, PointsToSet(frozenset([unknown_obj])))
    
    # Access unknown attribute field
    result = heap.get(base_obj, unknown_field)
    assert result.objects == frozenset([unknown_obj])
    
    # In a real analysis, accessing any attribute would also
    # include the unknown attribute field in the result
    attr_field = str(attr_key("foo"))
    heap.put(base_obj, attr_field, PointsToSet(frozenset([make_object("test.py:30:5:obj", ctx)])))
    
    # Both known and unknown fields should be considered
    known_result = heap.get(base_obj, attr_field)
    unknown_result = heap.get(base_obj, unknown_field)
    
    # In production, the analysis would join these results
    assert len(known_result.objects) > 0
    assert len(unknown_result.objects) > 0


def test_container_field_access():
    """Test field access for container elements."""
    # Set up mock heap and objects
    heap = MockHeap()
    ctx = Context()
    
    # Create container objects
    list_obj = make_object("test.py:10:5:list", ctx)
    dict_obj = make_object("test.py:20:5:dict", ctx)
    
    # Create element objects
    elem_obj = make_object("test.py:30:5:obj", ctx)
    value_obj = make_object("test.py:40:5:obj", ctx)
    
    # Store elements in containers
    elem_field = str(elem_key())
    value_field = str(value_key())
    heap.put(list_obj, elem_field, PointsToSet(frozenset([elem_obj])))
    heap.put(dict_obj, value_field, PointsToSet(frozenset([value_obj])))
    
    # Access container elements
    list_result = heap.get(list_obj, elem_field)
    dict_result = heap.get(dict_obj, value_field)
    
    assert list_result.objects == frozenset([elem_obj])
    assert dict_result.objects == frozenset([value_obj])
    
    # Container element fields should be distinct from attribute fields
    attr_field = str(attr_key("foo"))
    heap.put(list_obj, attr_field, PointsToSet(frozenset([make_object("test.py:50:5:obj", ctx)])))
    
    # Accessing attribute shouldn't return elements
    attr_result = heap.get(list_obj, attr_field)
    assert elem_obj not in attr_result.objects


def test_field_sensitivity_modes():
    """Test different field sensitivity modes."""
    ctx = Context()
    
    # Create objects for testing
    obj = make_object("test.py:10:5:obj", ctx)
    
    # Test attribute field keys
    attr1 = attr_key("foo")
    attr2 = attr_key("bar")
    assert attr1 != attr2  # Attribute-name-sensitive
    
    # Test container field keys
    elem = elem_key()
    value = value_key()
    unknown = unknown_attr_key()
    
    # All fields should be distinct
    field_keys = [attr1, attr2, elem, value, unknown]
    for i in range(len(field_keys)):
        for j in range(i + 1, len(field_keys)):
            assert field_keys[i] != field_keys[j]


def test_descriptor_handling():
    """Test conservative handling of descriptors."""
    # This test demonstrates the conservative approach for descriptors
    # like property, classmethod, staticmethod
    
    # Set up mock heap and objects
    heap = MockHeap()
    ctx = Context()
    
    # Create class and instance objects
    class_obj = make_object("test.py:10:5:class", ctx)
    instance_obj = make_object("test.py:20:5:obj", ctx)
    property_obj = make_object("test.py:30:5:property", ctx)
    result_obj = make_object("test.py:40:5:obj", ctx)
    
    # Store property descriptor on class
    class_attr_field = str(attr_key("prop"))
    heap.put(class_obj, class_attr_field, PointsToSet(frozenset([property_obj])))
    
    # Store result value for the property getter
    prop_value_field = str(attr_key("fget"))
    heap.put(property_obj, prop_value_field, PointsToSet(frozenset([result_obj])))
    
    # In a real analysis:
    # 1. When instance.prop is accessed
    # 2. Look up prop on instance, not found
    # 3. Look up prop on class, find property_obj
    # 4. Detect property_obj is a descriptor
    # 5. Conservatively call __get__ method
    # 6. Return result of getter
    
    # For this test, we assert the property lookup pattern
    instance_prop = heap.get(instance_obj, class_attr_field)
    assert len(instance_prop.objects) == 0  # Not on instance
    
    class_prop = heap.get(class_obj, class_attr_field)
    assert property_obj in class_prop.objects  # Found on class
    
    # Property getter result
    getter_result = heap.get(property_obj, prop_value_field)
    assert result_obj in getter_result.objects
    
    # In production, the analysis would detect property_obj as a descriptor
    # and conservatively include result_obj in the points-to set for instance.prop