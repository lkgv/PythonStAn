"""Actual native-operation dispatch witnesses, with synthetic bodies disabled."""
import ast

import pytest

from pythonstan.analysis.pointer.kcfa.object import ConstantObject, FunctionObject, InstanceObject
from pythonstan.analysis.pointer.kcfa.state import Worklist
from pythonstan.analysis.pointer.kcfa.native_mapping import NATIVE_MAPPING_KEYS
from pythonstan.analysis.pointer.kcfa.points_to_set import PointsToSet
from pythonstan.analysis.pointer.kcfa.variable import Variable, VariableKind
from pythonstan.ir import IRCall, IRLoadAttr
from pythonstan.world import World
from tests.pointer.kcfa.test_ai_bridge import analyze_source as _analyze_source


def analyze_source(tmp_path, source, **options):
    return _analyze_source(tmp_path, source, enable_synthetic_method_contexts=False, **options)


def values(query, name):
    return {obj.value for obj in query.points_to_at(name, World().get_entry_module())
            if isinstance(obj, ConstantObject)}


def call_line(source, method):
    return next(node.lineno for node in ast.walk(ast.parse(source))
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == method)


def dispatches_at(query, line):
    return {edge.callee.stmt.get_qualname() for edge in query.call_graph().edges
            if edge.callsite.scope.stmt.get_qualname() == "subject"
            and getattr(edge.callsite.content.statement.get_ast(), "lineno", None) == line}


def method_dispatches(query, method, receiver):
    # Bind the exact canonical call through its real receiver/load; no
    # unrelated call at the same display line can satisfy this assertion.
    module = World().get_entry_module()
    ir = World().scope_manager.get_ir(module, "ir")
    names = {stmt.get_lval().id for stmt in ir if isinstance(stmt, IRLoadAttr)
             and stmt.get_attr() == method and stmt.get_obj().id == receiver}
    calls = [stmt for stmt in ir if isinstance(stmt, IRCall) and stmt.get_func_name() in names]
    assert len(calls) == 1, (method, receiver)
    return {edge.callee.stmt.get_qualname() for edge in query.call_graph().edges
            if edge.callsite.scope.stmt is module and edge.callsite.content.statement is calls[0]}


SOURCE = '''
events = []
def stored_hash():
    events.append("stored_hash")
    return 1
def query_hash():
    events.append("query_hash")
    return 1
def stored_equal():
    events.append("stored_equal")
    return True
def query_equal():
    events.append("query_equal")
    return True
class Stored:
    def __hash__(self): return stored_hash()
    def __eq__(self, other): return stored_equal()
class Query:
    def __hash__(self): return query_hash()
    def __eq__(self, other): return query_equal()
stored = Stored()
lookup = Query()
mapping = {stored: "stored"}
result = mapping.get(lookup)
inserted = mapping.setdefault(lookup)
'''


@pytest.mark.parametrize("context_policy", ["0-cfa", "1-cfa", "2-cfa"])
@pytest.mark.parametrize("work_scheduling_policy", ["legacy", "fair"])
@pytest.mark.parametrize("index_sensitive", [False, True])
@pytest.mark.parametrize("enable_ai_intrinsics", [False, True])
def test_literal_get_setdefault_have_native_callsite_dispatch_and_effects(
        tmp_path, context_policy, work_scheduling_policy, index_sensitive, enable_ai_intrinsics):
    concrete = {}
    exec(SOURCE, concrete)
    assert concrete["result"] == concrete["inserted"] == "stored"
    query, edges = analyze_source(tmp_path, SOURCE, context_policy=context_policy,
                                  work_scheduling_policy=work_scheduling_policy,
                                  index_sensitive=index_sensitive,
                                  enable_ai_intrinsics=enable_ai_intrinsics)
    expected = {"subject.Query.__hash__", "subject.Stored.__eq__", "subject.Query.__eq__"}
    for operation in ("get", "setdefault"):
        assert expected <= dispatches_at(query, call_line(SOURCE, operation))
    literal_line = next(node.lineno for node in ast.walk(ast.parse(SOURCE)) if isinstance(node, ast.Dict))
    assert "subject.Stored.__hash__" in dispatches_at(query, literal_line)
    for name in ("stored_hash", "query_hash", "stored_equal", "query_equal"):
        method = "__hash__" if "hash" in name else "__eq__"
        owner = "Stored" if name.startswith("stored") else "Query"
        assert (f"subject.{owner}.{method}", f"subject.{name}") in edges
    event_objects = query.points_to_at("events", World().get_entry_module())
    event_values = {obj.value for event_list in event_objects for obj in query.list_elements(event_list)
                    if isinstance(obj, ConstantObject)}
    assert set(concrete["events"]) <= event_values
    assert "stored" in values(query, "result") and "stored" in values(query, "inserted")
    assert query.get_statistics()["converged"] == 1
    assert any("native_mapping_key_protocol" in row["effects"] for row in query.get_residual_effects())


