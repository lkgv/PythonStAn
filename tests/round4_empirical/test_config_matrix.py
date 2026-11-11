import pytest

from benchmark.realworld_hardcases.config_matrix import run, summarize_trials
from benchmark.realworld_hardcases.runner import base


CASE = dict(required_edges=[['a', 'b'], ['a', 'c']], forbidden_edges=[['a', 'd']])


def measured(edges, status='completed'):
    return dict(status=status, call_edges=edges, call_obligations=base.compare_edges(CASE, edges),
                wall_seconds=1.0, python_peak_bytes=100)


def test_repeated_equal_counts_do_not_imply_same_recovered_obligations():
    result = summarize_trials(CASE, [measured([['a', 'b']]), measured([['a', 'c']])])
    assert result['recovered_pairs_min'] == 1
    assert result['required_recovered_in_every_completed_trial'] == []
    assert not result['repeat_call_edges_stable']


def test_incomplete_trial_cannot_be_dropped_to_claim_stable_success():
    result = summarize_trials(CASE, [measured(CASE['required_edges']), measured([], 'incomplete')])
    assert not result['all_reviewed_calls_met'] and not result['repeat_call_edges_stable']
    assert result['required_pairs_in_unmeasured_trials'] == 2
    assert result['negative_pairs_in_unmeasured_trials'] == 1


@pytest.mark.parametrize('options', [dict(repeats=0), dict(repeats=True), dict(timeout_seconds=float('nan')),
                                    dict(timeout_seconds=float('inf')), dict(max_iterations=0),
                                    dict(case_ids=[]), dict(case_ids=['absent'])])
def test_invalid_experiments_fail_before_running(options):
    with pytest.raises(ValueError):
        run(**options)
