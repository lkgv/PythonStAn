"""Operational Native controls: values, contexts, roots, costs and real diagnostics."""
from dataclasses import asdict
import ast
import json
import re
from pathlib import Path
import subprocess
import sys
import pytest
from pythonstan import Source
from pythonstan.config import resolve_config,ConfigurationError
from pythonstan.inputs import snapshot_target
from pythonstan.analysis.pointer.kcfa.context_selector import ContextPolicy,ContextSelector
from pythonstan.analysis.pointer.kcfa.context import CallSite,CallStringContext,Ctx,ordered_binding_token
from pythonstan.analysis.pointer.kcfa.object import AbstractObject
from pythonstan.analysis.pointer.kcfa.variable import Variable
from pythonstan.ir import IRCall
from tests.pointer.kcfa.builders import make_scope,make_site

ROOT=Path(__file__).resolve().parents[3]
SEMANTIC_ROOTS=('values','returns','outcomes','calls','effects','obligations','solver_status','coverage_status')


def worker(source,**options):
    target=Source(source,module='subject')
    config=resolve_config(target,{'engine':'native','andersen':{'enable_synthetic_method_contexts':False,**options}})
    snapshot=snapshot_target(target,config)
    payload={'modules':[asdict(m) for m in snapshot.modules],
             'scenarios':[{'root_module':s.root_module,'entry_qualname':s.entry_qualname,
                           'arguments':list(s.arguments),'scenario_id':s.scenario_id} for s in snapshot.scenarios],
             'config':config.to_dict()}
    result=subprocess.run([sys.executable,'-m','pythonstan.compat.native_worker'],cwd=ROOT,
                          input=json.dumps(payload),capture_output=True,text=True,timeout=30)
    assert result.returncode==0,result.stderr
    frames=[json.loads(line) for line in result.stdout.splitlines()]
    assert frames and all(row['kind'] in {'progress','result'} for row in frames)
    data=frames[-1]['result'];assert 'error' not in data,data
    return data,'\n'.join(row['message'] for row in data.get('diagnostics',()) if row['kind']=='legacy_log')


def signature(result):
    value=json.loads(json.dumps({key:result[key] for key in SEMANTIC_ROOTS}))
    # Native constructor scratch names include host id() suffixes. Alpha-rename
    # only those internal return-target identities; never normalize user values.
    for obligation in value['obligations']:
        target=obligation.get('return_target')
        if isinstance(target,str) and target.startswith('$new_result@'):
            obligation['return_target']=re.sub(r'@\d{8,}', '@<host-id>', target)
    value['obligations']=sorted(value['obligations'],key=lambda row:json.dumps(row,sort_keys=True))
    # Native effect channels are unordered may observations (each record says
    # order unknown); scheduling order is not a temporal property.
    for channel,records in value['effects'].items():
        if isinstance(records,list):value['effects'][channel]=sorted(records,key=lambda row:json.dumps(row,sort_keys=True))
    return value


FIXED={'max_points_to_size':1,'enable_instrumentation':True,'entry_points':['subject.f'],
       'build_class_hierarchy':False,'use_mro_resolution':False,'project_path':'/ignored-native-root',
       'library_paths':['/ignored-library'],'max_import_depth':1,'type':'not-pointer-analysis',
       'enable_debug_monitor':True,'debug_log_interval':2,'track_object_flow':True,
       'track_pfg_activation':True,'export_debug_data':True,'debug_output_dir':'/ignored-debug-root'}

@pytest.mark.parametrize('name',sorted(FIXED))
def test_fixed_or_unavailable_public_native_option_rejects_nondefault(name):
    with pytest.raises(ConfigurationError):resolve_config(config={'engine':'native','andersen':{name:FIXED[name]}})


def test_user_log_level_changes_actual_worker_stderr_with_semantic_invariance():
    off,left=worker('result=7',log_level='ERROR')
    on,right=worker('result=7',log_level='INFO')
    assert left!=right and 'Analysis complete' in right
    assert signature(off)==signature(on)
    assert off['stats']['global_work_used']==on['stats']['global_work_used']


@pytest.mark.parametrize('option,source,marker',[
 ('verbose','result=missing(7)','[UNKNOWN]'),
 ('log_unknown_details','result=missing(7)','[UNKNOWN]'),
 ('debug_inheritance','class Base:\n    value=7\nclass Child(Base):\n    pass\nresult=Child.value','[ALLOC]')])
def test_diagnostic_switch_changes_log_without_changing_semantics(option,source,marker):
    off,left=worker(source,log_level='INFO',**{option:False})
    on,right=worker(source,log_level='INFO',**{option:True})
    assert left!=right and marker in right
    assert signature(off)==signature(on)
    assert off['stats']['global_work_used']==on['stats']['global_work_used']


def test_native_iteration_limit_changes_completion_without_fabricating_work():
    low,_=worker('result=7',max_iterations=1)
    high,_=worker('result=7',max_iterations=1000)
    assert low['solver_status']=='budget_exhausted' and high['solver_status']=='converged'
    assert high['values']['subject']['result']['constants']==[7]
    assert low['stats']['native_selected_work']==1<high['stats']['native_selected_work']


