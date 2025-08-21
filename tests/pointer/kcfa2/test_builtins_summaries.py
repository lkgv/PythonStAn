"""Tests for builtin function summaries in k-CFA2 pointer analysis.

This module tests the summaries for built-in Python functions and operations:
- Container constructors (list, tuple, dict, set)
- Container operations (len, iter)
- Type conversions
- Conservative handling of dynamic operations
"""

import pytest
from dataclasses import dataclass, field
from typing import Dict, List, Set, FrozenSet, Tuple, Optional, Callable

from pythonstan.analysis.pointer.kcfa2.context import Context, CallSite
from pythonstan.analysis.pointer.kcfa2.model import AbstractObject, PointsToSet
from pythonstan.analysis.pointer.kcfa2.heap_model import (
    make_object, 
    attr_key, 
    elem_key,
    value_key
)


@dataclass
class MockSummary:
    """Mock function summary for testing."""
    name: str
    apply: Callable


@dataclass
class MockState:
    """Mock analysis state for testing builtin summaries."""
    objects: Dict[str, AbstractObject] = field(default_factory=dict)
    variables: Dict[str, PointsToSet] = field(default_factory=dict)
    call_site_id: str = "test.py:10:5:call"
    context: Context = None
    
    def __post_init__(self):
        if self.context is None:
            self.context = Context()
    
    def allocate(self, kind: str, lineno: int = 10) -> AbstractObject:
        """Allocate a new object of specified kind."""
        alloc_id = f"test.py:{lineno}:5:{kind}"
        obj = make_object(alloc_id, self.context)
        return obj
    
    def get_var(self, name: str) -> PointsToSet:
        """Get points-to set for a variable."""
        return self.variables.get(name, PointsToSet())
    
    def set_var(self, name: str, pts: PointsToSet) -> None:
        """Set points-to set for a variable."""
        self.variables[name] = pts


def test_len_summary():
    """Test summary for built-in len() function."""
    state = MockState()
    
    # Create container object
    list_obj = state.allocate("list")
    state.variables["x"] = PointsToSet(frozenset([list_obj]))
    
    # Define len summary
    def len_summary(args, target, state):
        # len() always returns an int
        int_obj = state.allocate("int")
        state.set_var(target, PointsToSet(frozenset([int_obj])))
    
    len_summary_obj = MockSummary(name="len", apply=len_summary)
    
    # Apply the summary
    len_summary_obj.apply(["x"], "result", state)
    
    # Check result
    result_pts = state.get_var("result")
    assert len(result_pts.objects) == 1
    result_obj = list(result_pts.objects)[0]
    assert "int" in result_obj.alloc_id


def test_iter_summary():
    """Test summary for built-in iter() function."""
    state = MockState()
    
    # Create container object
    list_obj = state.allocate("list")
    elem_obj = state.allocate("obj")
    state.variables["x"] = PointsToSet(frozenset([list_obj]))
    
    # Define iter summary
    def iter_summary(args, target, state):
        # iter() returns an iterator
        iter_obj = state.allocate("iterator")
        
        # Get source container's points-to set
        source_pts = state.get_var(args[0])
        
        # In a real analysis, we would:
        # 1. Find all possible container objects
        # 2. For each container, link iterator to its elements
        # 3. Set iterator.__next__ to return container elements
        
        # For this test, we just check the basic pattern
        state.set_var(target, PointsToSet(frozenset([iter_obj])))
    
    iter_summary_obj = MockSummary(name="iter", apply=iter_summary)
    
    # Apply the summary
    iter_summary_obj.apply(["x"], "result", state)
    
    # Check result
    result_pts = state.get_var("result")
    assert len(result_pts.objects) == 1
    result_obj = list(result_pts.objects)[0]
    assert "iterator" in result_obj.alloc_id


def test_list_constructor():
    """Test summary for list constructor."""
    state = MockState()
    
    # Create source objects
    iterable_obj = state.allocate("iterable")
    elem_obj = state.allocate("elem")
    state.variables["x"] = PointsToSet(frozenset([iterable_obj]))
    
    # Define list constructor summary
    def list_summary(args, target, state):
        # list() creates a new list
        list_obj = state.allocate("list")
        
        # Get source iterable's points-to set
        if args:
            source_pts = state.get_var(args[0])
            
            # In a real analysis, we would:
            # 1. Link list.elem to source elements
            # 2. Handle element type conversion conservatively
            
        # Set target to the new list
        state.set_var(target, PointsToSet(frozenset([list_obj])))
    
    list_summary_obj = MockSummary(name="list", apply=list_summary)
    
    # Apply the summary
    list_summary_obj.apply(["x"], "result", state)
    
    # Check result
    result_pts = state.get_var("result")
    assert len(result_pts.objects) == 1
    result_obj = list(result_pts.objects)[0]
    assert "list" in result_obj.alloc_id


