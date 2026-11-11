"""Source-level protocol and namespace oracles, independent of helper layout."""
import ast
import pytest
from pythonstan import Source,ValueQuery,analyze


def run(source,bindings=None,**abstract):
    r=analyze(Source(source,module='subject'),config={'engine':'ai_primary','resources':{'max_work':50000,'max_seconds':0},'inputs':{'bindings':bindings or {}},'abstract':abstract},queries=['values','calls','effects','stats'])
    return r,r.raw_snapshot()

def exact(result,name,value):
    answer=result.query(ValueQuery('subject',name));assert answer.status=='answered',answer
    v=answer.value
    assert v['constants']==[value] and not v['top'] and not v['top_sorts'] and not v['may_unbound'] and not v['bottom'],v


@pytest.mark.parametrize(('source','expected'),[
("class A:\n    x='A'\nclass B(A):pass\nclass C(A):\n    x='C'\nclass D(B,C):pass\nresult=D().x\n",'C'),
("class C:\n    locals()['dynamic']='after'\n    result=dynamic\nresult=C.result\n",'after'),
("def f():\n    x='before'\n    view=locals()\n    view['x']='after'\n    return x\nresult=f()\n",'before'),
("view=globals()\nview['dynamic']='after'\nresult=dynamic\n",'after'),
("class C:\n    x='before'\nview=C.__dict__\ntry:\n    view['x']='after'\nexcept TypeError:\n    result=C.x\n",'before'),
("'MODULE-DOC'\nclass C:\n    observed=__doc__\nresult=C.observed\n",'MODULE-DOC'),
("class C:\n    'CLASS-DOC'\n    observed=__doc__\nresult=C.observed\n",'CLASS-DOC'),
("class C:\n    def __getattribute__(self,name):\n        return 'intercepted'\nresult=vars(C())\n",'intercepted'),
("def f(x='before'):return x\nf.__dict__['$default:0']='after'\nseen=getattr(f,'$default:0')\nresult=f()\n",'before'),
("d={'<unknown-key>':'after'}\nresult=d['<unknown-key>']\n",'after'),
("def write(d):d[None]='after'\nd={None:'before','None':'string'}\nwrite(d)\nresult=d[None]\nother=d['None']\n",'after'),
("try:\n    (1).upper()\nexcept AttributeError:\n    result='caught'\n",'caught'),
("class A:pass\ntry:\n    class Bad(A,A):pass\nexcept TypeError:\n    result='caught'\n",'caught'),
("class A:pass\nclass B:pass\nclass X(A,B):pass\nclass Y(B,A):pass\ntry:\n    class Bad(X,Y):pass\nexcept TypeError:\n    result='caught'\n",'caught'),
("try:\n    class Bad(42):pass\nexcept TypeError:\n    result='caught'\n",'caught'),
("view=globals()\ndel __name__\ntry:\n    result=view['__name__']\nexcept KeyError:\n    result='deleted'\n",'deleted'),
])
def test_actual_source_semantic_oracles(source,expected):
    result,snapshot=run(source);exact(result,'result',expected)
    assert result.coverage_status=='closed_under_profile',snapshot['obligations']
    if 'seen=' in source:exact(result,'seen','after')
    if 'other=' in source:exact(result,'other','string')


def test_existing_default_object_slot_precedes_getattr():
    result,snapshot=run("class C:\n    def __getattr__(self,name):\n        return lambda:'fake'\nresult=C().__repr__()\n")
    assert not any(call['callee']=='subject.C.__getattr__' for call in snapshot['calls'])
    assert result.coverage_status=='open'
    value=result.query(ValueQuery('subject','result')).value
    assert value['top'] and value['constants']!=['fake']


