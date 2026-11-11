"""Independent public-control counterfactuals over real Source/Project inputs."""
import hashlib

import pytest

from pythonstan import Source, Project, ValueQuery, PropertyQuery, analyze
from pythonstan.config import ConfigurationError


def run(source='result=17\n', *, config=None, queries=None):
    settings={'engine':'ai_primary','resources':{'max_work':100000,'max_seconds':0,'max_memory_mb':0}}
    for name,value in (config or {}).items():
        if isinstance(value,dict):settings.setdefault(name,{}).update(value)
        else:settings[name]=value
    return analyze(Source(source,module='subject'),config=settings,queries=queries or ['values','calls','stats'])


def exact(result,symbol,expected):
    value=result.query(ValueQuery('subject',symbol)).value
    assert value['constants']==[expected]
    assert not value['top'] and not value['top_sorts'] and not value['bottom'] and not value['may_unbound']


def test_fixed_schema_version_rejects_before_invalid_target_source():
    with pytest.raises(ConfigurationError,match='schema_version'):
        run('invalid python @',config={'schema_version':2})


def test_precise_preset_changes_actual_recursive_integer_equation():
    source='def walk(value):\n'+''.join('    '+('if' if i==0 else 'elif')+f' value=={i}:following={i+1}\n' for i in range(12))
    source+='    else:return value\n    return walk(following)\nresult=walk(seed)\n'
    settings={'abstract':{'context_depth':0,'max_stmt_visits':1000,'enable_narrowing':False,'solver_options':{'widen_delay':0}},
              'inputs':{'bindings':{'subject.seed':{'constants':[0,30]}}}}
    balanced=run(source,config={**settings,'preset':'balanced'})
    precise=run(source,config={**settings,'preset':'precise'})
    low=balanced.query(ValueQuery('subject','result')).value
    high=precise.query(ValueQuery('subject','result')).value
    assert low['integer_interval']=={'lower':0,'upper':30,'parity':'top'}
    assert high['constants']==[12,30] and not high['top'] and not high['top_sorts']
    assert balanced.coverage_status==precise.coverage_status=='closed_under_profile'


@pytest.mark.parametrize('engine',['native','native_then_ai','ai_primary','hybrid'])
def test_engine_selects_actual_execution_and_useful_source_precision(engine):
    result=run("value='before'\nvalue='after'\nresult=value\n",config={'engine':engine})
    value=result.query(ValueQuery('subject','result')).value
    assert result.actual_engine==engine and not value['top'] and not value.get('top_sorts')
    if engine=='native':
        assert set(value['constants'])=={'before','after'}
        assert result.query(ValueQuery('subject','result')).approximation=='flow_insensitive_may'
    else:assert value['constants']==['after']
    if engine=='native_then_ai':assert result.raw_snapshot()['stats']['native_prepass_work']>0


def test_configured_source_roots_change_actual_dependency_inventory_and_value(tmp_path):
    roots=[tmp_path/'one',tmp_path/'two']
    for root,text in zip(roots,['A','B']):root.mkdir();(root/'helper.py').write_text('value='+repr(text))
    results=[run('from helper import value\nresult=value\n',config={'inputs':{'source_roots':[str(root)]}}) for root in roots]
    exact(results[0],'result','A');exact(results[1],'result','B')
    assert all({row['module'] for row in result.raw_snapshot()['inventory']}=={'subject','helper'} for result in results)
    assert results[0].raw_snapshot()['source_digest']!=results[1].raw_snapshot()['source_digest']


def test_configured_exclusion_prevents_real_parse_of_excluded_project_file(tmp_path):
    from pythonstan.inputs import InputError
    (tmp_path/'good.py').write_text('result=17')
    (tmp_path/'ignored.py').write_text('invalid python @')
    settings={'engine':'ai_primary','resources':{'max_seconds':0,'max_memory_mb':0}}
    with pytest.raises(InputError):analyze(Project(tmp_path),config=settings)
    result=analyze(Project(tmp_path),config={**settings,'inputs':{'exclusions':['ignored.py']}},queries=['values'])
    assert {row['module'] for row in result.raw_snapshot()['inventory']}=={'good'}
    value=result.query(ValueQuery('good','result')).value
    assert value['constants']==[17] and not value['top']


