"""Actual host/session quotients, finite positive values and rejected half-publishes."""
import ast
import math
import pytest
import subprocess
import sys
from pythonstan.analysis.contracts import SourceModule,EntryScenario
from pythonstan.analysis.domains.values import Value,ValueOperations,ValuePolicy
from pythonstan.analysis.domains.reabstract import ReferenceKey
from pythonstan.analysis.engines.abstract.driver import InterproceduralDriver,BudgetStop
from pythonstan.analysis.engines.abstract.state import Store,HeapObject,Frame,Flow,Unit,FunctionInfo
from pythonstan.analysis.engines.abstract.state_transform import remap_store,semantic_payload_equal


def driver(source='',**options):
    config={'resources':{'max_work':100000,'max_seconds':0,'max_memory_mb':0},**options}
    return InterproceduralDriver([SourceModule('subject','subject.py',source)],config)


def host_state():
    state=Store(cells={'root':Value(refs=frozenset({'A','B'})),'captured':Value.ref('A')},heap={
        'A':HeapObject('instance',{'value':Value.constant('A'),'next':Value.ref('B'),ReferenceKey('A'):Value.ref('A'),'A':Value.constant('literal')}),
        'B':HeapObject('instance',{'value':Value.constant('B'),'next':Value.ref('A')}),
    },guards={'condition':('A','value',True)})
    state.heap['suspended']=HeapObject('generator',metadata={'entry':state,'frame':Frame('subject','f',(),'f',closure=(('captured','captured'),)),
        'pending':Flow(state,'raise',Value.ref('A'),'ValueError',continuation=(('resume',Value.ref('B')),))})
    state.heap['namespace']=HeapObject('namespace',metadata={'owner':'A','module':'subject'})
    state.writes={('heap','A',None),('heap_default','B',None),('heap_meta','suspended','cursor'),('cell','captured',None)}
    return state


def test_float_sign_and_nonfinite_values_have_json_safe_covering_order():
    ops=ValueOperations();positive=Value.constant(0.0);negative=Value.constant(-0.0)
    assert positive!=negative and len({positive,negative})==2
    assert not ops.leq(positive,negative) and not ops.leq(negative,positive)
    merged=ops.join(positive,negative)
    assert merged.top_sorts==frozenset({'float'}) and not merged.top
    assert ops.leq(positive,merged) and ops.leq(negative,merged)
    for value in (math.nan,math.inf,-math.inf):
        result=Value(floats=frozenset({value}));assert 'float' in result.top_sorts and not result.floats
    assert not semantic_payload_equal({'value':positive},{'value':negative})
    left=HeapObject('iterator',metadata={'observed':positive});right=HeapObject('iterator',metadata={'observed':negative})
    assert left.join(right,ops).havoc


@pytest.mark.parametrize('left,right,expected',[(None,None,{True}),(True,True,{True}),(True,1,{False}),(1000,1000,{True,False}),('equal','equal',{True,False}),('left','right',{False})])
def test_scalar_identity_does_not_infer_host_interning(left,right,expected):
    result,_=ValueOperations().scalar_binary(ast.Is(),Value.constant(left),Value.constant(right))
    assert result.bools==frozenset(expected)


def test_none_key_default_and_metadata_are_distinct_effect_channels():
    ops=ValueOperations();caller=Store(heap={'d':HeapObject('dict',{None:Value.constant('before'),'None':Value.constant('string')})})
    callee=caller.copy();callee.write_slot('d',None,Value.constant('after'),ops)
    applied=caller.apply(Flow(callee),ops)
    assert applied.heap['d'].fields[None].exact_constants()==['after']
    assert applied.heap['d'].fields['None'].exact_constants()==['string']
    residual=caller.copy();residual.heap['d'].default=Value.constant('residual');residual.writes.add(('heap_default','d',None))
    applied=caller.apply(Flow(residual),ops)
    assert applied.heap['d'].fields[None].exact_constants()==['before']
    assert applied.heap['d'].default.exact_constants()==['residual']


