"""Persistent descriptor joins, independent of class/hook arrival order."""
import pytest

from pythonstan.analysis.pointer.kcfa.object import (ClassObject, ConstantObject,
                                                    InstanceObject, BuiltinInstanceObject)
from pythonstan.analysis.pointer.kcfa.state import Worklist


@pytest.fixture(params=["fifo", "lifo"])
def schedule(request, monkeypatch):
    if request.param == "lifo":
        def pop(self):
            scope, node, points = self.items_list.pop()
            del self.items_dict[node]
            return scope, node, points.val
        monkeypatch.setattr(Worklist, "pop", pop)
    return request.param


@pytest.mark.parametrize("context", ["0-cfa", "1-cfa", "2-cfa"])
@pytest.mark.parametrize("ai", [False, True])
def test_late_descriptor_and_inherited_setter_preserve_alias_writes(run_analysis, schedule, context, ai):
    checker = run_analysis('''
def first():
    return 1
def second():
    return 2
def setter(self, instance, value):
    instance.saved = value
    value()
class DescriptorBase:
    pass
class Descriptor(DescriptorBase):
    pass
class Box:
    pass
descriptor = Descriptor()
Box.callback = descriptor
DescriptorBase.__set__ = setter
left = Box()
right = Box()
alias = left
alias.callback = first
right.callback = second
left.saved()
right.saved()
''', context_policy=context, enable_ai_intrinsics=ai)
    checker.assert_call_edge("module", "setter")
    checker.assert_call_edge("setter", "first")
    checker.assert_call_edge("setter", "second")
    checker.assert_call_edge("module", "first")
    checker.assert_call_edge("module", "second")
    assert checker.result.get_statistics()["converged"] == 1


@pytest.mark.parametrize("context", ["0-cfa", "2-cfa"])
def test_class_access_passes_none_and_actual_inherited_owner(run_analysis, schedule, context):
    checker = run_analysis('''
def parent():
    return 1
def child():
    return 2
def getter(self, instance, owner):
    return owner.callback
class Descriptor:
    pass
class Parent:
    callback = parent
    selected = Descriptor()
class Child(Parent):
    callback = child
Descriptor.__get__ = getter
Child.selected()
''', context_policy=context)
    checker.assert_call_edge("module", "getter")
    checker.assert_call_edge("module", "child")
    query = checker.result.query()
    getters = {edge.callee.stmt for edge in query.call_graph().edges
               if edge.callee.stmt.get_qualname().endswith(".getter")}
    assert getters
    for function in getters:
        receivers = list(query.points_to_at("instance", function))
        owners = list(query.points_to_at("owner", function))
        assert any(isinstance(obj, ConstantObject) and obj.value is None for obj in receivers)
        assert not any(isinstance(obj, InstanceObject) for obj in receivers)
        assert any(isinstance(obj, ClassObject) and obj.ir.name == "Child" for obj in owners)


@pytest.mark.parametrize("context", ["0-cfa", "2-cfa"])
def test_classmethod_get_binds_owner_and_keeps_argument_positions(run_analysis, schedule, context):
    checker = run_analysis('''
def callback():
    return 1
def selected(cls, callback):
    return callback()
class Parent:
    method = classmethod(selected)
class Child(Parent):
    pass
Child.method(callback)
''', context_policy=context)
    checker.assert_call_edge("module", "selected")
    checker.assert_call_edge("selected", "callback")
    query = checker.result.query()
    functions = {edge.callee.stmt for edge in query.call_graph().edges
                 if edge.callee.stmt.get_qualname().endswith(".selected")}
    assert any(isinstance(obj, ClassObject) and obj.ir.name == "Child"
               for function in functions for obj in query.points_to_at("cls", function))


def test_discarded_setter_result_keeps_effect_and_exception_prefix(run_analysis, schedule):
    checker = run_analysis('''
def callback():
    return 1
class Descriptor:
    def __set__(self, instance, value):
        value()
        raise ValueError()
class Box:
    attribute = Descriptor()
try:
    Box().attribute = callback
except ValueError:
    pass
''')
    checker.assert_call_edge("module", "Descriptor.__set__")
    checker.assert_call_edge("Descriptor.__set__", "callback")


@pytest.mark.parametrize("data_descriptor", [False, True])
def test_descriptor_and_plain_candidate_both_preserved(run_analysis, schedule, data_descriptor):
    source = '''
def plain():
    return 1
def through_descriptor():
    return 2
class Descriptor:
    def __get__(self, instance, owner):
        return through_descriptor
DESCRIPTOR_SETTER
class Box:
    selected = plain
Box.selected = Descriptor()
Box().selected()
'''
    setter = "    def __set__(self, instance, value):\n        pass" if data_descriptor else ""
    checker = run_analysis(source.replace("DESCRIPTOR_SETTER", setter))
    # Flow-insensitive assignments represent both temporal alternatives.
    checker.assert_call_edge("module", "plain")
    checker.assert_call_edge("module", "through_descriptor")


@pytest.mark.parametrize("context", ["0-cfa", "2-cfa"])
def test_distinct_descriptor_constructors_do_not_alias_by_source_location(tmp_path, context):
    from pythonstan.analysis.pointer.kcfa.config import Config
    from pythonstan.world.pipeline import Pipeline
    filename = tmp_path / "constructors.py"
    filename.write_text('''
def allowed(cls):
    return 1
def forbidden(cls):
    return 2
class Proxy:
    def __init__(self, wrapped):
        self.wrapped = wrapped
    def __get__(self, instance, owner):
        return self.wrapped.__get__(instance, owner)
class Container:
    selected = Proxy(classmethod(allowed))
    other = Proxy(classmethod(forbidden))
Container.selected()
''')
    pipeline = Pipeline(config={
        "filename": str(filename), "project_path": str(tmp_path), "library_paths": [],
        "import_level": 0, "mock_libs": False, "time_count": False,
        "analysis": [{"name": "pta", "id": "PointerAnalysis", "prev_analysis": ["ir"],
                      "options": Config(context_policy=context, log_level="ERROR").to_dict()}],
    })
    pipeline.run()
    query = pipeline.analysis_manager.get_results("pta").query()
    edges = {(e.callsite.scope.stmt.get_qualname(), e.callee.stmt.get_qualname())
             for e in query.call_graph().edges}
    assert ("constructors", "constructors.allowed") in edges
    objects = {obj for obj in query._state._heap.objects.values()
               if isinstance(obj, BuiltinInstanceObject) and obj.builtin_type == "classmethod"}
    assert len(objects) == 2
    if context != "0-cfa":
        assert ("constructors", "constructors.forbidden") not in edges
    # At 0-CFA the Proxy.__init__ parameter slots intentionally merge, so the
    # wrapper fields may alias even though the two allocation sites do not.


def test_same_descriptor_site_reuses_selected_context(run_analysis):
    checker = run_analysis('''
def first(cls):
    return 1
def second(cls):
    return 2
def construct(function):
    return classmethod(function)
left = construct(first)
right = construct(second)
''', context_policy="0-cfa")
    query = checker.result.query()
    objects = {obj for obj in query._state._heap.objects.values()
               if isinstance(obj, BuiltinInstanceObject) and obj.builtin_type == "classmethod"}
    assert len(objects) == 1
