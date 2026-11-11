"""Closed heap transfers compared to independent CPython witnesses.

All snippets are trusted test data; production never executes their bytecode.
"""
import hashlib
import pytest
from pythonstan.analysis.pointer.ai.certified.closed_heap_effects import (
    SEMANTICS_VERSION, analyze_closed_source, check_closed_effects,
)

WITNESSES = [
'''a=object()
b=object()
d={a:"a",b:"b"}
result=[a is a,a is b,a==a,a==b,bool(a),type(a).__name__,d[a],len(d)]
''',
'''events=[]
def mark(x):
    events.append(x)
    return x
def outer():
    async def dormant(value=mark("default")):
        await unknown()
    return 2
result=[outer(),events]
''',
'''events=[]
class M(type):
    @classmethod
    def __prepare__(meta,name,bases):
        events.append("prepare:"+name)
        return {"prepared":7}
    def __new__(meta,name,bases,ns):
        events.append("new:"+ns["__qualname__"])
        events.append(len(ns))
        return type.__new__(meta,name,bases,ns)
    def __init__(cls,name,bases,ns):
        events.append("init:"+cls.__name__)
class C(metaclass=M):
    x=3
result=[events,C.prepared,C.x]
''',
'''events=[]
class M(type):
    def __new__(meta,name,bases,ns):
        return 7
    def __init__(cls,*args):
        events.append("bad")
class C(metaclass=M):pass
result=[C,events]
''',
'''class A(type):pass
class B(A):pass
class X(metaclass=A):pass
class Y(metaclass=B):pass
class Z(X,Y):pass
result=[type(Z).__name__,isinstance(Z,B),isinstance(Z,A)]
''',
'''class A(type):pass
class B(type):pass
class X(metaclass=A):pass
class Y(metaclass=B):pass
try:
    class Z(X,Y):pass
except TypeError:
    result="conflict"
''',
'''class M(type):
    def __call__(cls,x):
        return cls.__name__+x
class C(metaclass=M):pass
result=C("!")
''',
'''events=[]
class D:
    def __get__(self,obj,owner):
        return obj.__name__
    def __set__(self,obj,value):
        events.append(value)
class M(type):
    x=D()
class C(metaclass=M):
    x="shadow"
C.x=7
result=[C.x,events]
''',
'''class M(type):
    def __bool__(cls):return False
class C(metaclass=M):pass
result=bool(C)
''',
'''class E(ValueError):pass
try:raise E("x")
except E as error:
    result=[isinstance(error,E),isinstance(error,ValueError),isinstance(error,Exception),type(error).__name__,list(error.args)]
''',
'''class E(Exception):
    args="override"
result=E("x").args
''',
'''values=[1]
alias=values
values+=values
result=[values,alias,values is alias]
''',
'''events=[]
class A:
    def __init__(self):self.items=[1]
def owner():
    events.append("owner")
    return a
def right():
    events.append("right")
    return [2]
a=A()
alias=a.items
owner().items+=right()
result=[events,a.items,alias]
''',
'''values=[1,2]
it=reversed(values)
values.append(3)
first=next(it)
values[0]=4
result=[first,list(it)]
''',
'''from __future__ import annotations
seen=[]
class M(type):
    def __new__(meta,name,bases,ns):
        seen.append(ns["__annotations__"])
        seen.append(ns["__doc__"])
        return type.__new__(meta,name,bases,ns)
class C(metaclass=M):
    "doc"
    x:int
    _hidden: list[str]
result=seen
''',
'''__name__="fixture"
class M(type):
    def __new__(meta,name,bases,ns):
        return type.__new__(meta,name,bases,ns)
class C(metaclass=M):pass
result=[C.__module__,C.__qualname__]
''',
'''def outer():
    def inner():
        class C:pass
        return C.__qualname__
    return inner()
result=outer()
''',
'''def g():yield 1
class Meta(type):pass
result=1
''',
]

@pytest.mark.parametrize("source", WITNESSES)
def test_closed_protocols_match_python(source):
    ns={"__name__":"test_witness"}
    exec(compile(source,"<closed-heap-witness>","exec"),ns)
    report=check_closed_effects(source,{
        "source_sha256":hashlib.sha256(source.encode()).hexdigest(),
        "semantics_version":SEMANTICS_VERSION,
        "globals":{"result":ns["result"]},
    })
    assert report["status"]=="conditional_verified",report
    assert report["observations"]=={"result":ns["result"]}
    assert not report["full_python_soundness_proved"]

@pytest.mark.parametrize("source",[
'''class M(type):
    def mro(cls):return [cls]
class C(metaclass=M):pass
result=1
''',
'''class M(type):
    def __new__(m,name,bases,ns):
        return ns["__module__"]=="__main__"
class C(metaclass=M):pass
result=C
''',
'''class E(Exception):
    def __init__(self,x):self.x=x
result=E(1)
''',
'''class Key:
    def __hash__(self):return 1
    def __eq__(self,other):return True
a={Key():1,Key():2}
result=len(a)
''',
'''class M(type):
    def __hash__(cls):return 1
class C(metaclass=M):pass
result={C:1}
''',
'''class A:
    def __init_subclass__(cls):pass
class B(A):pass
result=1
''',
'''class A:
    def __new__(cls):return 1
result=A()
''',
'''def entry(a,b):return a is b
result=entry((1,2),(1,2))
''',
])
def test_unmodeled_protocols_do_not_verify(source):
    report=analyze_closed_source(source,["result"])
    assert report["status"]=="unsupported",report
    assert not report["normal_entry_completion"]

@pytest.mark.parametrize("source,claim",[("result=0.0",-0.0),("result=[-0.0]",[0.0])])
def test_signed_zero_claims_are_type_and_bit_exact(source,claim):
    report=check_closed_effects(source,{"source_sha256":hashlib.sha256(source.encode()).hexdigest(),
        "semantics_version":SEMANTICS_VERSION,"globals":{"result":claim}})
    assert report["status"]=="rejected"
    assert report["conditional_effects_verified"]==0


def test_type_name_null_character_is_modeled_value_error():
    source="try:\n    type('a\\0b',(),{})\nexcept ValueError:\n    result='invalid'\n"
    assert analyze_closed_source(source,["result"])["observations"]=={"result":"invalid"}


def test_tuple_constructor_preserves_existing_tuple_identity():
    source="a=tuple([1,2])\nb=tuple(a)\nresult=a is b"
    assert analyze_closed_source(source,["result"])["observations"]=={"result":True}


def test_class_statement_obeys_metaclass_metaclass_call():
    source="""class MetaMeta(type):
    def __call__(meta,name,bases,namespace):return 7
class Meta(type,metaclass=MetaMeta):pass
class C(metaclass=Meta):pass
result=C
"""
    ns={}
    exec(source,ns)
    assert ns["result"]==7
    assert analyze_closed_source(source,["result"])["observations"]=={"result":7}


def test_custom_meta_meta_prepare_attribute_stays_unsupported():
    source="""class MetaMeta(type):
    def __getattribute__(meta,name):return 7
class Meta(type,metaclass=MetaMeta):pass
class C(metaclass=Meta):pass
result=1
"""
    assert analyze_closed_source(source,["result"])["status"]=="unsupported"
