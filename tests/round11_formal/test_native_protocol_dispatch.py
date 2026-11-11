"""Independent native mapping dispatch, effects and isolation obligations.

Required edges originate at each actual mapping operation. Synthetic method
roots are disabled, so internal hash/equality body edges cannot satisfy them.
"""
import ast

import pytest

from pythonstan.analysis.pointer.kcfa.object import ConstantObject
from pythonstan.world import World
from tests.pointer.kcfa.test_ai_bridge import analyze_source


SOURCE = '''events = []
def hashed():
    events.append("hash")
    return 1
def equal():
    events.append("equal")
    return True
def unrelated_hash(): return 2
def unrelated_equal(): return False
def shadow_hash(): return 3
def shadow_equal(): return False
class Key:
    def __hash__(self): return hashed()
    def __eq__(self, other): return equal()
class Noise:
    def __hash__(self): return unrelated_hash()
    def __eq__(self, other): return unrelated_equal()
stored = Key()
lookup = Key()
lookup.__hash__ = shadow_hash
lookup.__eq__ = shadow_equal
mapping = {stored: "stored"}
noise = {Noise(): "noise"}
got = mapping.get(lookup)
written = mapping.setdefault(lookup)
observed_effect = events[0]
'''


def constants(query, name):
    return {obj.value for obj in query.points_to_at(name, World().get_entry_module())
            if isinstance(obj, ConstantObject)}


def operation_lines(source, targets):
    return {node.targets[0].id: node.lineno for node in ast.parse(source).body
            if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id in targets}


def dispatch_at_line(query, line):
    return {edge.callee.stmt.get_qualname() for edge in query.call_graph().edges
            if edge.callsite.scope.stmt.get_qualname() == "subject"
            and getattr(edge.callsite.content.statement.get_ast(), "lineno", None) == line}


@pytest.mark.parametrize("policy", ["legacy", "fair"])
@pytest.mark.parametrize("index_sensitive", [False, True])
@pytest.mark.parametrize("intrinsics", [False, True])
def test_original_operation_calls_class_hooks_and_retains_per_result_and_heap_effects(
        tmp_path, policy, index_sensitive, intrinsics):
    concrete = {}
    exec(SOURCE, concrete)
    assert concrete["got"] == concrete["written"] == "stored"
    assert "hash" in concrete["events"] and "equal" in concrete["events"]
    query, edges = analyze_source(tmp_path, SOURCE,
        enable_synthetic_method_contexts=False, work_scheduling_policy=policy,
        context_policy="2-cfa", index_sensitive=index_sensitive,
        enable_ai_intrinsics=intrinsics)
    for target, line in operation_lines(SOURCE, {"got", "written"}).items():
        dispatch = dispatch_at_line(query, line)
        assert {"subject.Key.__hash__", "subject.Key.__eq__"} <= dispatch
        assert not dispatch.intersection({"subject.Noise.__hash__", "subject.Noise.__eq__",
                                          "subject.shadow_hash", "subject.shadow_equal"})
        assert "stored" in constants(query, target)
    assert {"hash", "equal"} <= constants(query, "observed_effect")
    assert {("subject.Key.__hash__", "subject.hashed"),
            ("subject.Key.__eq__", "subject.equal")} <= edges
    assert not any(callee in {"subject.shadow_hash", "subject.shadow_equal"} for _, callee in edges)
    assert query.get_statistics()["converged"] == 1


@pytest.mark.parametrize("policy", ["legacy", "fair"])
def test_late_class_hook_writes_activate_an_existing_native_key_subscription(tmp_path, policy):
    source = '''effects = []
def late_hash(self):
    effects.append("late hash")
    return 1
def late_equal(self, other):
    effects.append("late equal")
    return True
class Key: pass
stored = Key()
lookup = Key()
mapping = {stored: "payload"}
result = mapping.get(lookup)
Key.__hash__ = late_hash
Key.__eq__ = late_equal
seen = effects[0]
'''
    query, edges = analyze_source(tmp_path, source,
        enable_synthetic_method_contexts=False, work_scheduling_policy=policy,
        enable_ai_intrinsics=False)
    line = operation_lines(source, {"result"})["result"]
    assert {"subject.late_hash", "subject.late_equal"} <= dispatch_at_line(query, line)
    assert {"late hash", "late equal"} <= constants(query, "seen")
    assert "payload" in constants(query, "result")
    assert query.get_statistics()["converged"] == 1


