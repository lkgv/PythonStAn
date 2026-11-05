"""Test functions stored in containers (lists, dicts, tuples)."""

import pytest


def test_function_in_list(run_analysis):
    """Test function stored in list then called."""
    code = """
def func1():
    return 1

def func2():
    return 2

functions = [func1, func2]
result = functions[0]()
"""
    checker = run_analysis(code)
    # Should have call to func1 (and possibly func2 for soundness)
    checker.assert_call_edge("__main__", "func1")


def test_function_list_iteration(run_analysis):
    """Test calling functions from list iteration."""
    code = """
def action1():
    return 1

def action2():
    return 2

actions = [action1, action2]
for func in actions:
    result = func()
"""
    checker = run_analysis(code)
    # Both functions should be callable from iteration
    checker.assert_call_edge("__main__", "action1")
    checker.assert_call_edge("__main__", "action2")


def test_function_in_dict(run_analysis):
    """Test function stored in dict then called."""
    code = """
def handler_a():
    return "A"

def handler_b():
    return "B"

handlers = {"a": handler_a, "b": handler_b}
result = handlers["a"]()
"""
    checker = run_analysis(code)
    # Should have call to handler_a (and possibly handler_b for soundness)
    checker.assert_call_edge("__main__", "handler_a")


def test_function_dict_dynamic_key(run_analysis):
    """Test function retrieved from dict with dynamic key."""
    code = """
def cmd_start():
    return "started"

def cmd_stop():
    return "stopped"

commands = {"start": cmd_start, "stop": cmd_stop}

def execute(cmd_name):
    handler = commands[cmd_name]
    return handler()

result = execute("start")
"""
    checker = run_analysis(code)
    # With dynamic key, both handlers should be considered
    checker.assert_call_edge("execute", "cmd_start")
    checker.assert_call_edge("execute", "cmd_stop")


def test_function_in_tuple(run_analysis):
    """Test function stored in tuple then called."""
    code = """
def first():
    return 1

def second():
    return 2

pair = (first, second)
result = pair[0]()
"""
    checker = run_analysis(code)
    checker.assert_call_edge("__main__", "first")


def test_nested_container_functions(run_analysis):
    """Test functions in nested containers."""
    code = """
def f1():
    return 1

def f2():
    return 2

nested = [[f1], [f2]]
result = nested[0][0]()
"""
    checker = run_analysis(code)
    checker.assert_call_edge("__main__", "f1")


def test_function_list_append(run_analysis):
    """Test function added to list via append."""
    code = """
def callback():
    return 42

callbacks = []
callbacks.append(callback)
result = callbacks[0]()
"""
    checker = run_analysis(code)
    checker.assert_call_edge("__main__", "callback")


def test_function_dict_setitem(run_analysis):
    """Test function added to dict via setitem."""
    code = """
def processor():
    return "processed"

registry = {}
registry["process"] = processor
handler = registry["process"]
result = handler()
"""
    checker = run_analysis(code)
    checker.assert_call_edge("__main__", "processor")


def test_mixed_container_types(run_analysis):
    """Test function passed through different container types."""
    code = """
def target():
    return 42

# Store in list
lst = [target]
# Extract to dict
dct = {"func": lst[0]}
# Call from dict
result = dct["func"]()
"""
    checker = run_analysis(code)
    checker.assert_call_edge("__main__", "target")

