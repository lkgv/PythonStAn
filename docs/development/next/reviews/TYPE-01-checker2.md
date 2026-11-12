# Independent source/certificate checker review

Reviewer: requirements_audit. Accepted narrowly for the real closed-effects checker
and guarded-dispatch symbolic semantics typing/codec refactor. TYPE-01 remains open.

The explicit Flow binding fields preserve exit joins, handler-target clearing and
finally restoration. AST guards only restate facts established by the original
source validator, and the NoReturn annotation matches the actual exception path.
The source alphabet, postfixpoint obligations, step charging, semantic/schema
versions and complete function denominator remain unchanged. The finite raw-Mapping
codec performs actual validation; it does not treat type annotations as proof.

Independent execution passed 163 checker tests. Ordinary imports plus strict and
disallow_any_expr report zero diagnostics in both modules; 460 imported records
remain visible. Ruff is clean. Exact 44d13b1/current comparison covers 150 identical
source programs, 7304 candidate mutations, 1050 budget cases, 150 stale-source cases,
five complete schemas, 45 symbolic interpretations and 729 cover relations. Every
public result/certificate field, diagnostic, actual obligation-work count and source
function denominator is retained with no normalization or excluded fields; this
finite corpus has zero differences and source hashes stayed fixed.

UserDict, nested MappingProxyType and tuple-array compatibility also match. An
additional invalid non-JSON top-level integer key remains rejected with work 0, no
coverage and no certificate; its diagnostic now says certificate must be an object
rather than listing the exact expected keys. This bounded diagnostic change is
recorded verbatim, not hidden by the equality claim for the supported corpus.

The broad certified directory additionally contains a benchmark test that cannot
collect because benchmark.certified is absent. That separate collection failure is
retained; the 163-test result above names its precise three-file selection. No
credentialed LLM or client-project query was run.

The full machine-readable disposition and source hashes are in
docs/development/next/checker2_independent_review.json; raw evidence and the
reproducible differential are under artifacts/next/evidence/reviewer-W1/checker2/.
