# N4-BOOT-02 semantic and offline L1/L2 registration review

Reviewer: requirements_audit; author: semantic_baseline.
Baseline commit: `e8d9e0d3d7cdf540622537f5af0490522edde91c`.
Frozen source/input/gold manifest SHA256:
`16771ac12bb1267a032fb004c98b47680ee938fcf95a5f639eac5549f159e66b`.

Disposition: **accepted as W0 independent semantic registration**. The deliberately
failing product acceptance remains red. No semantic repair or LLM capability is
qualified by this review.

The manifest preserves 52 source cases and six additional offline L1/L2 cases.
Source expectations are independently stated, concretely checked with CPython 3.12,
and distinguished from analyzer behavior. The original A01-A18 ledger, mandatory
gold and held-out fixture files retain their identities and explicit references.
Positive controls, bounded component expectations and Open-profile observations
are not counted as independent defect roots or proofs of arbitrary Python soundness.

I inspected the runner, manifest, concrete/analysis separation, exact scalar and
wire predicates, L1/L2 query predicates and downgrade tests. The following initial
review gaps were repaired without changing the 52+6 source/input/gold denominator:

- Function-exit checking now requires the exact declared symbol set instead of
  accepting an empty mapping through a vacuous `all` predicate.
- L2 function/bulk results must retain the scalar identity result, retained box
  contents, relevant object identities and nonempty normal/return outcome scopes.
- L2 callsite checks also require useful non-Top module results; call-fact coverage
  does not silently satisfy the manifest's value requirement.
- L1 improvement validates the actual read-to-identity and write-to-box relation,
  useful scalar/box results and structured proposal statuses. Two wrong pairs no
  longer satisfy the target merely by having the expected count.
- An Open-status-only response is insufficient: the registered bytes boundary now
  requires a propagated residual value/availability state, explicit obligations,
  normal/raise alternatives and havoc/callback/exception effect evidence.

Independent verification:

```text
../quality-env/bin/python -m pytest --import-mode=importlib -p no:cacheprovider -q tests/regression/audit3/test_semantic_baseline.py
exit 0; 69 passed in 1.54s
```

This includes all 52 concrete expectations and focused missing-query, Top,
wrong-pair, empty-function-scope and empty-residual negatives. The author separately
retains current product output with 19 green and 39 red across 58 rows; these reds
remain actual failures, not skips or xfails. AS-07 is a convergence/usefulness
obligation, AS-08 and LV-03 are precision/usefulness defects, and LV-01 does not
invalidate the independent return-relation property certificate.

The full BOOT-02 task also depends on the domain/Native registration reviewed in
`BOOT-02-domain-native.md` and the accepted BOOT-01 source identity. This review
does not qualify the later G2 repairs, G5 TCB audit, runtime strict frontier,
real provider transport or paid live calls.
