import io,json
from copy import deepcopy
import pytest
from pythonstan import Source,OutputSpec,ValueQuery,PropertyQuery,analyze
from pythonstan.results import AnalysisResult


def retained():
    value={'constants':['after'],'types':['str'],'objects':[],'top':False,'top_sorts':[],'may_unbound':False,'bottom':False}
    return {'schema_version':1,'run_id':'test','source_digest':'source','model_digest':'model','policy_digest':'policy','target':{},'inventory':[{'module':'pkg.mod'},{'module':'other'}],
    'entry_contract':{'entry_coverage':'declared_entries'},'effective_config':{'output':OutputSpec().to_dict()},'actual_engine':'ai_primary','solver_status':'converged','coverage_status':'open','proof_status':'verified','obligations':[{'code':'residual'}],
    'values':{'pkg.mod':{'result':value},'other':{'result':value}},'returns':{'pkg.mod.C.method.inner':value},
    'outcomes':{'pkg.mod.C.method.inner':[{'kind':'return','value':value,'context':['pkg.mod:2:0:Call']}]},
    'effects':{'reads':[1,2,3],'writes':[4,5],'havoc':['opaque'],'exceptions':['BaseException']},
    'proof':{'queries':{},'properties':{'guarded_dispatch_return_relation':{'status':'verified','records':[{'status':'verified','source':'source','conditions':['complete inputs','no frame effects']}]}}},'stats':{'total_work':17}}


def test_scoped_values_keep_longest_module_prefix_and_filters():
    r=AnalysisResult(retained(),['values','returns','outcomes'])
    d=r.to_dict(OutputSpec(views=('returns','outcomes'),modules=('pkg.mod',)))
    assert 'pkg.mod.C.method.inner' in d['answers']['returns']['value']
    q=r.to_dict(OutputSpec(views=('values',),functions=('other.*',)))
    assert set(q['answers']['values']['value'])=={'other'}

@pytest.mark.parametrize('selector',[{'contexts':('x',)},{'regions':('x',)}])
def test_joined_value_and_effect_scopes_are_unavailable(selector):
    r=AnalysisResult(retained(),['values','effects'])
    d=r.to_dict(OutputSpec(views=('values','effects'),**selector))
    assert d['answers']['values']['status']==d['answers']['effects']['status']=='unavailable'
    assert d['obligations']==[{'code':'residual'}] and d['coverage_status']=='open'


def test_effect_limits_count_inner_records_after_fields_without_mutation():
    r=AnalysisResult(retained(),['effects']);before=r.raw_snapshot()
    d=r.to_dict(OutputSpec(views=('effects',),fields=('writes','exceptions'),max_items=2))
    assert d['answers']['effects']['value']=={'writes':[4,5],'exceptions':[]}
    assert d['projection']['total_items']==3 and d['projection']['output_items']==2 and d['projection']['truncated']
    assert r.raw_snapshot()==before

@pytest.mark.parametrize('view',['proof','property:guarded_dispatch_return_relation'])
def test_zero_limit_never_leaves_a_verified_answer_without_certificate(view):
    r=AnalysisResult(retained(),[view])
    views=('proof',) if view=='proof' else ()
    d=r.to_dict(OutputSpec(views=views,max_items=0))
    answer=next(iter(d['answers'].values()))
    assert answer['value'] is None and answer['status']=='unavailable' and answer['proof_status']=='unknown'
    assert d['proof_status']=='verified' and d['projection']['truncated']


def test_one_item_preserves_full_certificate_vector_and_scoped_request_declines():
    q=PropertyQuery('guarded_dispatch_return_relation');r=AnalysisResult(retained(),[q])
    d=r.to_dict(OutputSpec(views=(),max_items=1));answer=next(iter(d['answers'].values()))
    assert answer['value']['records'][0]['conditions']==['complete inputs','no frame effects']
    d=r.to_dict(OutputSpec(views=(),modules=('pkg.mod',)));assert next(iter(d['answers'].values()))['status']=='unavailable'


def test_configured_format_controls_serialize_write_without_override():
    r=analyze(Source('result=17',module='example'),config={'engine':'ai_primary','output':{'format':'text'}})
    assert r.serialize().startswith('PythonStAn:')
    stream=io.StringIO();r.write(stream);assert stream.getvalue()==r.serialize()
    assert json.loads(r.serialize(format='json'))['actual_engine']=='ai_primary'


def test_uncomputed_bottom_is_not_a_proved_empty_answer():
    data=retained();data['solver_status']='budget_exhausted';data['values']['pkg.mod']['missing']={'bottom':True,'constants':[],'top':False}
    q=ValueQuery('pkg.mod','missing');r=AnalysisResult(data,[q])
    assert r.query(q).status=='unavailable' and r.query(q).value is None
    assert r.query(ValueQuery('pkg.mod','result')).status=='not_requested'

@pytest.mark.parametrize('kwargs',[{'modules':'pkg'},{'functions':'f'},{'fields':'constants'},{'contexts':'ctx'}])
def test_direct_projection_does_not_silently_split_scalar_strings(kwargs):
    with pytest.raises(ValueError):OutputSpec(**kwargs)

@pytest.mark.parametrize('module,symbol',[(1,'x'),('pkg',1),('', 'x')])
def test_query_identity_is_not_an_arbitrary_json_scalar(module,symbol):
    with pytest.raises(ValueError):ValueQuery(module,symbol)


def test_actual_source_relations_view_retains_useful_pairs_and_declines_unretained_scope():
    from tests.refactor.composition.test_regions_oracle_relations import RELATION_SOURCE
    q=ValueQuery('subject','result')
    r=analyze(Source(RELATION_SOURCE,module='subject'),config={'engine':'ai_primary','inputs':{'bindings':{'subject.key':{'type':'str','top':True}}}},queries=[q,'relations'])
    before=r.raw_snapshot();rows=r.query('relations').value
    assert {(row['key'],row['handler']) for row in rows}=={("'read'",'subject.identity'),("'write'",'subject.box')}
    assert all(row['module']=='subject' for row in rows)
    for scopes in [{'contexts':('*',)},{'regions':('*',)}]:
        answer=r.to_dict(OutputSpec(views=('relations',),**scopes))['answers']['relations']
        assert answer['status']=='unavailable' and answer['value'] is None
    assert r.raw_snapshot()==before
