"""Class hook facts derived by KCFA; concrete executions are independent tests."""
import pytest

from tests.pointer.kcfa.test_ai_bridge import analyze_source


SET_NAME = '''
def activated(): return 7
class Descriptor:
    def __set_name__(self, owner, name):
        owner.callback = activated
class Owner:
    field = Descriptor()
result = Owner.callback()
'''

META_NEW = '''
def activated(): return 7
class Meta(type):
    def __new__(mcls, name, bases, namespace):
        namespace["callback"] = activated
        return type.__new__(mcls, name, bases, namespace)
class Owner(metaclass=Meta):
    pass
result = Owner.callback()
'''


@pytest.mark.parametrize("source,hook", [(SET_NAME, "Descriptor.__set_name__"), (META_NEW, "Meta.__new__")])
@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("index_sensitive", [False, True])
def test_construction_hook_restores_callback(tmp_path, source, hook, enabled, index_sensitive):
    namespace = {}
    exec(source, namespace)
    assert namespace["result"] == 7
    query, edges = analyze_source(tmp_path, source, enable_ai_intrinsics=enabled,
                                  index_sensitive=index_sensitive)
    assert ("subject", "subject." + hook) in edges
    assert ("subject", "subject.activated") in edges
    assert query.get_statistics()["converged"] == 1
    assert not query.get_semantic_status()["certificate_eligible"]


def test_inherited_descriptor_does_not_repeat_set_name(tmp_path):
    _, edges = analyze_source(tmp_path, '''
def initialized(): return 1
class Descriptor:
    def __set_name__(self, owner, name): initialized()
class Parent:
    field=Descriptor()
def make_child():
    class Child(Parent): pass
    return Child
make_child()
''')
    assert ("subject", "subject.Descriptor.__set_name__") in edges
    assert ("subject.make_child", "subject.Descriptor.__set_name__") not in edges


def test_later_setattr_has_no_automatic_set_name(tmp_path):
    _, edges = analyze_source(tmp_path, '''
class Descriptor:
    def __set_name__(self, owner, name): pass
class Owner: pass
Owner.later=Descriptor()
''')
    assert ("subject", "subject.Descriptor.__set_name__") not in edges


def test_instance_set_name_shadow_is_ignored(tmp_path):
    _, edges = analyze_source(tmp_path, '''
def shadow(owner,name): return 1
class Descriptor:
    def __set_name__(self, owner, name): pass
descriptor=Descriptor()
descriptor.__set_name__=shadow
class Owner:
    field=descriptor
''')
    assert ("subject", "subject.Descriptor.__set_name__") in edges
    assert ("subject", "subject.shadow") not in edges


def test_metaclass_init_mutates_actual_class_receiver(tmp_path):
    _, edges = analyze_source(tmp_path, '''
def callback(): return 9
class Meta(type):
    def __init__(cls,name,bases,namespace):
        cls.callback=callback
class Owner(metaclass=Meta): pass
result=Owner.callback()
''')
    assert ("subject", "subject.Meta.__init__") in edges
    assert ("subject", "subject.callback") in edges


def test_set_name_sees_actual_attribute_name(tmp_path):
    _, edges = analyze_source(tmp_path, '''
def callback(): return 1
class Descriptor:
    def __set_name__(self, owner, name):
        setattr(owner,name,callback)
class Owner:
    chosen=Descriptor()
result=Owner.chosen()
''')
    assert ("subject", "subject.callback") in edges


def test_meta_new_receives_own_namespace_functions(tmp_path):
    _, edges = analyze_source(tmp_path, '''
class Meta(type):
    def __new__(mcls,name,bases,namespace):
        namespace["factory"]()
        return type.__new__(mcls,name,bases,namespace)
class Owner(metaclass=Meta):
    def factory(): return 4
''')
    assert ("subject.Meta.__new__", "subject.Owner.factory") in edges
