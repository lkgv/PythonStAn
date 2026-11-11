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
