"""Tests for heap model in k-CFA2 pointer analysis.

This module tests the heap abstraction used in the k-CFA pointer analysis,
focusing on allocation-site abstraction, context sensitivity, and field addressing.
"""

import pytest
from pythonstan.analysis.pointer.kcfa2.context import Context, CallSite
from pythonstan.analysis.pointer.kcfa2.model import AbstractObject, FieldKey
from pythonstan.analysis.pointer.kcfa2.heap_model import (
    make_object,
    attr_key,
    elem_key,
    value_key,
    unknown_attr_key,
    compute_recv_context_fingerprint,
    format_alloc_id,
    format_call_id,
    format_fallback_id
)


def test_allocation_site_abstraction():
    """Test allocation site abstraction principles."""
    ctx = Context()
    
    # Different allocation sites should produce different objects
    obj1 = make_object("test.py:10:5:obj", ctx)
    obj2 = make_object("test.py:20:5:obj", ctx)
    assert obj1 != obj2
    
    # Same allocation site should produce same object
    obj3 = make_object("test.py:10:5:obj", ctx)
    assert obj1 == obj3


def test_allocation_context_sensitivity():
    """Test that allocation context affects object identity."""
    # Create different contexts
    ctx1 = Context()
    cs1 = CallSite(site_id="test.py:5:5:call", fn="caller1")
    ctx2 = Context((cs1,))
    
    # Same allocation site with different contexts
    alloc_id = "test.py:10:5:obj"
    obj1 = make_object(alloc_id, ctx1)
    obj2 = make_object(alloc_id, ctx2)
    
    # Should produce different objects
    assert obj1 != obj2


def test_receiver_context_sensitivity():
    """Test receiver context sensitivity (2-object sensitivity)."""
    # Create base objects with different allocation sites
    ctx = Context()
    base_obj1 = make_object("test.py:10:5:obj", ctx)
    base_obj2 = make_object("test.py:20:5:obj", ctx)
    
    # Create new object with receiver context fingerprints
    alloc_id = "test.py:30:5:obj"
    obj1 = make_object(alloc_id, ctx, recv_obj_ctx=(base_obj1,))
    obj2 = make_object(alloc_id, ctx, recv_obj_ctx=(base_obj2,))
    
    # Different receiver contexts should produce different objects
    assert obj1 != obj2
    
    # Same receiver context should produce same object
    obj3 = make_object(alloc_id, ctx, recv_obj_ctx=(base_obj1,))
    assert obj1 == obj3


def test_receiver_context_depth_limit():
    """Test that object sensitivity respects depth limit."""
    ctx = Context()
    
    # Create a chain of objects for receiver context
    obj1 = make_object("test.py:10:5:obj", ctx)
    obj2 = make_object("test.py:20:5:obj", ctx)
    obj3 = make_object("test.py:30:5:obj", ctx)
    
    # Create objects with different depth limits
    alloc_id = "test.py:40:5:obj"
    chain = (obj1, obj2, obj3)
    
    # With depth=1, only the most recent receiver matters
    obj_depth1 = make_object(alloc_id, ctx, recv_obj_ctx=chain, depth=1)
    obj_depth1_alt = make_object(alloc_id, ctx, recv_obj_ctx=(obj3,), depth=1)
    assert obj_depth1 == obj_depth1_alt
    
    # With depth=2, the two most recent receivers matter
    obj_depth2 = make_object(alloc_id, ctx, recv_obj_ctx=chain, depth=2)
    obj_depth2_alt = make_object(alloc_id, ctx, recv_obj_ctx=(obj2, obj3), depth=2)
    assert obj_depth2 == obj_depth2_alt
    
    # Different depths produce different objects
    assert obj_depth1 != obj_depth2


def test_compute_recv_context_fingerprint():
    """Test computation of receiver context fingerprint."""
    ctx = Context()
    obj1 = AbstractObject(alloc_id="test.py:10:5:obj", alloc_ctx=ctx)
    obj2 = AbstractObject(alloc_id="test.py:20:5:obj", alloc_ctx=ctx)
    
    # Empty receiver context
    fingerprint1 = compute_recv_context_fingerprint((), 2)
    assert fingerprint1 == ()
    
    # Single receiver with depth=1
    fingerprint2 = compute_recv_context_fingerprint((obj1,), 1)
    assert len(fingerprint2) == 1
    assert fingerprint2[0][0] == obj1.alloc_id
    
    # Multiple receivers with depth=1 (takes only most recent)
    fingerprint3 = compute_recv_context_fingerprint((obj1, obj2), 1)
    assert len(fingerprint3) == 1
    assert fingerprint3[0][0] == obj2.alloc_id
    
    # Multiple receivers with depth=2 (takes two most recent)
    fingerprint4 = compute_recv_context_fingerprint((obj1, obj2), 2)
    assert len(fingerprint4) == 2
    assert fingerprint4[0][0] == obj1.alloc_id
    assert fingerprint4[1][0] == obj2.alloc_id
    
    # Zero depth
    fingerprint5 = compute_recv_context_fingerprint((obj1, obj2), 0)
    assert fingerprint5 == ()


def test_field_addressing():
    """Test field addressing through field keys."""
    # Test attribute keys
    key1 = attr_key("foo")
    key2 = attr_key("foo")
    key3 = attr_key("bar")
    
    # Same attribute name should produce equal keys
    assert key1 == key2
    assert hash(key1) == hash(key2)
    
    # Different attribute names should produce different keys
    assert key1 != key3
    assert hash(key1) != hash(key3)
    
    # Container element keys should be distinct from attribute keys
    elem = elem_key()
    assert key1 != elem
    assert hash(key1) != hash(elem)
    
    # Dictionary value keys should be distinct
    value = value_key()
    assert key1 != value
    assert elem != value
    assert hash(key1) != hash(value)
    assert hash(elem) != hash(value)
    
    # Unknown attribute keys
    unknown = unknown_attr_key()
    assert key1 != unknown
    assert elem != unknown
    assert value != unknown
    assert hash(key1) != hash(unknown)
    assert hash(elem) != hash(unknown)
    assert hash(value) != hash(unknown)


def test_string_representations():
    """Test string representations of field keys."""
    # Attribute key
    assert str(attr_key("foo")) == ".foo"
    
    # Element key
    assert str(elem_key()) == ".elem"
    
    # Value key
    assert str(value_key()) == ".value"
    
    # Unknown key
    assert str(unknown_attr_key()) == ".?"


def test_allocation_id_formats():
    """Test allocation site ID formatting."""
    # Object allocation site
    alloc_id = format_alloc_id("test.py", 42, 10, "obj")
    assert alloc_id == "test.py:42:10:obj"
    
    # Call site
    call_id = format_call_id("test.py", 42, 10)
    assert call_id == "test.py:42:10:call"
    
    # Fallback ID
    fallback_id = format_fallback_id("test", "alloc", 0x12345678)
    assert fallback_id == "test:alloc:12345678"