def test_configured_entries_execute_only_the_selected_source_function():
    source="def first():return 'A'\ndef second():return 'B'\n"
    left=run(source,config={'inputs':{'entries':['subject:first']}},queries=['returns'])
    right=run(source,config={'inputs':{'entries':['subject:second']}},queries=['returns'])
    assert left.query('returns').value['subject.first']['constants']==['A']
    assert right.query('returns').value['subject.second']['constants']==['B']
    assert 'subject.second' not in left.query('returns').value
    assert 'subject.first' not in right.query('returns').value
    assert left.raw_snapshot()['entry_contract']['entry_coverage']=='declared_entries'


def test_public_deadline_bounds_real_source_preparation_and_zero_disables_it():
    stopped=run(config={'resources':{'max_seconds':1e-9}})
    completed=run(config={'resources':{'max_seconds':0}})
    assert stopped.solver_status=='budget_exhausted' and stopped.coverage_status=='open'
    assert any(row.get('kind')=='time_budget' for row in stopped.raw_snapshot()['obligations'])
    assert not stopped.raw_snapshot()['values']
    exact(completed,'result',17)


def test_public_memory_bound_samples_actual_resident_memory_before_source_work():
    stopped=run(config={'resources':{'max_memory_mb':1}})
    completed=run(config={'resources':{'max_memory_mb':0}})
    data=stopped.raw_snapshot()
    assert stopped.solver_status=='budget_exhausted' and data['stats']['memory_checks']>=1
    assert data['stats']['resident_memory_bytes']>1024*1024 and data['stats']['work']==0
    assert any(row.get('code')=='budget:max_memory_mb' for row in data['obligations'])
    assert completed.raw_snapshot()['stats'].get('memory_checks',0)==0
    exact(completed,'result',17)


DISPATCH="""def identity(payload):return payload
def box(payload):return {'value':payload}
def dispatch(key,payload):
    table={'read':identity,'write':box}
    return table[key](payload)
result=dispatch(key,'after')
"""


def l1_config():
    return {'abstract':{'enable_value_partition':False},'inputs':{'bindings':{'subject.key':{'type':'str','top':True}}},
            'llm':{'enabled':True,'level1':True}}


def relation_count(result):
    value=result.query(ValueQuery('subject','result')).value
    assert not value['top'] and not value['top_sorts'] and value['constants']==['after']
    return len(value['relations'])


@pytest.mark.parametrize('path,disabled,enabled',[
    ('llm.enabled',False,True),('llm.level1',False,True),
    ('llm.max_proposals',0,1),('resources.max_reanalyses',0,1)])
def test_each_public_l1_gate_and_retry_cap_changes_real_reanalysis(path,disabled,enabled):
    group,name=path.split('.')
    results=[]
    for setting in (disabled,enabled):
        config=l1_config();config.setdefault(group,{})[name]=setting
        if path=='llm.level1':config['llm']['level2']=True
        results.append(run(DISPATCH,config=config))
    assert relation_count(results[0])==4 and relation_count(results[1])==2
    assert results[1].raw_snapshot()['llm']['level1']['attempted']==1
    assert results[1].raw_snapshot()['llm']['level1']['reanalysis_work']>0
    assert results[0].raw_snapshot().get('llm',{}).get('level1',{}).get('attempted',0)==0


def test_explicit_l1_proposals_replace_automatic_policy_and_rejection_preserves_baseline():
    automatic=run(DISPATCH,config=l1_config())
    config=l1_config();config['llm']['proposals']=[{'strategy':'delete_entry','verified':True}]
    rejected=run(DISPATCH,config=config)
    assert relation_count(automatic)==2 and relation_count(rejected)==4
    assert rejected.raw_snapshot()['llm']['level1']['proposals'][0]['status']=='rejected'


