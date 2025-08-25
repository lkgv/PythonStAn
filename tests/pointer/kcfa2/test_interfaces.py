"""Tests for k-CFA2 pointer analysis interfaces.

This module tests that all the required modules and classes are available
and that the core data structures satisfy equality and hash invariants.
"""

import pytest
from pythonstan.analysis.pointer.kcfa2 import (
    analysis,
    context,
    heap_model,
    model,
    config,
    errors,
    ir_adapter,
    callgraph_adapter,
    worklist,
    summaries
)
from pythonstan.analysis.pointer.kcfa2.model import (
    AbstractLocation,
    FieldKey,
    AbstractObject,
    PointsToSet
)
from pythonstan.analysis.pointer.kcfa2.context import (
    CallSite,
    Context,
    ContextSelector,
    ContextManager
)
from pythonstan.analysis.pointer.kcfa2.heap_model import (
    make_object,
    attr_key,
    elem_key,
    value_key,
    unknown_attr_key
)


def test_imports():
    """Test that all modules can be imported."""
    assert hasattr(analysis, "KCFA2PointerAnalysis")
    assert hasattr(context, "Context")
    assert hasattr(heap_model, "make_object")
    assert hasattr(model, "AbstractObject")
    assert hasattr(config, "KCFAConfig")
    assert hasattr(errors, "KCFAError")
    

def test_abstract_location_equality():
    """Test equality and hash invariants for AbstractLocation."""
    fn_name = "test_function"
    var_name = "x"
    ctx1 = Context()
    ctx2 = Context()
    
    # Same values should be equal and have same hash
    loc1 = AbstractLocation(fn=fn_name, name=var_name, ctx=ctx1)
    loc2 = AbstractLocation(fn=fn_name, name=var_name, ctx=ctx2)
    assert loc1 == loc2
    assert hash(loc1) == hash(loc2)
    
    # Different function name
    loc3 = AbstractLocation(fn="another_function", name=var_name, ctx=ctx1)
    assert loc1 != loc3
    assert hash(loc1) != hash(loc3)
    
    # Different variable name
    loc4 = AbstractLocation(fn=fn_name, name="y", ctx=ctx1)
    assert loc1 != loc4
    assert hash(loc1) != hash(loc4)
    
    # Different context
    call_site = CallSite(site_id="test.py:10:5:call", fn=fn_name)
    ctx3 = Context((call_site,))
    loc5 = AbstractLocation(fn=fn_name, name=var_name, ctx=ctx3)
    assert loc1 != loc5
    assert hash(loc1) != hash(loc5)


def test_abstract_object_equality():
    """Test equality and hash invariants for AbstractObject."""
    alloc_id1 = "test.py:10:5:obj"
    alloc_id2 = "test.py:20:5:obj"
    ctx1 = Context()
    ctx2 = Context((CallSite(site_id="test.py:5:5:call", fn="caller"),))
    
    # Same values should be equal and have same hash
    obj1 = AbstractObject(alloc_id=alloc_id1, alloc_ctx=ctx1)
    obj2 = AbstractObject(alloc_id=alloc_id1, alloc_ctx=ctx1)
    assert obj1 == obj2
    assert hash(obj1) == hash(obj2)
    
    # Different allocation site
    obj3 = AbstractObject(alloc_id=alloc_id2, alloc_ctx=ctx1)
    assert obj1 != obj3
    assert hash(obj1) != hash(obj3)
    
    # Different context
    obj4 = AbstractObject(alloc_id=alloc_id1, alloc_ctx=ctx2)
    assert obj1 != obj4
    assert hash(obj1) != hash(obj4)
    
    # Different receiver context fingerprint
    obj5 = AbstractObject(alloc_id=alloc_id1, alloc_ctx=ctx1, recv_ctx_fingerprint=("x", "y"))
    obj6 = AbstractObject(alloc_id=alloc_id1, alloc_ctx=ctx1, recv_ctx_fingerprint=("a", "b"))
    assert obj5 != obj6
    assert hash(obj5) != hash(obj6)


def test_context_equality():
    """Test equality and hash invariants for Context."""
    # Empty contexts
    ctx1 = Context()
    ctx2 = Context()
    assert ctx1 == ctx2
    assert hash(ctx1) == hash(ctx2)
    
    # Same call string
    cs1 = CallSite(site_id="test.py:10:5:call", fn="test_function")
    cs2 = CallSite(site_id="test.py:10:5:call", fn="test_function")
    ctx3 = Context((cs1,))
    ctx4 = Context((cs2,))
    assert ctx3 == ctx4
    assert hash(ctx3) == hash(ctx4)
    
    # Different call string
    cs3 = CallSite(site_id="test.py:20:5:call", fn="other_function")
    ctx5 = Context((cs3,))
    assert ctx3 != ctx5
    assert hash(ctx3) != hash(ctx5)


def test_field_key_uniqueness():
    """Test that different field keys are unique."""
    # Attribute keys
    attr1 = attr_key("foo")
    attr2 = attr_key("foo")
    attr3 = attr_key("bar")
    
    # Same attribute names should be equal
    assert attr1 == attr2
    assert hash(attr1) == hash(attr2)
    
    # Different attribute names should be different
    assert attr1 != attr3
    assert hash(attr1) != hash(attr3)
    
    # Container access keys should be distinct
    elem = elem_key()
    value = value_key()
    unknown = unknown_attr_key()
    
    assert elem != value
    assert elem != unknown
    assert value != unknown
    assert hash(elem) != hash(value)
    assert hash(elem) != hash(unknown)
    assert hash(value) != hash(unknown)
    
    # Attribute and container keys should be distinct
    assert attr1 != elem
    assert attr1 != value
    assert attr1 != unknown
    assert hash(attr1) != hash(elem)
    assert hash(attr1) != hash(value)
    assert hash(attr1) != hash(unknown)


def test_field_key_validation():
    """Test field key validation rules."""
    # Named attributes require name
    with pytest.raises(ValueError):
        FieldKey(kind="attr", name=None)
    
    # Container elements should not have name
    with pytest.raises(ValueError):
        FieldKey(kind="elem", name="should_not_have_name")
    
    # Dictionary values should not have name
    with pytest.raises(ValueError):
        FieldKey(kind="value", name="should_not_have_name")


def test_points_to_set_operations():
    """Test points-to set lattice operations."""
    obj1 = AbstractObject(alloc_id="test.py:10:5:obj", alloc_ctx=Context())
    obj2 = AbstractObject(alloc_id="test.py:20:5:obj", alloc_ctx=Context())
    
    # Empty set
    pts1 = PointsToSet()
    assert pts1.is_empty()
    assert len(pts1) == 0
    
    # Single object
    pts2 = PointsToSet(frozenset([obj1]))
    assert not pts2.is_empty()
    assert len(pts2) == 1
    assert obj1 in pts2
    assert obj2 not in pts2
    
    # Join operation
    pts3 = PointsToSet(frozenset([obj2]))
    pts4 = pts2.join(pts3)
    assert len(pts4) == 2
    assert obj1 in pts4
    assert obj2 in pts4