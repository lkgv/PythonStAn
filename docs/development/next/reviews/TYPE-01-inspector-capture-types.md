# Independent Inspector capture boundary review

Reviewer: integration_review. Accepted narrowly for the passive capture DTOs,
owner enumeration extraction, nominal native alias lookup, explicit runner
selection and scalar trace decoding. The Inspector's existing failing tests and
broader algorithm/coverage obligations remain open.

Source inspection confirms that `_fact_owners` retains the old two enumeration
paths and ordering: existing matching environment owners, followed by existing
PFG nodes or heap-register owners. It does not allocate missing native nodes or
facts. The new capture records contain local references and bounded scalar
descriptions; their module and the trace/storage/offline reader remain free of
runtime analyzer imports. An independent fresh-process check forbids analyzer
imports while importing all four modules and decoding a scalar trace event.

Collector alias lookup now names the actual FunctionObject/ClassObject keys
produced by the native heap. The capture runner verifies the configured actual
PointerAnalysis before accessing its live solver. These are bounded nominal
native capabilities, not proof that arbitrary replacements of private storage
are supported. Runtime metadata traversal still accepts only owned primitive
trees. Sealing produces immutable encoded bytes rather than retaining live
objects.

The trace changes retain the existing primitive validation, ring-byte accounting,
loss sequencing, deferred sink calls and observation-only failure containment.
The new JSON decode validates the internally encoded object at one boundary;
it neither executes source nor adds solver facts.

The independent focused selection reports 96 passed and seven failed. The exact
same selection in a clean detached `31d0811` checkout also reports 96 passed and
the same seven failures; Inspector sources are unchanged between that commit and
the author's `0f19ceb` baseline. The failures concern inherited work-hook
interception expectations and the absent `benchmark.inspector_v1` fixture.
Raw current and baseline logs are retained under
`artifacts/next/evidence/resume-integration-review/`. No expectations were changed
or failures counted green. The author's wider 273-test selection remains 261
passed and 12 failed; this review does not independently reclassify its five
additional failures.

The final frozen original stage and audit3 control runs separately pass 1001 and 4114
tests with identical before/after source, test, script and benchmark hashes.
These checks do not imply that the full historical Inspector suite is green,
that all production mypy debt is clear, or that TYPE-01 is verified. Both final
receipts refer to the same 795-file Python snapshot and reside in
`artifacts/next/evidence/resume-current-final-20261112/`.
