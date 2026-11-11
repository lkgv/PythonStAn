"""Differential executable-TAC checks for iteration header and loop exits."""
import ast
import sys

import pytest

from pythonstan.analysis.transform.three_address import ThreeAddressTransformer


def execute(tree):
    namespace = {}
    filename = "<round5-for-correspondence>"
    budget = 10000

    def trace(frame, event, arg):
        nonlocal budget
        if frame.f_code.co_filename == filename and event == "line":
            budget -= 1
            assert budget > 0, "lowered loop failed to advance within bounded correspondence test"
        return trace

    previous = sys.gettrace()
    try:
        sys.settrace(trace)
        exec(compile(ast.fix_missing_locations(tree), filename, "exec"), namespace)
    finally:
        sys.settrace(previous)
    return namespace["result"]


@pytest.mark.parametrize("source, expected", [
    ('''
result = []
for item in [None, 1, None, 2, []]:
    if item is None:
        result.append("none")
        continue
    result.append(item)
else:
    result.append("done")
''', ["none", 1, "none", 2, [], "done"]),
    ('''
result = []
for item in [None, 1, 2]:
    result.append(item)
    if item == 1:
        break
else:
    result.append("wrong")
''', [None, 1]),
    ('''
result = []
item = "old"
for item in []:
    result.append("wrong")
else:
    result.append(item)
''', ["old"]),
    ('''
result = []
for outer in [0, 1, 2]:
    result.append(outer)
    for inner in []:
        result.append("wrong")
    else:
        continue
    result.append("wrong")
else:
    result.append("done")
''', [0, 1, 2, "done"]),
    ('''
result = []
for item in [None, 1, 2]:
    try:
        if item is None:
            continue
        result.append("body")
    finally:
        result.append(item)
else:
    result.append("done")
''', [None, "body", 1, "body", 2, "done"]),
    ('''
def choose():
    for item in [None, 1, 2]:
        if item is None:
            continue
        return item
    else:
        return "wrong"
result = choose()
''', 1),
    ('''
events = []
class Values:
    def __init__(self):
        self.count = 0
    def __iter__(self):
        events.append("iter")
        return self
    def __next__(self):
        self.count += 1
        events.append("next")
        if self.count == 1:
            return None
        if self.count == 2:
            return 9
        raise StopIteration
result = []
for item in Values():
    result.append(item)
    continue
else:
    result.append(events)
''', [None, 9, ["iter", "next", "next", "next"]]),
])
def test_for_lowering_matches_runtime_for_values_continue_break_and_else(source, expected):
    assert execute(ast.parse(source)) == expected
    lowered = ThreeAddressTransformer().visit(ast.parse(source))
    assert execute(lowered) == expected
