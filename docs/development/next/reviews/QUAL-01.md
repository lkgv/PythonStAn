# N4-QUAL-01 final integrator review

Reviewer: requirements_audit; implementation author: quality_tools.
The separate quality_negatives review found and verified repairs for six bypasses;
this record independently reviews the integrated Q0 scope and final receipts.
Source baseline: `e8d9e0d3d7cdf540622537f5af0490522edde91c`, tree
`13a79f550daf75445f9529e5d5a89d70adabb1c6`.

Disposition: **accepted for initial toolchain, precise debt and guard adoption**.
This does not qualify runtime TYPE-01/TYPE-02 contracts or the final strict core.

I read the actual collection/orchestration, diagnostic identities, changed-unit
checks, policy comparison, source-suppression checks, manual initial capture,
negative controls, regression tests, pyproject configuration and CI workflow.
The check command does not regenerate the baseline. Tool errors and missing import
contract execution fail the gate; production Ruff/mypy exit 1 is retained as debt,
not silently reported as whole-tree success. CI compares against the review base
and does not reset captured debt or permit frontier retreat.

Independent data checks confirmed:

- The baseline contains exactly all 305 production Python files, with every recorded
  source SHA256 matching the actual unchanged production source. No production
  diff exists at this review.
- Diagnostic counts are formatter 270, Ruff 12,477 and mypy 10,349. Records retain
  logical module/path, symbol, code, syntax anchor, message, owner and removal task.
  Navigation line numbers are not used as diagnostic identity.
- The version-one frontier has 13 clean package-marker modules and matches its
  frozen floor. It does not claim strict API, state, driver or semantic services.
- Full scan commands cover the complete inventory. Underlying formatter/lint/mypy
  exits are 1/1/1; Import Linter checks two contracts with exit 0. Guard implementation
  formatter/lint/type commands all exit 0.
- Strict-source overrides and import scopes are checked in addition to toy controls.
  The six adversarial policy regressions cover debt metadata/count replacement,
  production-specific typing relaxation, invalid comparison refs, additive Ruff
  ignores, duplicate qualname concealment and replacing protected import scopes.
- The requested same-arity unchecked-summary negative is fixed: passing a Candidate
  in the checker-seal position now fails with `[arg-type]`, rather than merely
  testing a missing constructor argument. The valid checker remains a positive.

Independent test execution:

```text
../quality-env/bin/python -m pytest --import-mode=importlib -p no:cacheprovider -q tests/regression/audit3/test_quality_debt.py tests/regression/audit3/test_quality_policy.py tests/regression/audit3/test_quality_negative_guards.py
exit 0; 12 passed in 5.93s
```

The final actual-tool report at
`artifacts/next/evidence/N4-QUAL-01/final-reviewed-gate/report.json` records pass,
zero added diagnostics, zero modified-unit diagnostics, 14 successful expected
tool-control decisions, and `whole_tree_mypy_clean=false`. This report follows the
same-arity control repair. The actual lock/tool versions are preserved; production
runtime PyYAML remains unchanged.

Remaining scope is explicit: all real runtime type adoption, final core-frontier
expansion, Native/Inspector per-module debt-removal subtasks, live runtime negative
tests, and actual release-wheel type/publication qualification. The separately
reported 29 strict call diagnostics in new SUT-calling tests are not part of the
305-production-file denominator and are not mislabeled clean. Existing production
debt remains visible and cannot be used as a permanent legacy exemption.
