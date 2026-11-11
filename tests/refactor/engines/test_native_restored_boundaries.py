"""Actual Native public-source and input publication boundaries, not config echo."""
import json
import subprocess
import sys
from pathlib import Path
import pytest
from pythonstan import Source,File,Project,ValueQuery,analyze


def get(target,module='subject',symbol='result',**extra):
    config={'engine':'native',**extra}
    q=ValueQuery(module,symbol)
    r=analyze(target,config=config,queries=[q,'stats'])
    return r,r.query(q)


@pytest.mark.parametrize('kind',['source','file','project'])
def test_actual_initial_bindings_are_published_before_native_work(kind,tmp_path):
    source='result=seed\n'
    path=tmp_path/'subject.py';path.write_text(source)
    target={'source':Source(source,module='subject'),'file':File(path),'project':Project(tmp_path)}[kind]
    result,answer=get(target,inputs={'bindings':{'subject.seed':{'value':7}}})
    assert answer.status=='answered' and answer.value['constants']==[7] and not answer.value['top'],answer
    assert result.raw_snapshot()['stats']['input_publications']==1


def test_bare_input_query_is_retained_even_without_source_assignment():
    result,answer=get(Source('pass',module='subject'),symbol='seed',inputs={'bindings':{'subject.seed':11}})
    assert answer.status=='answered' and answer.value['constants']==[11] and not answer.value['top'],answer


def test_finite_input_alternatives_keep_scalar_types_and_charge_each_publication():
    result,answer=get(Source('result=seed',module='subject'),inputs={'bindings':{'subject.seed':{'constants':[7,False,None,'after']}}})
    assert {(item['type'],repr(item['value'])) for item in answer.value['tagged_constants']}=={('int','7'),('bool','False'),('none','None'),('str',"'after'")}
    assert result.raw_snapshot()['stats']['input_publications']==4
    assert result.raw_snapshot()['stats']['global_work_used']>=4


def test_typed_top_input_widens_honestly_and_records_projection():
    result,answer=get(Source('result=seed',module='subject'),inputs={'bindings':{'subject.seed':{'type':'str','top':True}}})
    assert answer.value['top'] and result.coverage_status=='open'
    assert any(row.get('kind')=='native_input_projection' for row in result.raw_snapshot()['obligations'])


def test_source_cookie_never_redecodes_unicode_when_worker_materializes_utf8():
    source="# coding: latin-1\nresult='café😀'\n"
    result,answer=get(Source(source,module='subject'))
    assert answer.value['constants']==['café😀'] and not answer.value['top'],answer


def test_actual_latin1_file_retains_original_decoded_snapshot(tmp_path):
    path=tmp_path/'subject.py';path.write_bytes(b"# coding: latin-1\nresult='caf\xe9'\n")
    result,answer=get(File(path))
    assert answer.value['constants']==['café'] and not answer.value['top'],answer


def test_input_budget_denial_never_publishes_uncharged_remaining_values():
    result,answer=get(Source('result=seed',module='subject'),symbol='seed',inputs={'bindings':{'subject.seed':{'constants':[1,2,3]}}},resources={'max_work':2})
    data=result.raw_snapshot()
    assert result.solver_status=='budget_exhausted'
    assert data['stats']['global_work_used']==2 and data['stats']['input_publications']==2
    assert answer.value['constants']==[1,2]


def test_injected_source_is_captured_before_tac_and_inherited_by_nested_scopes(tmp_path):
    from dataclasses import FrozenInstanceError
    from pythonstan.analysis.contracts import SourceModule
    from pythonstan.world.pipeline import Pipeline
    from pythonstan.world import World
    from pythonstan.analysis.pointer.kcfa.config import Config
    source="def outer(value):\n    def inner():\n        return value\n    return inner\nresult=outer('after')()\n"
    path=tmp_path/'subject.py';path.write_text("result='wrong-file-version'\n")
    snapshot=SourceModule('subject','/virtual/original.py',source)
    supplied={'subject':snapshot}
    pipeline=Pipeline(config={'filename':str(path),'project_path':str(tmp_path),'library_paths':[],
        'import_level':0,'mock_libs':False,'time_count':False,
        'analysis':[{'name':'pta','id':'PointerAnalysis','prev_analysis':['ir'],'options':Config(enable_ai_intrinsics=False,enable_synthetic_method_contexts=False,log_level='ERROR').to_dict()}]},
        source_snapshots=supplied)
    supplied.clear()
    module=World().get_entry_module();scopes=World().scope_manager
    assert module.get_source_snapshot() is snapshot
    outer=scopes.get_scope('subject.outer') if hasattr(scopes,'get_scope') else scopes.names2scope['subject.outer']
    inner=scopes.names2scope['subject.outer.inner']
    assert outer.get_source_snapshot() is snapshot and inner.get_source_snapshot() is snapshot
    path.unlink();module.stmt.body.clear()
    assert module.get_source_snapshot().source==source and inner.get_source_snapshot().source==source
    with pytest.raises(FrozenInstanceError):snapshot.source='changed'


