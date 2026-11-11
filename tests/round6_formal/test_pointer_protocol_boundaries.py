"""Independent ordinary-instance operator/truth may-callback checks."""
import pytest

from tests.pointer.kcfa.test_ai_bridge import analyze_source


@pytest.mark.parametrize("ai", [False, True])
@pytest.mark.parametrize("policy", ["0-cfa", "2-cfa"])
def test_reflected_operator_preserves_receiver_and_left_operand_bindings(tmp_path, ai, policy):
    source = '''def left_callback(): return "left"
def right_callback(): return "right"
class Left:
    def __init__(self): self.callback = left_callback
class Right:
    def __init__(self): self.callback = right_callback
    def __rsub__(self, other): return other.callback
result = (Left() - Right())()
'''
    namespace = {}
    exec(source, namespace)
    assert namespace["result"] == "left"
    query, edges = analyze_source(tmp_path, source, enable_ai_intrinsics=ai, context_policy=policy)
    assert ("subject", "subject.Right.__rsub__") in edges
    assert ("subject", "subject.left_callback") in edges
    assert ("subject", "subject.right_callback") not in edges
    from pythonstan.analysis.pointer.kcfa.object import InstanceObject
    hook_edges = [edge for edge in query.call_graph().edges
                  if edge.callee.stmt.get_qualname() == "subject.Right.__rsub__"]
    assert hook_edges
    for edge in hook_edges:
        for parameter, expected in (("self", "subject.Right"), ("other", "subject.Left")):
            values = query.points_to_at(parameter, edge.callee.stmt, edge.callee.context)
            assert {value.class_obj.ir.get_qualname() for value in values
                    if isinstance(value, InstanceObject)} == {expected}
    assert query.get_semantic_status()["certificate_eligible"] is False


@pytest.mark.parametrize("ai", [False, True])
def test_inherited_reflected_hook_ignores_instance_shadow(tmp_path, ai):
    source = '''def callback(): return 9
def shadow(other): return 0
class Base:
    def __radd__(self, other): return callback
class Child(Base): pass
box = Child()
box.__radd__ = shadow
result = (1 + box)()
'''
    namespace = {}
    exec(source, namespace)
    assert namespace["result"] == 9
    _, edges = analyze_source(tmp_path, source, enable_ai_intrinsics=ai)
    assert ("subject", "subject.Base.__radd__") in edges
    assert ("subject", "subject.callback") in edges
    assert ("subject", "subject.shadow") not in edges


@pytest.mark.parametrize("method,expression", [("__radd__", "1 + box"), ("__neg__", "-box")])
def test_late_base_hook_mutation_reaches_existing_derived_receiver(tmp_path, method, expression):
    signature = "self, other" if method == "__radd__" else "self"
    source = f'''def callback(): return 11
class Base: pass
class Child(Base): pass
box = Child()
def hook({signature}): return callback
Base.{method} = hook
result = ({expression})()
'''
    namespace = {}
    exec(source, namespace)
    assert namespace["result"] == 11
    _, edges = analyze_source(tmp_path, source)
    assert ("subject", "subject.hook") in edges
    assert ("subject", "subject.callback") in edges


def test_derived_truth_hook_retains_inherited_callback_effect(tmp_path):
    source = '''def callback(): return 5
def shadow(): return True
class Base:
    def __bool__(self):
        self.callback()
        return False
class Child(Base):
    def __init__(self): self.callback = callback
value = Child()
value.__bool__ = shadow
result = not value
'''
    namespace = {}
    exec(source, namespace)
    assert namespace["result"] is True
    _, edges = analyze_source(tmp_path, source)
    assert ("subject", "subject.Base.__bool__") in edges
    assert ("subject.Base.__bool__", "subject.callback") in edges
    assert ("subject", "subject.shadow") not in edges


def test_right_subclass_reflected_priority_retains_both_may_paths(tmp_path):
    source = '''def normal(): return "normal"
def reflected(): return "reflected"
class Base:
    def __add__(self, other): return normal
class Child(Base):
    def __radd__(self, other): return reflected
result = (Base() + Child())()
'''
    namespace = {}
    exec(source, namespace)
    assert namespace["result"] == "reflected"
    query, edges = analyze_source(tmp_path, source)
    assert ("subject", "subject.Child.__radd__") in edges
    assert ("subject", "subject.reflected") in edges
    # Priority remains unresolved in this monotone may-analysis; do not erase
    # the normal method candidate merely because the concrete witness chose RHS.
    assert ("subject", "subject.Base.__add__") in edges
    assert query.get_semantic_status()["certificate_eligible"] is False
