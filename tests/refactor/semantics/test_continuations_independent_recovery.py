"""Independent concrete-oracle heldouts for source continuation semantics."""
import json
import subprocess
import sys
import pytest
from pythonstan import Source,ValueQuery,analyze

CASES=[
('throw_yielding_nested_finally',"""log=''
def gen():
    global log
    try:
        yield 'A'
    finally:
        log+='F'
        try:
            yield 'B'
        finally:
            log+='X'
g=gen()
a=next(g)
b=g.throw(ValueError('sent'))
mid=log
try:
    next(g)
except ValueError as error:
    result=a+b+mid+'|'+log+error.args[0]
""",'ABF|FXsent'),
('failed_close_then_replacing_throw',"""def gen():
    try:
        yield 1
    finally:
        yield 2
g=gen()
next(g)
try:
    g.close()
except RuntimeError:
    pass
try:
    g.throw(KeyError('replacement'))
except KeyError as error:
    result=error.args[0]
""",'replacement'),
('delegated_close_suppressed_inner_exception',"""log=''
class Manager:
    def __enter__(self):
        global log
        log+='E'
    def __exit__(self,t,v,b):
        global log
        log+='X'
        return True
def inner():
    global log
    with Manager():
        yield 1
    log+='T'
def outer():
    global log
    try:
        yield from inner()
    finally:
        log+='O'
g=outer()
next(g)
g.close()
result=log
""",'EXTO'),
('two_live_source_cursors',"""data=[1,2]
a=iter(data)
b=iter(data)
def advance(it):
    return next(it)
a1=advance(a)
b1=advance(b)
a2=advance(a)
data.append(3)
b2=advance(b)
b3=advance(b)
result=a1*10000+b1*1000+a2*100+b2*10+b3
""",11223),
('await_return_cleanup',"""log=''
async def inner():
    return 4
async def work():
    global log
    try:
        value=await inner()
        return value
    finally:
        log+='F'
c=work()
try:
    c.send(None)
except StopIteration as error:
    result=log+str(error.value)
""",'F4'),
('async_with_exception_suppression',"""log=''
class Manager:
    async def __aenter__(self):
        global log
        log+='E'
    async def __aexit__(self,t,v,b):
        global log
        log+='X'
        return True
async def work():
    async with Manager():
        raise ValueError('body')
    return 7
c=work()
try:
    c.send(None)
except StopIteration as error:
    result=log+str(error.value)
""",'EX7'),
('function_definition_callback_order',"""log=''
def mark(text):
    global log
    log+=text
    return text
def decorator():
    global log
    log+='D'
    def apply(fn):
        global log
        log+='C'
        return fn
    return apply
@decorator()
def f(x:mark('A')=mark('V'))->mark('R'):
    return x
result=log+'|'+f()+'|'+f.__annotations__['x']+'|'+f.__annotations__['return']
""",'DVARC|V|A|R'),
('future_function_annotations_do_not_call',"""from __future__ import annotations
log=''
def mark(text):
    global log
    log+=text
    return text
def decorator():
    global log
    log+='D'
    def apply(fn):
        global log
        log+='C'
        return fn
    return apply
@decorator()
def f(x:mark('A')=mark('V'))->mark('R'):
    return x
result=log+'|'+f()+'|'+f.__annotations__['x']+'|'+f.__annotations__['return']
""","DVC|V|mark('A')|mark('R')"),
('bare_reraise_after_handler_yield',"""def gen():
    try:
        raise ValueError('held')
    except ValueError:
        yield 1
        raise
g=gen()
a=next(g)
try:
    next(g)
except ValueError as error:
    result=str(a)+error.args[0]
""",'1held'),
('suspended_handler_does_not_leak_to_other_generator',"""def holding():
    try:
        raise ValueError('held')
    except ValueError:
        yield 1
def bare():
    raise
    yield 0
g=holding()
h=bare()
next(g)
try:
    next(h)
except RuntimeError:
    result='right'
except ValueError:
    result='leak'
g.close()
""",'right'),
]

@pytest.mark.parametrize('name,source,expected',CASES,ids=[row[0] for row in CASES])
def test_independent_source_continuation(name,source,expected):
    concrete=subprocess.run([sys.executable,'-I','-c',source+'\nimport json\nprint(json.dumps(result))'],capture_output=True,text=True,check=True,timeout=5)
    assert json.loads(concrete.stdout)==expected
    result=analyze(Source(source,module='subject'),config={'engine':'ai_primary','resources':{'max_work':100000,'max_seconds':0,'max_memory_mb':0}},queries=['values','calls','effects'])
    answer=result.query(ValueQuery('subject','result'))
    assert result.solver_status=='converged',result.raw_snapshot()['obligations']
    assert answer.status=='answered',answer
    assert answer.value['constants']==[expected] and not answer.value['top'] and not answer.value['top_sorts'] and not answer.value['may_unbound'] and not answer.value['bottom'],answer
    assert result.coverage_status=='closed_under_profile',result.raw_snapshot()['obligations']
