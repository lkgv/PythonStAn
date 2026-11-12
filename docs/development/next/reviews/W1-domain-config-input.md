# W1 domain, configuration and source-input slice review

Reviewer: requirements_audit. Authors: semantic_baseline (domains), quality_tools
(configuration), input_typing (source inputs). Reviewed working-tree source identities
are recorded in `artifacts/next/evidence/reviewer-W1/dependency-types.receipt.json`;
the current git commit alone does not contain these uncommitted implementations.

Disposition: **accepted as the listed W1 dependency slices**. This is not completion
of N4-TYPE-01, N4-API-01, the final frontier, semantic repairs or release qualification.

I inspected the actual ten canonical domain modules and their generic registry
boundary, five configuration modules including the independently required catalog,
three input modules and two frontend source/private-name modules. The changes use
real consumers: nominal Value references, typed object/cell alias ports, validated
heterogeneous provider element classes, resolved configuration records, frozen source
inventory records and parser-owned AST metadata. They do not install a second solver.

Independent verification:

- Full normal-import mypy scanning of these 20 files reports **zero owned errors**.
  It still exits 1 for 1,875 errors in imported unfinished modules; the full stdout,
  stderr, owned filtering, command and file hashes are retained. No `follow_imports`
  skip or suppression was used.
- Ruff lint and formatter checks pass for all 20 files.
- Runtime-effect, new input-contract, effective-config and domain-typing tests:
  **29 passed**. This is a combined selected test command, not the sum of all owner
  test counts.
- Actual default configuration, public option catalog and generated schema were
  independently compared with the pristine e8d9e0d checkout in separate processes.
  All three outputs are unchanged.
- The broader selected effect/state/composition suites now have **75 passed**;
  the earlier public ValueQuery class-identity failures are resolved in integration.

Specific scope conclusions:

- Registry erasure is a validated plugin boundary. A provider can declare a finite
  representation variant different from bottom/top; a malformed element is rejected.
  Explicit read-only settings replace unbounded provider attribute forwarding. Legacy
  native-provider implementation typing remains outside this slice.
- The source/input snapshot detaches target and inventory JSON while keeping immutable
  source identities and scenarios. Reruns discard the AST cache and retain source
  bytes after file removal. AST objects remain deliberately execution-owned mutable
  IR, so this is not a claim that arbitrary AST aliases are immutable.
- Private-name/annotation AST extension reads now validate their exact finite value
  kinds. CPython 3.12-only AST fields match the declared minimum language version.
- Output Mapping/object normalization and strict query/key errors are exercised at
  the actual configuration boundary. Catalog defaults are frozen internally and
  public projections are detached.

The finite before/after differential receipts (7,462 scalar and 40,405 lattice cases)
are useful additional parity evidence. They are not proofs of soundness or permission
to bless baseline defects. Existing mixed-sort, float-truth and string-wire audit reds
remain separate repair obligations. Domain/heap identity publication, whole runtime
Flow/metadata ownership and the complete public Session strict path still require
their own completed implementation and independent review.

Follow-up production-port review covered `normalize_source_modules`, its use by
frontend parsing, Source/File module type validation, the retained input-selector
snapshot contract and `analysis/contracts/observation.py`. Source-record duplicate
validation still precedes parsing; the helper performs no parser or target execution
work. The observer now has a concrete producer port, immutable limit record and
validated frozen JSON events. Its passive behavior is preserved: producer failures
affect observation errors, dropped work-dequeue records cannot retain a stale active
unit, and causal caches update only for retained events. Serialized source metadata
now requires strings rather than coercing arbitrary objects; this is an explicit
input-boundary validation change, while actual SourceModule inputs are unchanged.
The independent original observer and focused input suites passed together:
**14 passed in 0.15s**. This follow-up does not close the pending typed driver or
parent-card gates.
