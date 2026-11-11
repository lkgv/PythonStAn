"""Fair-cost and immutable-input controls for the four-arm L2 family."""
import gzip
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

ROOT=Path(__file__).resolve().parents[3]

def family():
    spec=importlib.util.spec_from_file_location('checked_family',ROOT/'benchmark/refactor/guarded_dispatch_family.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module

def value(**changes):
    result={'constants':['payload'],'objects':[],'top':False,'bottom':False,'top_sorts':[],'may_unbound':False}
    result.update(changes);return result

@pytest.mark.parametrize('extra',[{'top_sorts':['str']},{'may_unbound':True},{'bottom':True},
    {'integer_interval':[0,9]},{'string_patterns':['p*']},{'objects':['other']}])
def test_sample_constant_beside_other_behavior_is_not_exact_family_quality(extra):
    result={'solver_status':'converged','coverage_status':'closed_under_profile',
            'values':{'example':{'out_0_0':value(**extra)}}}
    quality=family().quality(result,SimpleNamespace(functions={}),1,1)
    assert quality['exact_matching_answers']==0 and not quality['all_gold_useful']

def test_fresh_box_payload_is_checked_as_a_full_value():
    box=value(constants=[],objects=['fresh'],slots=[{'entries':[{'key':'value','value':value(top_sorts=['str'])}]}])
    result={'solver_status':'converged','coverage_status':'closed_under_profile',
            'values':{'example':{'out_0_0':value(),'out_0_1':box}}}
    assert family().quality(result,SimpleNamespace(functions={}),2,1)['exact_matching_answers']==1


@pytest.mark.parametrize('changes',[{'havoc':True},{'default':value(top=True)},
    {'default':value(bottom=True)},{'maybe_missing':['value']}])
def test_fresh_box_known_slot_cannot_hide_unknown_default_havoc_or_missing_key(changes):
    slot={'object':'fresh','entries':[{'key':'value','value':value()}],
          'havoc':False,'maybe_missing':[],'default':value(constants=[],bottom=True)}
    box=value(constants=[],objects=['fresh'],slots=[slot])
    result={'solver_status':'converged','coverage_status':'closed_under_profile',
            'values':{'example':{'out_0_0':value(),'out_0_1':box}}}
    assert family().quality(result,SimpleNamespace(functions={}),2,1)['all_gold_useful']
    slot.update(changes)
    assert not family().quality(result,SimpleNamespace(functions={}),2,1)['all_gold_useful']

def test_full_process_cost_and_complete_denominator_govern_release_benefit():
    module=family();rows=[]
    for arm in module.ARMS:
        rows.append({'size':2,'repeats':4,'arm':arm,'quality':{'all_gold_useful':True},
                     'total_work':100,'wall_seconds':0.5 if arm=='baseline' else 0.1,
                     'process_wall_seconds':1 if arm=='baseline' else 2,
                     'process_cpu_seconds':1 if arm=='baseline' else 2})
    summary=module.summarize(rows,{(2,4)})
    assert summary['denominator_complete']
    assert summary['rows'][0]['lower_total_wall_at_equal_precision']
    assert not summary['rows'][0]['lower_full_process_wall_at_equal_precision']
    assert not summary['benefit_demonstrated'],'Inner solve-only timing cannot hide setup/startup cost'
    missing=module.summarize(rows[:-1],{(2,4),(8,4)})
    assert not missing['denominator_complete'] and len(missing['missing_runs'])==5
    duplicate=module.summarize([*rows,rows[0]],{(2,4)})
    assert not duplicate['denominator_complete']

def test_frozen_capture_detects_copy_race_and_refuses_false_identity(tmp_path,monkeypatch):
    module=family();source=tmp_path/'source';package=source/'pythonstan';package.mkdir(parents=True)
    (source/'pythonstan_inspector').mkdir();(package/'module.py').write_text('VALUE=1\n')
    monkeypatch.setattr(module,'ROOT',source)
    output=tmp_path/'capture';output.mkdir();copy=module.shutil.copyfile
    def changing(src,dst):
        result=copy(src,dst)
        Path(src).write_text('VALUE=2\n')
        return result
    monkeypatch.setattr(module.shutil,'copyfile',changing)
    with pytest.raises(RuntimeError,match='source changed'):
        module.freeze_runtime(output)
    assert (output/'source_capture_error.json').exists()

def test_family_never_overwrites_an_earlier_evidence_directory(tmp_path):
    (tmp_path/'raw.json').write_text('retained original')
    with pytest.raises(SystemExit):family().main(['--output',str(tmp_path)])
    assert (tmp_path/'raw.json').read_text()=='retained original'

def _worker(tmp_path,mode):
    target=tmp_path/(mode+'.json.gz')
    script=r'''
import builtins,importlib.abc,importlib.util,sys
from pathlib import Path
from types import SimpleNamespace
root,output,mode=Path(sys.argv[1]),Path(sys.argv[2]),sys.argv[3]
spec=importlib.util.spec_from_file_location('frozen_family',root/'benchmark/refactor/guarded_dispatch_family.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
if mode=='baseline':
    class Forbidden(importlib.abc.MetaPathFinder):
        def find_spec(self,fullname,path=None,target=None):
            if fullname.startswith('pythonstan.analysis.verification') or fullname=='pythonstan.analysis.composition.guarded_dispatch':
                raise AssertionError('Baseline imported optional verification')
    sys.meta_path.insert(0,Forbidden())
else:
    clock=[0.0];module.time.perf_counter=lambda:clock[0]
    original=builtins.__import__
    def importer(name,*args,**kwargs):
        if name=='pythonstan.analysis.composition.guarded_dispatch':clock[0]+=17.0
        return original(name,*args,**kwargs)
    builtins.__import__=importer
args=SimpleNamespace(memory_mib=1024,seconds=1000,work=100000,executable_root=root,
    size=2,repeats=1,arm='baseline' if mode=='baseline' else 'correct_candidate',output=output)
module.worker(args)
'''
    result=subprocess.run([sys.executable,'-c',script,str(ROOT),str(target),mode],capture_output=True,text=True,timeout=15)
    assert result.returncode==0,result.stdout+result.stderr
    with gzip.open(target,'rt') as stream:return json.load(stream)

def test_ordinary_baseline_runs_without_importing_optional_checker(tmp_path):
    row=_worker(tmp_path,'baseline')
    assert row['quality']['all_gold_useful'] and row['provider'] is None
    assert row['optional_verification_imported'] is False

def test_candidate_import_setup_is_inside_the_measured_timer(tmp_path):
    row=_worker(tmp_path,'candidate')
    assert row['wall_seconds']==17.0,'Simulated17s optional import must be included in complete arm setup'
    assert row['provider']['proposals']>0
