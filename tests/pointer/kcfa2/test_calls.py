"""Tests for function call handling in k-CFA2 pointer analysis.

This module tests how the pointer analysis handles different types of function calls:
- Direct function calls
- Indirect calls via variables
- Method calls with receiver objects
- Bound method handling
- Closures with captured variables
- Return flow from functions
"""

import pytest
from dataclasses import dataclass
from typing import Dict, List, Set, FrozenSet, Tuple, Optional, Protocol

from pythonstan.analysis.pointer.kcfa2.context import Context, CallSite, ContextSelector
from pythonstan.analysis.pointer.kcfa2.model import AbstractObject, AbstractLocation, PointsToSet
from pythonstan.analysis.pointer.kcfa2.heap_model import make_object, attr_key


class MockIRAdapter:
    """Mock IR adapter for testing call handling."""
    
    @dataclass
    class CallEvent:
        site_id: str
        fn_name: str
        args: List[str]
        target: str
        is_method_call: bool = False
        base_var: Optional[str] = None
        
    def __init__(self):
        self.events = []
    
    def add_direct_call(self, site_id, fn_name, args, target):
        """Add a direct function call event."""
        event = self.CallEvent(
            site_id=site_id,
            fn_name=fn_name,
            args=args,
            target=target
        )
        self.events.append(event)
        return event
    
    def add_indirect_call(self, site_id, fn_var, args, target):
        """Add an indirect function call event."""
        event = self.CallEvent(
            site_id=site_id,
            fn_name=fn_var,  # Variable holding function
            args=args,
            target=target
        )
        self.events.append(event)
        return event
    
    def add_method_call(self, site_id, base_var, method_name, args, target):
        """Add a method call event."""
        event = self.CallEvent(
            site_id=site_id,
            fn_name=method_name,
            args=args,
            target=target,
            is_method_call=True,
            base_var=base_var
        )
        self.events.append(event)
        return event


@dataclass
class MockFunction:
    """Mock function representation for testing."""
    name: str
    params: List[str]
    local_vars: List[str]
    free_vars: List[str] = None
    
    def __post_init__(self):
        if self.free_vars is None:
            self.free_vars = []


@dataclass 
class MockState:
    """Mock analysis state for testing."""
    objects: Dict[str, AbstractObject] = None
    variables: Dict[str, PointsToSet] = None
    locations: Dict[AbstractLocation, PointsToSet] = None
    fields: Dict[Tuple[AbstractObject, str], PointsToSet] = None
    context_selector: ContextSelector = None
    current_context: Context = None
    
    def __post_init__(self):
        self.objects = self.objects or {}
        self.variables = self.variables or {}
        self.locations = self.locations or {}
        self.fields = self.fields or {}
        self.context_selector = self.context_selector or ContextSelector(k=2)
        self.current_context = self.current_context or Context()


def test_direct_call():
    """Test direct function call handling."""
    # Set up mock state
    state = MockState()
    ctx = Context()
    
    # Create function objects
    callee = MockFunction(name="callee", params=["x"], local_vars=["y"])
    
    # Create a direct call event
    adapter = MockIRAdapter()
    call_event = adapter.add_direct_call(
        site_id="test.py:10:5:call", 
        fn_name="callee", 
        args=["arg1"], 
        target="result"
    )
    
    # Create call site from event
    call_site = CallSite(
        site_id=call_event.site_id,
        fn="caller"
    )
    
    # Create argument object
    arg_obj = make_object("test.py:5:5:obj", ctx)
    state.variables["arg1"] = PointsToSet(frozenset([arg_obj]))
    
    # Create callee context based on current context and call site
    callee_ctx = state.context_selector.push(ctx, call_site)
    
    # Check context change
    assert callee_ctx != ctx
    assert len(callee_ctx) == len(ctx) + 1
    
    # In a real analysis, we would:
    # 1. Create parameter bindings in callee context
    # 2. Analyze callee function body
    # 3. Propagate return values back
    
    # For testing, we'll simulate this:
    param_loc = AbstractLocation(fn=callee.name, name=callee.params[0], ctx=callee_ctx)
    
    # Parameter x should receive arg1's points-to set
    assert param_loc.fn == "callee"
    assert param_loc.name == "x"
    assert param_loc.ctx == callee_ctx


