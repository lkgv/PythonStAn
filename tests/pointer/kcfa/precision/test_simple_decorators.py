"""Test simple decorators."""

import pytest


def test_simple_decorator(run_analysis):
    """Test basic function decorator."""
    code = """
def my_decorator(func):
    def wrapper():
        return func()
    return wrapper

@my_decorator
def target():
    return 42

result = target()
"""
    checker = run_analysis(code)
    # Should have call to my_decorator during decoration
    edges_dec = checker.get_call_edges(callee="my_decorator")
    # Should have call to wrapper (the decorated function)
    edges_wrap = checker.get_call_edges(callee="wrapper")
    # Should have call to original target within wrapper
    edges_target = checker.get_call_edges(callee="target")
    
    assert len(edges_dec) > 0, "Expected call to decorator"
    assert len(edges_wrap) > 0, "Expected call to wrapper"


def test_decorator_with_args(run_analysis):
    """Test decorator that wraps function with arguments."""
    code = """
def my_decorator(func):
    def wrapper(x):
        return func(x) + 10
    return wrapper

@my_decorator
def double(n):
    return n * 2

result = double(5)
"""
    checker = run_analysis(code)
    edges_dec = checker.get_call_edges(callee="my_decorator")
    edges_wrap = checker.get_call_edges(callee="wrapper")
    
    assert len(edges_dec) > 0, "Expected call to decorator"
    assert len(edges_wrap) > 0, "Expected call to wrapper"


def test_decorator_chain(run_analysis):
    """Test multiple decorators on same function."""
    code = """
def decorator_a(func):
    def wrapper_a():
        return func()
    return wrapper_a

def decorator_b(func):
    def wrapper_b():
        return func()
    return wrapper_b

@decorator_a
@decorator_b
def target():
    return 42

result = target()
"""
    checker = run_analysis(code)
    edges_a = checker.get_call_edges(callee="decorator_a")
    edges_b = checker.get_call_edges(callee="decorator_b")
    
    assert len(edges_a) > 0, "Expected call to decorator_a"
    assert len(edges_b) > 0, "Expected call to decorator_b"


def test_decorator_factory(run_analysis):
    """Test decorator factory pattern (crucial for Flask routes!)."""
    code = """
def decorator_factory(arg):
    def decorator(func):
        def wrapper():
            return func()
        return wrapper
    return decorator

@decorator_factory("config")
def target():
    return 42

result = target()
"""
    checker = run_analysis(code)
    # Should have call to decorator_factory
    edges_factory = checker.get_call_edges(callee="decorator_factory")
    # Should have call to returned decorator
    edges_dec = checker.get_call_edges(callee="decorator")
    # Should have call to wrapper
    edges_wrap = checker.get_call_edges(callee="wrapper")
    
    assert len(edges_factory) > 0, "Expected call to decorator factory"
    assert len(edges_dec) > 0, "Expected call to decorator"


def test_method_decorator(run_analysis):
    """Test decorator on method."""
    code = """
def method_decorator(func):
    def wrapper(self):
        return func(self)
    return wrapper

class MyClass:
    @method_decorator
    def method(self):
        return 42

obj = MyClass()
result = obj.method()
"""
    checker = run_analysis(code)
    edges_dec = checker.get_call_edges(callee="method_decorator")
    edges_wrap = checker.get_call_edges(callee="wrapper")
    
    assert len(edges_dec) > 0, "Expected call to method decorator"
    assert len(edges_wrap) > 0, "Expected call to wrapper"
    checker.assert_call_edge("wrapper", "MyClass.method")

