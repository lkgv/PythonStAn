"""Independent obligations for live namespaces, copies, and paired dict items."""

import pytest

from pythonstan.analysis.pointer.kcfa.object import ConstantObject, FunctionObject
from pythonstan.analysis.pointer.kcfa.state import Worklist
from pythonstan.ir import IRModule
from tests.pointer.kcfa.test_ai_bridge import analyze_source


@pytest.fixture(params=["fifo", "lifo"])
def schedule(request, monkeypatch):
    if request.param == "lifo":
        def pop(self):
            scope, node, points = self.items_list.pop()
            del self.items_dict[node]
            return scope, node, points.val
        monkeypatch.setattr(Worklist, "pop", pop)
    return request.param


def module(query):
    return next(scope for scope in query.get_reachable_scopes() if isinstance(scope, IRModule))


@pytest.mark.parametrize("context", ["0-cfa", "2-cfa"])
def test_live_view_keeps_late_fields_and_alias_writes_without_inherited_members(tmp_path, schedule, context):
    query, edges = analyze_source(tmp_path, '''
def first():
    return 1
def second():
    return 2
def inherited():
    return 3
class Base:
    inherited = inherited
class Box(Base):
    pass
box = Box()
view = box.__dict__
alias = box
alias.early = first
view["late"] = second
view["early"]()
box.late()
''', context_policy=context, enable_ai_intrinsics=False)
    assert {("subject", "subject.first"), ("subject", "subject.second")} <= edges
    views = query.points_to_at("view", module(query))
    assert views
    for view in views:
        assert not query.dict_get(view, "inherited"), "a live instance namespace excludes class inheritance"
    assert query.get_statistics()["converged"] == 1


@pytest.mark.parametrize("context", ["0-cfa", "2-cfa"])
def test_copy_is_directional_not_an_alias_of_live_namespace(tmp_path, schedule, context):
    query, edges = analyze_source(tmp_path, '''
def original():
    return 1
def replacement():
    return 2
class Box:
    pass
def from_original(obj):
    return obj.callback()
def from_copy(mapping):
    return mapping["callback"]()
box = Box()
box.callback = original
view = box.__dict__
copied = dict(view)
copied["callback"] = replacement
from_copy(copied)
from_original(box)
''', context_policy=context, enable_ai_intrinsics=False)
    assert ("subject.from_copy", "subject.replacement") in edges
    assert ("subject.from_original", "subject.original") in edges
    assert ("subject.from_original", "subject.replacement") not in edges
    scope = module(query)
    live = set(query.points_to_at("view", scope))
    copied = set(query.points_to_at("copied", scope))
    assert live and copied and live.isdisjoint(copied)


def test_items_preserve_each_precise_key_value_pair(tmp_path, schedule):
    query, _ = analyze_source(tmp_path, '''
def alpha():
    return 1
def beta():
    return 2
mapping = {"left": alpha, "right": beta}
items = mapping.items()
''', context_policy="2-cfa", index_sensitive=True, enable_ai_intrinsics=False)
    scope = module(query)
    items = query.points_to_at("items", scope)
    pairs = set()
    for collection in items:
        for pair in query.list_elements(collection):
            keys = {obj.value for obj in query.tuple_element(pair, 0) if isinstance(obj, ConstantObject)}
            values = {obj.ir.name for obj in query.tuple_element(pair, 1) if isinstance(obj, FunctionObject)}
            pairs.add((frozenset(keys), frozenset(values)))
    assert (frozenset({"left"}), frozenset({"alpha"})) in pairs
    assert (frozenset({"right"}), frozenset({"beta"})) in pairs
    assert not any("left" in keys and "beta" in values for keys, values in pairs)
    assert not any("right" in keys and "alpha" in values for keys, values in pairs)


@pytest.mark.parametrize("index_sensitive", [False, True])
def test_unknown_namespace_key_retains_every_possible_callback(tmp_path, schedule, index_sensitive):
    query, edges = analyze_source(tmp_path, '''
def alpha():
    return 1
def beta():
    return 2
class Box:
    pass
box = Box()
view = box.__dict__
view[unknown_name()] = alpha
box.selected = beta
box.selected()
''', context_policy="2-cfa", index_sensitive=index_sensitive, enable_ai_intrinsics=False)
    assert {("subject", "subject.alpha"), ("subject", "subject.beta")} <= edges
    assert query.get_residual_effects(), "unknown name/effects are not a complete precise namespace"
