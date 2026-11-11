import json
import pytest
from pythonstan import Source,File,Project,ValueQuery,analyze
from pythonstan.config import ConfigurationError,resolve_config

@pytest.mark.parametrize('field',['max_iterations','max_stmt_visits','narrowing_iterations'])
def test_nested_zero_uses_historical_neutral_range(field):
    c=resolve_config(config={'engine':'native','andersen':{'enable_ai_intrinsics':True,'ai_budget':{field:0}}})
    assert c['andersen']['ai_budget'][field]==0

@pytest.mark.parametrize('engine',['ai_primary','native_then_ai','hybrid'])
def test_precise_override_changes_real_core_capacity(engine):
    c=resolve_config(config={'preset':'precise'},overrides={'engine':engine})
    assert c['abstract']['max_const_strings']==50 and c['abstract']['int_domain_max_consts']==20
    assert c['andersen']['context_policy']=='2-cfa'

@pytest.mark.parametrize('options',[
    {'abstract':{'narrowing_iterations':-1}},
    {'inputs':{'source_roots':[None]}},
    {'inputs':{'bindings':{'bad.x':{'constants':[1],'top':True}}}},
    {'inputs':{'bindings':{'bad.x':{'value':True,'type':'int'}}}},
    {'inputs':{'bindings':{'bad.x':{'value':float('nan')}}}},
    {'inputs':{'bindings':{'bad.x':{'unknown_field':1}}}},
    {'queries':['not_registered']},
    {'output':{'views':['not_registered']}},
])
def test_invalid_control_fails_before_bad_target_parse(options):
    with pytest.raises(ConfigurationError):analyze(Source('not valid python @'),config={'engine':'ai_primary',**options})

@pytest.mark.parametrize('kind',['source','file'])
def test_project_root_actually_resolves_memory_and_file_dependencies(tmp_path,kind):
    dep=tmp_path/'deps';dep.mkdir();(dep/'helper.py').write_text("value='after'")
    source="from helper import value\nresult=value\n"
    path=tmp_path/'subject.py';path.write_text(source)
    target=Source(source,module='subject') if kind=='source' else File(path)
    q=ValueQuery('subject','result')
    result=analyze(target,config={'engine':'ai_primary','inputs':{'project_root':dep}},queries=[q])
    value=result.query(q).value
    assert value['constants']==['after'] and not value['top'] and not value['top_sorts']


def test_unknown_binding_module_rejects_instead_of_silently_ignoring():
    from pythonstan.inputs import InputError
    with pytest.raises(InputError):analyze(Source('result=1',module='subject'),config={'engine':'ai_primary','inputs':{'bindings':{'typo.x':7}}})


def test_relative_root_and_pathlike_normalize_only_path_fields(tmp_path):
    c=resolve_config(config={'inputs':{'project_root':tmp_path,'source_roots':[tmp_path/'src']}})
    assert c['inputs']['project_root']==str(tmp_path) and c['inputs']['source_roots']==[str(tmp_path/'src')]
    with pytest.raises(ConfigurationError):resolve_config(config={'abstract':{'context_policy':tmp_path}})


def test_failed_l1_runtime_publication_reserves_actual_window(monkeypatch):
    from pythonstan.session import AnalysisSession
    from tests.refactor.llm.test_strategy_independent import setup_run,proposal_for,run_controller
    context=setup_run();snapshot=context[-2];actual=AnalysisSession.execute;spent=[]
    class Provider:
        def propose(self,*args):return [proposal_for(snapshot),proposal_for(snapshot,strategy='call_context',parameters={'context_depth':3})]
    def fail(session):
        r=actual(session);spent.append(r.raw_snapshot()['stats']['total_work'])
        raise RuntimeError('publication failed after actual work')
    monkeypatch.setattr(AnalysisSession,'execute',fail)
    selected,feedback=run_controller(Provider(),*context)
    assert spent and selected['values']==context[-1]['values']
    assert feedback['total_work']>=feedback['baseline_work']+sum(spent)
    assert feedback['total_work']<=context[2]['resources']['max_work']
    assert feedback['proposals'][0]['error_kind']=='RuntimeError'
