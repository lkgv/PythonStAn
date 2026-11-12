# W1 runtime effect slice review

Reviewer: requirements_audit; state/effect author: domain_native_baseline.
Disposition: **typed footprint migration and the corrected private-cell behavior
accepted as a partial slice**. N4-TYPE-01 remains incomplete.

The live Store write footprints now use CellWrite, HeapWrite, HeapDelete,
HeapDefaultWrite, MetadataUpdate and Havoc records, and Store.apply consumes them.
Graph remapping and Native/legacy adapters have corresponding real consumers.
The revised old tests replace tuple representations with records while preserving
their existing semantic assertions; no expected target value was weakened.

HeapDelete is correctly treated as a poststate footprint, not an imperative replay
instruction: after joining a may-write and may-delete path, application retains the
written value and missing alternative. The new test exercises that distinction.
The real call-to-cell/heap-write-to-return/raise-to-apply path is also exercised.

Independent review found an avoidable precision regression in initial Havoc
publication. Transfer.unknown skipped unescaped caller-private locals while
performing mutation, but its new Havoc record listed every cell. Applying a reusable
callee summary with a joined private local consequently overwrote the caller's
more precise current value. This was a new migration regression, not an asserted
universal underapproximation.

The author restricted the record to the cells actually havoced. The added actual
Transfer.unknown-to-Havoc-to-Store.apply regression covers normal and raising exits,
keeps the caller-private value exactly current, and still makes global/captured
cells Top with an unbound alternative. Red and green receipts with source hashes
are retained under `artifacts/next/evidence/N4-TYPE-01/havoc-private-caller`.

Independent test commands after the correction:

```text
runtime effects + new input/config/domain contracts: 29 passed
runtime effects + old state recovery + old composition suites: 75 passed
```

The new private-cell regression and its shared-cell positive controls are included.
Partial implementation remains visible: the currently live Flow, metadata and graph
transport still contain unfinished dynamic/untyped boundaries, and the new typed
continuation records are not yet the complete executed representation. This review
does not qualify those contracts, the full strict state module, graph/epoch repairs,
or any parent TYPE-01 completion claim.

A later narrow metadata integration correction was independently reviewed for the
API checkpoint. HeapObject.join first detaches its result, then reclassifies that
owned Metadata facade in place when incompatible shapes become a summary. It no
longer replaces the facade with another object and strands an admitted self-cycle
on the prior facade. The original input graph remains unchanged; the summary keeps
its conservative havoc behavior and typed reference-role fields. The reviewer ran
the actual join/self-cycle regression plus the reclassification alias control:
**2 passed in 0.10s**. This accepts that correction only, while the broader runtime
Flow/state/metadata typing obligations remain open.
