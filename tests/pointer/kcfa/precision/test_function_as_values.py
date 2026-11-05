"""Test functions passed as arguments and returned from functions."""

import pytest


def test_function_as_argument(run_analysis):
    """Test function passed as argument."""
    code = """
def executor(func):
    return func()

def task():
    return 42

result = executor(task)
"""
    checker = run_analysis(code)
    checker.assert_call_edge("__main__", "executor")
    checker.assert_call_edge("executor", "task")


def test_function_returned_from_function(run_analysis):
    """Test function returned from another function."""
    code = """
def get_handler():
    def handler():
        return 42
    return handler

h = get_handler()
result = h()
"""
    checker = run_analysis(code)
    checker.assert_call_edge("__main__", "get_handler")
    edges = checker.get_call_edges(callee="handler")
    assert len(edges) > 0, "Expected call to returned handler function"


def test_function_factory(run_analysis):
    """Test function factory pattern."""
    code = """
def make_greeter(greeting):
    def greet(name):
        return greeting + " " + name
    return greet

hello = make_greeter("Hello")
result = hello("World")
"""
    checker = run_analysis(code)
    checker.assert_call_edge("__main__", "make_greeter")
    edges = checker.get_call_edges(callee="greet")
    assert len(edges) > 0, "Expected call to closure from factory"


def test_callback_pattern(run_analysis):
    """Test callback function pattern."""
    code = """
def process_data(data, callback):
    result = data * 2
    return callback(result)

def handler(value):
    return value + 10

final = process_data(5, handler)
"""
    checker = run_analysis(code)
    checker.assert_call_edge("__main__", "process_data")
    checker.assert_call_edge("process_data", "handler")


def test_multiple_callbacks(run_analysis):
    """Test multiple callback functions."""
    code = """
def dispatcher(callback1, callback2):
    r1 = callback1()
    r2 = callback2()
    return r1 + r2

def cb1():
    return 1

def cb2():
    return 2

result = dispatcher(cb1, cb2)
"""
    checker = run_analysis(code)
    checker.assert_call_edge("dispatcher", "cb1")
    checker.assert_call_edge("dispatcher", "cb2")


def test_function_chain_return(run_analysis):
    """Test chain of function returns."""
    code = """
def get_a():
    def get_b():
        def get_c():
            return 42
        return get_c
    return get_b

c = get_a()()()
"""
    checker = run_analysis(code)
    checker.assert_call_edge("__main__", "get_a")
    edges_b = checker.get_call_edges(callee="get_b")
    edges_c = checker.get_call_edges(callee="get_c")
    assert len(edges_b) > 0, "Expected call to get_b"
    assert len(edges_c) > 0, "Expected call to get_c"


def test_conditional_function_return(run_analysis):
    """Test function returning different functions based on condition."""
    code = """
def get_handler(mode):
    def handler_a():
        return "A"
    
    def handler_b():
        return "B"
    
    if mode == "a":
        return handler_a
    else:
        return handler_b

h = get_handler("a")
result = h()
"""
    checker = run_analysis(code)
    checker.assert_call_edge("__main__", "get_handler")
    # Both handlers should be possible callees
    edges_a = checker.get_call_edges(callee="handler_a")
    edges_b = checker.get_call_edges(callee="handler_b")
    # At least one should be present (ideally both for soundness)
    assert len(edges_a) > 0 or len(edges_b) > 0, "Expected call to at least one handler"


def test_function_composition(run_analysis):
    """Test function composition pattern."""
    code = """
def compose(f, g):
    def composed(x):
        return f(g(x))
    return composed

def double(x):
    return x * 2

def add_one(x):
    return x + 1

func = compose(double, add_one)
result = func(5)
"""
    checker = run_analysis(code)
    checker.assert_call_edge("__main__", "compose")
    edges = checker.get_call_edges(callee="composed")
    assert len(edges) > 0, "Expected call to composed function"
    # The composed function should call double and add_one
    edges_double = checker.get_call_edges(callee="double")
    edges_add = checker.get_call_edges(callee="add_one")
    assert len(edges_double) > 0, "Expected composed function to call double"
    assert len(edges_add) > 0, "Expected composed function to call add_one"

