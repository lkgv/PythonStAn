# N4-BOOT-02 domain and Native registration review

Reviewer: requirements_audit; implementation author: domain_native_baseline.
Baseline: `e8d9e0d3d7cdf540622537f5af0490522edde91c` / tree
`13a79f550daf75445f9529e5d5a89d70adabb1c6`.

Disposition: **accepted as W0 regression registration**, not semantic repair acceptance.

The manifest freezes five R3-DOM and three NCOMP finding families, exact fixture
hashes and an explicit 36-node denominator. Public API evidence, component graph
evidence, current red results and successful controls are distinguished. In particular
the seeded cyclic Store witness is not presented as a source-generated graph.

I read the test bodies, independent finite wire/string membership logic, CPython
fixture invocation, registration runner and downgrade guards. The predicates exercise
the reported behaviors: mixed-sort normal/exception coverage, finite-zero truthiness,
pattern-plus-residual export, generator identity refresh, root aliases across cyclic
transform/join, completed target prefixes, live old dictionary aliases, and effective
region context/relation/trace consumers. Positive controls preserve useful precision
and reject a global Top replacement on the public path. Expected results are not
derived from old analyzer output. Using a finite membership test or CPython witness
does not establish unrestricted soundness; the manifest makes this limit explicit.

Independent verification:

```text
../quality-env/bin/python tests/regression/audit3/domain_native_acceptance.py --verify-freeze-only
exit 0; freeze_integrity=true; execution_qualified=false

../quality-env/bin/python -m pytest --import-mode=importlib -p no:cacheprovider -q tests/regression/audit3/test_domain_registration_guard.py
exit 0; 4 passed
```

The normal runner propagates pytest failure, requires the frozen test count and rejects
skips. The recorded baseline 20 red / 16 green is an honest failing semantic baseline;
no repair card or G2 can close from the registration check. The raw original probes
remain observational evidence with their original status interpretation.

Remaining obligations are correctly outside this review: all eight production fixes,
the larger epoch/failure-injection/typed metadata obligations, all mandatory old-profile
positive controls, and the other W0 semantic/quality/client-query registrations.

The 29 new-test `no-untyped-call` diagnostics are explicitly retained with ownership
in N4-TYPE-01. They are not a strict-clean result and must remain visible in the
overall quality account. The clean runner/guard typing result does not imply clean
typing for the SUT-calling tests or production analyzer.