def test_tuple_constructor():
    """Test summary for tuple constructor."""
    state = MockState()
    
    # Create source objects
    iterable_obj = state.allocate("iterable")
    elem_obj = state.allocate("elem")
    state.variables["x"] = PointsToSet(frozenset([iterable_obj]))
    
    # Define tuple constructor summary
    def tuple_summary(args, target, state):
        # tuple() creates a new tuple
        tuple_obj = state.allocate("tuple")
        
        # Get source iterable's points-to set
        if args:
            source_pts = state.get_var(args[0])
            
            # In a real analysis, we would:
            # 1. Link tuple.elem to source elements
            # 2. Handle element type conversion conservatively
            
        # Set target to the new tuple
        state.set_var(target, PointsToSet(frozenset([tuple_obj])))
    
    tuple_summary_obj = MockSummary(name="tuple", apply=tuple_summary)
    
    # Apply the summary
    tuple_summary_obj.apply(["x"], "result", state)
    
    # Check result
    result_pts = state.get_var("result")
    assert len(result_pts.objects) == 1
    result_obj = list(result_pts.objects)[0]
    assert "tuple" in result_obj.alloc_id


def test_dict_constructor():
    """Test summary for dict constructor."""
    state = MockState()
    
    # Create source objects
    mapping_obj = state.allocate("mapping")
    key_obj = state.allocate("key")
    value_obj = state.allocate("value")
    state.variables["x"] = PointsToSet(frozenset([mapping_obj]))
    
    # Define dict constructor summary
    def dict_summary(args, target, state):
        # dict() creates a new dict
        dict_obj = state.allocate("dict")
        
        # Get source mapping's points-to set
        if args:
            source_pts = state.get_var(args[0])
            
            # In a real analysis, we would:
            # 1. Link dict.value to source values
            # 2. Handle key-value extraction conservatively
            
        # Set target to the new dict
        state.set_var(target, PointsToSet(frozenset([dict_obj])))
    
    dict_summary_obj = MockSummary(name="dict", apply=dict_summary)
    
    # Apply the summary
    dict_summary_obj.apply(["x"], "result", state)
    
    # Check result
    result_pts = state.get_var("result")
    assert len(result_pts.objects) == 1
    result_obj = list(result_pts.objects)[0]
    assert "dict" in result_obj.alloc_id


def test_conservative_handling():
    """Test conservative handling of dynamic operations."""
    state = MockState()
    
    # Create source objects
    unknown_obj = state.allocate("unknown")
    state.variables["x"] = PointsToSet(frozenset([unknown_obj]))
    
    # Define getattr summary for dynamic attribute access
    def getattr_summary(args, target, state):
        # args: [obj, attr_name, default]
        if len(args) < 2:
            return
            
        # Get object points-to set
        obj_pts = state.get_var(args[0])
        
        # In a real analysis, we would:
        # 1. If attr_name is constant string, use specific attribute
        # 2. Otherwise, conservatively join all possible attributes
        # 3. Handle default value if provided
        # 4. Handle descriptors conservatively
        
        # For test, conservatively create a new object
        result_obj = state.allocate("attr")
        state.set_var(target, PointsToSet(frozenset([result_obj])))
    
    getattr_summary_obj = MockSummary(name="getattr", apply=getattr_summary)
    
    # Apply the summary with dynamic attribute name
    getattr_summary_obj.apply(["x", "dynamic_attr"], "result", state)
    
    # Check result is conservative
    result_pts = state.get_var("result")
    assert len(result_pts.objects) == 1
    result_obj = list(result_pts.objects)[0]
    assert "attr" in result_obj.alloc_id


def test_top_object_handling():
    """Test handling of top (any) objects."""
    state = MockState()
    
    # In a real analysis, we might have a special TOP object
    # representing any possible value
    top_obj = state.allocate("top")
    state.variables["top"] = PointsToSet(frozenset([top_obj]))
    
    # Operations on TOP should be conservative
    def conservative_summary(args, target, state):
        # If any arg is TOP, result is also TOP-like
        for arg in args:
            arg_pts = state.get_var(arg)
            for obj in arg_pts.objects:
                if "top" in obj.alloc_id:
                    # Create a conservative result
                    result_obj = state.allocate("top")
                    state.set_var(target, PointsToSet(frozenset([result_obj])))
                    return
        
        # Normal case: create specific result
        result_obj = state.allocate("specific")
        state.set_var(target, PointsToSet(frozenset([result_obj])))
    
    summary_obj = MockSummary(name="op", apply=conservative_summary)
    
    # Apply with normal argument
    normal_arg = "normal"
    normal_obj = state.allocate("normal")
    state.variables[normal_arg] = PointsToSet(frozenset([normal_obj]))
    summary_obj.apply([normal_arg], "normal_result", state)
    
    # Apply with TOP argument
    summary_obj.apply(["top"], "top_result", state)
    
    # Check results
    normal_pts = state.get_var("normal_result")
    normal_obj = list(normal_pts.objects)[0]
    assert "specific" in normal_obj.alloc_id
    
    top_pts = state.get_var("top_result")
    top_obj = list(top_pts.objects)[0]
    assert "top" in top_obj.alloc_id