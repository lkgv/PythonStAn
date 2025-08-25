"""Tests for k-CFA context management.

This module tests context management functionality in the k-CFA pointer analysis,
including context creation, truncation, and manipulation for different k values.
"""

import pytest
from pythonstan.analysis.pointer.kcfa2.context import Context, ContextSelector, ContextManager, CallSite


def test_empty_context():
    """Test empty context properties."""
    ctx = Context()
    assert ctx.is_empty()
    assert len(ctx) == 0
    assert str(ctx) == "[]"


def test_call_site_string_representation():
    """Test call site string representation."""
    # Call site with basic block
    cs1 = CallSite(
        site_id="test.py:10:5:call",
        fn="test_function",
        bb="block1",
        idx=2
    )
    assert str(cs1) == "test.py:10:5:call:block1#2"
    
    # Call site without basic block
    cs2 = CallSite(
        site_id="test.py:20:5:call",
        fn="other_function",
        bb=None,
        idx=1
    )
    assert str(cs2) == "test.py:20:5:call#1"


def test_context_string_representation():
    """Test context string representation."""
    cs1 = CallSite(site_id="test.py:10:5:call", fn="func1")
    cs2 = CallSite(site_id="test.py:20:5:call", fn="func2")
    
    # Empty context
    ctx1 = Context()
    assert str(ctx1) == "[]"
    
    # Single call site
    ctx2 = Context((cs1,))
    assert str(ctx2) == f"[{str(cs1)}]"
    
    # Multiple call sites
    ctx3 = Context((cs1, cs2))
    assert str(ctx3) == f"[{str(cs1)} → {str(cs2)}]"


def test_context_push_with_k1(make_call_site):
    """Test context pushing with k=1."""
    k = 1
    ctx = Context()
    
    # First push
    cs1 = make_call_site("test.py", 10, 5, "func1")
    ctx1 = ctx.push(cs1, k)
    assert len(ctx1) == 1
    assert ctx1.call_string == (cs1,)
    
    # Second push should replace the first (k=1)
    cs2 = make_call_site("test.py", 20, 5, "func2")
    ctx2 = ctx1.push(cs2, k)
    assert len(ctx2) == 1
    assert ctx2.call_string == (cs2,)
    
    # Third push should replace the second (k=1)
    cs3 = make_call_site("test.py", 30, 5, "func3")
    ctx3 = ctx2.push(cs3, k)
    assert len(ctx3) == 1
    assert ctx3.call_string == (cs3,)


def test_context_push_with_k2(make_call_site):
    """Test context pushing with k=2."""
    k = 2
    ctx = Context()
    
    # First push
    cs1 = make_call_site("test.py", 10, 5, "func1")
    ctx1 = ctx.push(cs1, k)
    assert len(ctx1) == 1
    assert ctx1.call_string == (cs1,)
    
    # Second push should keep both (k=2)
    cs2 = make_call_site("test.py", 20, 5, "func2")
    ctx2 = ctx1.push(cs2, k)
    assert len(ctx2) == 2
    assert ctx2.call_string == (cs1, cs2)
    
    # Third push should drop the first (k=2)
    cs3 = make_call_site("test.py", 30, 5, "func3")
    ctx3 = ctx2.push(cs3, k)
    assert len(ctx3) == 2
    assert ctx3.call_string == (cs2, cs3)


def test_context_pop(make_call_site):
    """Test popping call sites from context."""
    # Set up context with multiple call sites
    cs1 = make_call_site("test.py", 10, 5, "func1")
    cs2 = make_call_site("test.py", 20, 5, "func2")
    ctx = Context((cs1, cs2))
    
    # Pop the most recent call site
    ctx1 = ctx.pop()
    assert len(ctx1) == 1
    assert ctx1.call_string == (cs1,)
    
    # Pop again to get empty context
    ctx2 = ctx1.pop()
    assert ctx2.is_empty()
    
    # Pop from empty context should remain empty
    ctx3 = ctx2.pop()
    assert ctx3.is_empty()


