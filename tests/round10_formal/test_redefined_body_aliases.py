"""Saved aliases must retain the original source body's CFG and captures."""
import pytest

from pythonstan.analysis.pointer.kcfa.object import ConstantObject
from pythonstan.world import World
from tests.pointer.kcfa.test_ai_bridge import analyze_source


SOURCES = {
    "module_alias": '''
def primary(): return "primary"
def secondary(): return "secondary"
def action(): return primary()
saved = action
def action(): return secondary()
old_result = saved()
new_result = action()
''',
    "nested_capture_and_default": '''
def primary(): return "primary"
def secondary(): return "secondary"
def build(callback=primary):
    def action(): return callback()
    saved = action
    def action(): return secondary()
    return saved
old_result = build()()
new_result = secondary()
''',
    "method_alias": '''
def primary(): return "primary"
def secondary(): return "secondary"
class Adapter:
    def action(self): return primary()
    original = action
    def action(self): return secondary()
instance = Adapter()
old_result = instance.original()
new_result = instance.action()
''',
}


@pytest.mark.parametrize("source", SOURCES.values(), ids=SOURCES.keys())
@pytest.mark.parametrize("context_policy", ["0-cfa", "1-cfa", "2-cfa"])
@pytest.mark.parametrize("index_sensitive", [False, True])
@pytest.mark.parametrize("intrinsics", [False, True])
def test_saved_aliases_keep_original_body_results_and_closure_cells(
        tmp_path, source, context_policy, index_sensitive, intrinsics):
    concrete = {}
    exec(source, concrete)
    assert concrete["old_result"] == "primary"
    assert concrete["new_result"] == "secondary"
    query, _ = analyze_source(tmp_path, source, context_policy=context_policy,
                              index_sensitive=index_sensitive,
                              enable_ai_intrinsics=intrinsics,
                              enable_synthetic_method_contexts=False)
    module = World().get_entry_module()
    for name, value in (("old_result", "primary"), ("new_result", "secondary")):
        observed = {obj.value for obj in query.points_to_at(name, module)
                    if isinstance(obj, ConstantObject)}
        assert value in observed, (name, observed)
    assert query.get_statistics()["converged"] == 1
