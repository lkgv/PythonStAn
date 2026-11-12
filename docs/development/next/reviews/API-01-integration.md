# N4-API-01 integration review

Reviewer: requirements_audit; API owner: client_preregistration, with separately
reviewed configuration and source-input dependencies.

Current disposition: **accepted for N4-API-01 on the reviewed source checkpoint**.
This does not qualify N4-TYPE-01 or the complete imported runtime typing frontier.

The actual execution path now follows one canonical representation:

- `analyze` calls `resolve_request`; Session only accepts a ResolvedRequest and its
  constructor performs no source preparation or solving.
- Source preparation is explicit and session-owned. Configuration, canonical query
  objects and OutputSpec are reused; raw input mappings do not reenter Session.
- Schema and public import paths reexport the same query/output/result classes;
  the earlier duplicate ValueQuery class-identity regression is resolved.
- L1 reruns consume SourceSnapshot.for_rerun through the same resolved request
  boundary, preserving frozen source bytes and fresh AST ownership.
- Published results are detached and immutable. Serialization and query projection
  consume retained facts without rerunning the solver or renormalizing run queries.

Independent verification:

```text
API / CLI / L1 / new request / configuration / input suite: 165 passed in 6.09s
Selected runtime effect / old state / composition suites: 75 passed in 1.23s
```

The source/file/project matrix, default discovery, output Mapping/object equality,
strict field errors, session lifetime, L1 rerun and output projection behaviors are
covered. A later actual events-serialization regression exposed optional source-point
None values at the typed projection boundary; the owner fixed it and added a read-only
serialization control. The reviewer independently ran that control and the persistent
actual-session typing guard: **2 passed in 3.18s**.

The actual static request boundary was additionally tested independently with normal
imports, not just the initial toy quality fixture:

- AnalysisSession(resolve_request(Source(...))) has zero consumer diagnostics.
- AnalysisSession(raw output Mapping) produces exactly one `[arg-type]` diagnostic
  requiring ResolvedRequest.
- Both underlying mypy runs still exit 1 for imported unfinished-module debt. The
  full outputs, file hashes and filtered consumer diagnostics are retained under
  `artifacts/next/evidence/reviewer-W1/actual-session-types`.

The final production peer-call typing condition is satisfied through actual typed
ports, with normal imports and without suppression, import skipping or a generic
object/getattr facade. Runtime
Flow/metadata/continuation completion, QueryLedger/L2 fact repair, full cost ledger
and installed-wheel qualification remain separate task obligations.

The frozen rerun boundary was additionally reviewed after the owner identified a
scope-consistency gap. A retained input-resolution contract now binds target kind,
paths and inventory selectors without reading source bytes. The boundary separately
checks the exact memory source, explicit file module and requested entry sequence
against the frozen source/scenarios. It rejects changed source, entries, roots or
exclusions before solving, while allowing an explicitly changed abstract global
binding. The independent focused controls passed: **6 passed, 20 deselected in
0.16s**, including execution from retained file bytes after the file was deleted.
This acceptance is limited to the rerun consistency guard; the strict production
peer-port condition above still applies.

The root's narrow actual proposal-factory dependency is accepted independently.
The reviewer inspected `llm/providers.py`, `llm/candidates.py`, the two actually
consumed language encoder functions and SourceBundle integration. SourceBundle
exposes only read-only source records. Disabled L2 returns before bridge import;
the factory adds no SDK, network or credential reads. Untrusted proposal records
still cannot construct a trusted certificate. Invalid table syntax outside the
guarded-dispatch profile can now return unavailable at proposal construction
instead of reaching a later malformed-candidate rejection; ordinary analysis
fallback remains the same.