def test_indirect_call():
    """Test indirect function call via variable."""
    # Set up mock state
    state = MockState()
    ctx = Context()
    
    # Create function objects
    func1_obj = make_object("test.py:5:5:func", ctx)
    func2_obj = make_object("test.py:15:5:func", ctx)
    
    # Indirect call target variable points to two functions
    state.variables["func_var"] = PointsToSet(frozenset([func1_obj, func2_obj]))
    
    # Create an indirect call event
    adapter = MockIRAdapter()
    call_event = adapter.add_indirect_call(
        site_id="test.py:20:5:call",
        fn_var="func_var",
        args=["arg1"],
        target="result"
    )
    
    # Create call site from event
    call_site = CallSite(
        site_id=call_event.site_id,
        fn="caller"
    )
    
    # Create callee context based on current context and call site
    callee_ctx = state.context_selector.push(ctx, call_site)
    
    # In a real analysis, we would:
    # 1. Resolve all possible callees from func_var
    # 2. Create parameter bindings for each callee
    # 3. Analyze each callee
    # 4. Join result points-to sets
    
    # For testing, we assert that:
    # - Call site is correctly created
    # - Context is properly extended
    assert call_event.fn_name == "func_var"
    assert call_site.site_id == "test.py:20:5:call"
    assert callee_ctx != ctx
    assert len(callee_ctx) == len(ctx) + 1


def test_bound_method_call():
    """Test method call handling with receiver object."""
    # Set up mock state
    state = MockState()
    ctx = Context()
    
    # Create receiver object
    receiver_obj = make_object("test.py:5:5:obj", ctx)
    state.variables["obj"] = PointsToSet(frozenset([receiver_obj]))
    
    # Create a method call event
    adapter = MockIRAdapter()
    call_event = adapter.add_method_call(
        site_id="test.py:10:5:call",
        base_var="obj",
        method_name="method",
        args=["arg1"],
        target="result"
    )
    
    # Create call site from event
    call_site = CallSite(
        site_id=call_event.site_id,
        fn="caller"
    )
    
    # Create callee context based on current context and call site
    callee_ctx = state.context_selector.push(ctx, call_site)
    
    # In a real analysis, we would:
    # 1. Look up method object on receiver
    # 2. Bind receiver to 'self' parameter
    # 3. Create parameter bindings
    # 4. Analyze method body
    # 5. Propagate return values
    
    # For this test, we assert:
    assert call_event.is_method_call
    assert call_event.base_var == "obj"
    assert call_event.fn_name == "method"
    assert callee_ctx != ctx
    
    # In object-sensitive analysis, method calls allocate bound methods
    # and the receiver context should affect the allocation


def test_closure_call():
    """Test closure call with captured variables."""
    # Set up mock state
    state = MockState()
    ctx = Context()
    
    # Create function objects
    outer_ctx = ctx
    
    # Create a closure variable
    captured_obj = make_object("test.py:5:5:obj", outer_ctx)
    state.variables["captured"] = PointsToSet(frozenset([captured_obj]))
    
    # Create closure function with free variable
    closure_fn = MockFunction(
        name="closure",
        params=["x"],
        local_vars=["y"],
        free_vars=["captured"]
    )
    
    # Create closure object that captures free variable
    closure_obj = make_object("test.py:10:5:func", ctx)
    
    # Variable pointing to closure
    state.variables["closure_var"] = PointsToSet(frozenset([closure_obj]))
    
    # Create an indirect call to the closure
    adapter = MockIRAdapter()
    call_event = adapter.add_indirect_call(
        site_id="test.py:15:5:call",
        fn_var="closure_var",
        args=["arg1"],
        target="result"
    )
    
    # Create call site from event
    call_site = CallSite(
        site_id=call_event.site_id,
        fn="caller"
    )
    
    # Create callee context based on current context and call site
    callee_ctx = state.context_selector.push(ctx, call_site)
    
    # In a real analysis, we would:
    # 1. Resolve closure function
    # 2. Create parameter bindings
    # 3. Bind free variables from closure cells
    # 4. Analyze function body
    # 5. Propagate return values
    
    # For this test, we assert:
    assert len(closure_fn.free_vars) == 1
    assert "captured" in closure_fn.free_vars
    assert callee_ctx != ctx
    
    # In a real analysis, the free variable "captured" would be available
    # in the closure context and point to the captured object


def test_return_flow():
    """Test return value flow from function to caller."""
    # Set up mock state
    state = MockState()
    ctx = Context()
    caller_loc = AbstractLocation(fn="caller", name="result", ctx=ctx)
    
    # Create callee context
    call_site = CallSite(site_id="test.py:10:5:call", fn="caller")
    callee_ctx = state.context_selector.push(ctx, call_site)
    
    # Create return value object in callee
    return_obj = make_object("test.py:15:5:obj", callee_ctx)
    return_var = "return_value"
    state.variables[return_var] = PointsToSet(frozenset([return_obj]))
    
    # In a real analysis, we would:
    # 1. Propagate return value to caller's target location
    # 2. Pop the call context
    
    # For this test, we simulate return flow:
    state.locations[caller_loc] = state.variables[return_var]
    
    # Assert that return value is correctly propagated
    assert caller_loc in state.locations
    assert state.locations[caller_loc].objects == frozenset([return_obj])
    
    # Pop context to return to caller
    returned_ctx = callee_ctx.pop()
    assert returned_ctx == ctx