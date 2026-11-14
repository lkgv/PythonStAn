# Cold source-call analysis continuation: first slice

Status: design and preregistration only; implementation and new witnesses are not_run.
This precursor does not close C-SEM04, AI01/02 or any original parent card.

## Measured trigger and existing structures

The original callback/max_stmt_visits=1 gate stops after a cold callee causes its module caller to replay. The35callback gate separately stops at the original2s owner wall limit. These are distinct obligations; neither guard reset nor a larger cap is permitted.

Ordinary cold calls currently return an empty flow list. ContinuationEngine.block then has no continuing path; the driver later replays the unit on summary growth. Existing Continuation, AssignResume, ReturnResume and ExprResume provide statement consumers. ContinuationEngine.marker can consume an already-applied normal expression result and finish the original statement, then block can run the remaining commands. CallResumeContext carries generator exception ownership; it is not a normal-call wait record.

## Bounded implementation contract to freeze first

Initially accept only a module's straight-line assignment of one statically known synchronous leaf source call with literal positional arguments, no defaults/star/keywords, and at most one active wait per caller. Admit sequential waits after the preceding one completes. Recursive calls, nested expressions, try/finally and comprehension builder/cursor waits remain on their existing path and explicitly outside this precursor.

Introduce a finite driver-owned AnalysisCallWait scheduling record. It holds the already-evaluated callable and bound inputs, caller Frame/StateHandle, assignment consumer, remaining Continuation, source/policy identity, identity epoch and separate caller/callee input-generation and callee-summary revisions. It is analysis state, not a Python Flow, SuspendFlow or RaiseFlow. Retain the callable/binding snapshot: resumption must not re-evaluate expressions or rebind changed defaults.

request_function/_request may register an eligible cold wait after dependency registration. On summary publication, _solve queues restoration; it must not recursively call _solve. Apply each actual callee completion to the captured caller state through the normal Store.apply path, then use marker(AssignResume(targets, Continuation()), ...) and execute the remaining block. Preserve raise completions through the normal machinery. Reuse the ordinary source-call writeback policy, including current-epoch local selection, inherited weak contributions and paid input/output admission; bare default Store.apply is not a substitute for that policy. Do not tick the original statement or execute its prefix again; ordinary later commands still tick normally.

Wait state is an owned graph root. Freeze its finite schema, handle/root ownership, copy/signature/remap and inclusion in the host identity transaction before storing it. Charge registration, retention, lookup, checking, restoration and publication under the existing model. A new hidden unmetered cache is not an acceptable implementation.

Validate caller input generation, callee input generation, current summary revision, source/policy and identity epoch before use. Summary revision and input revision are different identities. Input growth or an incompatible epoch invalidates the wait; reschedule through an admitted path, retaining pending/open obligations until covered. No new caller input may be discharged by an old captured prefix. Budget stop or unresolved wait must not publish a closed normal result. Observer and procedure-bridge state must reflect the actual generation and completions.

## Independent sources to preregister

Run CPython controls independently and query only result at module_exit, ai_primary, context_depth0, original10k work/2s/512MB and max_stmt_visits1. All expected values below remain not_run.

| Purpose | Source | Expected result |
| --- | --- | --- |
| One cold leaf | `def f(): return 11\nresult=f()\n` |11|
| Prefix retained | `log=""\nlog+="p"\ndef f(): return 11\nresult=f()\nresult+=len(log)\n` |12|
| Two sequential waits | `def f(): return 17\ndef g(): return 19\na=f()\nresult=g()\n` |19|

Also freeze negative controls for unresolved callees, actual raise completions, budget denial, input/summary invalidation and epoch changes. Preserve original globals/nonlocals/escaping-cell effects and all existing graph-cost tests. Require closed useful normal values only after every relevant wait is discharged.

Only after this first slice qualifies should its statement consumer be generalized to nested expressions and explicit comprehension cursor/builder state. The two original SEM04 gold/cap gates remain required; passing the three leaf sources does not substitute for them.
