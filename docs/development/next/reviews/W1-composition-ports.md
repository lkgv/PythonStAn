# W1 composition production-port review

Reviewer: requirements_audit. Author: quality_tools, with late provider/driver
integration owned by the root and state owner.

Current disposition: **accepted as the listed narrow production ports**.
This is not acceptance of the complete API-01 or TYPE-01 cards.

The typed region-plan records preserve source statement identities, selector
precedence, descendant propagation, recursive-SCC checks and the published plan
shape. The independent diff review found no change in the inclusion kernel's
delta queue, late-edge replay, address-key distinction, preparation charging or
partial candidate/no-exclusion rule. CandidateAnswer is one canonical generic
record, with no concrete Value or Store import in its contract module. Actual
executors now consume RegionDecision attributes; its legacy Mapping support
preserves key access and structural equality after disabling dataclass equality.

Two integration matters were reported and corrected:

- NativeSnapshotOracle.statistics thaws the whole retained Native snapshot for
  two metadata fields on every call. This changes a repeatable metadata read
  into a copy proportional to the whole result. Both binding and statistics now
  read the frozen projection directly; only exact exported metadata leaves are
  thawed. The full values snapshot is no longer copied by statistics.
- A fresh independent 46-test region/oracle/epoch/migration run returned
  **3 failed, 43 passed**. The late GuardedDispatchProvider snapshot boundary
  accepts SourceSnapshot or Mapping but now sends the existing modules-provider
  fixture to `.values()`, raising AttributeError before the L2 invalidation
  checks run. A narrow read-only SourceBundle.modules port now preserves that
  actual source inventory boundary. The same three tests pass with identical
  test-file hash; red/green source identities and raw outputs are retained in
  `artifacts/next/evidence/reviewer-W1/late-ports/guarded_snapshot_{red,green}.*`.

After both corrections, the reviewer independently reran the full relevant
region/oracle/epoch/migration selection plus the three new immutable-policy,
candidate-only and repeated-statistics controls: **49 passed in 0.88s**. A final
normal-import strict scan of the six production port modules, five configuration
dependencies and new test module has **zero owned diagnostics**, with stable source
hashes. The underlying mypy exit remains 1 for 1,783 imported diagnostics. Raw
commands, outputs and source identities are retained under
`artifacts/next/evidence/reviewer-W1/composition-config-final-types` and
`artifacts/next/evidence/reviewer-W1/composition-final`; the machine-readable review
is `docs/development/next/composition_ports_review.json`.

This does not qualify the whole parent runtime graph or assert that the declared
IncrementalOraclePort has replaced every optional lifecycle hook. Complete runtime
Flow/state/metadata ownership, frozen semantic repairs, full cost accounting and
release qualification remain separate obligations.
