"""Operational controls: actual Source equations, heap quotients and honest retirement.

Legacy witnesses use the real compatibility projection followed by Source
execution in its shared driver.  They do not assert native-worker coverage.
"""
from dataclasses import fields
from types import SimpleNamespace

import pytest

from pythonstan import Source, analyze
from pythonstan.config import ConfigurationError, resolve_config
from pythonstan.analysis.contracts import SourceModule
from pythonstan.analysis.contracts.budgets import AnalysisBudget
from pythonstan.analysis.engines.abstract.driver import InterproceduralDriver
from pythonstan.analysis.pointer.ai.solver import SolverConfig
from pythonstan.compat.shared_ai import BoundaryUnavailable, options


LOOP = "x=0\nwhile x<4:\n    x=x+1\nresult=x\n"
DISPATCH = """def read(x):return x
def write(x):return {'value':x}
def dispatch(key,payload):
    table={'read':read,'write':write}
    return table[key](payload)
result=dispatch(key,'after')
"""
STR_RECURSION = """def walk(value):
    if value=='token-a':following='token-b'
    elif value=='token-b':following='token-c'
    elif value=='token-c':following='token-d'
    elif value=='token-d':following='token-e'
    elif value=='token-e':following='token-f'
    elif value=='token-f':following='token-g'
    elif value=='token-g':following='token-h'
    elif value=='token-h':following='token-i'
    else:return value
    return walk(following)
result=walk('token-a')
"""
TRACE = """if choose:
    x='A'
    marker=1
else:
    x='B'
    marker=2
if marker==1:result=x
else:result='X'
"""
ADDRESS = """class Box:pass
a=Box()
a.value='A'
b=Box()
b.value='B'
table={'read':a,'write':b}
x=table[key]
x.value='late'
result=a.value
"""


def run(source, abstract=None, bindings=None, *, legacy=False, solver=None):
    if legacy:
        budget = AnalysisBudget(**(abstract or {}))
        settings = options(budget, SolverConfig(**solver) if solver is not None else None)
        settings['resources']['max_memory_mb'] = 0
        settings['inputs'] = {'bindings': {'subject.'+key:value for key,value in (bindings or {}).items()}}
        driver = InterproceduralDriver([SourceModule('subject','subject.py',source)], settings)
        return driver.run().to_dict()
    settings = {'engine':'ai_primary', 'abstract':dict(abstract or {}),
                'inputs':{'bindings':{'subject.'+key:value for key,value in (bindings or {}).items()}},
                'resources':{'max_work':100000,'max_seconds':0,'max_memory_mb':0}}
    if solver is not None:settings['abstract']['solver_options']=solver
    return analyze(Source(source,module='subject'),config=settings,
                   queries=['values','stats','effects','relations','obligations']).raw_snapshot()


def value(snapshot):
    return snapshot['values']['subject']['result']


def finite(snapshot, expected):
    result=value(snapshot)
    assert set(result['constants'])==set(expected)
    assert not result['top'] and not result['top_sorts'] and not result['may_unbound'] and not result['bottom']
    assert snapshot['solver_status']=='converged' and snapshot['coverage_status']=='closed_under_profile'


@pytest.mark.parametrize('legacy',[False,True],ids=['public','legacy-projection'])
@pytest.mark.parametrize('name',['max_iterations','max_stmt_visits'])
def test_zero_iteration_and_statement_limits_stop_actual_source_work(name,legacy):
    stopped=run('result=17\n',{name:0},legacy=legacy)
    completed=run('result=17\n',{name:100},legacy=legacy)
    assert stopped['solver_status']=='budget_exhausted'
    assert stopped['stats'].get('ast_work',0)==0
    assert stopped['stats']['work']<completed['stats']['work']
    finite(completed,{17})


@pytest.mark.parametrize('legacy',[False,True],ids=['public','legacy-projection'])
@pytest.mark.parametrize('name,off,on',[
    ('enable_narrowing',False,True),('narrowing_iterations',0,2)])
def test_narrowing_controls_recover_actual_exit_bound(name,off,on,legacy):
    loose=run(LOOP,{name:off},legacy=legacy,solver={'widen_delay':0})
    precise=run(LOOP,{name:on},legacy=legacy,solver={'widen_delay':0})
    assert value(loose)['integer_interval']['lower']==4
    assert value(loose)['integer_interval']['upper'] is None
    assert not value(loose)['top'] and not value(loose)['top_sorts']
    assert loose['stats'].get('narrowing_checks_passed',0)==0
    finite(precise,{4})
    assert precise['stats']['narrowing_checks_passed']>0


