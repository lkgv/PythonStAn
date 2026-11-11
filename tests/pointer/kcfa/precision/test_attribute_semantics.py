"""Tests for descriptor and attribute interceptor semantics."""


def test_property_returns_function(run_analysis):
    """@property returning a function should be callable."""
    code = """
class property:
    def __init__(self, fget):
        self.fget = fget
    
    def __get__(self, obj, owner):
        return self.fget
    
    def __set__(self, obj, value):
        return None

class C:
    @property
    def prop(self):
        return 1

obj = C()
f = obj.prop
f()
"""
    checker = run_analysis(code)
    edges = checker.get_call_edges(callee="prop")
    assert len(edges) > 0, "Expected call to prop via property"


def test_descriptor_get_returns_function(run_analysis):
    """Descriptor __get__ returning a function should be callable."""
    code = """
class Desc:
    def __get__(self, obj, owner):
        return obj.target

class C:
    def target(self):
        return 1
    
    d = Desc()

obj = C()
obj.d()
"""
    checker = run_analysis(code)
    edges = checker.get_call_edges(callee="target")
    assert len(edges) > 0, "Expected call to target via __get__"


def test_getattr_fallback_returns_function(run_analysis):
    """__getattr__ fallback returning a function should be callable."""
    code = """
def target():
    return 1

class C:
    def __getattr__(self, name):
        return target

obj = C()
obj.missing()
"""
    checker = run_analysis(code)
    edges = checker.get_call_edges(callee="target")
    assert len(edges) > 0, "Expected call to target via __getattr__"


def test_getattribute_intercept_returns_function(run_analysis):
    """__getattribute__ intercept returning a function should be callable."""
    code = """
def target():
    return 1

class C:
    def __getattribute__(self, name):
        return target

obj = C()
obj.anything()
"""
    checker = run_analysis(code)
    edges = checker.get_call_edges(callee="target")
    assert len(edges) > 0, "Expected call to target via __getattribute__"


def test_setattr_intercept_updates_slot(run_analysis):
    """__setattr__ intercept should affect later attribute reads."""
    code = """
def target():
    return 1

class C:
    def __setattr__(self, name, value):
        C.slot = value

obj = C()
obj.any = target
obj.slot()
"""
    checker = run_analysis(code)
    edges = checker.get_call_edges(callee="target")
    assert len(edges) > 0, "Expected call to target via __setattr__"
