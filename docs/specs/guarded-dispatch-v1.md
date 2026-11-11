# guarded-dispatch-v1

This optional profile certifies a conditional key–handler–return relation. It
does not certify arbitrary Python or change the original solver status.
Candidate proposals and point-input coverage are separate inputs to the trusted
application boundary.

The source language is Python 3.12 in the sequential closed-module model used by
`python-core-v1`. Resource-exhaustion exceptions, tracing, concurrent mutation,
reflective frames, finalizers and GC callbacks are outside this profile. Source
is parsed, checked for static compiler validity, and symbolically checked; it
is never executed by the checker. AST preparation is metered before the
nonexecuting compiler validity check.

## Source fragment and denotation

A module contains unique undecorated source function definitions, one exact
builtin dict literal with finite distinct string keys and known source handler
values, and optional finite top-level dispatch calls with literal key/payload.
Definitions must precede table construction and calls must follow the dispatch
and table definitions. Names cannot be rebound. All module statements and all
source functions are checked, including unused handlers and source after a
return. Optional literal docstrings have no modeled user effects.

Each handler has one symbolic payload parameter. Local alias assignments may
copy that parameter; its explicit return is either the same payload or a fresh
dict literal `{'value': payload}`. Dispatch has three parameters; local aliases
may preserve its table, key, selected handler and call result. Its result must
be exactly the result of `table[key](payload)`. Loops, recursion, hidden calls,
mutation, imports, custom container protocols, decorators, annotations,
defaults, suspension and exception/cleanup code are rejected by this profile.
Those behaviors remain on the ordinary analysis/fallback path.

For an input heap H, exact table t={k_i:h_i}, key k_i and arbitrary modeled
payload p, the checked relation is:

- An identity handler returns p and leaves H unchanged.
- A box handler returns f with f fresh for the pre-existing heap, sets
  H'[f]['value']=p and preserves H on all old objects.
- The selected handler is correlated with the selected key and result. Joining
  separate key, handler and return sets does not establish this relation.

Literal absent-string keys are covered by an independent exact-dict complement:
KeyError with no handler call or old-heap mutation. Unknown key/table protocols
retain a residual that may return, call callbacks, write, allocate, raise or
suspend; they cannot receive a whole-input verified return-relation answer.

## Candidate language

`language.py` defines a bounded JSON DSL. A candidate binds schema/profile,
source digest, AST IR digest, model digest, policy digest, state version and
positive/negative dependency versions. It supplies a finite exact-table guard,
a complete handler vector and one arm per table key. Return relations are
`input`, `fresh_box` and legal covering `top`. The fresh box has the literal
`value` slot; existing user heap is unchanged; the supported source handlers
have normal exits with no exception/suspension exits.

Unknown fields, duplicate keys, malformed booleans, invented authority fields
and over-budget candidate bytes are rejected. JSON strings are data, never
evaluated Python. A candidate cannot select ignored functions/statements,
construct caller coverage evidence or grant its own certificate.

## Source refinement and proof outline

`generate_obligations` derives the complete function vector, source table,
initialization ordering, dispatch shape and literal call points independently
of the candidate. The checker compares the vector, every key–handler pairing,
return relation, frame and completion. Budget exhaustion before any part of
the vector closes returns unknown and no certificate.

The symbolic handler environment maps a parameter to Input(p). Alias
assignment preserves that symbol. Returning it establishes identity; returning
the one supported dict literal establishes a new Fresh(f) and
Slot(f,'value')=Input(p). No supported statement mutates the old heap. This is
an induction over every statement in a validated handler body. Dispatch's
symbolic environment preserves Table(t), Key(k) and Input(p), so the only
accepted call selects t[k] and passes the same p. Exact builtin dict lookup on
an exact string key invokes no user protocol. Composing this rule with every
source-derived handler establishes the correlated finite disjunction. Module
initialization induction proves function/table bindings at each finite call;
an earlier definite missing-key exception makes later calls unreachable and
does not create complete input evidence for them.

