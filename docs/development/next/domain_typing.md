# Actual domain typing for N4-TYPE-01

Status: implementation slice under integration and independent review. This is
not completion of the parent runtime-contract task or the full strict frontier.
The separate mechanical checkpoint is `37f9a93`; its executable AST is unchanged
(the recorded blank docstring whitespace normalization is the only raw AST
exception).

All ten canonical domain modules now have complete callable/attribute types,
zero module-owned mypy diagnostics under the configured strict and
`disallow_any_expr` checks, and zero Ruff lint/format diagnostics. The mypy
command still exits nonzero for transitive modules outside this slice; those
raw diagnostics remain visible and have not been suppressed or rebaselined.

The running `Value.refs`, reference constructor, relation handlers, operations
and identity arena use the canonical nominal IDs. Integer, string, boolean and
None lattice methods retain their algorithms. Concrete host operators have a
small scalar-only port; the host result is immediately classified by
`Value.constant`, including unsupported constant sorts. This port contains no
Store, driver or semantic service and does not replace an algorithm with an
unchecked protocol. The finite comparison against the mechanical baseline
checked 7,462 exact scalar operation/truth/refinement cases with no changes in
exported values or exception families.

The actual heterogeneous `DomainRegistry` now validates provider element
representations before dispatch. `Domain[D]` remains generic inside each
provider. `element_types()` explicitly declares the legal finite runtime
variants; the default includes both bottom and top classes, and a union-valued
provider declares additional classes. A positive control uses a finite value
class different from both boundary classes, and a malformed-component control
is rejected. Registered views expose explicit lattice/fact/reduction methods
and finite read-only settings. The previous unbounded `__getattr__` and all
canonical-domain `Any` types are gone.

This is an explicit plugin boundary contract: custom providers with additional
runtime representation variants declare them through `element_types`, and
extra settings through `public_settings`. All currently registered built-in
and legacy providers use a single immutable element record. The real optional TokenCapacityProvider port preserves the existing
Callable/Type/Descriptor max_tokens settings without dynamic forwarding.
Address-signature consumers still belong to integration; their remaining debt
is not hidden by the canonical-module result.

`IdentitySubstitution` detaches object and cell maps. `IdentityArena` exposes
separate typed object/cell resolution, and publication accepts an explicitly
prepared cell map. The state-transform owner supplies the compatibility
projection for old seeded mixed-map fixtures. This addition does not close
any graph, epoch or continuation finding by itself.

Known mixed-sort fallback, float truth and residual-string export defects were
preserved. Their frozen mandatory acceptance remains red until separately
reviewed semantic repairs. Existing domain tests and the new union/identity
controls pass (21 tests). A broader historical domain run recorded 188 passes
and five failures in retired private TAC Interpreter/ConditionRefiner services;
these were not relabeled as supported. Cross-component integration is rerun
once the concurrent API query re-exports and state/effect changes are stable.

Evidence is under `artifacts/next/evidence/N4-TYPE-01`, including the mechanical
AST record, finite differential results and all test stdout/stderr. The input
plan is `domain_typing_plan.json`. No live LLM or credential is used.