def test_synthetic_receiver_option_changes_actual_roots_and_calls():
    source='def called():\n    return 7\nclass C:\n    def method(self):\n        return called()\n'
    off,_=worker(source,enable_synthetic_method_contexts=False)
    on,_=worker(source,enable_synthetic_method_contexts=True)
    assert not off['calls']
    assert any(row['caller']=='subject.C.method' and row['callee']=='subject.called' for row in on['calls']),on
    assert 'synthetic_entry_contract' not in off
    contract=on['synthetic_entry_contract']
    assert contract['entry_coverage']=='initialization_and_synthetic_method_contexts'
    assert [row['qualname'] for row in contract['roots']]==['subject.C.method']
    assert contract['roots'][0]['translation_status']=='registered'


def test_index_sensitivity_changes_real_dictionary_key_values():
    source="mapping={'a':1,'b':2}\nresult=mapping['a']\n"
    precise,_=worker(source,index_sensitive=True)
    coarse,_=worker(source,index_sensitive=False)
    assert precise['values']['subject']['result']['constants']==[1]
    assert coarse['values']['subject']['result']['constants']==[1,2]


def test_unknown_tracking_controls_diagnostics_but_not_real_havoc():
    source="class Box: pass\nbox=Box()\nbox.value=1\neval('box.value=7')\nresult=box.value\n"
    off,_=worker(source,track_unknowns=False)
    on,_=worker(source,track_unknowns=True)
    assert signature(off)==signature(on)
    assert off['effects']['havoc'] and off['effects']['calls'] and off['effects']['exceptions']
    assert off['values']['subject']['result']['top']


@pytest.mark.parametrize('policy',list(ContextPolicy),ids=lambda policy:policy.value)
def test_each_context_policy_has_actual_discrimination_or_insensitive_invariance(policy):
    selector=ContextSelector(policy)
    ctx=selector.empty_context();scope=make_scope('subject','module')
    calls=[CallSite(IRCall(ast.parse(name+'()').body[0].value),'subject') for name in ('left','right')]
    objects=[AbstractObject(ctx,make_site(name=name)) for name in ('left','right')]
    params=[ordered_binding_token((Ctx(ctx,scope,Variable(name)),)) for name in ('left','right')]
    left=selector.select_call_context(calls[0],ctx,objects[0],params[0])
    right=selector.select_call_context(calls[1],ctx,objects[1],params[1])
    assert (left==right)==(policy is ContextPolicy.INSENSITIVE)


def probe(case,**settings):
    command=[sys.executable,str(ROOT/'scripts/refactor/native_context_probe.py'),'--repo',str(ROOT),
             '--case',case,'--policy','2-cfa','--budget','6000','--config-json',json.dumps(settings)]
    result=subprocess.run(command,capture_output=True,text=True,timeout=30)
    assert result.returncode==0,result.stderr
    row=json.loads(result.stdout);assert row['status']=='ok',row
    return row


@pytest.mark.parametrize('control,value',[('heap_context_policy','0-cfa'),('heap_context_depth',0)])
def test_each_heap_control_changes_actual_object_aliases_without_losing_values(control,value):
    precise=probe('heap_precision')
    coarse=probe('heap_precision',**{control:value})
    def constants(row,name):return {item['value'] for item in row['values'][name] if item['kind']=='ConstantObject'}
    assert constants(precise,'left')=={1} and constants(precise,'right')=={2}
    assert constants(coarse,'left')==constants(coarse,'right')=={1,2}
    assert precise['object_identity_counts']['union_ab']==2
    assert coarse['object_identity_counts']['union_ab']==1


def test_intrinsic_toggle_changes_actual_charged_service_work_and_preserves_result():
    plain=probe('intrinsic_getattr',enable_ai_intrinsics=False)
    assisted=probe('intrinsic_getattr',enable_ai_intrinsics=True)
    assert plain['statistics']['ai_intrinsic_work']==0
    assert assisted['statistics']['ai_intrinsic_work']>0
    for row in (plain,assisted):
        assert {item['value'] for item in row['values']['result'] if item['kind']=='ConstantObject'}=={7}
        assert row['statistics']['global_work_used']==sum(row['statistics']['work_by_kind'].values())


def test_nested_intrinsic_budget_is_not_a_configuration_only_switch():
    complete=probe('intrinsic_getattr',enable_ai_intrinsics=True)
    limited=probe('intrinsic_getattr',enable_ai_intrinsics=True,ai_budget={'max_iterations':0})
    assert complete['statistics']['ai_intrinsic_work']>limited['statistics']['ai_intrinsic_work']
    assert limited['statistics']['ai_intrinsic_fallbacks']>0


@pytest.mark.parametrize('engine',['native','native_then_ai'])
def test_public_synthetic_entry_scope_preserves_stage_specific_coverage(engine):
    from pythonstan import analyze
    source='def called():\n    return 7\nclass C:\n    def method(self):\n        return called()\n'
    data=analyze(Source(source,module='subject'),config={'engine':engine,
            'andersen':{'enable_synthetic_method_contexts':True}},queries=['calls']).raw_snapshot()
    entry=data['entry_contract']
    if engine=='native':
        assert entry['entry_coverage']=='initialization_and_synthetic_method_contexts'
        contract=entry['synthetic_method_contexts']
        assert any(call['caller']=='subject.C.method' and call['callee']=='subject.called' for call in data['calls'])
    else:
        assert entry['entry_coverage']=='initialization_only'
        contract=entry['native_prepass_synthetic_method_contexts']
        assert 'not AI external-input coverage' in entry['final_query_scope']
        assert not any(call['caller']=='subject.C.method' for call in data['calls'])
    assert {root['qualname'] for root in contract['roots']}=={'subject.C.method'}