@pytest.mark.parametrize("copy_expression", ["source.copy()", "dict(source)", "{**source}", "updated"])
@pytest.mark.parametrize("work_scheduling_policy", ["legacy", "fair"])
@pytest.mark.parametrize("index_sensitive", [False, True])
def test_stored_key_identity_survives_copy_update_and_unpack(tmp_path, copy_expression, work_scheduling_policy, index_sensitive):
    source = SOURCE[:SOURCE.index("mapping =")] + f'''
source = {{stored: "stored"}}
updated = {{}}
updated.update(source)
mapping = {copy_expression}
result = mapping.get(lookup)
'''
    query, _ = analyze_source(tmp_path, source, index_sensitive=index_sensitive, work_scheduling_policy=work_scheduling_policy)
    observed = dispatches_at(query, call_line(source, "get"))
    assert {"subject.Query.__hash__", "subject.Stored.__eq__", "subject.Query.__eq__"} <= observed
    assert "stored" in values(query, "result")
    # Key metadata must never become a Python dictionary value through copies.
    assert not any(isinstance(obj, InstanceObject)
                   for obj in query.points_to_at("result", World().get_entry_module()))


@pytest.mark.parametrize("schedule", ["fifo", "lifo"])
def test_late_insertions_receiver_alias_and_discarded_calls_keep_dispatch(tmp_path, monkeypatch, schedule):
    if schedule == "lifo":
        def pop(self):
            scope, node, points = self.items_list.pop()
            del self.items_dict[node]
            return scope, node, points.val
        monkeypatch.setattr(Worklist, "pop", pop)
    source = SOURCE[:SOURCE.index("mapping =")] + '''
mapping = {}
alias = mapping
alias.get(lookup)
mapping[stored] = "stored"
read = mapping[lookup]
alias.setdefault(lookup)
'''
    query, _ = analyze_source(tmp_path, source)
    expected = {"subject.Query.__hash__", "subject.Stored.__eq__", "subject.Query.__eq__"}
    assert expected <= method_dispatches(query, "get", "alias")
    assert expected <= method_dispatches(query, "setdefault", "alias")
    assert "stored" in values(query, "read")


def test_implicit_hash_bypasses_instance_shadow_and_overridden_mapping_methods(tmp_path):
    source = '''
def actual(): return 1
def shadow(): return 2
def marker(): return "marker"
class Key:
    def __hash__(self): return actual()
key = Key()
key.__hash__ = shadow
native = {}
native.get(key)
class Mapping(dict):
    def get(self, key): return marker
overridden = Mapping()
result = overridden.get(key)
result()
'''
    query, edges = analyze_source(tmp_path, source)
    assert ("subject.Key.__hash__", "subject.actual") in edges
    assert not any(callee == "subject.shadow" for _, callee in edges)
    assert "subject.Key.__hash__" in method_dispatches(query, "get", "native")
    assert "subject.Key.__hash__" not in method_dispatches(query, "get", "overridden")
    assert "subject.Mapping.get" in method_dispatches(query, "get", "overridden")
    assert "subject.marker" in {obj.ir.get_qualname() for obj in query.points_to_at("result", World().get_entry_module())
                                if isinstance(obj, FunctionObject)}


def test_exact_scalar_keys_do_not_fabricate_callbacks_or_native_protocol_residuals(tmp_path):
    query, edges = analyze_source(tmp_path, '''
def unused(): return 1
mapping = {"text": "text value", 1: "number value", None: "none value"}
a = mapping.get("text")
b = mapping.setdefault(1)
c = mapping.get(None)
''')
    assert not edges
    assert not any("native_mapping_key_protocol" in row["effects"] for row in query.get_residual_effects())
    assert "text value" in values(query, "a")
    assert "number value" in values(query, "b")


def test_source_metaclass_key_callbacks_have_actual_cache_callsite_edges(tmp_path):
    source = '''
def hashed(): return 1
def compared(): return True
class Meta(type):
    def __hash__(cls): return hashed()
    def __eq__(cls, other): return compared()
class Stored(metaclass=Meta): pass
class Query(metaclass=Meta): pass
mapping = {Stored: "stored"}
result = mapping.get(Query)
mapping.setdefault(Query)
'''
    query, edges = analyze_source(tmp_path, source)
    for operation in ("get", "setdefault"):
        assert {"subject.Meta.__hash__", "subject.Meta.__eq__"} <= method_dispatches(query, operation, "mapping")
    assert {("subject.Meta.__hash__", "subject.hashed"), ("subject.Meta.__eq__", "subject.compared")} <= edges
    assert "stored" in values(query, "result")
    assert any("native_mapping_key_protocol" in row["effects"] for row in query.get_residual_effects())


