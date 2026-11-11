"""Source-level suspension and cleanup tests, independent of block identities."""
import pytest
from pythonstan import Source,ValueQuery,analyze


def run(source,bindings=None):
    return analyze(Source(source,module='example'),config={'engine':'ai_primary','resources':{'max_work':80000,'max_seconds':0},'inputs':{'bindings':bindings or {}}},queries=['values','calls','effects','stats'])

def exact(result,name,value):
    answer=result.query(ValueQuery('example',name))
    assert answer.status=='answered',answer
    actual=answer.value
    assert actual['constants']==[value] and not actual['top'] and not actual['top_sorts'] and not actual['may_unbound'] and not actual['bottom'],actual


CASES=[
('sequence',"""def gen():
    yield 1
    yield 2
    yield 3
g=gen()
a=next(g)
b=next(g)
c=next(g)
result=a*100+b*10+c
""",123),
('send',"""def gen():
    x=yield 1
    yield x+1
g=gen()
a=next(g)
b=g.send(6)
result=a*10+b
""",17),
('for_suffix',"""def gen():
    for x in [1,2]:
        yield x
        yield x+10
g=gen()
a=next(g)
b=next(g)
c=next(g)
d=next(g)
result=a*10000+b*100+c*10+d
""",11132),
('while_suffix',"""def gen():
    i=0
    while i<2:
        i+=1
        yield i
    yield 9
g=gen()
a=next(g)
b=next(g)
c=next(g)
result=a*100+b*10+c
""",129),
('throw_handler',"""log=''
def gen():
    global log
    try:
        yield 1
    except ValueError:
        yield 2
    finally:
        log+='F'
g=gen()
a=next(g)
b=g.throw(ValueError)
before=log
g.close()
result=str(a)+str(b)+before+log
""",'12F'),
('finally_return',"""def gen():
    try:
        yield 1
        return 3
    finally:
        return 2
g=gen()
a=next(g)
try:
    next(g)
except StopIteration as error:
    result=error.value
""",2),
('failed_close_stays_suspended',"""def gen():
    try:
        yield 1
    finally:
        yield 2
        yield 3
g=gen()
a=next(g)
try:
    g.close()
except RuntimeError:
    pass
result=next(g)
""",3),
('delegate_return',"""log=''
def inner():
    global log
    try:
        yield 1
        return 4
    finally:
        log+='I'
def outer():
    global log
    try:
        x=yield from inner()
        yield x+1
    finally:
        log+='O'
g=outer()
a=next(g)
b=next(g)
g.close()
result=str(a)+str(b)+log
""",'15IO'),
('delegate_send_value',"""def inner():
    x=yield 1
    return x
def outer():
    return (yield from inner())
g=outer()
next(g)
try:
    g.send(7)
except StopIteration as error:
    result=error.value
""",7),
('with_deferred_exit',"""log=''
class Manager:
    def __enter__(self):
        global log
        log+='E'
        return self
    def __exit__(self,typ,value,tb):
        global log
        if typ is GeneratorExit:
            log+='X'
        return False
def gen():
    with Manager():
        yield 1
g=gen()
next(g)
before=log
g.close()
result=before+'|'+log
""",'E|EX'),
('lazy_genexpr',"""log=''
def mark(x):
    global log
    log+='B'
    return x+1
g=(mark(x) for x in [1,2])
before=log
a=next(g)
b=next(g)
result=before+'|'+log+str(a)+str(b)
""",'|BB23'),
('eager_annotation_order',"""log=''
def value():
    global log
    log+='V'
    return 'payload'
def annotation():
    global log
    log+='A'
    return 'tag'
x:annotation()=value()
result=log+'|'+x+'|'+__annotations__['x']
""",'VA|payload|tag'),
('future_annotations',"""from __future__ import annotations
log=''
def annotation():
    global log
    log+='BAD'
    return 'tag'
x:annotation()=7
result=log+'|'+__annotations__['x']
""",'|annotation()'),
('local_annotation_no_eval',"""def f():
    x:missing_callback()
    return 7
result=f()
""",7),
('unreachable_setup_annotations',"""if False:
    x:int
result=len(__annotations__)
""",0),
('async_with',"""log=''
class Manager:
    async def __aenter__(self):
        global log
        log+='E'
        return 7
    async def __aexit__(self,t,v,b):
        global log
        log+='X'
        return False
async def work():
    async with Manager() as x:
        return x
c=work()
try:
    c.send(None)
except StopIteration as e:
    result=log+str(e.value)
""",'EX7'),
('async_for',"""class Iter:
    def __init__(self):
        self.i=0
    def __aiter__(self):
        return self
    async def __anext__(self):
        self.i+=1
        if self.i>2:
            raise StopAsyncIteration
        return self.i
async def work():
    total=0
    async for x in Iter():
        total+=x
    return total
c=work()
try:
    c.send(None)
except StopIteration as e:
    result=e.value
""",3),
]

