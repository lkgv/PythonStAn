"""End-to-end mapping/keyword transfer obligations, separate from framework gold."""
import pytest

from pythonstan.analysis.pointer.kcfa.object import FunctionObject
from pythonstan.analysis.pointer.kcfa.state import Worklist
from pythonstan.ir import IRModule
from tests.pointer.kcfa.test_ai_bridge import analyze_source


@pytest.fixture(params=["fifo", "lifo"], autouse=True)
def schedule(request, monkeypatch):
    if request.param == "lifo":
        def pop(self):
            scope, node, points = self.items_list.pop()
            del self.items_dict[node]
            return scope, node, points.val
        monkeypatch.setattr(Worklist, "pop", pop)


@pytest.mark.parametrize("context_policy", ["0-cfa", "2-cfa"])
@pytest.mark.parametrize("index_sensitive", [False, True])
def test_multiple_keyword_expansions_reach_named_formals(tmp_path, context_policy, index_sensitive):
    query, edges = analyze_source(tmp_path, '''
def alpha(): return 1
def beta(): return 2
def invoke(first, *, second):
    first()
    second()
one = {}
two = {}
invoke(**one, **two)
one["first"] = alpha
two["second"] = beta
''', context_policy=context_policy, index_sensitive=index_sensitive, enable_ai_intrinsics=False)
    assert {("subject.invoke", "subject.alpha"), ("subject.invoke", "subject.beta")} <= edges
    assert query.get_statistics()["converged"] == 1
    assert any("argument_presence" in item["effects"] for item in query.get_residual_effects())


@pytest.mark.parametrize("index_sensitive", [False, True])
def test_kwargs_forwarding_tracks_fields_created_after_subscription(tmp_path, index_sensitive):
    _, edges = analyze_source(tmp_path, '''
def callback(): return 1
def invoke(**kwargs):
    kwargs["chosen"]()
def forward(**kwargs):
    invoke(**kwargs)
mapping = {}
forward(**mapping)
mapping["chosen"] = callback
''', index_sensitive=index_sensitive, enable_ai_intrinsics=False)
    assert ("subject.invoke", "subject.callback") in edges


def test_kwargs_collection_excludes_consumed_finite_keys(tmp_path):
    query, edges = analyze_source(tmp_path, '''
def consumed(): return 1
def retained(): return 2
def collect(used, **rest):
    return rest
result = collect(**{"used": consumed, "extra": retained})
result["extra"]()
''', index_sensitive=True, enable_ai_intrinsics=False)
    scope = next(s for s in query.get_reachable_scopes() if isinstance(s, IRModule))
    mappings = query.points_to_at("result", scope)
    assert mappings
    for mapping in mappings:
        assert not query.dict_get(mapping, "used")
        assert any(isinstance(obj, FunctionObject) and obj.ir.name == "retained"
                   for obj in query.dict_get(mapping, "extra"))
    assert ("subject", "subject.retained") in edges


def test_positional_only_name_can_remain_in_keyword_collector(tmp_path):
    _, edges = analyze_source(tmp_path, '''
def positional(): return 1
def keyword(): return 2
def invoke(name, /, **rest):
    name()
    rest["name"]()
invoke(positional, **{"name": keyword})
''', enable_ai_intrinsics=False)
    assert {("subject.invoke", "subject.positional"), ("subject.invoke", "subject.keyword")} <= edges


@pytest.mark.parametrize("enable_ai_intrinsics", [False, True])
def test_setattr_is_visible_in_vars_and_dict_copies_stay_distinct(tmp_path, enable_ai_intrinsics):
    query, edges = analyze_source(tmp_path, '''
def original(): return 1
def copied_only(): return 2
class Box: pass
def source_call(obj): obj.callback()
box = Box()
view = vars(box)
setattr(box, "callback", original)
copied = view.copy()
copied["callback"] = copied_only
copied["callback"]()
source_call(box)
''', enable_ai_intrinsics=enable_ai_intrinsics)
    assert ("subject.source_call", "subject.original") in edges
    assert ("subject.source_call", "subject.copied_only") not in edges
    assert ("subject", "subject.copied_only") in edges
    scope = next(s for s in query.get_reachable_scopes() if isinstance(s, IRModule))
    assert set(query.points_to_at("view", scope)).isdisjoint(query.points_to_at("copied", scope))


@pytest.mark.parametrize("index_sensitive", [False, True])
def test_items_comprehension_update_and_constructor_forward_callbacks(tmp_path, index_sensitive):
    _, edges = analyze_source(tmp_path, '''
def original(): return 1
def replacement(): return 2
class Box:
    def __init__(self, callback): self.callback = callback
    def clone(self, **overrides):
        values = {name: value for name, value in self.__dict__.items()}
        values.update(**overrides)
        return type(self)(**values)
first = Box(original)
second = first.clone(callback=replacement)
second.callback()
''', index_sensitive=index_sensitive, enable_ai_intrinsics=False)
    assert ("subject", "subject.replacement") in edges


def test_dict_copy_keeps_numeric_and_string_fields_separate(tmp_path):
    query, _ = analyze_source(tmp_path, '''
def integer_key(): return 1
def string_key(): return 2
mapping = {1: integer_key, "1": string_key}
copied = dict(mapping)
''', index_sensitive=True, enable_ai_intrinsics=False)
    scope = next(s for s in query.get_reachable_scopes() if isinstance(s, IRModule))
    copies = query.points_to_at("copied", scope)
    assert copies
    for copied in copies:
        integers = {obj.ir.name for obj in query.dict_get(copied, 1) if isinstance(obj, FunctionObject)}
        strings = {obj.ir.name for obj in query.dict_get(copied, "1") if isinstance(obj, FunctionObject)}
        assert integers == {"integer_key"}
        assert strings == {"string_key"}


def test_unknown_keyword_survives_forwarding_and_exclusion(tmp_path):
    _, edges = analyze_source(tmp_path, '''
def callback(): return 1
def invoke(used=None, **rest):
    rest["other"]()
mapping = {}
invoke(**mapping)
mapping[unknown_name()] = callback
''', index_sensitive=True, enable_ai_intrinsics=False)
    assert ("subject.invoke", "subject.callback") in edges