def test_source_installation_rejects_late_or_mismatched_module_snapshots(tmp_path):
    from pythonstan.world.scope_manager import ScopeManager
    from pythonstan.analysis.contracts import SourceModule
    manager=ScopeManager();manager.build()
    with pytest.raises(TypeError):manager.install_source_snapshots({'wrong':SourceModule('subject','subject.py','pass')})
    manager.install_source_snapshots({'subject':SourceModule('subject','subject.py','pass')})
    manager.scopes.add(object())
    with pytest.raises(ValueError):manager.install_source_snapshots({})


def test_failed_synthetic_translation_has_real_future_havoc_not_only_an_open_label(tmp_path,monkeypatch):
    from pythonstan.world.pipeline import Pipeline
    from pythonstan.world import World
    from pythonstan.analysis.pointer.kcfa.config import Config
    from pythonstan.analysis.pointer.kcfa.ir_translator import IRTranslator
    from pythonstan.analysis.pointer.kcfa.object import UnknownValue
    path=tmp_path/'subject.py'
    path.write_text("value=1\nclass C:\n    def method(self):\n        pass\nresult=value\n")
    def fail(self,scope):raise ValueError('deliberate unrepresented method body')
    monkeypatch.setattr(IRTranslator,'translate_function',fail)
    pipeline=Pipeline(config={'filename':str(path),'project_path':str(tmp_path),'library_paths':[],
        'import_level':0,'mock_libs':False,'time_count':False,
        'analysis':[{'name':'pta','id':'PointerAnalysis','prev_analysis':['ir'],
                     'options':Config(enable_ai_intrinsics=False,enable_synthetic_method_contexts=True,log_level='ERROR').to_dict()}]})
    pipeline.run();query=pipeline.analysis_manager.get_results('pta').query();module=World().get_entry_module()
    assert any(isinstance(obj,UnknownValue) for obj in query.points_to_at('result',module))
    assert any(row['kind']=='heap_havoc' for row in query.get_unknown_effect_events())
    assert query.get_statistics()['synthetic_method_roots'][0]['translation_status']=='failed_open'


def test_old_native_query_keeps_its_entry_and_source_manager_after_world_replacement(tmp_path):
    from pythonstan.world.pipeline import Pipeline
    from pythonstan.world import World
    from pythonstan.analysis.pointer.kcfa.config import Config
    def build(name):
        path=tmp_path/(name+'.py');path.write_text('result=7')
        pipeline=Pipeline(config={'filename':str(path),'project_path':str(tmp_path),'library_paths':[],
            'import_level':0,'mock_libs':False,'time_count':False,
            'analysis':[{'name':'pta','id':'PointerAnalysis','prev_analysis':['ir'],
                         'options':Config(enable_ai_intrinsics=False,enable_synthetic_method_contexts=False,log_level='ERROR').to_dict()}]})
        pipeline.run();return pipeline.analysis_manager.get_results('pta').query(),World().get_entry_module(),World().scope_manager
    first,module,manager=build('first')
    second,other,other_manager=build('second')
    assert manager is not other_manager
    assert first._state.scope_manager is manager and second._state.scope_manager is other_manager
    assert first._scope_label(module)=='__main__' and first._scope_label(other)=='second'
    assert first._state.scope_manager.names2scope['first'] is module
    assert first._state.scope_manager.names2scope['first'].source_snapshot.source=='result=7'
    # Numeric old PTS handles across the reset remain a documented legacy limit;
    # this test intentionally does not decode those stale global bit positions.
