# PythonStAn semantic profiles and frozen acceptance obligations

Status: SPEC-01 frozen target contract, 2026-11-10. The task prompt and supplied
task board are normative. This document specifies targets; an implementation
may claim a capability only after its task and independent review pass.

## Language and execution model

The target source language is Python 3.12, independently of the Python version
running the analyzer. The core model is sequential execution with no tracing,
profiling, signal, concurrent interference, reflective frame access, GC or
finalizer callbacks. Resource exhaustion exceptions are outside this model;
explicit source exceptions of those types remain source behavior. Exclusions
are reported in the profile identity, not silently used to remove difficult
source constructs. Static analysis never imports or executes target code.

A Source/File entry is module initialization. A Project without explicit
entries analyzes every selected first-party initialization root in an
independent scenario. The module cache and partially initialized module
objects belong to that scenario. Results explicitly say initialization_only;
they make no claim about arbitrary external requests. Explicit function roots
carry bound abstract arguments covering the declared input contract. A sample
value is insufficient for an unconstrained external parameter. No roots is
no_entry, not a successful empty whole-program analysis.

## Profiles

| Identity | Contract and release obligations |
| --- | --- |
| python-core-v1 | Full scalar values, source-defined functions and classes, lexical/global/cell locations, definition-time defaults, binding including receivers and variadics, finite builtin containers, ordinary control and call/return, basic exception/finally/with cleanup, state effects and finite contexts. All mandatory A01–A13 fixtures and their declared new variants are compulsory. |
| python-protocol-v1 | Adds the specified descriptor/MRO/construction/decorator and iterator/generator/resume/close/throw/yield-from/await fixture families. These are required by SEM-05/06; failure cannot be hidden by saying an entire task is outside python-core-v1. Unmodeled external scheduler/cancellation behavior stays open with state effects. |
| closed-call-effects-v2 | Existing separate source checker: exact restricted syntax, stable closed function bindings, correctly bound inputs, unordered may call/raise prefix-event alphabet and normal/exception exits. It certifies no scalar return, arbitrary heap, temporal order or whole-Python property. Keep its current version and restrictions. |
| guarded-dispatch-v1 | Source-bound exact builtin dict, finite known keys, stable known source handlers, identity and fresh-box relations. Initial profile rejects loops, recursion, hidden mutation, decorators, dynamic handler creation and custom dict protocols in every relevant function. Complete source-derived validation, caller guard evidence and complement coverage are mandatory. |

Capabilities are granular. Every unsupported operation adds its source point,
affected effects and dependency obligations. It also applies a conservative
transfer for affected reachable heap/cells/globals/callback/exception and
continuation state, or leaves dependent answers unavailable/open. Logging an
unknown effect does not make an unchanged precise heap sound.

## Values, state and outcomes

Full values include strings, integers, booleans, None, references, callable
identity, container shapes and configured relation components. An empty
address projection does not imply an empty full value and never implies None.
Bottom denotes an empty concretization; Unknown/Top denotes unconstrained
behavior in a declared sort. A state has explicit reachability, independent of
whether its environment is empty. Natural function fallthrough creates None;
an explicit return creates the evaluated full value; an exception-only or
suspension-only body creates no normal return.

The state concretization covers environment locations, heap objects and
effective slots, shared cells/globals, aliases, object cardinality, suspended
frames/pending cleanup, relation tokens and client monitors. Address summary
conversion applies to the entire state and future dependency subscriptions.
Strong updates need a trusted concrete singleton/cardinality argument, not
one abstract address. Source-backed fresh allocation is renamed at each
summary application under the supported identity abstraction.

Completion distinguishes normal continuation, return(value), raise(value),
break, continue and suspend(value,frame). Finally executes separately for each
pending completion; normal cleanup restores it, abrupt cleanup replaces it,
and divergent cleanup preserves observed prefixes without fabricating an exit.
Return expressions are evaluated before cleanup. A possible exception creates
a distinct successor, not a Boolean annotation on the normal state.

## Domains and compression

Integer concretization is unambiguous: Bottom, Finite(S), Interval(lo,hi) or
Top, with an optional equivalent tagged implementation. Finite {0,3} excludes
1 and 2; Interval[0,3] includes them and cannot be exactly enumerated as {0,3}.
Unbounded widening results never carry an exact finite enumeration flag.
Boolean/int dictionary key equivalence follows Python equality while value
type information remains available where relevant.

String compression must cover every discarded constant by a retained pattern
or Top. Join/widen cover both operands; a representable intersection is exact;
an unrepresentable intersection uses an explicitly named covering assume or
approximation contract, not a false GLB. Narrowing must stay between its
required bounds and be followed by a post-fixpoint check before publication.

