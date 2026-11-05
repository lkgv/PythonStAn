"""Tests for builtin intrinsics and iterator helpers."""


def test_getattr_setattr_constant_name(run_analysis):
    """getattr/setattr with constant name should preserve call edges."""
    code = """
def handler():
    return 42

class Obj:
    pass

obj = Obj()
setattr(obj, "cb", handler)
f = getattr(obj, "cb")
result = f()
"""
    checker = run_analysis(code)
    edges = checker.get_call_edges(callee="handler")
    assert len(edges) > 0, "Expected call to handler via getattr/setattr"


def test_map_calls_function(run_analysis):
    """map() should invoke the mapped function on elements."""
    code = """
def double(x):
    return x * 2

nums = [1, 2, 3]
mapped = map(double, nums)
it = iter(mapped)
result = next(it)
"""
    checker = run_analysis(code)
    edges = checker.get_call_edges(callee="double")
    assert len(edges) > 0, "Expected call to double via map"


def test_filter_calls_predicate(run_analysis):
    """filter() should invoke the predicate on elements."""
    code = """
def keep(x):
    return True

nums = [1, 2, 3]
filtered = filter(keep, nums)
it = iter(filtered)
result = next(it)
"""
    checker = run_analysis(code)
    edges = checker.get_call_edges(callee="keep")
    assert len(edges) > 0, "Expected call to keep via filter"