@pytest.mark.parametrize('legacy',[False,True],ids=['public','legacy-projection'])
def test_address_limit_changes_real_heap_identity_without_losing_field_values(legacy):
    bindings={'key':{'type':'str','top':True}}
    low=run(ADDRESS,{'max_addr_set_size':1,'enable_value_partition':False},bindings,legacy=legacy)
    high=run(ADDRESS,{'max_addr_set_size':50,'enable_value_partition':False},bindings,legacy=legacy)
    assert low['stats']['address_capacity_collapses']>0
    assert high['stats'].get('address_capacity_collapses',0)==0
    finite(low,{'A','B','late'})
    finite(high,{'A','late'})
    # The low-cap write is applied to the published summary identity itself.
    assert any('summary:' in event[0] for event in low['effects']['writes'])


@pytest.mark.parametrize('legacy',[False,True],ids=['public','legacy-projection'])
def test_string_constant_limit_changes_recursive_equation_to_useful_prefix(legacy):
    low=run(STR_RECURSION,{'max_const_strings':1,'max_stmt_visits':1000},legacy=legacy)
    high=run(STR_RECURSION,{'max_const_strings':10,'max_stmt_visits':1000},legacy=legacy)
    assert value(low)['string_patterns']['prefixes']==['token-']
    assert not value(low)['top'] and not value(low)['top_sorts']
    assert low['coverage_status']=='closed_under_profile'
    finite(high,{'token-i'})


@pytest.mark.parametrize('legacy',[False,True],ids=['public','legacy-projection'])
@pytest.mark.parametrize('name,off,on',[
    ('enable_value_partition',False,True),('max_value_partitions',1,4)])
def test_partition_controls_change_actual_key_handler_pairs(name,off,on,legacy):
    bindings={'key':{'type':'str','top':True}}
    coarse=run(DISPATCH,{name:off},bindings,legacy=legacy)
    precise=run(DISPATCH,{name:on},bindings,legacy=legacy)
    def pairs(snapshot):
        return {(row['key'],row['handler']) for row in value(snapshot)['relations']}
    assert len(pairs(coarse))==4 and len(pairs(precise))==2
    assert pairs(precise)<pairs(coarse)
    assert all(row['partitioned'] for row in value(precise)['relations'])
    assert not any(row['partitioned'] for row in value(coarse)['relations'])
    for snapshot in (coarse,precise):
        assert value(snapshot)['constants']==['after'] and value(snapshot)['objects']
        assert not value(snapshot)['top'] and not value(snapshot)['top_sorts']
    if name=='max_value_partitions':assert coarse['stats']['value_partition_collapses']>0


def test_relations_alias_controls_actual_partition_and_rejects_conflict():
    bindings={'key':{'type':'str','top':True}}
    off=run(DISPATCH,{'relations':False},bindings)
    on=run(DISPATCH,{'relations':True},bindings)
    assert len(value(off)['relations'])==4 and len(value(on)['relations'])==2
    with pytest.raises(ConfigurationError,match='conflicts'):
        run(DISPATCH,{'relations':False,'enable_value_partition':True},bindings)


def test_context_depth_changes_source_call_inputs_and_observed_return():
    source="def ident(value):return value\ndef wrap(value):return ident(value)\na=wrap('A')\nb=wrap('B')\nresult=a\n"
    merged=run(source,{'context_depth':0})
    distinct=run(source,{'context_depth':2})
    finite(merged,{'A','B'});finite(distinct,{'A'})
    assert merged['stats']['units']<distinct['stats']['units']


def test_boolean_refinement_changes_normal_return_but_keeps_real_exception():
    source='def only_true(flag):\n    if flag:return flag\n    raise ValueError()\nresult=only_true(condition)\n'
    bindings={'condition':{'type':'bool','top':True}}
    off=run(source,{'refine_booleans':False},bindings)
    on=run(source,{'refine_booleans':True},bindings)
    finite(off,{False,True});finite(on,{True})
    assert 'ValueError' in on['effects']['exceptions'] and 'ValueError' in off['effects']['exceptions']