A dict default slot describes untracked keys; it is not the value of an
arbitrary-key lookup. Unknown reads include every matching tracked partition
and default; unknown writes update every possibly matched effective slot.
Partition splitting inherits the previous effective value/presence. Presence
is may/must existence: absence and possible absence propagate KeyError flows.

Every operation uses one immutable run-local DomainBundle/version, including
nested values, heap slots, reductions and summary application. Domain capacity
is distinct from an operation's finite enumeration budget. Resource exhaustion
may safely merge/coarsen or mark an incomplete worklist, never discard behavior.

## Fixed points and composition

AI-primary derives definitions, objects, modules, calls and input states from
the front end and models with NoPTAProvider installed. It maintains shared
entry/block/exit/summary equations and caller continuations. New input, heap,
cell, class/model or summary facts wake all affected consumers; recursive SCCs
iterate jointly. An independent depth cap is not an interprocedural fixed
point. Narrowing publishes rebuilt summaries only after coverage checks.

PTA facts are candidates with coverage and version. Empty, partial, unknown
and closed answers are distinct. Partial answers never prune AI discoveries.
Default adapters are covering unions/projections; intersection requires both
solutions to cover the same entry/profile/observation and a proved reduction.
Context tokens range over finite projections of source identities and ordered
argument bindings; no token recursively embeds full caller or heap contexts.

## Guarantee ledger

Solver convergence, semantic coverage and client property proof are independent.
Each verified property records profile, source/IR/model/policy identity, entry
and point input bound, noninterference/frame assumptions, closed obligations,
checker version and evidence. A may counterexample is potential/unknown unless
it is independently established as a concrete property violation. No requested
property means not_requested. Run-level verified requires at least one
property and every requested property verified. A checked local property does
not rewrite an exhausted original solver as converged.

## Frozen acceptance sets

`query-gold.json` contains independently authored expectations. Its
mandatory_supported_fixtures cannot be reclassified or removed after
implementation failure. Required-open fixtures allow openness but require
real effect propagation and no stale precise downstream answer. Component
bugs require both a component witness and a masking-controlled integration
witness. Concrete witnesses are test-only subprocess executions and establish
inclusion for those inputs, never universal soundness.

L1 usefulness is a relation query improvement at fixed input/profile and work
budget, with all reanalysis costs included. L2 usefulness uses the frozen
guarded dispatch family: sizes 2, 8, 24 and repeated call counts 1, 4, 16;
identity/fresh handlers alternate. Measure key-handler mispairs, output-to-input
and fresh-slot relation precision, known answers, actual source/checker/proposer
work and total cost. Improvement may be better relations or lower total work
at equal precision. A baseline already exact in the same domain is not
deliberately degraded and need not be beaten in precision. Correct, malicious,
coarse-valid and no-candidate arms share source and budgets. Rejecting every
candidate is a safety baseline, not LLM usefulness.

## Second-stage W0 independent acceptance extension

The immutable `tests/regression/audit3/semantic_manifest.json` extends the
existing obligations with AS-01–AS-08 and LV-01–LV-03. It retains the original
A01–A18 ledger references and the digests of the original gold and held-out
fixtures. The companion domain/native registry is
`docs/development/next/domain_native_manifest.json`; its component evidence is
distinct from public API evidence.

The new semantic denominator has 52 handcrafted source cases and six offline
L1/L2 cases. CPython 3.12 subprocesses check independently stated concrete
results and source call occurrences; the analyzer never supplies the expected
answers. Only the handcrafted fixtures are executed by the concrete oracle.
The generated concrete traces establish bounded witnesses, not arbitrary
entry coverage or a universal soundness proof.

`semantic_oracle.py --check concrete` checks the independent oracle.
`semantic_oracle.py --check analyzer` is the actual mandatory semantic gate and
currently fails at the frozen W0 source. Its nonzero status must be preserved.
The separately green `test_semantic_baseline.py` validates oracle integrity and
negative guard behavior; it does not mark the product semantic gate green.
The analyzer gate rejects missing, unsupported, partial or truncated mandatory
answers, typed or universal Top, budget exhaustion, and a changed frozen
denominator. Context-zero recursion explicitly requires useful finite
inclusion, while the default four-level sum and heap recursion retain their
stated exact targets; this does not require equal precision across policies.

Additional source-order witnesses pin the distinction between starred
expression evaluation and iterable expansion, the exception prefix of
duplicate `**` keys, and deferred non-string-key validation. These fine-order
observations are CPython-3.12 bounded profile requirements. Comprehension
false-filter effects, ordered tuple classinfo, repeated deletion, and
single-evaluation truth protocols have separate variants and positive controls.
Offline L2 cases preserve source call occurrences, function exits, heap fields,
and explicit read facts. An already valid return-relation certificate does
not discharge these different query obligations. No live LLM is used by W0.