def test_recursive_remap_covers_defaults_keys_cyclic_stores_and_pending_frames():
    ops=ValueOperations();original=host_state();mapped=remap_store(original,{'A':'S','B':'S'},ops)
    summary=mapped.heap['S'];assert not summary.singleton and not summary.havoc
    assert set(summary.fields['value'].exact_constants())=={'A','B'}
    assert summary.fields['next'].refs==frozenset({'S'})
    assert ReferenceKey('S') in summary.fields and 'A' in summary.fields
    assert mapped.heap['suspended'].metadata['entry'] is mapped
    pending=mapped.heap['suspended'].metadata['pending']
    assert pending.store is mapped and pending.value.refs==frozenset({'S'}) and pending.continuation[0][1].refs==frozenset({'S'})
    assert mapped.heap['namespace'].metadata['owner']=='S' and not mapped.guards
    assert ('heap','S',None) in mapped.writes and ('heap_default','S',None) in mapped.writes
    assert original.heap['A'].fields['next'].refs==frozenset({'B'})


def test_actual_host_epoch_rewrites_units_defaults_and_future_old_aliases():
    d=driver('def f(x=None):\n    return x\n');state=host_state()
    frame=Frame('subject','subject.f',(),'U');node=d.trees['subject'].body[0]
    d.units['U']=Unit('U',frame,tuple(node.body),state,[Flow(state,value=Value.ref('A'))]);d.unit_scenarios['U']='scenario'
    d.functions['f']=FunctionInfo('f',node,'subject','subject.f',(),(Value.ref('A'),),(('key',Value.ref('B')),),frozenset({'x'}),frozenset(),frozenset())
    extra=Flow(state,value=Value.ref('B'),continuation=(('await',Value.ref('A')),))
    returned=d.reabstract_addresses({'A':'S','B':'S'},(extra,))
    assert d.ops.arena.epoch==1 and d.units['U'].entry.cells['root'].refs==frozenset({'S'})
    assert d.functions['f'].defaults[0].refs==frozenset({'S'}) and d.functions['f'].kw_defaults[0][1].refs==frozenset({'S'})
    assert returned[0].value.refs==frozenset({'S'}) and returned[0].continuation[0][1].refs==frozenset({'S'})
    assert 'U' in d.queued and d.allocate_identity('A')=='S'
    assert d.ops.join(Value.ref('A'),Value.ref('B')).refs==frozenset({'S'})
    state.write_slot('A','value',Value.constant('late'),d.ops)
    value,_=state.read_slot('B','value',d.ops)
    assert set(value.exact_constants())=={'A','B','late'}
    state.write_slot('A','new',Value.constant('maybe'),d.ops)
    assert 'new' in state.heap['S'].maybe_missing


def test_reabstract_budget_denial_cannot_publish_a_half_epoch():
    d=driver(resources={'max_work':2,'max_seconds':0,'max_memory_mb':0});state=host_state()
    d.units['U']=Unit('U',Frame('subject','f',(),'U'),(),state);d.unit_scenarios['U']='scenario'
    units=d.units
    with pytest.raises(BudgetStop):d.reabstract_addresses({'A':'S','B':'S'})
    assert not d.ops.arena.aliases and d.ops.arena.epoch==0
    assert d.units is units and d.units['U'].entry is state and set(state.heap)=={'A','B','suspended','namespace'}
    assert d.work<=2


def test_zero_statement_budget_stops_before_ast_work_and_optional_entry_retains_default():
    d=driver('x=1',abstract={'max_stmt_visits':0});result=d.run().to_dict()
    assert result['solver_status']=='budget_exhausted' and result['stats'].get('ast_work',0)==0
    d=driver("def f(x='after'):\n    return x\n")
    result=d.run([EntryScenario('subject',entry_qualname='f',arguments=())]).to_dict()
    assert result['returns']['subject.f']['constants']==['after'] and not result['returns']['subject.f']['top']