@pytest.mark.parametrize('name,source,expected',CASES,ids=[row[0] for row in CASES])
def test_source_continuation(name,source,expected):
    result=run(source)
    assert result.solver_status=='converged',result.to_dict()
    exact(result,'result',expected)
    assert result.coverage_status=='closed_under_profile',result.raw_snapshot()['obligations']


def test_private_default_slots_and_annotation_map_are_not_user_attributes():
    result=run("""def f(x:'TAG'='before',*,k='keyword')->'RET':
    return x+'|'+k
setattr(f,'$default:0','user')
setattr(f,'$kwdefault:k','kw-user')
setattr(f,'$annotation:x','annotation-user')
result=f()
user=getattr(f,'$default:0')
annotation=f.__annotations__['x']+'|'+f.__annotations__['return']
visible='$default:0' in vars(f)
private_visible='__annotations__' in vars(f)
""")
    exact(result,'result','before|keyword');exact(result,'user','user')
    exact(result,'annotation','TAG|RET');exact(result,'visible',True);exact(result,'private_visible',False)


@pytest.mark.parametrize('declaration',['def f[T]():\n        return T\n    return f','class C[T]:\n        pass\n    return C','type A = int\n    return A'])
def test_unmodeled_pep695_keeps_real_bound_local_normal_value(declaration):
    result=run('def outer():\n    '+declaration+'\nresult=outer()\n')
    answer=result.query(ValueQuery('example','result'))
    assert answer.value['top'] and not answer.value['may_unbound']
    assert result.coverage_status=='open' and result.query('effects').value['havoc']
    assert any(row['callee']=='<callback>' for row in result.query('calls').value)


def test_yielding_finally_preserves_pending_return_and_inner_handler_cleanup():
    result=run("""def gen():
    try:
        return 7
    finally:
        yield 2
g=gen()
a=next(g)
try:
    next(g)
except StopIteration as error:
    result=a*10+error.value
""")
    exact(result,'result',27)


def test_multiple_manager_entry_failure_unwinds_already_entered_prefix():
    result=run("""log=''
class A:
    def __enter__(self):
        global log
        log+='A'
    def __exit__(self,t,v,b):
        global log
        log+='X'
        return False
class B:
    def __enter__(self):
        global log
        log+='B'
        raise ValueError
    def __exit__(self,t,v,b):
        global log
        log+='BAD'
try:
    with A(), B():
        log+='BODY'
except ValueError:
    result=log
""")
    exact(result,'result','ABX')


@pytest.mark.parametrize('expression',['1','object()'])
def test_missing_context_manager_protocol_is_typeerror(expression):
    result=run('try:\n    with '+expression+':\n        pass\nexcept TypeError:\n    result=7\n')
    exact(result,'result',7)


def test_collected_generator_and_attribute_augassign_keep_actual_effects():
    result=run("""log=''
class Box:
    def __init__(self):
        self.value=1
box=Box()
def gen():
    global log
    for x in [1,2]:
        box.value+=x
        log+=str(x)
        yield box.value
items=list(gen())
result=log+'|'+str(items[0])+str(items[1])
""")
    exact(result,'result','12|24')


def test_len_and_str_source_protocols_execute_callback_writes():
    result=run("""log=''
class C:
    def __len__(self):
        global log
        log+='L'
        return 3
    def __str__(self):
        global log
        log+='S'
        return 'text'
obj=C()
n=len(obj)
s=str(obj)
result=log+'|'+str(n)+'|'+s
""")
    exact(result,'result','LS|3|text')


def test_finite_conversion_uses_base_and_all_keyword_alternatives():
    result=run("result=int('10',base=2)\n")
    exact(result,'result',2)


def test_opaque_star_protocol_havocs_before_a_known_pure_callee():
    result=run("""class Box:
    pass
box=Box()
box.value='before'
def identity(*args):
    return 1
identity(*external)
result=box.value
""",{'example.external':{'type':'object','top':True}})
    answer=result.query(ValueQuery('example','result'))
    assert result.coverage_status=='open'
    assert answer.value['top']
    assert result.query('effects').value['havoc']