@pytest.mark.parametrize(('pairs','unpack','expected'),[(2,False,'k0v0k1v1H'),(15,False,''.join(f'k{i}v{i}' for i in range(15))+'H'),(16,False,'k0v0H'),(20,False,'k0v0H'),(2,True,'k0v0H')])
def test_dict_hash_callbacks_follow_actual_cpython_build_group(pairs,unpack,expected):
    prefix="""log=''
class K:
    def __hash__(self):
        global log
        log+='H'
        raise ValueError()
def key(tag):
    global log
    log+=tag
    return K()
def value(tag):
    global log
    log+=tag
    return 0
"""
    entries=[f"key('k{i}'):value('v{i}')" for i in range(pairs)]
    if unpack:entries.insert(1,"**{}")
    source=prefix+'try:\n    d={'+','.join(entries)+'}\nexcept ValueError:\n    result=log\n'
    # Test-only CPython execution supplies an independent ordering oracle.
    scope={};exec(compile(source,'<protocol-oracle>','exec'),scope)
    assert scope['result']==expected
    result,snapshot=run(source);exact(result,'result',expected)
    assert result.coverage_status=='closed_under_profile',snapshot['obligations']
    assert snapshot['stats']['dictionary_build_profile'].startswith('cpython-3.12.')


def test_dict_hash_group_cache_uses_entire_ast_after_mutation():
    from pythonstan.analysis.contracts import SourceModule
    from pythonstan.analysis.engines.abstract.driver import InterproceduralDriver
    d=InterproceduralDriver([SourceModule('subject','subject.py','x={}')])
    node=ast.parse('{k():v(),q():z()}',mode='eval').body
    assert d.transfer.dictionaries.plan(node)==(('pairs',(0,1)),)
    node.keys.insert(1,None);node.values.insert(1,ast.copy_location(ast.Dict(keys=[],values=[]),node))
    assert d.transfer.dictionaries.plan(node)!= (('pairs',(0,1)),)


def test_typed_numeric_attributes_retain_facets_and_exact_imaginary_zero():
    result,_=run('result=value.real\nimag=value.imag\n',{'subject.value':{'type':'int','top':True}})
    value=result.query(ValueQuery('subject','result')).value
    assert not value['top'] and value['types']==['int']
    exact(result,'imag',0)


def test_deleted_module_metadata_uses_real_builtin_value_not_fake_callable():
    result,_=run("view=globals()\ndel __name__\nresult=__name__\n")
    exact(result,'result','builtins')


@pytest.mark.parametrize('body',[
    "def __getattribute__(self,name):\n        raise AttributeError()",
    "@property\n    def missing(self):\n        raise AttributeError()",
])
def test_attributeerror_from_lookup_protocol_runs_getattr_once(body):
    source="class C:\n    "+body+"\n    def __getattr__(self,name):\n        return 'after'\nresult=C().missing\n"
    result,snapshot=run(source)
    exact(result,'result','after')
    assert result.coverage_status=='closed_under_profile',snapshot['obligations']


@pytest.mark.parametrize('hook',[
    "def __init_subclass__(cls):\n        global marker\n        marker='after'",
    "@classmethod\n    def __init_subclass__(cls):\n        global marker\n        marker='after'",
    "@staticmethod\n    def __init_subclass__():\n        global marker\n        marker='after'",
])
def test_second_c3_base_init_subclass_and_descriptor_kind(hook):
    source="marker='before'\nclass A:pass\nclass B:\n    "+hook+"\nclass C(A,B):pass\nresult=marker\n"
    scope={};exec(compile(source,'<protocol-oracle>','exec'),scope)
    assert scope['result']=='after'
    result,snapshot=run(source)
    exact(result,'result','after')
    assert result.coverage_status=='closed_under_profile',snapshot['obligations']


def test_unmodeled_builtin_subclass_is_real_open_not_closed_false_typeerror():
    result,snapshot=run("class S(str):pass\nresult=S\n")
    assert result.coverage_status=='open' and snapshot['effects']['havoc']
    assert result.query(ValueQuery('subject','result')).value['top']


