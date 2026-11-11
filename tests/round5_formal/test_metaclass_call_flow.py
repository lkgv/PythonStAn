"""Class invocation must retain metaclass callbacks and actual receivers."""
import pytest

from pythonstan.analysis.pointer.kcfa.object import ClassObject, FunctionObject
from pythonstan.ir import IRFunc, IRModule
from tests.pointer.kcfa.test_ai_bridge import analyze_source
from tests.round5_formal.test_heap_views import schedule


@pytest.mark.parametrize("context", ["0-cfa", "2-cfa"])
def test_inherited_metaclass_call_forwards_actual_class_receiver(tmp_path, schedule, context):
    query, edges = analyze_source(tmp_path, '''
def first(): return 1
def second(): return 2
class ParentMeta(type):
    def __call__(cls):
        return cls.callback
class Meta(ParentMeta):
    pass
class A(metaclass=Meta):
    callback = first
class B(metaclass=Meta):
    callback = second
left = A()
right = B()
left()
right()
''', context_policy=context, enable_ai_intrinsics=False)
    assert {("subject", "subject.first"), ("subject", "subject.second")} <= edges
    method = next(scope for scope in query.get_reachable_scopes()
                  if isinstance(scope, IRFunc) and scope.get_qualname() == "subject.ParentMeta.__call__")
    receivers = {obj.ir.name for obj in query.points_to_at("cls", method) if isinstance(obj, ClassObject)}
    assert receivers == {"A", "B"}
    assert query.get_statistics()["converged"] == 1


@pytest.mark.parametrize("context", ["0-cfa", "2-cfa"])
@pytest.mark.parametrize("ai_enabled", [False, True])
def test_late_patch_and_keyword_forwarding_retain_callback_result(tmp_path, schedule, context, ai_enabled):
    query, edges = analyze_source(tmp_path, '''
def callback(): return 19
def construct(cls, *, selected):
    return selected
class Meta(type):
    pass
class Widget(metaclass=Meta):
    pass
Meta.__call__ = construct
selected = Widget(selected=callback)
result = selected()
''', context_policy=context, enable_ai_intrinsics=ai_enabled, track_unknowns=False)
    assert ("subject", "subject.construct") in edges
    assert ("subject", "subject.callback") in edges
    module = next(scope for scope in query.get_reachable_scopes() if isinstance(scope, IRModule))
    assert "callback" in {obj.ir.name for obj in query.points_to_at("selected", module) if isinstance(obj, FunctionObject)}
    assert any(effect["effects"] for effect in query.get_residual_effects())
    assert query.get_statistics()["converged"] == 1


@pytest.mark.parametrize("context", ["0-cfa", "2-cfa"])
def test_metaclass_inherited_by_subclass_and_reused_callsite_keep_all_arguments(tmp_path, schedule, context):
    query, edges = analyze_source(tmp_path, '''
def first(): return 1
def second(): return 2
class Meta(type):
    def __call__(cls, callback):
        return callback
class Parent(metaclass=Meta):
    pass
class Child(Parent):
    pass
def construct(cls, callback):
    return cls(callback)
left = construct(Parent, first)
right = construct(Child, second)
left()
right()
''', context_policy=context, enable_ai_intrinsics=False)
    assert {("subject", "subject.first"), ("subject", "subject.second")} <= edges
    assert ("subject.construct", "subject.Meta.__call__") in edges
    assert query.get_statistics()["converged"] == 1
