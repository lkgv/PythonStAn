"""Runtime-grounded witnesses for persistent metaclass __call__ dispatch."""
import pytest
from pythonstan.analysis.pointer.kcfa.config import Config
from pythonstan.analysis.pointer.kcfa.object import ConstantObject
from pythonstan.world import World
from pythonstan.world.pipeline import Pipeline


def analyze(tmp_path, source, policy, ai):
    path = tmp_path / "metacall.py"
    path.write_text(source)
    World.setup()
    pipeline = Pipeline(config={
        "filename": str(path), "project_path": str(tmp_path), "library_paths": [],
        "import_level": 0, "mock_libs": False, "time_count": False,
        "analysis": [{"name": "pta", "id": "PointerAnalysis", "prev_analysis": ["ir"],
                      "options": Config(context_policy=policy, enable_ai_intrinsics=ai,
                                        log_level="ERROR", max_iterations=20000).to_dict()}],
    })
    pipeline.run()
    query = pipeline.analysis_manager.get_results("pta").query()
    assert query.get_statistics()["converged"] == 1
    module = World().get_entry_module()
    return query, module


@pytest.mark.parametrize("policy", ["0-cfa", "2-cfa"])
@pytest.mark.parametrize("ai", [False, True])
def test_metaclass_call_override_result_and_edges(tmp_path, policy, ai):
    source = '''
def callback():
    return 19
class Meta(type):
    def __call__(cls):
        return callback
class Container(metaclass=Meta):
    pass
function = Container()
result = function()
'''
    concrete = {}
    exec(source, concrete)
    assert concrete["result"] == 19
    query, module = analyze(tmp_path, source, policy, ai)
    constants = {obj.value for obj in query.points_to_at("result", module) if isinstance(obj, ConstantObject)}
    assert constants == {19}
    edges = {(edge.callsite.scope.stmt.get_qualname(), edge.callee.stmt.get_qualname()) for edge in query.call_graph().edges}
    assert ("metacall", "metacall.Meta.__call__") in edges
    assert ("metacall", "metacall.callback") in edges
    assert any("construction_precedence" in item["effects"] for item in query.get_residual_effects())
    assert not query.get_semantic_status()["certificate_eligible"]


@pytest.mark.parametrize("policy", ["0-cfa", "2-cfa"])
@pytest.mark.parametrize("ai", [False, True])
@pytest.mark.parametrize("patched", [False, True])
def test_inherited_metaclass_call_and_late_function_patch_forward_arguments(tmp_path, policy, ai, patched):
    definition = '''
class ParentMeta(type):
    def __call__(cls, function, *, token):
        cls.seen = token
        return function
class Meta(ParentMeta):
    pass
''' if not patched else '''
class Meta(type):
    pass
def patched_call(cls, function, *, token):
    cls.seen = token
    return function
'''
    patch = "Meta.__call__ = patched_call\n" if patched else ""
    source = '''
def callback():
    return 23
''' + definition + '''
class Container(metaclass=Meta):
    pass
''' + patch + '''
function = Container(callback, token=29)
result = function()
seen = Container.seen
'''
    concrete = {}
    exec(source, concrete)
    assert (concrete["result"], concrete["seen"]) == (23, 29)
    query, module = analyze(tmp_path, source, policy, ai)
    result = {obj.value for obj in query.points_to_at("result", module) if isinstance(obj, ConstantObject)}
    seen = {obj.value for obj in query.points_to_at("seen", module) if isinstance(obj, ConstantObject)}
    assert result == {23}
    assert seen == {29}
    callees = {edge.callee.stmt.get_qualname() for edge in query.call_graph().edges}
    assert ("metacall.patched_call" if patched else "metacall.ParentMeta.__call__") in callees
    assert "metacall.callback" in callees


def test_ordinary_class_does_not_install_empty_metaclass_call(tmp_path):
    query, module = analyze(tmp_path, "class Ordinary: pass\nvalue=Ordinary()\n", "2-cfa", False)
    assert len(query.points_to_at("value", module)) == 1
    constraints = [constraint for _, _, constraint in query._state.constraints.iter_scoped()]
    assert not any(getattr(getattr(constraint, "callee", None), "name", "").startswith("$metaclass_call_active@")
                   for constraint in constraints)
    assert not any("metaclass __call__" in residual["reason"] for residual in query.get_residual_effects())
