"""Test single inheritance method resolution."""

import pytest


def test_inherit_method(run_analysis):
    """Test calling inherited method."""
    code = """
class Base:
    def method(self):
        return 42

class Child(Base):
    pass

obj = Child()
result = obj.method()
"""
    checker = run_analysis(code)
    edges = checker.get_call_edges(callee="method")
    assert len(edges) > 0, "Expected call to inherited method"


def test_override_method(run_analysis):
    """Test method overriding."""
    code = """
class Base:
    def method(self):
        return 1

class Child(Base):
    def method(self):
        return 2

obj = Child()
result = obj.method()
"""
    checker = run_analysis(code)
    # Should call Child's method, not Base's
    edges = checker.get_call_edges(callee="method")
    assert len(edges) > 0, "Expected call to overridden method"


def test_super_call(run_analysis):
    """Test super() call to parent method."""
    code = """
class Base:
    def method(self):
        return 10

class Child(Base):
    def method(self):
        return super().method() + 5

obj = Child()
result = obj.method()
"""
    checker = run_analysis(code)
    edges = checker.get_call_edges(callee="method")
    # Should have calls to both Base.method and Child.method
    assert len(edges) >= 2, "Expected calls to both parent and child methods"


def test_multilevel_inheritance(run_analysis):
    """Test multi-level inheritance."""
    code = """
class GrandParent:
    def method(self):
        return 1

class Parent(GrandParent):
    pass

class Child(Parent):
    pass

obj = Child()
result = obj.method()
"""
    checker = run_analysis(code)
    edges = checker.get_call_edges(callee="method")
    assert len(edges) > 0, "Expected call through multi-level inheritance"


def test_inherited_and_own_methods(run_analysis):
    """Test class with both inherited and own methods."""
    code = """
class Base:
    def base_method(self):
        return 10

class Child(Base):
    def child_method(self):
        return 20

obj = Child()
r1 = obj.base_method()
r2 = obj.child_method()
"""
    checker = run_analysis(code)
    edges_base = checker.get_call_edges(callee="base_method")
    edges_child = checker.get_call_edges(callee="child_method")
    assert len(edges_base) > 0, "Expected call to inherited method"
    assert len(edges_child) > 0, "Expected call to own method"