@pytest.mark.parametrize('call',["g.send()","g.send(None,None)","g.throw()","g.close(1)","next(g,None,None)","object(1)","range(3,step=1)","repr()","abs()"])
def test_missing_or_excess_builtin_arguments_raise_typeerror(call):
    result=run("def gen():\n    yield 1\ng=gen()\ntry:\n    "+call+"\nexcept TypeError:\n    result=7\n")
    exact(result,'result',7)


def test_legacy_throw_type_and_value_constructs_actual_exception_arguments():
    result=run("""def gen():
    try:
        yield 1
    except ValueError as error:
        yield error.args[0]
g=gen()
next(g)
result=g.throw(ValueError,'message')
""")
    exact(result,'result','message')


def test_completed_coroutine_cannot_be_sent_again():
    result=run("""async def work():
    return 3
c=work()
try:
    c.send(None)
except StopIteration:
    pass
try:
    c.send(None)
except RuntimeError:
    result=7
""")
    exact(result,'result',7)


def test_print_executes_source_conversion_prefix_without_host_printing(capsys):
    result=run("""log=''
class C:
    def __str__(self):
        global log
        log+='S'
        return 'text'
print(C())
result=log
""")
    exact(result,'result','S')
    assert capsys.readouterr().out==''


def test_fstring_applies_scalar_format_spec_and_conversion():
    result=run("result=f'{7:04d}|{\"word\"!r}'\n")
    exact(result,'result',"0007|'word'")


def test_unknown_fstring_callback_applies_actual_effects():
    result=run("""class Box:
    pass
box=Box()
box.value='before'
text=f'{external}'
result=box.value
""",{'example.external':{'type':'object','top':True}})
    assert result.coverage_status=='open'
    assert result.query(ValueQuery('example','result')).value['top']
    assert result.query('effects').value['havoc']


def test_instance_call_uses_class_special_slot_and_ignores_attribute_hook():
    result=run("""class C:
    def __getattribute__(self,name):
        return lambda:'wrong'
    def __call__(self):
        return 'after'
result=C()()
""")
    exact(result,'result','after')


def test_created_generator_inherits_current_callers_exception():
    result=run("""def bare():
    raise
    yield 0
try:
    raise ValueError('caller')
except ValueError:
    g=bare()
    try:
        next(g)
    except ValueError as error:
        result=error.args[0]
""")
    exact(result,'result','caller')


def test_inherited_exception_is_not_saved_as_owned_suspension_context():
    result=run("""def bare_after_yield():
    yield 1
    raise
g=bare_after_yield()
try:
    raise ValueError('caller')
except ValueError:
    next(g)
try:
    next(g)
except RuntimeError:
    result='right'
except ValueError:
    result='leak'
""")
    exact(result,'result','right')


def test_owned_generator_exception_restores_current_caller_after_escape():
    result=run("""def holding():
    try:
        raise ValueError('owned')
    except ValueError:
        yield 1
        raise
g=holding()
next(g)
try:
    raise KeyError('caller')
except KeyError:
    try:
        next(g)
    except ValueError as error:
        owned=error.args[0]
    try:
        raise
    except KeyError as caller:
        result=owned+'|'+caller.args[0]
""")
    exact(result,'result','owned|caller')


def test_private_function_display_and_future_annotation_keep_source_spelling():
    result=run("""from __future__ import annotations
class C:
    __T='tag'
    __x:__T=1
    def __f(self,__arg:__T)->__T:
        return __arg
result=C._C__f.__name__+'|'+C._C__f.__qualname__+'|'+C._C__f.__annotations__['_C__arg']+'|'+C.__annotations__['_C__x']
""")
    exact(result,'result','__f|C.__f|__T|__T')


def test_dictionary_view_truth_and_exhausted_iterator_remain_live_and_closed():
    result=run("""d={}
v=d.keys()
a=bool(v)
d['x']=1
b=bool(v)
i=iter(v)
first=next(i)
try:
    next(i)
except StopIteration:
    pass
d['y']=2
try:
    next(i)
except StopIteration:
    result=(not a) and b and first=='x'
""")
    exact(result,'result',True)
    assert result.coverage_status=='closed_under_profile',result.raw_snapshot()['obligations']
