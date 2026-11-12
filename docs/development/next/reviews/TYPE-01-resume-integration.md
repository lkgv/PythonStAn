# Independent resumed integration review

Reviewer: integration_review. This review accepts only the bounded host iteration
codec and corrected nominal function/class registration route. It does not close
TYPE-01 or qualify the whole Native, semantic, scale or release frontier.

The host codec captures the fixed Python `iter` primitive once and directly
returns its result. Its sole cast erases the input protocol at an external
stdlib compatibility boundary. Python performs the actual slot validation;
algorithm implementations and user callbacks receive no enlarged capability.
This preserves the legacy integer `__getitem__` fallback that an `Iterable` ABC
guard would omit. A generator wrapper would change eager errors, iterator
identity and the terminal `StopIteration.value`, so it is unsuitable here.

Eight independent controls pass: eager errors; actual iterator identity and
terminal payload; slotted legacy sequences and lazy index reads; dynamic
attributes versus special-method slots; explicitly disabled iteration; invalid
iterator returns; generator terminal payload; and later replacement of the
mutable builtin name. The complete helper and new tests have zero own ordinary
mypy diagnostics, and the helper also has zero own Any-expression diagnostics.
Imported command failures and diagnostics remain visible. Ruff passes.

Review also found function lowering invoking `ScopeManager.add_class` with an
`IRFunc`. A registration-contract witness failed on the original route and
passed after the owner selected `add_func`. The two manager bodies currently
perform the same runtime bookkeeping; this is a real nominal service violation,
not evidence of a previously lost program fact. The witness covers nested
functions/classes, ordinary/async methods, lexical parents, source-module
propagation and retained statement IR. Its ordinary mypy diagnostics are zero
after correcting the transformer's wildcard-shadowed `List` annotation.

The combined nine controls pass with all seven relevant source/test hashes
stable, as recorded in
`artifacts/next/evidence/resume-integration-review/bounded-controls.json`.
The original routing red remains in `registration-red.txt` in that directory.

Broader mutable-source integration probes passed 105 lifecycle/Flow/Native
controls, 61 benchmark/ownership controls and seven fresh import entry points.
These are probes, not a frozen whole-tree acceptance. The historical Native
selection reports 679 passed and four failed; a clean detached `31d0811` replay
reproduces all four failures. They concern the old dynamic-getattr intrinsic
expectation, the context-exit count and a manually seeded call-graph query, and
remain inherited obligations. No test expectation was relaxed. The quality debt
ledger, frontier and tool configuration were unchanged during this review.

The reviewer subsequently became the implementation owner of the broader IR
cleanup-frame migration. That later implementation is outside this bounded
independent acceptance and requires the root agent's separate review.
