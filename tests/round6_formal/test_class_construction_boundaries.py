"""Independent metaclass/exception witnesses discovered after v2 integration."""
import hashlib

import pytest

from pythonstan.analysis.pointer.ai.certified.closed_heap_effects import (
    SEMANTICS_VERSION, analyze_closed_source, check_closed_effects,
)


CASES = [
    pytest.param('''events = []
class Meta(type):
    def __new__(mcls, name, bases, ns):
        events.append(ns.get("__qualname__", "missing"))
        return type.__new__(mcls, name, bases, ns)
class C(metaclass=Meta):
    pass
result = events
''', ["C"], ["missing"], id="implicit_namespace_qualname"),
    pytest.param('''from __future__ import annotations
events = []
class Meta(type):
    def __new__(mcls, name, bases, ns):
        events.append(ns.get("__annotations__", {}))
        return type.__new__(mcls, name, bases, ns)
class C(metaclass=Meta):
    x: int
result = events
''', [{"x": "int"}], [{}], id="future_class_annotations_in_namespace"),
    pytest.param('''class E(Exception):
    args = "replacement"
result = E("original").args == "replacement"
''', True, False, id="exception_subclass_args_override"),
    pytest.param('''class E(StopIteration):
    value = "replacement"
result = E("original").value
''', "replacement", "original", id="stopiteration_subclass_value_override"),
    pytest.param('''events = []
class M(type):
    def __new__(meta, name, bases, ns):
        return type.__new__(N, name, bases, ns)
    def __init__(cls, name, bases, ns):
        events.append("M")
class N(M):
    def __init__(cls, name, bases, ns):
        events.append("N")
class C(metaclass=M):
    pass
result = events
''', ["N"], ["M"], id="new_result_actual_type_selects_init"),
    pytest.param('''class M(type):
    __name__ = "replacement"
class C(metaclass=M):
    pass
result = C.__name__
''', "replacement", "C", id="metaclass_override_shadows_type_name_descriptor"),
    pytest.param('''class D:
    def __get__(self, obj, owner):
        raise AttributeError()
class M(type):
    def __getattr__(cls, name):
        return "fallback"
class C(metaclass=M):
    x = D()
try:
    result = C.x
except AttributeError:
    result = "missed"
''', "fallback", "missed", id="descriptor_failure_enters_metaclass_getattr"),
]


@pytest.mark.parametrize("source, expected, wrong", CASES)
def test_new_class_transfers_never_certify_wrong_python_result(source, expected, wrong):
    namespace = {}
    exec(source, namespace)
    assert namespace["result"] == expected
    derived = analyze_closed_source(source, ["result"])
    if derived["status"] == "closed_model_completed":
        assert derived["observations"]["result"] == expected
    candidate = {"source_sha256": hashlib.sha256(source.encode()).hexdigest(),
                 "semantics_version": SEMANTICS_VERSION, "globals": {"result": wrong}}
    checked = check_closed_effects(source, candidate)
    assert checked["status"] != "conditional_verified", checked
    assert checked["conditional_effects_verified"] == 0
