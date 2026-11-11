"""Independent evidence-integrity mutants; no target execution or import."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]


def aggregator():
    path = REPO / 'benchmark/refactor/aggregate.py'
    spec = importlib.util.spec_from_file_location('independently_reviewed_aggregate', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def fixture(root, *, status='valid_result', after_identity=None, mode='D'):
    identity = {'git_commit':'fixed', 'code_sha256':'fixed-bytes'}
    manifest = {'stage':'official','executable':identity,'selected_cases':['large'],
                'selected_modes':[mode], 'input_identities':{'large':{'digest':'source'}}}
    (root/'run_manifest.json').write_text(json.dumps(manifest))
    row = {'case_id':'large','mode':mode,'process_status':status,'capture_status':'complete',
           'solver_status':'converged','coverage_status':'closed_under_profile','proof_status':'not_requested',
           'executable':identity,'executable_before':identity,'executable_after':after_identity or identity,
           'matched_version_valid':True,'input_digest':'source','observed_input_digest':'source',
           'input_identity_matches':True,'expected_inventory_count':1000,
           'result':{'stats':{},'inventory':[]},
           'reference_fact':{'kind':'pure_module_literal','expected':'after'},
           'answer':{'status':'answered','value':{'constants':['after'],'objects':[],'top':False,'bottom':False}},
           'quality':{'query_status':'answered','reference_kind':'pure_module_literal','not_top':True,
                      'contains_reference':True,'exact_reference':True,'useful_reference_hit':True,'proof_claim':False}}
    folder=root/'large'/mode
    folder.mkdir(parents=True)
    (folder/'record.json').write_text(json.dumps(row))
    return row,manifest


def test_failed_receipt_does_not_become_a_useful_large_project_path(tmp_path):
    # The supervisor can retain complete JSON and then observe a failed exit or
    # outer safeguard. Keep that raw answer, but do not promote it as a result.
    fixture(tmp_path,status='outer_timeout')
    summary=aggregator().aggregate(tmp_path)
    assert summary['denominator_complete']
    assert summary['modes']['D']['process_status']=={'outer_timeout':1}
    assert summary['modes']['D']['useful_reference_hits']==0
    assert summary['modes']['D']['non_top_reference_matches']==0
    assert summary['large_ai_primary_useful_paths']==[]


def test_wrong_executable_quality_cannot_count_as_a_matched_useful_path(tmp_path):
    fixture(tmp_path,after_identity={'git_commit':'other','code_sha256':'other-bytes'})
    summary=aggregator().aggregate(tmp_path)
    assert not summary['same_version_valid']
    assert summary['modes']['D']['useful_reference_hits']==0
    assert summary['large_ai_primary_useful_paths']==[]


def test_denominator_requires_exact_selected_case_mode_pairs(tmp_path):
    row,manifest=fixture(tmp_path,mode='A')
    manifest['selected_modes']=['A','B']
    (tmp_path/'run_manifest.json').write_text(json.dumps(manifest))
    # Cardinalities still match, but C is not an observation of selected B.
    row['mode']='C'
    folder=tmp_path/'large'/'C';folder.mkdir()
    (folder/'record.json').write_text(json.dumps(row))
    summary=aggregator().aggregate(tmp_path)
    assert summary['record_count']==summary['expected_rows']==summary['unique_rows']==2
    assert not summary['denominator_complete']


def test_valid_matched_reference_remains_a_bounded_usefulness_observation(tmp_path):
    fixture(tmp_path)
    summary=aggregator().aggregate(tmp_path)
    assert summary['same_version_valid'] and summary['same_input_valid']
    assert summary['modes']['D']['useful_reference_hits']==1
    assert summary['large_ai_primary_useful_paths']==['large']


def test_typed_top_with_a_constant_witness_is_not_an_exact_literal_hit(tmp_path):
    row,_=fixture(tmp_path)
    # A stale/sample constant alongside Top[str] still denotes every string.
    # It cannot establish exact literal precision or reference usefulness.
    row['answer']['value']['top_sorts']=['str']
    (tmp_path/'large/D/record.json').write_text(json.dumps(row))
    summary=aggregator().aggregate(tmp_path)
    assert not summary['records'][0]['quality']['exact_reference']
    assert summary['modes']['D']['useful_reference_hits']==0
    assert summary['large_ai_primary_useful_paths']==[]