def test_source_capacity_drives_actual_summary_and_keeps_useful_field_values():
    source="""class Box: pass
a=Box()
a.value='A'
b=Box()
b.value='B'
table={'read':a,'write':b}
x=table[key]
x.value='late'
result=a.value
"""
    d=driver(source,abstract={'max_addr_set_size':1,'enable_value_partition':False},inputs={'bindings':{'subject.key':{'type':'str','top':True}}})
    result=d.run().to_dict();value=result['values']['subject']['result']
    assert result['stats']['address_capacity_collapses']>0
    assert {'A','late'}<=set(value['constants']) and not value['top'] and not value['top_sorts']


def test_signed_zero_source_calls_at_one_site_keep_both_observations():
    d=driver("def render(value):\n    return str(value)\nresult=''\nfor value in (0.0,-0.0):\n    result=result+render(value)+'|'\n")
    snapshot=d.run().to_dict();value=snapshot['values']['subject']['result']
    assert value['constants']==['0.0|-0.0|'] and not value['top'] and not value['top_sorts']


def test_source_literal_tuple_key_never_becomes_internal_address_tag():
    ops=ValueOperations();state=Store(heap={'A':HeapObject('dict',{('ref','A'):Value.constant('target-literal')})})
    mapped=remap_store(state,{'A':'S'},ops)
    assert ('ref','A') in mapped.heap['S'].fields and ('ref','S') not in mapped.heap['S'].fields


def test_same_generator_factory_site_creates_distinct_frames_in_finite_loop():
    source="""def gen():
    value=1
    yield value
    value=2
    yield value
items=[]
for i in [1,2]:
    items.append(gen())
a=next(items[0])
b=next(items[1])
result=a*10+b
"""
    concrete=subprocess.run([sys.executable,'-I','-c',source+'\nprint(result)'],capture_output=True,text=True,check=True,timeout=3)
    assert concrete.stdout.strip()=='11'
    d=driver(source);snapshot=d.run().to_dict();value=snapshot['values']['subject']['result']
    assert value['constants']==[11] and not value['top'] and not value['top_sorts']
    generators=[obj for unit in d.units.values() for flow in unit.outcomes for obj in flow.store.heap.values() if obj.kind=='generator']
    assert len({obj.metadata['frame'].identity for obj in generators})>=2


def test_lazy_generator_reads_updated_live_global_not_creation_snapshot():
    source="value='before'\ndef gen():\n    yield value\ng=gen()\nvalue='after'\nresult=next(g)\n"
    snapshot=driver(source).run().to_dict();value=snapshot['values']['subject']['result']
    assert value['constants']==['after'] and not value['top'] and not value['top_sorts']


def test_suspended_activation_overflow_is_open_without_retaining_old_exact_cursor():
    source="def gen():\n    yield 1\n    yield 2\nitems=[]\nfor i in range(34):\n    items.append(gen())\na=next(items[-2])\nb=next(items[-1])\nresult=a*10+b\n"
    d=driver(source,abstract={'max_stmt_visits':1000})
    snapshot=d.run().to_dict()
    assert snapshot['coverage_status']=='open'
    assert any(row['code']=='suspended_activation_summary' for row in snapshot['obligations'])
    summaries=[obj for unit in d.units.values() for flow in unit.outcomes for obj in flow.store.heap.values() if obj.kind=='generator' and not obj.singleton]
    assert summaries and all(obj.havoc for obj in summaries)
    for obj in summaries:assert not obj.metadata['entry'].heap
    value=snapshot['values']['subject']['result']
    assert value['top'] or 11 in value['constants']


def test_lazy_frame_collectors_are_live_heap_objects_with_precise_bindings():
    source="def gen(*values,**named):\n    yield values[0]+named['suffix']\ng=gen('A',suffix='B')\nresult=next(g)\n"
    snapshot=driver(source).run().to_dict();value=snapshot['values']['subject']['result']
    assert value['constants']==['AB'] and not value['top'] and not value['top_sorts']
