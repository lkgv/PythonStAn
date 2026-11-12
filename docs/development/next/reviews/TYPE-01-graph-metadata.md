# TYPE-01 recovered graph/metadata slice review

Reviewer: requirements_audit. Author: semantic_baseline, with canonical Store/Flow
integration by the state owner and root. Disposition: **accepted as the reviewed
finite graph/metadata slice for the partial checkpoint**. TYPE-01 and complete
state/epoch semantic repair remain unverified.

Normal heap metadata has actual per-kind records and typed field wrappers. The
compatibility facade retains declared graph attachments and exact known-field
validation; it is not a renamed dict[str, Any]. Generator entry state is stored as
one StateHandle, while the legacy entry lookup projects its Store. Graph copying
rebinds physical owner IDs to the detached root without treating physical
owner/revision bookkeeping as semantic identity.

The graph decoder admits explicit registered record classes and exact builtin
containers. Its field projection is finite and typed; object.__setattr__ populates
those named detached shells rather than reflecting over arbitrary dataclasses.
An independent schema audit compared all 45 current record projections with their
actual declared dataclass/record fields and found no omitted or extra fields.
The persisted test_projection_covers_every_actual_record_field control also checks
that invariant and is included in the 60-test run below.
Boundary records preserve canonical runtime roots while their flat maps remain
authoritative; decoding does not install boundary objects in a Store heap.

The charged preparation pass records closure/reference roles before visiting
alias-first sequences and registers canonical heap equivalence shells. Deferred
in-place heap merging avoids copying an incomplete cyclic root. The canonical
HeapObject merge routine is shared rather than duplicated by the transformer.
Plain strings and tagged references keep their distinct roles.

Two previous source-review admission gaps now have actual controls:

- Complex literal attachments have consistent copy, charged preparation, equality
  and packed real/imaginary signature behavior, including signed zero and nonfinite
  components. The exact committed 735b9ff baseline rejected complex at admission;
  coherent complex support is therefore an explicit profile addition. The lost
  intermediate admission/copy mismatch was never executed and is not presented as
  a committed-baseline reproduction.
- Unregistered record subclasses are rejected at metadata/component/known-field
  admission and finite graph dispatch instead of silently losing extra fields.
  BoundaryObject remains explicitly registered.

The reviewer additionally reproduced a supplied-memo shape violation: a Store key
cached to a Value could make the generic helper return Value while annotated as
Store. The narrow correction now checks cached node class/registration on hits,
including the dictionary path. It deliberately avoids recursively inspecting
unfinished cycle shells. Wrong-class memos are rejected, while coherent anchors and
tuple/list cycles are preserved.

External memo element/provenance coherence remains an explicit caller precondition.
The generic overload is qualified by that invariant; this is not a proof for every
arbitrarily populated PayloadMemo or every erased container element type.

Independent final verification on stable source:

- 60 graph/metadata/DOM05 controls passed in 0.21s.
- Four actual source modules have zero owned strict and Any-expression diagnostics
  with normal imports. The underlying exit 1 and 3,382 imported diagnostic records
  are retained.
- Exact commands, source/test hashes and raw outputs are under
  artifacts/next/evidence/reviewer-W1/graph-recovery/receipt.json. The independent
  finite schema audit is schema_projection.json in that directory.

The author's exact isolated 735b9ff red/green evidence was reviewed with its stated
scope. These selected controls do not establish every alias/quotient, reentrancy,
summary dependency, contextual-ID or epoch obligation; those remain in the parent
TYPE/state tasks and must not inherit a completed label from this review.