def test_attribute_name_window_executes_known_reads_and_keeps_covering_residual():
    source="class C:pass\nc=C()\nc.a='A'\nc.b='B'\nresult=getattr(c,name)\n"
    bindings={'name':{'type':'str','top':True}}
    zero=run(source,{'attribute_name_limit':0},bindings)
    full=run(source,{'attribute_name_limit':256},bindings)
    assert zero['stats']['reflective_known_names']==0 and full['stats']['reflective_known_names']>0
    assert any(row[1]=='a' for row in full['effects']['reads'])
    assert not any(row[1]=='a' for row in zero['effects']['reads'])
    for snapshot in (zero,full):
        assert snapshot['stats']['reflective_residuals']>0
        assert snapshot['coverage_status']=='open' and snapshot['effects']['havoc']
        assert '<callback>' in snapshot['effects']['calls'] and 'BaseException' in snapshot['effects']['exceptions']
    # A bounded name must retain exact normal semantics even at a zero window.
    for cap in (0,256):finite(run(source,{'attribute_name_limit':cap},{'name':'a'}),{'A'})


def test_integer_constant_capacity_changes_real_recursive_input_widening():
    source='def walk(value):\n    if value==0:return walk(1)\n    if value==1:return walk(2)\n    return value\nresult=walk(seed)\n'
    settings={'context_depth':0,'enable_narrowing':False}
    bindings={'seed':{'constants':[0,10]}}
    low=run(source,{**settings,'int_domain_max_consts':1},bindings,solver={'widen_delay':0})
    high=run(source,{**settings,'int_domain_max_consts':10},bindings,solver={'widen_delay':0})
    assert value(low)['integer_interval']=={'lower':0,'upper':10,'parity':'top'}
    assert not value(low)['top'] and not value(low)['top_sorts']
    finite(high,{2,10})


def test_scalar_pair_limit_covers_product_while_large_limit_publishes_all_sums():
    bindings={'x':{'constants':[1,2]},'y':{'constants':[10,20]}}
    low=run('result=x+y\n',{'max_scalar_pairs':1},bindings)
    high=run('result=x+y\n',{'max_scalar_pairs':8},bindings)
    assert value(low)['integer_interval']=={'lower':None,'upper':None,'parity':'top'}
    assert not value(low)['top'] and low['coverage_status']=='closed_under_profile'
    finite(high,{11,12,21,22})


def test_range_limit_changes_real_materialization_and_open_obligation():
    low=run('result=len(range(3))\n',{'max_range_size':1})
    high=run('result=len(range(3))\n',{'max_range_size':10})
    assert low['coverage_status']=='open'
    assert any(row['code']=='range_enumeration_budget' for row in low['obligations'])
    assert value(low)['integer_interval']['upper'] is None
    finite(high,{3})


@pytest.mark.parametrize('legacy',[False,True],ids=['public','legacy-projection'])
def test_solver_widen_delay_changes_actual_loop_equation(legacy):
    low=run(LOOP,{'enable_narrowing':False},legacy=legacy,solver={'widen_delay':0})
    high=run(LOOP,{'enable_narrowing':False},legacy=legacy,solver={'widen_delay':20})
    assert value(low)['integer_interval']['upper'] is None
    finite(high,{4})
    assert low['stats']['loop_iterations']<high['stats']['loop_iterations']


def test_legacy_loop_unroll_alias_has_real_loop_effect_and_conflict_rejection():
    low=run(LOOP,{'enable_narrowing':False},legacy=True,solver={'loop_unroll':0})
    high=run(LOOP,{'enable_narrowing':False},legacy=True,solver={'loop_unroll':20})
    assert value(low)['integer_interval']['upper'] is None
    finite(high,{4})
    with pytest.raises(BoundaryUnavailable,match='Conflicting legacy widening'):
        options(AnalysisBudget(),SolverConfig(widen_delay=3,loop_unroll=4))


@pytest.mark.parametrize('legacy',[False,True],ids=['public','legacy-projection'])
def test_solver_trace_cap_merges_real_correlations(legacy):
    bindings={'choose':{'type':'bool','top':True}}
    low=run(TRACE,bindings=bindings,legacy=legacy,solver={'max_traces_per_block':1})
    high=run(TRACE,bindings=bindings,legacy=legacy,solver={'max_traces_per_block':8})
    finite(low,{'A','B','X'});finite(high,{'A','X'})
    assert low['stats']['trace_merges']>0 and high['stats'].get('trace_merges',0)==0


