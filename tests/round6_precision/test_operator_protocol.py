"""Source-grounded arithmetic protocol callback and result-flow regressions."""
import pytest
from tests.pointer.kcfa.test_ai_bridge import analyze_source


@pytest.mark.parametrize("ai", [False, True])
@pytest.mark.parametrize("method,expression", [("__add__", "Box() + 1"), ("__radd__", "1 + Box()")])
def test_binary_callback_result_reaches_later_call(tmp_path, ai, method, expression):
    source = f'''
def callback(): return 17
class Box:
    def {method}(self, other): return callback
result = ({expression})()
'''
    namespace = {}
    exec(source, namespace)
    assert namespace["result"] == 17
    query, edges = analyze_source(tmp_path, source, enable_ai_intrinsics=ai)
    assert ("subject", "subject.Box." + method) in edges
    assert ("subject", "subject.callback") in edges
    assert not query.get_semantic_status()["certificate_eligible"]


@pytest.mark.parametrize("method,expression", [("__neg__", "-box"), ("__pos__", "+box"), ("__invert__", "~box")])
def test_unary_callback_and_result_flow(tmp_path, method, expression):
    _, edges = analyze_source(tmp_path, f'''
def callback(): return 19
class Box:
    def {method}(self): return callback
box = Box()
result = ({expression})()
''')
    assert ("subject", "subject.Box." + method) in edges
    assert ("subject", "subject.callback") in edges


def test_not_preserves_hook_effect_without_returning_operand_or_hook_value(tmp_path):
    query, edges = analyze_source(tmp_path, '''
def callback(): return 1
class Box:
    def __bool__(self):
        callback()
        return False
box = Box()
value = not box
''')
    assert ("subject", "subject.Box.__bool__") in edges
    assert ("subject.Box.__bool__", "subject.callback") in edges
    from pythonstan.world import World
    from pythonstan.analysis.pointer.kcfa.object import InstanceObject
    assert not any(isinstance(obj, InstanceObject)
                   for obj in query.points_to_at("value", World().get_entry_module()))


def test_late_type_patch_and_instance_shadow_exclusion(tmp_path):
    _, edges = analyze_source(tmp_path, '''
def callback(): return 23
def shadow(other): return callback
def addition(self, other): return callback
class Box: pass
box = Box()
box.__add__ = shadow
Box.__add__ = addition
result = (box + 1)()
''')
    assert ("subject", "subject.addition") in edges
    assert ("subject", "subject.callback") in edges
    assert ("subject", "subject.shadow") not in edges


def test_discarded_operator_result_retains_side_effect_callbacks(tmp_path):
    _, edges = analyze_source(tmp_path, '''
def callback(): return 1
class Box:
    def __add__(self, other):
        callback()
        return None
Box() + 1
''')
    assert ("subject", "subject.Box.__add__") in edges
    assert ("subject.Box.__add__", "subject.callback") in edges


def test_normal_and_reflected_candidates_are_both_retained(tmp_path):
    query, edges = analyze_source(tmp_path, '''
class Left:
    def __add__(self, other): return NotImplemented
class Right:
    def __radd__(self, other): return 29
result = Left() + Right()
''')
    assert ("subject", "subject.Left.__add__") in edges
    assert ("subject", "subject.Right.__radd__") in edges
    assert any("operator_protocol" in item.get("effects", ())
               for item in query.get_residual_effects())


@pytest.mark.parametrize("expression", ["1 + 1", "not False"])
def test_partial_value_producer_never_authorizes_branch_pruning(tmp_path, expression):
    _, edges = analyze_source(tmp_path, f'''
def yes(): return 1
def no(): return 0
def invoke():
    flag = False
    flag = {expression}
    if flag: yes()
    else: no()
invoke()
''')
    assert ("subject.invoke", "subject.yes") in edges
    assert ("subject.invoke", "subject.no") in edges
