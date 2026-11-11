"""Independent regression contracts for failed and partial static measurements."""
import copy

import pytest

from benchmark.realworld_hardcases import runner


def result(case, status, edges):
    return dict(status=status, analysis_executed=True, call_edges=edges,
                call_obligations=runner.base.compare_edges(case, edges),
                useful_reviewed_call_result=True)


def test_partial_graph_cannot_close_case_or_exclude_negative(monkeypatch):
    cohort = runner.COHORTS[0]
    case = runner.manifest(cohort)['cases'][0]
    partial = result(case, 'incomplete', case['required_edges'])
    monkeypatch.setattr(runner, 'worker', lambda *args, **kwargs: copy.deepcopy(partial))
    report = runner.run_suite([cohort], [case['id']], include_runtime=False)
    assert not report['reviewed_call_gate_passed']
    for totals in report['totals'].values():
        assert totals['recovered_pairs'] == totals['missing_pairs_in_completed_rows'] == 0
        assert totals['checked_negative_pairs'] == totals['useful_reviewed_cases'] == 0
        assert totals['required_pairs_in_failed_rows'] == len(case['required_edges'])
        assert totals['negative_pairs_in_failed_rows'] == len(case['forbidden_edges'])
        assert totals['partial_recovered_pairs'] == len(case['required_edges'])
        assert totals['partial_graph_rows'] == 1
    assert report['rows'][0]['current']['call_edges'] == case['required_edges']


def test_completed_and_incomplete_denominators_are_disjoint():
    case = dict(required_edges=[['a', 'b'], ['a', 'c']],
                forbidden_edges=[['a', 'd']], effects=[])
    rows = [dict(case=case, current=result(case, 'completed', [['a', 'b']])),
            dict(case=case, current=result(case, 'incomplete', [['a', 'b'], ['a', 'd']])),
            dict(case=case, current={'status': 'timeout'})]
    totals = runner.summarize_static_results(rows, 'current')
    assert (totals['recovered_pairs'], totals['missing_pairs_in_completed_rows'],
            totals['required_pairs_in_failed_rows']) == (1, 1, 4)
    assert totals['required_pairs'] == 6
    assert (totals['checked_negative_pairs'], totals['negative_pairs_in_failed_rows']) == (1, 2)
    assert totals['violated_negatives'] == 0
    assert totals['partial_violated_negatives'] == 1
    assert totals['useful_reviewed_cases'] == 0


@pytest.mark.parametrize('edges', [[['a', 'b']], [['a', 'b'], ['a', 'c'], ['a', 'd']]])
def test_completed_success_flag_cannot_override_missing_or_forbidden_pairs(edges):
    case = dict(required_edges=[['a', 'b'], ['a', 'c']],
                forbidden_edges=[['a', 'd']], effects=[])
    r = result(case, 'completed', edges)
    assert not runner.reviewed_call_result_met(r)


@pytest.mark.parametrize('status', ['incomplete', 'timeout', 'error', 'worker_error'])
def test_a_success_flag_does_not_override_failed_status(status):
    case = dict(required_edges=[['a', 'b']], forbidden_edges=[['a', 'c']], effects=[])
    r = result(case, status, [['a', 'b']])
    assert not runner.completed_static_result(r)
    totals = runner.summarize_static_results([dict(case=case, current=r)], 'current')
    assert totals['useful_reviewed_cases'] == 0
    assert totals['required_pairs_in_failed_rows'] == 1