def test_recursive_context(make_call_site):
    """Test context with recursive calls to the same site."""
    k = 2
    
    # Create a call site for a recursive function
    recursive_cs = make_call_site("test.py", 42, 10, "recursive_func")
    
    # Empty context
    ctx = Context()
    
    # First recursive call
    ctx1 = ctx.push(recursive_cs, k)
    assert len(ctx1) == 1
    assert ctx1.call_string == (recursive_cs,)
    
    # Second recursive call
    ctx2 = ctx1.push(recursive_cs, k)
    assert len(ctx2) == 2
    assert ctx2.call_string == (recursive_cs, recursive_cs)
    
    # Third recursive call (should drop oldest due to k=2)
    ctx3 = ctx2.push(recursive_cs, k)
    assert len(ctx3) == 2
    assert ctx3.call_string == (recursive_cs, recursive_cs)
    
    # Context string should indicate recursion by showing same call site twice
    call_site_str = str(recursive_cs)
    assert str(ctx3).count(call_site_str) == 2


def test_higher_order_context(make_call_site):
    """Test higher-order call contexts through function pointers."""
    k = 2
    
    # Create call sites for higher-order scenario
    caller_cs = make_call_site("test.py", 10, 5, "caller")
    get_callback_cs = make_call_site("test.py", 20, 5, "get_callback")
    invoke_cs = make_call_site("test.py", 30, 5, "invoke")
    
    # Build the context chain: caller -> get_callback -> invoke
    ctx = Context()
    ctx1 = ctx.push(caller_cs, k)
    ctx2 = ctx1.push(get_callback_cs, k)
    ctx3 = ctx2.push(invoke_cs, k)
    
    # k=2 means we should only see the last 2 call sites
    assert len(ctx3) == 2
    assert ctx3.call_string == (get_callback_cs, invoke_cs)


def test_context_selector(k, make_call_site):
    """Test context selector with different k values."""
    selector = ContextSelector(k=k)
    ctx = Context()
    
    # Create a sequence of call sites
    cs1 = make_call_site("test.py", 10, 5, "func1")
    cs2 = make_call_site("test.py", 20, 5, "func2")
    cs3 = make_call_site("test.py", 30, 5, "func3")
    
    # Push them through the selector
    ctx1 = selector.push(ctx, cs1)
    ctx2 = selector.push(ctx1, cs2)
    ctx3 = selector.push(ctx2, cs3)
    
    # Check results based on k value
    if k == 1:
        assert len(ctx3) == 1
        assert ctx3.call_string == (cs3,)
    elif k == 2:
        assert len(ctx3) == 2
        assert ctx3.call_string == (cs2, cs3)
    else:
        assert False, f"Test not configured for k={k}"


def test_context_manager(make_call_site):
    """Test context manager for tracking current context."""
    # Create context manager with k=2
    selector = ContextSelector(k=2)
    manager = ContextManager(selector)
    
    # Initial context should be empty
    assert manager.current().is_empty()
    
    # Enter first call
    cs1 = make_call_site("test.py", 10, 5, "func1")
    ctx1 = manager.enter_call(cs1)
    assert len(ctx1) == 1
    assert ctx1.call_string == (cs1,)
    assert manager.current() == ctx1
    
    # Enter second call
    cs2 = make_call_site("test.py", 20, 5, "func2")
    ctx2 = manager.enter_call(cs2)
    assert len(ctx2) == 2
    assert ctx2.call_string == (cs1, cs2)
    assert manager.current() == ctx2
    
    # Leave second call
    ctx3 = manager.leave_call()
    assert len(ctx3) == 1
    assert ctx3.call_string == (cs1,)
    assert manager.current() == ctx3
    
    # Leave first call
    ctx4 = manager.leave_call()
    assert ctx4.is_empty()
    assert manager.current().is_empty()


def test_context_manager_truncate(make_call_site):
    """Test truncating the current context in context manager."""
    # Create context manager with k=3
    selector = ContextSelector(k=3)
    manager = ContextManager(selector)
    
    # Build up a context with 3 call sites
    cs1 = make_call_site("test.py", 10, 5, "func1")
    cs2 = make_call_site("test.py", 20, 5, "func2")
    cs3 = make_call_site("test.py", 30, 5, "func3")
    
    manager.enter_call(cs1)
    manager.enter_call(cs2)
    manager.enter_call(cs3)
    
    # Current context should have all 3 call sites
    assert len(manager.current()) == 3
    
    # Truncate to k=2
    manager.truncate(k=2)
    
    # Should now have only the 2 most recent call sites
    assert len(manager.current()) == 2
    assert manager.current().call_string == (cs2, cs3)
    
    # Truncate to k=1
    manager.truncate(k=1)
    
    # Should now have only the most recent call site
    assert len(manager.current()) == 1
    assert manager.current().call_string == (cs3,)