def test_legacy_strict_exceptions_controls_actual_host_failure_boundary(monkeypatch):
    import pythonstan.compat.shared_ai as bridge
    from pythonstan.compat.legacy_ai.api import AISummaryEngine
    def fail(*args,**kwargs):raise RuntimeError('independent-host-failure')
    monkeypatch.setattr(bridge,'execute_intrinsic',fail)
    conservative=AISummaryEngine(SimpleNamespace(),AnalysisBudget(strict_exceptions=False)).analyze_intrinsic('len',None,[])
    assert not conservative.is_complete and conservative.ret_value.addr_top
    assert all(domain.is_top(conservative.ret_value.components[domain.name]) for domain in conservative.ret_value.registry.domains)
    assert conservative.normal_exit and conservative.may_raise==frozenset({'BaseException'})
    assert {event.kind for event in conservative.unknown_effects}=={'may_read','may_write','may_call'}
    assert 'RuntimeError:independent-host-failure' in conservative.incomplete_reasons
    with pytest.raises(RuntimeError,match='independent-host-failure'):
        AISummaryEngine(SimpleNamespace(),AnalysisBudget(strict_exceptions=True)).analyze_intrinsic('len',None,[])


RETIRED_BUDGET = (
    'max_recursion_depth','max_field_keys','max_ho_tokens','value_partition_str_cap',
    'max_const_ints','max_tuple_len_exact','max_tuple_alts','max_star_expand_len',
    'max_star_expand_alts','max_dict_parts','max_dict_key_const_strs',
    'max_dict_key_const_ints','max_dict_key_tuple_len','max_dict_key_tuple_alts',
    'enable_dict_identity_keys','max_dict_identity_keys')


def changed_budget(name):
    default=getattr(AnalysisBudget(),name)
    return not default if type(default) is bool else default+1


@pytest.mark.parametrize('name',RETIRED_BUDGET+('strict_exceptions',))
def test_retired_public_budget_nondefault_is_rejected_before_analysis(name):
    with pytest.raises(ConfigurationError,match=name):
        resolve_config(Source('result=17',module='subject'),{'engine':'ai_primary','abstract':{name:changed_budget(name)}})


@pytest.mark.parametrize('name',RETIRED_BUDGET)
def test_retired_nested_budget_nondefault_is_rejected_by_actual_bridge(name):
    with pytest.raises(BoundaryUnavailable,match='Retired legacy budget control: '+name):
        options(AnalysisBudget(**{name:changed_budget(name)}))


@pytest.mark.parametrize('name,nondefault',[
    ('pta_analysis_name','other'),('plan_scopes',['subject.f']),('entry_scope','subject.f'),
    ('context_policy','1-cfa'),('log_level','DEBUG'),('type','other analysis')])
def test_retired_public_driver_nondefault_is_rejected_before_analysis(name,nondefault):
    with pytest.raises(ConfigurationError,match=name):
        resolve_config(Source('result=17',module='subject'),{'engine':'ai_primary','abstract':{name:nondefault}})


RETIRED_SOLVER = [('loop_iters',{0:1}),('enable_trace_partitioning',True),
                  ('infer_merge_points',True),('merge_points',[0]),('max_total_traces',33)]


@pytest.mark.parametrize('name,nondefault',[('loop_unroll',6),*RETIRED_SOLVER])
def test_retired_public_solver_nondefault_is_rejected_before_analysis(name,nondefault):
    with pytest.raises(ConfigurationError,match=name):
        resolve_config(Source('result=17',module='subject'),{'engine':'ai_primary','abstract':{'solver_options':{name:nondefault}}})


@pytest.mark.parametrize('name,nondefault',RETIRED_SOLVER)
def test_retired_private_solver_nondefault_is_rejected_by_actual_bridge(name,nondefault):
    with pytest.raises(BoundaryUnavailable,match='Retired private solver control: '+name):
        options(AnalysisBudget(),SolverConfig(**{name:nondefault}))


def test_control_witness_categories_cover_all_twenty_five_historical_budget_fields():
    active={'max_iterations','max_stmt_visits','max_addr_set_size','max_const_strings',
            'enable_narrowing','narrowing_iterations','enable_value_partition','max_value_partitions','strict_exceptions'}
    assert active.isdisjoint(RETIRED_BUDGET)
    assert active|set(RETIRED_BUDGET)=={field.name for field in fields(AnalysisBudget)}
