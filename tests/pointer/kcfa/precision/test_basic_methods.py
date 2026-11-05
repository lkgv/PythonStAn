"""Test basic method calls."""

import pytest


def test_instance_method_call(run_analysis):
    """Test instance method call."""
    code = """
class MyClass:
    def method(self):
        return 42

obj = MyClass()
result = obj.method()
"""
    checker = run_analysis(code)
    # Should have call to method from module-level code
    edges = checker.get_call_edges(callee="method")
    assert len(edges) > 0, "Expected call to instance method"


def test_class_method_call(run_analysis):
    """Test class method call."""
    code = """
class MyClass:
    @classmethod
    def class_method(cls):
        return 42

result = MyClass.class_method()
"""
    checker = run_analysis(code)
    edges = checker.get_call_edges(callee="class_method")
    assert len(edges) > 0, "Expected call to class method"


def test_static_method_call(run_analysis):
    """Test static method call."""
    code = """
class MyClass:
    @staticmethod
    def static_method():
        return 42

result = MyClass.static_method()
"""
    checker = run_analysis(code)
    edges = checker.get_call_edges(callee="static_method")
    assert len(edges) > 0, "Expected call to static method"


def test_method_calling_method(run_analysis):
    """Test method calling another method."""
    code = """
class MyClass:
    def helper(self):
        return 10
    
    def method(self):
        return self.helper() + 32

obj = MyClass()
result = obj.method()
"""
    checker = run_analysis(code)
    edges = checker.get_call_edges(callee="method")
    assert len(edges) > 0, "Expected call to method"
    edges = checker.get_call_edges(callee="helper")
    assert len(edges) > 0, "Expected method to call helper"


def test_constructor_call(run_analysis):
    """Test class instantiation."""
    code = """
class MyClass:
    def __init__(self):
        self.value = 42

obj = MyClass()
"""
    checker = run_analysis(code)
    # Should have call to __init__
    edges = checker.get_call_edges(callee="__init__")
    assert len(edges) > 0, "Expected call to __init__"


def test_instance_dunder_call(run_analysis):
    """Test calling an instance with __call__."""
    code = """
class CallableObj:
    def __call__(self, value):
        return value

obj = CallableObj()
result = obj(1)
"""
    checker = run_analysis(code)
    edges = checker.get_call_edges(callee="__call__")
    assert len(edges) > 0, "Expected call to __call__"


def test_multiple_instance_methods(run_analysis):
    """Test multiple methods on same instance."""
    code = """
class Calculator:
    def add(self, a, b):
        return a + b
    
    def multiply(self, a, b):
        return a * b

calc = Calculator()
r1 = calc.add(2, 3)
r2 = calc.multiply(4, 5)
"""
    checker = run_analysis(code)
    edges_add = checker.get_call_edges(callee="add")
    edges_mul = checker.get_call_edges(callee="multiply")
    assert len(edges_add) > 0, "Expected call to add method"
    assert len(edges_mul) > 0, "Expected call to multiply method"


def test_method_via_variable(run_analysis):
    """Test method accessed via variable."""
    code = """
class MyClass:
    def method(self):
        return 42

obj = MyClass()
m = obj.method
result = m()
"""
    checker = run_analysis(code)
    edges = checker.get_call_edges(callee="method")
    assert len(edges) > 0, "Expected call to method via bound method"


def test_method_call_chain(run_analysis):
    """Test chained method calls."""
    code = """
class Builder:
    def step1(self):
        return self
    
    def step2(self):
        return self
    
    def build(self):
        return 42

obj = Builder()
result = obj.step1().step2().build()
"""
    checker = run_analysis(code)
    edges_s1 = checker.get_call_edges(callee="step1")
    edges_s2 = checker.get_call_edges(callee="step2")
    edges_build = checker.get_call_edges(callee="build")
    assert len(edges_s1) > 0, "Expected call to step1"
    assert len(edges_s2) > 0, "Expected call to step2"
    assert len(edges_build) > 0, "Expected call to build"