@pytest.mark.parametrize('target',["d={}\nd[key]='after'\nresult=len(d)\n", "d=globals()\nd[key]='after'\nresult=('a' in d) or ('b' in d)\n"])
def test_finite_key_choice_is_one_write_not_simultaneous_creation(target):
    result,_=run("if choose:key='a'\nelse:key='b'\n"+target,{'subject.choose':{'type':'bool','top':True}})
    exact(result,'result',1 if 'len(d)' in target else True)

@pytest.mark.parametrize('source',[
    "d=globals()\nd[1]='after'\nresult=d[1]\n",
    "class C:\n    locals()[1]='after'\nresult=C.__dict__[1]\n",
])
def test_nonstr_namespace_keys_are_real_mapping_entries(source):
    scope={};exec(compile(source,'<namespace-oracle>','exec'),scope)
    assert scope['result']=='after'
    result,snapshot=run(source);exact(result,'result','after')
    assert result.coverage_status=='closed_under_profile',snapshot['obligations']



def test_source_class_field_can_hide_inherited_class_slot():
    source="class C:\n    __class__='source-field'\nresult=C().__class__\n"
    scope={};exec(compile(source,'<protocol-oracle>','exec'),scope)
    assert scope['result']=='source-field'
    result,snapshot=run(source);exact(result,'result','source-field')
    assert result.coverage_status=='closed_under_profile',snapshot['obligations']


def test_unmodeled_instance_dictionary_replacement_has_actual_havoc():
    source="class C:pass\nc=C()\nc.x='before'\nc.__dict__={'x':'after'}\nresult=c.x\n"
    result,snapshot=run(source)
    assert result.coverage_status=='open' and snapshot['effects']['havoc']
    value=result.query(ValueQuery('subject','result')).value
    assert value['top'] and value['constants']!=['before']


@pytest.mark.parametrize('expression',["(None).__class__", "b'hello'.decode", "(1j).conjugate"])
def test_unmodeled_existing_scalar_attributes_keep_normal_complement(expression):
    result,snapshot=run('result='+expression+'\n')
    assert result.query(ValueQuery('subject','result')).status=='answered'
    assert result.query(ValueQuery('subject','result')).value['top']
    assert result.coverage_status=='open'



def test_bound_method_identity_and_function_user_attributes_survive_binding():
    source="class C:\n    def method(self):return self\nc=C()\nC.method.marker='after'\nmethod=c.method\nresult=method.marker\nreceiver=method.__self__ is c\nfunction=method.__func__ is C.method\n"
    result,snapshot=run(source);exact(result,'result','after');exact(result,'receiver',True);exact(result,'function',True)
    assert result.coverage_status=='closed_under_profile',snapshot['obligations']


def test_property_missing_setter_metadata_is_actual_none():
    result,_=run("class C:\n    @property\n    def x(self):return 1\nresult=C.x.fset is None\n")
    exact(result,'result',True)

@pytest.mark.parametrize('source',["def f():pass\nresult=f.__get__\n", "class C:pass\nresult=C.mro\n"])
def test_known_unmodeled_descriptor_entries_retain_normal_open_value(source):
    result,_=run(source)
    assert result.coverage_status=='open' and result.query(ValueQuery('subject','result')).status=='answered'


@pytest.mark.parametrize('body',[
    "x='before'\n    d=locals()\n    x='after'\n    locals()\n    return d['x']",
    "d=locals()\n    d['extra']='after'\n    locals()\n    return d['extra']",
    "d=locals()\n    other=locals()\n    return 'after' if d is other else 'wrong'",
])
def test_fast_locals_sync_refreshes_declared_slots_keeps_extra_keys_and_identity(body):
    source='def f():\n    '+body+'\nresult=f()\n'
    scope={};exec(compile(source,'<namespace-oracle>','exec'),scope)
    assert scope['result']=='after'
    result,snapshot=run(source);exact(result,'result','after')
    assert result.coverage_status=='closed_under_profile',snapshot['obligations']


