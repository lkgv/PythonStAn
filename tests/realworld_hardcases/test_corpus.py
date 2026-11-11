"""Pinned real-source corpus and independent oracle/measurement contracts."""
import copy
import json
import subprocess

import pytest

from benchmark.realworld_hardcases import runner

CASES = [(cohort, case) for cohort in runner.COHORTS for case in runner.manifest(cohort)['cases']]


@pytest.mark.parametrize('cohort,case', CASES, ids=[case['id'] for _,case in CASES])
def test_pinned_source_and_closed_runtime_oracles(cohort, case):
    old = runner.base.ROOT
    try:
        runner.base.ROOT = runner.ROOT / cohort
        source = runner.base.read_source(case)
        compile(source, '<frozen-hardcase>', 'exec')
    finally:
        runner.base.ROOT = old
    observed = runner.worker(cohort, case, 'runtime', timeout_seconds=20)
    assert runner.runtime_valid(observed), observed
    assert len(observed['effect_observations']) == len(case['effects'])
    # This is one execution's consistency check on a closed fixture. It cannot
    # prove the source-reviewed oracle exhaustive for arbitrary environments.
    edges = {tuple(x) for x in observed['edges']}
    assert set(map(tuple,case['required_edges'])) <= edges
    assert not set(map(tuple,case['forbidden_edges'])) & edges


def test_six_projects_have_real_source_and_explicit_boundaries():
    projects = {ref['project'].lower() for _,case in CASES for ref in case['provenance']}
    assert {'django','sqlalchemy','fastapi','pydantic','pluggy','click'} <= projects
    assert len({case['id'] for _,case in CASES}) == len(CASES)
    assert all(case['required_edges'] and case['forbidden_edges'] and case['effects']
               and case['boundary_models'] for _,case in CASES)


def test_failed_workers_keep_positive_and_negative_denominators(monkeypatch):
    cohort, case = CASES[0]
    monkeypatch.setattr(runner,'worker',lambda *args,**kwargs: {'status':'timeout'})
    report = runner.run_suite([cohort], [case['id']], include_runtime=False)
    assert not report['reviewed_call_gate_passed']
    for totals in report['totals'].values():
        assert totals['required_pairs_in_failed_rows'] == len(case['required_edges'])
        assert totals['negative_pairs_in_failed_rows'] == len(case['forbidden_edges'])
        assert totals['checked_negative_pairs'] == 0
        assert totals['recovered_pairs'] == totals['useful_reviewed_cases'] == 0


def test_integrity_failure_never_executes_source_and_retains_row(monkeypatch):
    cohort, original = CASES[0]
    case = copy.deepcopy(original)
    case['sha256'] = '0' * 64
    monkeypatch.setattr(runner, 'manifest', lambda _: {'cases':[case]})
    def forbidden(*args, **kwargs):
        raise AssertionError('a corrupt source must not be analyzed or executed')
    monkeypatch.setattr(runner,'worker',forbidden)
    report = runner.run_suite([cohort])
    assert report['source_integrity_failed'] == 1
    assert report['rows'][0]['current']['status'] == 'source_integrity_error'
    assert report['totals']['current']['required_pairs_in_failed_rows'] == len(case['required_edges'])


@pytest.mark.parametrize('options',[{'cohorts':[]},{'cohorts':['plugins','plugins']},
                                    {'cohorts':['plugins'],'case_ids':['absent-case']},
                                    {'timeout_seconds':0},{'max_iterations':0}])
def test_invalid_selection_cannot_be_empty_success(options):
    with pytest.raises(ValueError):
        runner.run_suite(**options)


def test_timeout_does_not_claim_analysis_never_started(monkeypatch):
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired('worker', 0.001)
    monkeypatch.setattr(runner.subprocess,'run',timeout)
    result = runner.worker(*CASES[0],'static',timeout_seconds=0.001)
    assert result['status'] == 'timeout' and result['worker_started']
    assert result['analysis_executed'] is None and not result['analysis_completed']
