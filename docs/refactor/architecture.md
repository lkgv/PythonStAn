# Architecture and incremental migration contract

This is the ARCH-01 target map frozen against the imported archive tree. It is
not a claim that 150 mapped modules have already moved. Mechanical relocation
and behavior changes belong in separate commits, and release checks enforce
all surviving legacy debts.

The active frontend currently uses `analysis.closure.ClosureAnalysis`. The
classes in `analysis.scope.closure` and `analysis.class_hierarchy` are divergent
old implementations. `scripts/do_closure.py` imports absent CFG-era modules and
must move to the maintained API. `deprecated_ai` and the PyCG ICFG prototype
have broken or unused imports; they must be audited before retirement rather
than copied into the new core.

The native solver maintains a dynamic inclusion-constraint/PFG fixed point.
Preserve delta delivery, old-fact/new-edge replay, all allocation kinds and
fair scheduling. Its AI intrinsic must become an injected provider. Its
`PtaQueryAdapter` and object/scalar conversions belong in composition and carry
source/model/policy/state identity and coverage. The abstract engine must not
receive direct access to native private state.

The current local AI engine borrows native objects and predecessor query state.
It remains explicitly `native_then_ai` while independent AI gains source-backed
definitions, call entries/exits, shared heap/cells and SCC dependencies. Shared
domains hold values and stores without importing a concrete engine. Shared
semantics hold name/binding/protocol/completion plans; backend constraint
generation and sequential state execution remain separate implementations of
those plans. No giant shared transfer hierarchy is required.

`AnalysisSession` owns mutable worlds, object arenas, registries, caches,
statistics and queues. The new execution path does not use World. Legacy
native execution may be process-isolated during transition; results freeze
decoded identities before the process exits. The package registry built by
`Analysis.__init_subclass__` is compatibility machinery, not the new scheduler.

Public contracts are generic and frozen by SPEC/RESULT owners. Their minimum
identity is source bytes/decoded source, module, stable node/region, finite
context, profile, model/policy and state/dependency versions. `Outcome[V,S]`
keeps completion channels distinct. `ProcedureSummary[V,H]` carries input,
full return, effects, fresh/frame relations and dependencies. `EngineSnapshot`
is a frozen result, never an exposed mutable SolverQuery. Composition consumes
CandidateAnswer(values, coverage, version, provenance) and injected providers.

Migration order is contracts/results/session facade, one independently checked
vertical path, caller migration, then old implementation retirement. Existing
dataflow/security clients stay available. Existing public imports are thin
delegates with a documented deprecation period. The core never calls back
through an old shim. Builtin names needed by closure belong in models so
frontend construction no longer imports the native builtin implementation.

The module map records each old symbol set, destination, responsibility,
consumers and acceptance. New owners update rows after moving implementation;
the map is not an excuse to call a rename a semantic fix. Before RELEASE-01,
every active row must resolve to one implementation and relevant callers must
pass their tests.

`scripts/refactor/check_architecture.py` inspects all static imports, including
function-local imports and literal dynamic imports. It detects engine cycles,
domain/contract reverse dependencies, optional consumer imports and duplicate
closure implementations. Migration mode reports legacy debt; `--release`
enforces it. Its mutation self-test proves the negative rules detect examples;
it does not substitute for the full Python dependency semantics or manual
review of nonliteral dynamic loading.