`top` may cover a checked return relation. That can be a legal certificate,
but it does not prove the requested exact return/fresh relation. A fixed-domain
baseline that already has the exact result need not gain precision; the useful
gain may instead be lower total checked execution cost.

## Point coverage and application

A source certificate proves validity under its guard. `PointCoverageProof`
separately binds profile, origin, source point, full input-bound digest,
source/IR/model/policy/state/dependency versions, table identity and payload
bound. Source-call evidence is derived from the checked finite initialization
sequence. Explicit entry evidence is conditional on the declared exact table,
complete nonempty key domain and the canonical dispatch entry; payloads are
universally quantified. It does not cover external calls outside that entry
contract. Partial worklists, missing PTA edges, sampled inputs and unproved
bottom never create this evidence.

The current key set must equal the complete evidence key set. A read-only
partial state cannot stand in for a complete read/write input bound. Source,
IR, model, policy, point, table, payload and later input changes invalidate
applicability. Certificates and coverage records have host-only integrity
stamps; copying/replacing immutable records cannot preserve authority for
changed contents. Serialized candidate caches are rechecked, not deserialized
as trusted certificates.

Fresh application additionally requires a complete caller frame object-ID
bound supplied by the trusted consumer. The applicator reserves that bound and
all payload references, verifies every allocated ID is disjoint, and preserves
input references in the fresh slot. Missing/unknown frame bounds or a capturing
allocator yield unknown/fallback. This frame completeness is a consumer/heap
representation obligation, not authority supplied by an LLM. The AI adapter
uses the complete modeled `Store.heap` at a checked acyclic initializer call.
Its stable fresh name denotes that single source allocation event on replay;
different source call points and scenarios remain distinct. Standalone
`FreshArena` applications use capture-avoiding generative names.

`DispatchApplication` exports correlated outcomes, fresh objects/slots,
relations, guard/frame digests, explicit complement residual and property
status. It never changes the original solver status. A checked property is
scoped to `guarded_dispatch_return_relation` at its declared point; the source
certificate does not automatically verify arbitrary value or global queries.

## Engine bridge and budgets

The core exposes an optional generic summary-provider hook and imports no
verifier or LLM module. Composition checks actual module/model/policy identity,
the whole runtime module AST digest, actual dispatch and handler AST digests,
exact runtime table bindings and
complete source-call input evidence before applying a summary. Unsupported or
inconsistent cases return None and retain ordinary analysis.

The provider charges proposal, checker, bridge setup, runtime applicability and
application work to the driver's shared budget. Setup is charged even if no
call is analyzed or every candidate is rejected. Remaining resource caps do
not alter policy identity. Every unproposed/unchecked module remains unknown
for an all-module property query. Individual point proofs retain their own
input/profile scope.

`benchmark/refactor/guarded_dispatch_family.py` freezes executable bytes and
runs the gold family sizes 2/8/24 and repeated call counts 1/4/16 in baseline,
correct, malicious and legal coarse-top arms. It records all source/checker/
proposer/application costs, runtime validation, wall time, peak memory,
solver/coverage status and exact/fresh/handler quality. The ordinary AI baseline
keeps its existing policies and caches. Larger failed/budget runs remain in the
raw records. Intermediate families are retained with their executable hashes.
Final claims use `docs/refactor/evidence/20261110/LLM-03/family_reviewed`, after
independent capture/frame, whole-module IR, source-validity and budget review.
Eight of nine correct-candidate scenarios converge with exact requested
answers; the 24-handler/16-repetition scenario remains work-budget-exhausted.
The demonstrated gain is lower measured total wall time at equal precision,
or recovered decidability within the fixed deadline. It is not a decrease in
the deterministic charged work measure or a precision gain over an already
exact baseline. `LLM-03/experiment_report.json` records the raw-data scope.
