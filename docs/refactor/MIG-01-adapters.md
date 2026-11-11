# Source, model and staged-engine migration

The public `native_then_ai` path runs Native as an explicitly costed preparatory
phase, then uses the same `InterproceduralDriver` as `ai_primary`. Its optional
`NativeSnapshotOracle` converts source-bound module scalar observations into
partial positive candidates. It never treats a missing candidate as impossible,
never imports opaque legacy object hashes as valid new-engine references, and
never copies Native return results into a purported AI value answer. Source,
profile and model identity must match before binding; an explicit policy bound
must match too. Unknown Native rows and unmapped references remain visible in
adapter statistics. The staged phase differs from `hybrid`'s incremental
inclusion oracle, which does not require a complete Native prepass.

`frontend/source.py` owns source parsing, Python 3.12 syntax/symbol validity
checks and node identities shared by planning and execution. Span-based node
IDs retain their established format; the full source digest is checked beside
them. Nodes without a column, such as `TypeIgnore`, have an explicit zero
column. Nodes without a source span use structural paths rooted at their
nearest located ancestor. Shared operator/context marker singletons are not
program points; their enclosing expressions retain their ordinary source IDs.
The session-owned point mapping formats IDs on demand rather than allocating
an ID string for every node at startup. An internal normalizer parser cache
can reuse the exact source-digest-bound AST; compiler validity checks still run
for reused trees. Parsing and compiler checks never execute target programs or
import target modules. There is no global mutable AST cache.

`models/builtins.py` owns shared static builtin declarations. A name's presence
does not claim that all of its Python behavior is modeled. The active closure
analysis reads the legacy catalog directly rather than importing the Native
solver. Plain exception constructor declarations are separate from the
historical function/container catalog; special exception groups and OSError
constructors need their own model contracts. The complete frozen Python 3.12
builtin namespace and exception parent declarations are separate from the
subset with precise transfer models. Resolving a known but unmodeled builtin
therefore does not prove that its use raises `NameError`.

`analysis/contracts/budgets.py` owns the unchanged 25-field `AnalysisBudget`
and validator. The old AI API/config modules re-export those exact objects,
allowing Native configuration to use them without importing another engine.
Runtime shared-work accounting is a separate ledger passed by identity through
intrinsic dispatch and nested statement transfers.

The sole maintained `ClosureAnalysis` remains `pythonstan.analysis.closure`.
`analysis.scope.closure` and the historically misnamed `analysis.class_hierarchy`
now delegate to that same class; they do not register or run duplicate transfer
algorithms. The abandoned CFG-model-era closure API is retired. The maintained
script is now `python -m scripts.do_closure FILE --format json`; it statically
builds the ordinary frontend pipeline and accepts real file arguments instead
of hardcoded developer paths.

The historical scope AI and its `AISummaryEngine` still support explicit
compatibility/intrinsic consumers while those adapters are migrated. Their
caller-state, complete-value, cleanup, binding and checked-narrowing repairs are
covered by separate regression evidence. Their presence does not turn them
into the independent AI-primary/SCC implementation. Release-mode architecture
checks continue to expose remaining cross-engine legacy imports; this migration
slice does not by itself satisfy the final single-implementation release gate.

The module map and task board remain the authority for remaining owners,
compatibility lifetimes, evidence and unresolved obligations.
