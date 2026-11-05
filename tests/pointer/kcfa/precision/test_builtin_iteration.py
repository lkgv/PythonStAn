"""Tests for iterator protocol coverage in builtin modeling."""


def test_list_iteration_calls_function(run_analysis):
    """List iteration should flow elements to loop variable."""
    code = """
def handler():
    return 42

funcs = [handler]
for fn in funcs:
    fn()
"""
    checker = run_analysis(code)
    edges = checker.get_call_edges(callee="handler")
    assert len(edges) > 0, "Expected call to handler via list iteration"


def test_dict_values_iteration_calls_function(run_analysis):
    """Dict.values() iteration should yield values."""
    code = """
def handler():
    return 42

d = {"k": handler}
for fn in d.values():
    fn()
"""
    checker = run_analysis(code)
    edges = checker.get_call_edges(callee="handler")
    assert len(edges) > 0, "Expected call to handler via dict.values() iteration"


def test_dict_items_iteration_calls_function(run_analysis):
    """Dict.items() iteration should yield (key, value) tuples."""
    code = """
def handler():
    return 42

d = {"k": handler}
for key, fn in d.items():
    fn()
"""
    checker = run_analysis(code)
    edges = checker.get_call_edges(callee="handler")
    assert len(edges) > 0, "Expected call to handler via dict.items() iteration"


def test_generator_send_flows_value(run_analysis):
    """generator.send(x) should flow x to yield target."""
    code = """
def handler():
    return 42

def gen():
    x = yield
    x()

g = gen()
g.send(handler)
"""
    checker = run_analysis(code)
    edges = checker.get_call_edges(callee="handler")
    assert len(edges) > 0, "Expected call to handler via generator.send"
