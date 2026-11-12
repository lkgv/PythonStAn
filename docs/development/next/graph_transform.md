# Canonical graph transformation contract

The actual implementation is `analysis/state/transform.py`, consumed by Store,
HeapObject, host address transactions and full-state composition boundaries. The
finite GraphRecord projection covers all 45 currently registered records and all
actual fields. The persisted `test_projection_covers_every_actual_record_field`
guard checks field completeness; the independent review also checked it.

A charged preparation pass registers reference/closure sequence roles and owner
quotient classes before following aliases. Copying memoizes detached shells
before children. Only after all shells are initialized does the transformer call
the canonical HeapObject._merge_from in place. BoundaryObject explicitly reuses
that same heap implementation. There is no reflective dataclass fallback, cast,
getattr or Any in the owned graph implementation.

This repairs the measured STATE02 component properties: cyclic three-way owner
collisions no longer copy an incomplete root; standalone losing-owner aliases
resolve to the canonical quotient shell; immutable closure/reference sequences
are assigned their semantic role before an earlier alias is copied. Exact
committed735b9ff replay reports four red-to-green properties, including rejection
of an unregistered Store subclass at metadata admission. Frozen W0 source/gold
and denominators are unchanged. Complex support is an explicit finite transport
capability addition: committed735b9ff rejected it, and the lost dirty candidate's
admission/copy mismatch was source-derived only.

StateHandle copying binds its physical owner to the cloned root without reading
unfinished fields. Explicit transform epochs rebind handle/store epochs while
preserving observed revision evidence. Physical owner/version fields are excluded
from semantic equality and signatures. SummaryDependency checkpoints remain in
the unit namespace and preserve their observed version/policy/input/epoch data.
HostStateBoundary carries actual typed units/functions/extras/events; walk_graph
provides finite Store/Value callbacks and charged traversal for actual host
invalidation and overflow collection.

BoundaryState flat fields remain authoritative. Typed runtime Store/HeapObject
anchors preserve metadata/continuation/root/map aliases; decoding updates the
canonical heap map in place. NormalStmt and NormalExpr(None) remain distinct.
GeneratorMeta.entry stores one StateHandle. entry_handle() is the canonical
runtime accessor; the documented legacy mapping entry projects the same Store.

Memo compatibility is conditional: externally supplied entries must be coherent.
The boundary rejects incompatible cached node classes and unknown node shapes,
while admitting unfinished tuple/list/record shells and explicit Store anchors.
Erased container element parameters and source provenance are a recorded remaining
invariant obligation. This is not an unconditional proof of generic T preservation.
The guard addresses API misuse; no ordinary solver poisoning path was shown.

The module and finite projection exceed the standard's readability review
thresholds. The responsibility remains one explicit graph-language fold used by
copying, equality and signatures. Splitting each syntax case into a forwarding
microfunction would scatter schema coverage; new fields and roles require the
schema guard plus independent graph controls. The complete review is in
`reviews/TYPE-01-graph-metadata.md`.

The graph slice has 60 focused tests and zero local strict/Any-expression
 diagnostics. The whole imported tree retains debt. Parent TYPE01/STATE02 remain
unverified until their full dependency and integration gates complete.
