"""Independent empirical interpretation of finite-domain relation results."""
from pythonstan.analysis.pointer.ai.certified import finite_entry as family


def test_declared_rows_are_fresh_heaps_not_a_shared_request_sequence():
    source='''
events = []
def entry(value):
    events.append(value)
    return len(events)
'''
    spec=family.FiniteEntrySpec('entry',((10,20),),observed_globals=('events',))
    result=family.analyze_finite_entry(source,spec)
    assert result['status']=='finite_model_completed'
    assert result['declared_variants']==result['covered_variants']==2
    candidate={
        'schema_version':1,
        **{key:result[key] for key in ('semantics_version','model_semantics_version','source_sha256','entry_spec_sha256')},
        'rows':{row['variant_id']:{'return':1,'globals':{'events':[value]}}
                for row,value in zip(result['rows'],(10,20))},
    }
    verified=family.check_finite_entry(source,spec,candidate)
    assert verified['status']=='conditional_family_verified'
    assert verified['verified_variants']==2
    assert not verified['kcfa_results_certified']
    assert not verified['full_python_soundness_proved']
    assert any('repeated invocations on one heap' in residual for residual in verified['residual'])


def test_a_successful_subset_does_not_count_as_verified_family():
    source='''
def entry(value):
    if value:
        raise ValueError('declared exceptional branch')
    return 4
'''
    spec=family.FiniteEntrySpec('entry',((False,True),))
    result=family.analyze_finite_entry(source,spec)
    assert result['declared_variants']==2
    assert result['covered_variants']==1
    assert result['verified_variants']==0
    assert result['status']=='residual'
    assert len(result['rows'])==2
    assert [row['status'] for row in result['rows']]==['closed_model_completed','model_exception']


# This oracle reads the shipped candidate, not a candidate constructed from the
# analyzer's own answer. Only argument identities use the public typed encoding.
import hashlib
import itertools
import json
from pathlib import Path
import pytest


@pytest.mark.parametrize('use_override,fail,route',itertools.product((False,True),(False,True),('/a','/b')))
def test_shipped_candidate_has_independent_concrete_witness(use_override,fail,route):
    root=Path(__file__).resolve().parents[2]
    fixture=root/'benchmark/round6_composition'
    source=(fixture/'registry_entry.py').read_text()
    candidate=json.loads((fixture/'candidate.json').read_text())
    assert candidate['source_sha256']==hashlib.sha256(source.encode()).hexdigest()
    arguments={'positional':[['bool',use_override],['bool',fail]],'keywords':[['route',['str',route]]]}
    row_id=hashlib.sha256(json.dumps(arguments,ensure_ascii=True,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()
    namespace={}
    exec(compile(source,'<finite-candidate-independent-oracle>','exec'),namespace)
    result=namespace['request'](use_override,fail,route=route)
    assert candidate['rows'][row_id]=={'return':result,'globals':{'events':namespace['events']}}
