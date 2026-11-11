# Engine-neutral state, call/return and guarantee contracts

Status: SPEC-01 frozen target interface, 2026-11-10. Generics below do not import
concrete engine/domain classes. Mutable solver records are session-owned;
published result/summary records are immutable snapshots.

## Shared identifiers and versions

`SourceId` binds module identity, decoded semantic source digest, original byte digest/encoding when file-backed, and import-root identity. In-memory Source binds its UTF-8 bytes. `RegionId`
binds SourceId, qualname, stable AST/IR node ID and a resolvable span. A selector
with a stale digest fails or is explicitly rematched against new source.
`ObjectId` includes the session arena and abstract allocation identity; values
from different arenas cannot be compared as bitmask lanes. Canonical constants
are not fresh objects. `CallKey` includes target identity, finite method
context, profile/domain/policy version and the explicit entry scenario.

`SemanticIdentity` contains source/IR digest, Python/profile version, model
digest, policy digest, state epoch and dependency versions. Dependencies include
positive and negative name/attribute/call discoveries. No stable dependency
identity means no stable cache. Old result snapshots retain their own arena
interpretation after later sessions.

## State and effects

`StateProtocol` exposes reachability, full value locations, heap/cells/globals,
completion/frame/alias facts, relation/monitor state, join/order/reabstract and
dependency reads. Implementations remain engine-owned. A conversion contract
`StateAdapter[A,B]` requires gamma_A(s) subset gamma_B(adapt(s)) for every
supported edge and preserves source/arena/fresh identity. Every normal,
exception, suspend and loop edge passes through it.

`Effects[Obj,Field,V]` contains may reads/writes/calls, exception events,
allocation/fresh effects, havoc reachability and open obligations. Prefix
effects include effects before nontermination; no exit is required for them
to be observable. An unordered may-effect set cannot answer ordering queries.
`UnknownEffects` explicitly identifies return sorts, reachable heap/cells,
globals, callbacks, exception and suspension channels; consumers apply it.

## Completion and outcome

Suggested generic shape:

```python
@dataclass(frozen=True)
class Outcome(Generic[V, S]):
    kind: Literal['normal', 'return', 'raise', 'break', 'continue', 'suspend']
    state: S
    value: V | None                 # None is absent payload, never Python None
    continuation: ContinuationId | None
    pending_cleanup: tuple[CleanupId, ...]
    guard: GuardId | None

@dataclass(frozen=True)
class ProcedureSummary(Generic[V, H]):
    identity: SemanticIdentity
    input_bound: InputBound[V, H]
    outcomes: tuple[Outcome[V, ExitState[H]], ...]
    prefix_effects: Effects
    relation: RelationSnapshot
    fresh: tuple[FreshTemplate, ...]
    frame: FrameMap
    dependencies: tuple[VersionedDependency, ...]
    coverage: CoverageRecord
    derivation: DerivationRecord
```

The implementation can use an explicit optional/payload wrapper to avoid
confusing absent payload with the domain value for Python None. Function normal
completion is normalized into a return outcome with domain-None only at natural
fallthrough. Summary bottom means no known prefix/exit yet; it is not proof of
unreachability before the input and dependency equations are closed. Divergent
functions may have prefix effects and no exits.

## Call boundary

`CallRequest[V,S]` contains callee value/target, evaluated positional and named
argument shapes in Python order, caller state, call site, context and separate
normal/exception/suspend continuations. `BindArguments` returns normal binding
alternatives and TypeError alternatives; unknown expansion retains all possible
bindings/effects or opens the corresponding obligation. Receiver insertion
precedes signature matching; defaults are saved definition-time values, not
re-evaluated source expressions.

`enter(request)` passes caller reachable heap/overlay/havoc, cells/globals,
aliases, relations, pending cleanup and entry input. It creates local parameter
locations while retaining shared cells. `leave(summary, caller_remainder)`
checks input applicability/version, renames fresh templates and combines the
correct exit state/effects with the caller frame. It never imports facts from
an unrelated Native baseline to replace missing AI values. Writes affect the
caller continuation state, not only an effect list. Each outcome remains
associated with its guard, return and heap effect through application.

## Solver and cache

EntryStore maps CallKey to a joined input bound. ExitStore maps it to current
outcome equations. CallDependencies record caller continuation subscriptions.
New entry inputs schedule the callee; new exits/heap/cell/class facts schedule
subscribers. Recursive SCCs share the global queue and budget; widened
intermediate equations are not stable caches. Stopped runs expose pending work
and reasons. Stable summaries require covered inputs, closed dependencies and
version checks. Policy changes initially use clean reruns; incremental updates
must invalidate producer/SCC/consumer closure and have a clean-run oracle.

Narrowing recomputes public summaries and full prefix/exit effects. A proposed
smaller result is published only after a fresh post-fixpoint/coverage check;
otherwise retain the already justified upper approximation and record failure.
Deleting old effects without that check is forbidden.

## Oracle and verification boundary

`CandidateAnswer[V]` contains values, `coverage` (closed/partial/unknown),
SemanticIdentity, provenance and subscriptions. NoPTAProvider returns no
candidate information, not an assertion that the candidate universe is empty.
Partial/unknown PTA observations are never absence proofs. The composition
layer owns conversions; engines cannot access the other engine's private state.

`GuardEvidence` binds origin, profile, source point, complete input bound,
source/IR/model/policy/state versions and the source of its coverage proof.
Candidate validity under Pre and entailment of Pre by all current call inputs
are separate checks. Partial worklist snapshots, missing PTA edges and
unproved bottom cannot establish the latter. Applying a guarded summary must
also cover the complement with a justified fallback/open transfer. Later
inputs or alias/table changes recheck applicability and invalidate consumers.

## Public results and query boundary

`AnalysisResult` freezes schema/version/run/source/model/policy identity,
target/entry contract, effective config with per-leaf provenance, actual phase
trace, statuses, query answers, diagnostics/obligations, stats and evidence.
`QueryAnswer` records requested query/point, approximation kind, value/relation,
coverage and per-property proof. `ValueQuery(module,symbol,point)` must state a
program point; a Native result says flow_insensitive_may. Missing unrequested
data is not_requested, never an empty complete answer. `OutputSpec` changes only
projection, retains guarantees and marks filters/truncation; it cannot schedule
work or mutate solver/result caches.

`GuaranteeLedger` keeps solver_status, coverage_status and proof_status separate.
`is_complete` is a documented compatibility projection of convergence and
closed declared coverage, never a proof flag. Inspector receives stable,
versioned read-only events. Observer failure/truncation affects observational
coverage only and cannot delete semantic work or mark proof obligations closed.
