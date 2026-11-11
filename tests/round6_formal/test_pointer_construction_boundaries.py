"""Independent source class-construction may-call and non-hook checks."""
import pytest

from tests.pointer.kcfa.test_ai_bridge import analyze_source


@pytest.mark.parametrize("ai", [False, True])
def test_inherited_descriptor_type_hook_receives_own_class_value(tmp_path, ai):
    source = '''def callback(): return 13
class BaseDescriptor:
    def __set_name__(self, owner, name):
        owner.selected = self.callback
class Descriptor(BaseDescriptor):
    def __init__(self, callback): self.callback = callback
class Owner:
    field = Descriptor(callback)
result = Owner.selected()
'''
    namespace = {}
    exec(source, namespace)
    assert namespace["result"] == 13
    query, edges = analyze_source(tmp_path, source, enable_ai_intrinsics=ai)
    assert ("subject", "subject.BaseDescriptor.__set_name__") in edges
    assert ("subject", "subject.callback") in edges
    assert query.get_semantic_status()["certificate_eligible"] is False


def test_derived_class_has_no_automatic_inherited_descriptor_hook(tmp_path):
    source = '''events = []
class Descriptor:
    def __set_name__(self, owner, name): events.append(name)
class Parent:
    field = Descriptor()
def create_child():
    class Child(Parent): pass
    return Child
create_child()
result = events
'''
    namespace = {}
    exec(source, namespace)
    assert namespace["result"] == ["field"]
    _, edges = analyze_source(tmp_path, source)
    assert ("subject", "subject.Descriptor.__set_name__") in edges
    assert ("subject.create_child", "subject.Descriptor.__set_name__") not in edges


def test_late_replacement_of_own_plain_field_does_not_invoke_set_name(tmp_path):
    source = '''events = []
class Descriptor:
    def __set_name__(self, owner, name): events.append(name)
class Owner:
    field = None
Owner.field = Descriptor()
result = events
'''
    namespace = {}
    exec(source, namespace)
    assert namespace["result"] == []
    _, edges = analyze_source(tmp_path, source)
    assert ("subject", "subject.Descriptor.__set_name__") not in edges


@pytest.mark.parametrize("ai", [False, True])
def test_metaclass_returned_function_and_its_result_callback_flow(tmp_path, ai):
    source = '''def callback(): return 17
class Meta(type):
    def __new__(meta, name, bases, namespace): return namespace["factory"]
class Replacement(metaclass=Meta):
    def factory(): return callback
result = Replacement()()
'''
    namespace = {}
    exec(source, namespace)
    assert namespace["result"] == 17
    query, edges = analyze_source(tmp_path, source, enable_ai_intrinsics=ai)
    assert ("subject", "subject.Meta.__new__") in edges
    assert ("subject", "subject.Replacement.factory") in edges
    assert ("subject", "subject.callback") in edges
    assert query.get_semantic_status()["certificate_eligible"] is False


def test_repeated_nested_class_factory_retains_each_callback(tmp_path):
    source = '''def first(): return 1
def second(): return 2
class Descriptor:
    def __init__(self, callback): self.callback = callback
    def __set_name__(self, owner, name): owner.callback = self.callback
def factory(callback):
    class Owner:
        field = Descriptor(callback)
    class Noise:
        unused = None
    return Owner
a = factory(first)
b = factory(second)
a.callback()
result = b.callback()
'''
    namespace = {}
    exec(source, namespace)
    assert namespace["result"] == 2
    _, edges = analyze_source(tmp_path, source, context_policy="2-cfa")
    assert ("subject.factory", "subject.Descriptor.__set_name__") in edges
    assert ("subject", "subject.first") in edges
    assert ("subject", "subject.second") in edges


def test_metaclass_new_closure_callback_reaches_created_class(tmp_path):
    source = '''def callback(): return 19
def build(selected):
    class Meta(type):
        def __new__(meta, name, bases, namespace):
            namespace["selected"] = selected
            return type.__new__(meta, name, bases, namespace)
    class Target(metaclass=Meta): pass
    return Target
Created = build(callback)
result = Created.selected()
'''
    namespace = {}
    exec(source, namespace)
    assert namespace["result"] == 19
    _, edges = analyze_source(tmp_path, source)
    assert ("subject.build", "subject.build.Meta.__new__") in edges
    assert ("subject", "subject.callback") in edges
