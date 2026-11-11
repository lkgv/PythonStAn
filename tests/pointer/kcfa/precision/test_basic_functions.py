"""Test basic function calls and closures."""

import pytest


def test_direct_call(run_analysis):
    """Test simple direct function call."""
    code = """
def target():
    return 42

def caller():
    result = target()
    return result

caller()
"""
    checker = run_analysis(code)
    checker.assert_call_edge("caller", "target")


def test_nested_functions(run_analysis):
    """Test nested function definitions and calls."""
    code = """
def outer():
    def inner():
        return 42
    result = inner()
    return result

outer()
"""
    checker = run_analysis(code)
    checker.assert_call_edge("outer", "inner")


def test_multiple_calls_chain(run_analysis):
    """Test chain of function calls."""
    code = """
def func1():
    return 1

def func2():
    return func1()

def func3():
    return func2()

func3()
"""
    checker = run_analysis(code)
    checker.assert_call_edge("func3", "func2")
    checker.assert_call_edge("func2", "func1")


def test_closure_capture(run_analysis):
    """Test closure capturing outer variable."""
    code = """
def make_adder(x):
    def adder(y):
        return x + y
    return adder

add5 = make_adder(5)
result = add5(3)
"""
    checker = run_analysis(code)
    # Should have call to make_adder
    checker.assert_call_edge("__main__", "make_adder")
    # Should have call to the returned adder function
    # Note: This might be tricky due to how closures are named
    edges = checker.get_call_edges(callee="adder")
    assert len(edges) > 0, "Expected call to closure function 'adder'"


def test_function_reference(run_analysis):
    """Test function assigned to variable then called."""
    code = """
def target():
    return 42

func_ref = target
result = func_ref()
"""
    checker = run_analysis(code)
    checker.assert_call_edge("__main__", "target")


def test_conditional_call(run_analysis):
    """Test function calls in conditional branches."""
    code = """
def func_a():
    return 1

def func_b():
    return 2

def caller(x):
    if x:
        result = func_a()
    else:
        result = func_b()
    return result

caller(True)
"""
    checker = run_analysis(code)
    # Both branches should be considered by static analysis
    checker.assert_call_edge("caller", "func_a")
    checker.assert_call_edge("caller", "func_b")


def test_recursive_call(run_analysis):
    """Test recursive function call."""
    code = """
def factorial(n):
    if n <= 1:
        return 1
    return n * factorial(n - 1)

result = factorial(5)
"""
    checker = run_analysis(code)
    checker.assert_call_edge("__main__", "factorial")
    checker.assert_call_edge("factorial", "factorial")


def test_mutually_recursive(run_analysis):
    """Test mutually recursive functions."""
    code = """
def is_even(n):
    if n == 0:
        return True
    return is_odd(n - 1)

def is_odd(n):
    if n == 0:
        return False
    return is_even(n - 1)

result = is_even(4)
"""
    checker = run_analysis(code)
    checker.assert_call_edge("__main__", "is_even")
    checker.assert_call_edge("is_even", "is_odd")
    checker.assert_call_edge("is_odd", "is_even")


def test_higher_order_function(run_analysis):
    """Test function passed as argument and called."""
    code = """
def apply(func, value):
    return func(value)

def double(x):
    return x * 2

result = apply(double, 5)
"""
    checker = run_analysis(code)
    checker.assert_call_edge("__main__", "apply")
    checker.assert_call_edge("apply", "double")


def test_function_return_value(run_analysis):
    """Test function returning another function."""
    code = """
def make_multiplier(n):
    def multiplier(x):
        return x * n
    return multiplier

mul3 = make_multiplier(3)
result = mul3(7)
"""
    checker = run_analysis(code)
    checker.assert_call_edge("__main__", "make_multiplier")
    # Should have call to returned multiplier function
    edges = checker.get_call_edges(callee="multiplier")
    assert len(edges) > 0, "Expected call to returned function 'multiplier'"