def test_future_bound_method_allocations_resolve_published_aliases():
    from pythonstan.analysis.contracts import SourceModule
    from pythonstan.analysis.engines.abstract.driver import InterproceduralDriver
    from pythonstan.analysis.domains.values import Value
    source="class C:\n    def method(self):return 'after'\nc=C()\n"
    driver=InterproceduralDriver([SourceModule('subject','subject.py',source)],options={'resources':{'max_work':50000,'max_seconds':0}})
    driver.run();unit=next(unit for unit in driver.units.values() if unit.is_module)
    store=next(flow.store for flow in unit.outcomes if flow.kind=='normal').copy()
    receiver=store.cells['global:subject:c'];function=next(identifier for identifier,info in driver.functions.items() if info.qualname=='subject.C.method')
    node=driver.trees['subject'].body[-1]
    old=driver.transfer.bound(Value.ref(function),receiver,store,unit.frame,node)
    oldid=next(iter(old.refs));(changed,)=driver.reabstract_addresses({oldid:'summary:bound'},(store,))
    new=driver.transfer.bound(Value.ref(function),receiver,changed,unit.frame,node)
    assert new.refs==frozenset(('summary:bound',)) and oldid not in changed.heap
    assert changed.heap['summary:bound'].kind=='bound_method' and not changed.heap['summary:bound'].singleton


def test_coarsened_fastlocals_refresh_is_weak_and_keeps_both_snapshots():
    from pythonstan.analysis.contracts import SourceModule
    from pythonstan.analysis.engines.abstract.driver import InterproceduralDriver
    from pythonstan.analysis.domains.values import Value
    source="def f():\n    x='before'\n    return locals()\nview=f()\n"
    driver=InterproceduralDriver([SourceModule('subject','subject.py',source)],options={'resources':{'max_work':50000,'max_seconds':0}})
    driver.run();module=next(unit for unit in driver.units.values() if unit.is_module);function=next(unit for unit in driver.units.values() if unit.frame.qualname=='subject.f')
    store=next(flow.store for flow in module.outcomes if flow.kind=='normal').copy()
    oldid=next(iter(store.cells['global:subject:view'].refs))
    (changed,)=driver.reabstract_addresses({oldid:'summary:locals'},(store,))
    changed.write_cell(function.frame.cell('x'),Value.constant('after'))
    view=driver.transfer.namespaces.local_view(function.frame,changed)
    assert view.refs==frozenset(('summary:locals',))
    fields=driver.transfer.namespaces.bindings(changed.heap['summary:locals'],changed)
    assert set(fields['x'].exact_constants())=={'before','after'}


@pytest.mark.parametrize(('source','expected'),[
    ("class Outer:\n    __qualname__='Renamed'\n    class Inner:pass\nresult=Outer.Inner.__qualname__\n",'Outer.Inner'),
    ("def outer():\n    def inner():\n        class C:pass\n        return C\n    return inner\nresult=outer()().__qualname__\n",'outer.<locals>.inner.<locals>.C'),
])
def test_class_qualname_is_lexical_and_includes_all_function_scope_markers(source,expected):
    scope={};exec(compile(source,'<class-oracle>','exec'),scope)
    assert scope['result']==expected
    result,snapshot=run(source);exact(result,'result',expected)
    assert result.coverage_status=='closed_under_profile',snapshot['obligations']


@pytest.mark.parametrize(('source','expected'),[
    ("class C:\n    class __Inner:pass\nresult=C._C__Inner.__name__+'|'+C._C__Inner.__qualname__\n",'__Inner|C.__Inner'),
    ("class C:\n    @staticmethod\n    def __factory():\n        class D:pass\n        return D\nresult=C._C__factory().__qualname__\n",'C.__factory.<locals>.D'),
])
def test_private_lowering_retains_source_declared_class_and_scope_display(source,expected):
    scope={};exec(compile(source,'<class-oracle>','exec'),scope)
    assert scope['result']==expected
    result,snapshot=run(source);exact(result,'result',expected)
    assert result.coverage_status=='closed_under_profile',snapshot['obligations']