def test_hash_reentry_heap_effect_propagates_and_raise_boundary_remains(tmp_path):
    source = '''
def callback(): return "callback"
class Key:
    def __hash__(self):
        mapping["side"] = callback
        raise RuntimeError("open exceptional exit")
key = Key()
mapping = {}
mapping.get(key)
result = mapping["side"]
result()
'''
    query, edges = analyze_source(tmp_path, source)
    assert "subject.Key.__hash__" in method_dispatches(query, "get", "mapping")
    assert ("subject", "subject.callback") in edges
    assert any("native_mapping_key_protocol" in row["effects"] and "exception_routing" in row["effects"]
               for row in query.get_residual_effects())
    assert query.get_statistics()["converged"] == 1


def test_keyword_created_string_key_can_reach_custom_query_equality(tmp_path):
    source = '''
class Query:
    def __hash__(self): return 1
    def __eq__(self, other): return self.mark(other)
    def mark(self, value): return True
lookup = Query()
mapping = dict(name="payload")
result = mapping.get(lookup)
'''
    query, edges = analyze_source(tmp_path, source)
    assert "subject.Query.__eq__" in method_dispatches(query, "get", "mapping")
    assert ("subject.Query.__eq__", "subject.Query.mark") in edges
    module = World().get_entry_module()
    cls = next(scope for scope in World().scope_manager.get_scopes()
               if scope.get_qualname() == "subject.Query.__eq__")
    assert "name" in {obj.value for obj in query.points_to_at("other", cls) if isinstance(obj, ConstantObject)}
    assert not any(isinstance(obj, InstanceObject) for obj in query.points_to_at("result", module))


def test_opaque_mapping_copy_retains_native_key_provenance_obligation(tmp_path):
    query, _ = analyze_source(tmp_path, '''
class Opaque:
    def keys(self): return ["key"]
    def __getitem__(self, name): return "value"
mapping = dict(Opaque())
result = mapping.get("key")
''')
    assert any("native_mapping_key_provenance" in row["effects"] for row in query.get_residual_effects())


@pytest.mark.parametrize("policy", ["legacy", "fair"])
def test_scalar_only_literals_have_no_pair_scan_or_all_key_query_observers(tmp_path, policy):
    count = 40
    source = "mapping = {" + ", ".join(f'{index}: "value"' for index in range(count)) + "}\n"
    query, edges = analyze_source(tmp_path, source, work_scheduling_policy=policy)
    stats = query._state._native_mapping_protocols.statistics
    assert stats["classified_key_objects"] == count
    assert stats["key_query_objects"] == count
    assert stats["equality_candidate_pairs"] == stats["all_key_observers"] == 0
    assert not edges and query.get_statistics()["converged"] == 1


@pytest.mark.parametrize("arrival", ["stored", "query"])
@pytest.mark.parametrize("policy", ["legacy", "fair"])
def test_scalar_filter_preserves_late_custom_stored_and_query_keys(tmp_path, arrival, policy):
    source = '''
class Key:
    def __hash__(self): return 1
    def __eq__(self, other): return True
custom = Key()
lookup = "old"
mapping = {"old": "payload"}
result = mapping.get(lookup)
'''
    query, _ = analyze_source(tmp_path, source, work_scheduling_policy=policy)
    state = query._state
    manager = state._native_mapping_protocols
    assert manager.statistics["all_key_observers"] == manager.statistics["equality_candidate_pairs"] == 0
    module = World().get_entry_module()
    scope = state.get_scope_for_ir(module)
    key = next(iter(query.points_to_at("custom", module)))
    if arrival == "stored":
        mapping = next(iter(query.points_to_at("mapping", module)))
        target = state.get_field(scope, scope.context, mapping, NATIVE_MAPPING_KEYS)
    else:
        target = state.get_variable(scope, scope.context, Variable("lookup", VariableKind.GLOBAL))
    manager.solver.handle_new_points_to(target, scope, PointsToSet.singleton(key))
    manager.solver.solve_to_fixpoint()
    dispatch = method_dispatches(query, "get", "mapping")
    assert "subject.Key.__eq__" in dispatch
    if arrival == "query":
        assert "subject.Key.__hash__" in dispatch
        assert manager.statistics["all_key_observers"] > 0
    else:
        assert manager.statistics["all_key_observers"] == 0
    assert manager.statistics["equality_candidate_pairs"] > 0