The final provider receipt's source hashes match the reviewed files. It reports
zero owned proposer errors and zero errors in the two encoder functions, while
retaining the underlying mypy exit 1 and 1,763 imported/parser diagnostic records.
The reviewer also independently repeated all 18 finite old/new proposal cases
against git 7a2f0f5: normal/coarse/five adversarial mutations for normal and empty
string keys, three work budgets and opaque recorded replay. All candidate,
status, provider, work and diagnostic fields match except measured seconds.
Full old/new records and exact source hashes are retained under
`artifacts/next/evidence/reviewer-W1/provider-differential/receipt.json`.
This is proposal representation parity, not checker or whole-program soundness.

The reviewer also identified and required correction of the initial phase timer:
source_preparation_seconds had received overall elapsed since request resolution,
including configuration construction and idle after explicit prepare(). Resolution,
preparation, frozen-snapshot validation and engine-pipeline durations are now measured
at their real boundaries. The overall request clock still includes idle for deadline
accounting. Manual ResolvedRequest construction publishes unavailable resolution
timing with an explicit reason rather than inventing a zero. Engine timing states its
inclusive pipeline scope; this is not the later complete cost ledger. The reviewer
independently ran the deterministic step-clock control through the actual engine:
**1 passed in 0.11s**. It retains resolution = 1 and preparation = 1 after a 1,000-unit
explicit idle interval while global wall time retains that interval.

The final input probe exposed an additional frozen-rerun case: removing an explicit
File.module override used to retain the prior frozen module even though fresh
discovery selected another module. The reviewer independently reproduced that case.
The retained selector contract now binds the normalized requested module override,
including its absence, and validates it before reuse. The reviewer independently ran
None/empty removal negatives plus retained and changed override controls:
**4 passed in 0.19s**, with stable source hashes in
`artifacts/next/evidence/reviewer-W1/file-module-reuse/receipt.json`.

The actual disposable Native runner dependency is accepted narrowly. The reviewer
inspected source/scenario serialization, latest-valid NDJSON frame selection,
timeout prefix retention, conservative unobserved-work reservation and process/error
failure behavior. All eight SourceModule fields and scenario identities are retained;
no retry or closed result is invented on protocol failure. Nonfinite or malformed
JSON frames now fail validation at the wire boundary. Independent focused contracts
plus the actual Native Source/File/Project public case passed: **8 passed, 1 deselected
in 1.72s**. The final runner/test receipt hashes match the reviewed source and retain
zero owned strict errors, the underlying mypy exit 1 and 1,783 imported diagnostics
under `artifacts/next/evidence/N4-API-01/legacy_runner_typing/receipt.json`. Its prior
four test reexport errors remain archived; only the monkeypatch target was changed
to the same imported standard-library subprocess module, with no production change
or suppression. The complete Native engine remains outside this dependency review.

Final independent disposition:

- The full API/CLI/L1/observer/config/input matrix passed **186 tests in 10.04s**.
  All 312 production/audit3 source-file hashes remained stable during the run.
- The final strict normal-import scan of the actual 36-file API/dependency/test
  frontier reports **zero owned diagnostics**. It retains the underlying mypy
  exit 1 and 1,783 imported diagnostic records rather than declaring whole-tree
  cleanliness.
- That first final scan exposed one test private-import error in the step-clock
  monkeypatch target. A test-only standard-library time import fixes it using the
  exact same module object. The corrected strict scan has zero owned diagnostics,
  and the real-engine clock control passes again: **1 passed in 0.13s**. Every
  production hash is identical to the independent 186-test matrix; only that test
  file changed. Both failed and corrected raw receipts are retained under
  `artifacts/next/evidence/reviewer-W1/api-final` and `api-final-corrected`.
- A second read-only cross-check by requirements_audit/api_final_crosscheck found
  no additional material API-01 requirement gap in request/session, source reuse,
  query/output ownership, discovery or CLI paths.

The exact final 36 source hashes in the corrected independent receipt match the
current files. This accepts API-01's canonical resolved request, three input paths,
strict query/output boundary, isolated publication and measured stage separation.
The live Flow/state frontier, full semantic closure and client prepared-state
execution, complete cost ledger, installed-wheel release and real LLM qualification
remain open in their own tasks.
