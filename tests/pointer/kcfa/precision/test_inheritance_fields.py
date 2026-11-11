"""Test inheritance and field propagation edge cases.

These tests focus on verifying that fields (attributes) are correctly
propagated through:
- Instance field creation and access
- Class field creation and access
- Inherited fields from parent classes
- Multiple inheritance scenarios
- Field shadowing and overriding
"""

import pytest


def test_instance_field_function_assignment(run_analysis):
    """Test assigning function to instance field after creation."""
    code = """
def handler():
    return 42

class C:
    def method(self):
        return 1

obj = C()
obj.func = handler
result = obj.func()
"""
    checker = run_analysis(code)
    edges = checker.get_call_edges(callee="handler")
    assert len(edges) > 0, "Function assigned to instance field should be callable"


def test_instance_field_and_method_coexist(run_analysis):
    """Test instance field and method both accessible."""
    code = """
def external():
    return 1

class C:
    def method(self):
        return 2

obj = C()
obj.func = external

m = obj.method
f = obj.func

r1 = m()
r2 = f()
"""
    checker = run_analysis(code)
    checker.assert_call_edge("module", "method")
    edges_ext = checker.get_call_edges(callee="external")
    assert len(edges_ext) > 0, "Both method and instance field should work"


def test_class_field_function_before_instantiation(run_analysis):
    """Test class field set before any instance created."""
    code = """
def processor():
    return 42

class Service:
    pass

Service.process = processor

obj = Service()
result = obj.process()
"""
    checker = run_analysis(code)
    edges = checker.get_call_edges(callee="processor")
    assert len(edges) > 0, "Class field set before instantiation should be accessible"


def test_inherited_class_field(run_analysis):
    """Test accessing class field through inheritance."""
    code = """
def handler():
    return 42

class Base:
    func = handler

class Derived(Base):
    pass

result = Derived.func()
"""
    checker = run_analysis(code)
    edges = checker.get_call_edges(callee="handler")
    assert len(edges) > 0, "Inherited class field should be accessible"


def test_inherited_class_field_via_instance(run_analysis):
    """Test accessing inherited class field via instance."""
    code = """
def handler():
    return 42

class Base:
    func = handler

class Derived(Base):
    pass

obj = Derived()
result = obj.func()
"""
    checker = run_analysis(code)
    edges = checker.get_call_edges(callee="handler")
    assert len(edges) > 0, "Inherited class field accessible via instance"


def test_field_shadowing_in_inheritance(run_analysis):
    """Test child class shadowing parent field."""
    code = """
def parent_func():
    return 1

def child_func():
    return 2

class Parent:
    func = parent_func

class Child(Parent):
    func = child_func

obj = Child()
result = obj.func()
"""
    checker = run_analysis(code)
    # Should call child_func, not parent_func
    edges_child = checker.get_call_edges(callee="child_func")
    assert len(edges_child) > 0, "Child class should shadow parent field"


def test_multiple_inheritance_field_resolution(run_analysis):
    """Test field resolution with multiple inheritance."""
    code = """
def func_a():
    return 1

def func_b():
    return 2

class A:
    func = func_a

class B:
    other = func_b

class C(A, B):
    pass

obj = C()
r1 = obj.func()
r2 = obj.other()
"""
    checker = run_analysis(code)
    edges_a = checker.get_call_edges(callee="func_a")
    edges_b = checker.get_call_edges(callee="func_b")
    assert len(edges_a) > 0, "Should access field from first parent"
    assert len(edges_b) > 0, "Should access field from second parent"


def test_mro_field_resolution(run_analysis):
    """Test MRO-based field resolution."""
    code = """
def base_func():
    return 1

def mixin_func():
    return 2

class Base:
    func = base_func

class Mixin:
    func = mixin_func

class Derived(Mixin, Base):
    pass

obj = Derived()
result = obj.func()
"""
    checker = run_analysis(code)
    # Should resolve to Mixin.func per MRO (left-to-right)
    edges_mixin = checker.get_call_edges(callee="mixin_func")
    assert len(edges_mixin) > 0, "Should use MRO for field resolution"


def test_instance_field_overrides_class_field(run_analysis):
    """Test instance field shadowing class field."""
    code = """
def class_handler():
    return 1

def instance_handler():
    return 2

class C:
    handler = class_handler

obj = C()
obj.handler = instance_handler
result = obj.handler()
"""
    checker = run_analysis(code)
    # Should call instance_handler
    edges_inst = checker.get_call_edges(callee="instance_handler")
    assert len(edges_inst) > 0, "Instance field should shadow class field"


def test_dynamic_field_creation_in_init(run_analysis):
    """Test fields created in __init__."""
    code = """
def callback():
    return 42

class Container:
    def __init__(self):
        self.callback = callback

obj = Container()
result = obj.callback()
"""
    checker = run_analysis(code)
    edges = checker.get_call_edges(callee="callback")
    assert len(edges) > 0, "Field created in __init__ should be accessible"


