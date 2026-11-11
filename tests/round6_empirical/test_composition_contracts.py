"""Independent corpus/oracle checks, not an analyzer soundness theorem."""
from copy import deepcopy
import hashlib
import json

import pytest

from benchmark.empirical import runner as base
from benchmark.round6_realworld import runner
from benchmark.round6_realworld.build_cases import cases as constructed_cases


CASES=runner.manifest()['cases']


@pytest.mark.parametrize('case',CASES,ids=lambda c:c['id'])
def test_exact_pinned_source_methods_and_licenses(case,monkeypatch):
    monkeypatch.setattr(base,'ROOT',runner.ROOT)
    source=base.read_source(case)
    assert hashlib.sha256(source.encode()).hexdigest()==case['sha256']
    assert case['derivation_review']['source_methods_unchanged'] is True


def test_reconstruction_matches_frozen_source_bytes():
    for old,identifier,source,*_ in constructed_cases():
        case=next(c for c in CASES if c['id']==identifier)
        assert (runner.ROOT/case['source']).read_text()==source
        assert old['sha256']==case['derivation_review']['base_source_sha256']


@pytest.mark.parametrize('case',CASES,ids=lambda c:c['id'])
def test_handwritten_effects_match_controlled_execution(case):
    result=runner.execute_worker({'case':case,'kind':'runtime'})
    assert result['exception_matches']
    assert len(result['effect_observations'])==len(case['effects'])
    assert all(e['matches'] for e in result['effect_observations'])
    assert result['soundness_evidence'] is False


def observe(source):
    namespace={}
    exec(compile(source,'<mutation-oracle>','exec'),namespace)
    return namespace


@pytest.mark.parametrize('identifier,old,new',[
    ('django_registry_mutation_two_dispatches','return list(self.receivers), []','return self.receivers, []'),
    ('click_context_rebinding_exception_cleanup','return current','return captured'),
    ('sqlalchemy_sibling_copies_late_source_write','return type(self)(**defaults)','self.__dict__.update(defaults)\n        return self'),
    ('pluggy_registration_change_three_dispatches','hooks = [HookImpl(skipped), HookImpl(selected), HookImpl(recover, True)]','hooks = [HookImpl(skipped), HookImpl(selected), HookImpl(recover, True), HookImpl(outer, True)]'),
])
def test_gold_rejects_semantic_boundary_mutants(identifier,old,new):
    case=next(c for c in CASES if c['id']==identifier)
    source=(runner.ROOT/case['source']).read_text()
    assert old in source
    mutant=source.replace(old,new)
    if identifier.startswith('click'):
        mutant=mutant.replace('pass_repo = make_pass_decorator','captured = current\npass_repo = make_pass_decorator')
    if identifier.startswith('sqlalchemy'):
        with pytest.raises(AttributeError, match="has no attribute 'value'"):
            observe(mutant)
        return
    result=observe(mutant)
    assert any(not runner.exact_equal(result[e['runtime_global']],e['expected']) for e in case['effects'])


def test_typed_effect_comparison_does_not_confuse_boolean_and_integer():
    assert not runner.exact_equal([True], [1])
    assert not runner.exact_equal({'nested':[1]}, {'nested':[1.0]})
    assert runner.exact_equal({'nested':[True,None]}, {'nested':[True,None]})


def test_source_tamper_rejected_before_analysis(monkeypatch):
    monkeypatch.setattr(base,'ROOT',runner.ROOT)
    case=deepcopy(CASES[0]);case['sha256']='0'*64
    with pytest.raises(ValueError,match='hash mismatch'):
        base.read_source(case)


@pytest.mark.parametrize('budget',[0,-1,float('nan'),float('inf')])
def test_invalid_wall_budgets_rejected(budget):
    with pytest.raises(ValueError,match='Budgets'):
        runner.run_suite(timeout_seconds=budget)


def test_failed_workers_keep_all_obligations(monkeypatch):
    monkeypatch.setattr(runner,'worker',lambda *a,**k:dict(status='timeout'))
    report=runner.run_suite(include_runtime=False)
    expected_positive=sum(len(c['required_edges']) for c in CASES)
    expected_negative=sum(len(c['forbidden_edges']) for c in CASES)
    expected_effects=sum(len(c['effects']) for c in CASES)
    for mode in ('baseline','current'):
        total=report['totals'][mode]
        assert total['required_pairs']==expected_positive
        assert total['required_pairs_in_failed_rows']==expected_positive
        assert total['negative_pairs_in_failed_rows']==expected_negative
        assert total['checked_negative_pairs']==0
        assert total['recovered_pairs']==0
    assert report['conditional']['residual']==expected_effects
    assert report['conditional']['conditional_verified']==0
    assert report['reviewed_call_gate_passed'] is False
    assert report['whole_packages_statically_analyzed']==0


def test_native_package_version_mismatch_is_explicit(monkeypatch):
    import pluggy
    from benchmark.round6_realworld.native_pluggy_oracle import run
    monkeypatch.setattr(pluggy,'__version__','unexpected')
    result=run()
    assert result['status']=='version_mismatch'
    assert result['static_evidence'] is False
