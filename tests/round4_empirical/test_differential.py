import copy

import pytest

from benchmark.realworld_hardcases.differential import compare_reports
from benchmark.realworld_hardcases.runner import base


def report(status='completed', edges=None):
    case = dict(id='fixed-case', sha256='source', required_edges=[['a', 'b'], ['a', 'c']],
                forbidden_edges=[['a', 'd']], boundary_models=['closed environment'], effects=[])
    edges = [['a', 'b']] if edges is None else edges
    result = dict(status=status, call_edges=edges, call_obligations=base.compare_edges(case, edges))
    return dict(implementation_stable=True, implementation_sha256_after='same-core',
                manifests=[dict(cohort='test', sha256='frozen')],
                rows=[dict(cohort='test', case=case, baseline=copy.deepcopy(result), current=copy.deepcopy(result))])


def test_pair_identities_reveal_regression_despite_equal_total():
    r = compare_reports(report(), report(edges=[['a', 'c'], ['a', 'd']]))
    assert not r['no_reviewed_regressions']
    assert r['totals']['newly_recovered_required'] == r['totals']['lost_required'] == 2
    assert r['totals']['newly_violated_negatives'] == 2


def test_partial_before_is_not_empty_graph_or_precision_baseline():
    r = compare_reports(report(status='incomplete', edges=[]), report(edges=[['a', 'b'], ['a', 'c']]))
    assert r['totals']['unmeasured_comparisons'] == 2
    assert r['totals']['newly_recovered_required'] == 0
    assert not r['no_reviewed_regressions']
    assert all(row['after_recovered'] == 2 for row in r['rows'])


@pytest.mark.parametrize('mutation', ['case', 'manifest', 'population', 'implementation', 'config'])
def test_revision_comparison_rejects_changed_population_or_contract(mutation):
    before, after = report(), report()
    if mutation == 'case':
        after['rows'][0]['case']['boundary_models'] = ['different assumption']
    elif mutation == 'manifest':
        after['manifests'][0]['sha256'] = 'new-gold'
    elif mutation == 'population':
        after['rows'] = []
    elif mutation == 'implementation':
        after['implementation_stable'] = False
    elif mutation == 'config':
        before['rows'][0]['baseline']['config'] = {'context_policy': '0-cfa'}
        after['rows'][0]['baseline']['config'] = {'context_policy': '2-cfa'}
    with pytest.raises(ValueError):
        compare_reports(before, after)