def test_field_set_by_method(run_analysis):
    """Test field set by instance method."""
    code = """
def handler():
    return 42

class C:
    def set_handler(self, h):
        self.handler = h

obj = C()
obj.set_handler(handler)
result = obj.handler()
"""
    checker = run_analysis(code)
    edges = checker.get_call_edges(callee="handler")
    assert len(edges) > 0, "Field set by method should be accessible"


def test_field_propagation_across_instances(run_analysis):
    """Test field from one instance to another."""
    code = """
def processor():
    return 42

class Source:
    def __init__(self):
        self.func = processor

class Dest:
    pass

src = Source()
dest = Dest()
dest.func = src.func
result = dest.func()
"""
    checker = run_analysis(code)
    edges = checker.get_call_edges(callee="processor")
    assert len(edges) > 0, "Field should propagate across instances"


def test_inherited_method_calling_inherited_field(run_analysis):
    """Test inherited method accessing inherited field."""
    code = """
def helper():
    return 42

class Base:
    handler = helper
    
    def process(self):
        return self.handler()

class Derived(Base):
    pass

obj = Derived()
result = obj.process()
"""
    checker = run_analysis(code)
    checker.assert_call_edge("process", "helper")


def test_staticmethod_accessing_class_field(run_analysis):
    """Test staticmethod accessing class field."""
    code = """
def implementation():
    return 42

class Service:
    func = implementation
    
    @staticmethod
    def call_func():
        return Service.func()

result = Service.call_func()
"""
    checker = run_analysis(code)
    checker.assert_call_edge("call_func", "implementation")


def test_classmethod_modifying_class_field(run_analysis):
    """Test classmethod modifying and accessing class field."""
    code = """
def handler1():
    return 1

def handler2():
    return 2

class Registry:
    handler = handler1
    
    @classmethod
    def set_handler(cls, h):
        cls.handler = h
    
    @classmethod
    def call_handler(cls):
        return cls.handler()

Registry.set_handler(handler2)
result = Registry.call_handler()
"""
    checker = run_analysis(code)
    # Should track handler2 being set and called
    checker.assert_call_edge("call_handler", "handler2")


def test_nested_class_field_access(run_analysis):
    """Test accessing field on nested class."""
    code = """
def handler():
    return 42

class Outer:
    class Inner:
        func = handler

result = Outer.Inner.func()
"""
    checker = run_analysis(code)
    edges = checker.get_call_edges(callee="handler")
    assert len(edges) > 0, "Nested class field should be accessible"


def test_field_in_multiple_inheritance_diamond(run_analysis):
    """Test field resolution in diamond inheritance."""
    code = """
def top_func():
    return 1

def left_func():
    return 2

class Top:
    func = top_func

class Left(Top):
    func = left_func

class Right(Top):
    pass

class Bottom(Left, Right):
    pass

obj = Bottom()
result = obj.func()
"""
    checker = run_analysis(code)
    # Should resolve to Left.func per MRO
    edges_left = checker.get_call_edges(callee="left_func")
    assert len(edges_left) > 0, "Should use MRO for diamond inheritance"


def test_constructor_field_inheritance(run_analysis):
    """Test field set in parent __init__."""
    code = """
def callback():
    return 42

class Parent:
    def __init__(self):
        self.callback = callback

class Child(Parent):
    pass

obj = Child()
result = obj.callback()
"""
    checker = run_analysis(code)
    # Should have: module->Child.__init__ (implicit), Child.__init__->Parent.__init__
    edges_cb = checker.get_call_edges(callee="callback")
    assert len(edges_cb) > 0, "Field from parent __init__ should be accessible"


def test_overridden_init_with_super(run_analysis):
    """Test overridden __init__ calling super().__init__."""
    code = """
def handler():
    return 42

class Base:
    def __init__(self):
        self.handler = handler

class Derived(Base):
    def __init__(self):
        super().__init__()
        self.extra = None

obj = Derived()
result = obj.handler()
"""
    checker = run_analysis(code)
    # Should call Base.__init__ and access handler
    checker.assert_call_edge("__init__", "__init__")  # Derived.__init__ -> Base.__init__
    edges = checker.get_call_edges(callee="handler")
    assert len(edges) > 0, "Field from super().__init__ should be accessible"


def test_property_like_field_access(run_analysis):
    """Test field that holds function accessed multiple times."""
    code = """
def compute():
    return 42

class C:
    def __init__(self):
        self.compute = compute

obj = C()
r1 = obj.compute()
r2 = obj.compute()
"""
    checker = run_analysis(code)
    edges = checker.get_call_edges(callee="compute")
    # Should have at least 2 edges (possibly more depending on context sensitivity)
    assert len(edges) >= 2, "Function field should be callable multiple times"


def test_field_accessed_through_chain(run_analysis):
    """Test field accessed through chain of objects."""
    code = """
def handler():
    return 42

class Inner:
    def __init__(self):
        self.func = handler

class Outer:
    def __init__(self):
        self.inner = Inner()

obj = Outer()
result = obj.inner.func()
"""
    checker = run_analysis(code)
    edges = checker.get_call_edges(callee="handler")
    assert len(edges) > 0, "Function accessible through object chain"