@pytest.mark.parametrize('setting',[{'level2':False},{'checker_budget':0}])
def test_l2_gate_and_checker_budget_change_actual_authority_without_losing_fallback(setting):
    from tests.refactor.verification.test_public_l2 import SOURCE,PROPERTY
    query=PropertyQuery(PROPERTY)
    base={'enabled':True,'level1':True,'level2':True,'provider':'offline','checker_budget':10000}
    stopped=run(SOURCE,config={'llm':{**base,**setting}},queries=['values',query])
    checked=run(SOURCE,config={'llm':base},queries=['values',query])
    exact(stopped,'plain','original');exact(checked,'plain','original')
    assert stopped.query(query).proof_status=='unknown' and checked.query(query).proof_status=='verified'
    assert checked.raw_snapshot()['llm']['level2']['applications']==2
    assert stopped.raw_snapshot().get('llm',{}).get('level2',{}).get('applications',0)==0


def test_recorded_l2_provider_consumes_explicit_candidate_and_empty_recording_falls_back():
    from tests.refactor.verification.test_public_l2 import SOURCE,PROPERTY
    from pythonstan.llm.providers import OfflineDispatchProposer
    candidate=OfflineDispatchProposer().propose(SOURCE).candidate
    assert candidate is not None
    record={'level':2,'source_digest':hashlib.sha256(SOURCE.encode()).hexdigest(),'candidate':candidate}
    config={'enabled':True,'level2':True,'provider':'recorded'}
    query=PropertyQuery(PROPERTY)
    empty=run(SOURCE,config={'llm':config},queries=['values',query])
    recorded=run(SOURCE,config={'llm':{**config,'proposals':[record]}},queries=['values',query])
    exact(empty,'plain','original');exact(recorded,'plain','original')
    assert empty.query(query).proof_status=='unknown' and recorded.query(query).proof_status=='verified'
    assert empty.raw_snapshot()['llm']['level2']['applications']==0
    assert recorded.raw_snapshot()['llm']['level2']['applications']==2
    assert recorded.raw_snapshot()['llm']['level2']['proposal_work']==1


@pytest.mark.parametrize('field',['contexts','regions'])
def test_configured_scope_filter_selects_real_retained_source_outcomes(field):
    source='def first():return 1\ndef second():return 2\na=first()\nb=second()\n'
    full=run(source,config={'output':{'views':['outcomes']}},queries=['outcomes'])
    selected=run(source,config={'output':{'views':['outcomes'],field:['*:3:2:Call']}},queries=['outcomes'])
    all_rows=full.to_dict()['answers']['outcomes']['value']
    rows=selected.to_dict()['answers']['outcomes']['value']
    assert {'subject.first','subject.second'}<=set(all_rows)
    assert set(rows)=={'subject.first'} and rows['subject.first'][0]['value']['constants']==[1]
    assert selected.raw_snapshot()['outcomes']==full.raw_snapshot()['outcomes']


def test_configured_output_views_select_retained_records_without_changing_analysis():
    calls=run(config={'output':{'views':['calls']}},queries=['calls','values'])
    values=run(config={'output':{'views':['values']}},queries=['calls','values'])
    assert set(calls.to_dict()['answers'])=={'calls'} and set(values.to_dict()['answers'])=={'values'}
    exact(calls,'result',17);exact(values,'result',17)
    assert calls.raw_snapshot()['values']==values.raw_snapshot()['values']


def test_public_observation_gate_and_event_cap_preserve_source_facts_and_charged_work():
    source='def ident(x):return x\nresult=ident(17)\n'
    off=run(source,config={'observation':{'enabled':False}}).raw_snapshot()
    tiny=run(source,config={'observation':{'enabled':True,'max_events':0}}).raw_snapshot()
    full=run(source,config={'observation':{'enabled':True,'max_events':1000}}).raw_snapshot()
    assert 'events' not in off and tiny['events']['retained_events']==0 and tiny['events']['truncated']
    assert full['events']['retained_events']>0 and not full['events']['truncated']
    for key in ('values','returns','calls','effects','outcomes','coverage_status','solver_status'):
        assert off[key]==tiny[key]==full[key]
    assert off['stats']['work']==tiny['stats']['work']==full['stats']['work']
