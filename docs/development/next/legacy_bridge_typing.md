# Shared legacy bridge and observer boundary

This is a bounded TYPE implementation record. It does not close the parent TYPE
task or qualify the complete Native engine. The machine receipt is
`artifacts/next/evidence/N4-TYPE-01/legacy_bridge_typing/final_receipt.json`.

The actual `LegacyStateBridge`, `CodeBridge` and `SharedDriver` now consume the
real Source, Store, Frame, nominal identity, Flow, oracle and work-ledger
contracts. They do not forward through an unbounded solver proxy. The optional
Native metadata capabilities each expose one real read-only field, checked
against its actual producer class. The runtime-only state class handle is used
only for nominal validation; it cannot construct or operate a Native state.

`NormalStmtFlow` contributes its state and effects without adding a return
payload. Explicit returned `None` and raised values retain their distinct real
payloads. Address canonicalization retains the actual AbstractObject identity
map, and field conversion retains existing key encodings, private-slot filters,
aliasing, cycles, presence and havoc information. Source metadata comes from
the producer-captured immutable SourceModule; target source is not executed.

The Native declarations follow the real producers. `_env` contains both
PointerFlowNode keys and context-qualified Variable/FieldAccess keys. Source
global seeding explicitly narrows the relevant subset. The first inventory
incorrectly excluded node keys; that candidate was not accepted. A new actual
PointerAnalysisState/PtaQueryAdapter control exercises mixed node, field and
variable storage and checks the retained source-global value.

Captured-variable dictionaries hold one qualified Variable per name. Heap
register keys use the actual contextual scope/object and module-only shapes.
The legacy Scope-only `get_all_variables` lookup shape is admitted as a query
shape; its existing mismatch with stored shapes is retained, without inventing
a stored fact or fixing the getter in this typing slice.

Standalone Native states retain an absent `_residual_effects` attribute before
Solver installation. The actual optional peek accessor catches only that
missing owned storage. Solver's existing dictionary assignment activates it.
Initializing the attribute to None broke the unchanged passive observation
control; initializing it to an empty dictionary would have activated old feature
guards. Neither shortcut remains in the implementation.

Worklist storage now describes the existing pending tuple, including a nullable
scope. A private one-member enum replaces only the missing-item marker. The
exact-dict fast path, first scope and node, hash operations, Box retention, FIFO
order and custom-dictionary callback sequence remain covered by existing
controls. PointerFlowEdge uses the real FieldAccess/class/instance invariant
for inheritance projections, and NormalNode carries the actual finite content
union. UnknownEffects retains its actual future-field/global/cell subscriptions
and copied JSON diagnostic events.

FrozenResultInspector remains detached and read-only. It imports no live engine
and returns defensive JSON snapshots/events. Revision selection, branch roots,
missing-parent explanations and truncation retain their published meanings.

Explicit unsupported boundaries remain visible: standalone source-only bridge
projection reports unavailable metadata rather than a missing-method error;
unknown custom Native context implementations are unavailable at Native
admission; malformed field-kind projections and impossible inheritance content
fail explicitly. Supported CodeBridge and mandatory policy paths retain their
existing behavior. Call ordering, deletion semantics, existing AS red cases and
source profile gold were not changed.

The pre-cleanup receipt binds 17 unchanged before/after source hashes to 140
passing controls. After five final Ruff edits, 40 affected source/key/bridge
controls pass on another stable 17-source binding. An independent comparison
also preserves 29 old/new key encodings and hashes plus eight numeric-subclass
callback sequences. The changes modernize four annotations and combine two
identical numeric branches with their existing short-circuit order.

Six fully owned files, ten newly typed Native units and eight additional
source/key/getter units now have zero strict, Any-expression and Ruff
diagnostics. Whole-import strict still exits 1 with 10,976 other records
retained; 428 whole-file Native/IR lint records outside the qualified units are
also retained. No skip-import override, Any suppression, cast, golden-fixture
change or LLM call was used.

The independent completed bridge/work/Inspector review is in
`reviews/TYPE-01-shared-bridge.md`. Earlier failed probes and the corrected test
path probe remain alongside the final evidence. Mechanical formatter bases are
saved separately as git blobs in the evidence manifests so the parent can keep
formatting history distinct from these contracts.