@pytest.mark.parametrize("policy", ["legacy", "fair"])
def test_native_equality_result_truth_callback_is_observed_and_exception_boundary_retained(tmp_path, policy):
    source = '''effects = []
def truth_callback():
    effects.append("truth")
    return True
class Truth:
    def __bool__(self): return truth_callback()
class Key:
    def __hash__(self): return 1
    def __eq__(self, other): return Truth()
mapping = {Key(): "payload"}
result = mapping.get(Key())
seen = effects[0]
'''
    concrete = {}; exec(source, concrete)
    assert concrete["effects"] == ["truth"]
    query, edges = analyze_source(tmp_path, source,
        enable_synthetic_method_contexts=False, work_scheduling_policy=policy,
        enable_ai_intrinsics=False)
    assert ("subject.Truth.__bool__", "subject.truth_callback") in edges
    assert "truth" in constants(query, "seen")
    assert "payload" in constants(query, "result")
    assert any("native_mapping_key_protocol" in row["effects"]
               and "exception_routing" in row["effects"]
               for row in query.get_residual_effects())


@pytest.mark.parametrize("policy", ["legacy", "fair"])
def test_source_dictionary_override_does_not_fabricate_native_key_dispatch(tmp_path, policy):
    source = '''def hash_body(): return 1
class Key:
    def __hash__(self): return hash_body()
class Override(dict):
    def get(self, key): return "override"
mapping = Override()
result = mapping.get(Key())
'''
    query, _ = analyze_source(tmp_path, source,
        enable_synthetic_method_contexts=False, work_scheduling_policy=policy,
        enable_ai_intrinsics=False)
    line = operation_lines(source, {"result"})["result"]
    assert "subject.Override.get" in dispatch_at_line(query, line)
    assert "subject.Key.__hash__" not in dispatch_at_line(query, line)
    assert "override" in constants(query, "result")


@pytest.mark.parametrize("policy", ["legacy", "fair"])
def test_class_keys_dispatch_through_represented_metaclass_methods(tmp_path, policy):
    source = '''events = []
def hash_callback():
    events.append("hash")
    return 1
def equality_callback():
    events.append("equal")
    return True
class Meta(type):
    def __hash__(cls): return hash_callback()
    def __eq__(cls, other): return equality_callback()
class First(metaclass=Meta): pass
class Second(metaclass=Meta): pass
mapping = {First: "stored"}
result = mapping.get(Second)
seen = events[0]
'''
    concrete = {}; exec(source, concrete)
    assert concrete["result"] == "stored" and "equal" in concrete["events"]
    query, edges = analyze_source(tmp_path, source,
        enable_synthetic_method_contexts=False, work_scheduling_policy=policy,
        enable_ai_intrinsics=False)
    line = operation_lines(source, {"result"})["result"]
    assert {"subject.Meta.__hash__", "subject.Meta.__eq__"} <= dispatch_at_line(query, line)
    assert {("subject.Meta.__hash__", "subject.hash_callback"),
            ("subject.Meta.__eq__", "subject.equality_callback")} <= edges
    assert {"hash", "equal"} <= constants(query, "seen")


@pytest.mark.parametrize("policy", ["legacy", "fair"])
@pytest.mark.parametrize("method", ["get", "setdefault"])
def test_hash_callback_heap_write_flows_to_later_source_call(tmp_path, policy, method):
    source = f'''registry = {{}}
def installed(): return "installed"
class Key:
    def __hash__(self):
        registry["ready"] = installed
        return 1
mapping = {{}}
ignored = mapping.{method}(Key())
result = registry["ready"]()
'''
    concrete = {}; exec(source, concrete)
    assert concrete["result"] == "installed"
    query, edges = analyze_source(tmp_path, source,
        enable_synthetic_method_contexts=False, work_scheduling_policy=policy,
        enable_ai_intrinsics=False)
    line = operation_lines(source, {"ignored"})["ignored"]
    assert "subject.Key.__hash__" in dispatch_at_line(query, line)
    assert ("subject", "subject.installed") in edges
    assert "installed" in constants(query, "result")
