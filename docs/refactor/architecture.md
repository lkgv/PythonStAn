# Architecture and incremental migration contract

This is the ARCH-01 target map frozen against the imported archive tree. It is
not a claim that 150 mapped modules have already moved. Mechanical relocation
and behavior changes belong in separate commits, and release checks enforce
all surviving legacy debts.

The active frontend uses `analysis.closure.ClosureAnalysis`. The old closure
imports delegate to that owner, and `scripts/do_closure.py` uses the maintained
source frontend pipeline. Seventeen unused `deprecated_ai`, transform ICFG and graph ICFG
prototype files passed a zero-consumer import audit and were preserved byte for
byte outside the installed package under `legacy/prototypes/20261111`. Their
manifest is historical evidence, not an implemented analysis or import shim.

The native solver maintains a dynamic inclusion-constraint/PFG fixed point.
Preserve delta delivery, old-fact/new-edge replay, all allocation kinds and
fair scheduling. Its AI intrinsic uses an injected provider. Its
`PtaQueryAdapter` and object/scalar conversions live in composition and carry
source/model/policy/state identity and coverage. The abstract engine must not
receive direct access to native private state.

The old `AISummaryEngine` is a compatibility adapter into the same source-backed
abstract Driver and Transfer used by AI-primary. Captured original source,
lexical entry, arguments, full heap/cells and completion channels cross this
boundary. Source-less private TAC execution and retired nondefault budgets
reject explicitly. This adapter no longer owns a second Python interpreter.
The public `native_then_ai` mode retains a Native prepass and reports its
candidate coverage separately from the shared abstract engine's final roots. Shared
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
dataflow/security clients stay available. Maintained old public imports are
compatibility delegates; historical prototypes and private interpreter methods
are not maintained imports. The core never calls back
through an old shim. Builtin names needed by closure belong in models so
frontend construction no longer imports the native builtin implementation.

The module map records each old symbol set, intended destination, responsibility,
consumers and acceptance. New owners update rows after moving implementation;
the map is not an excuse to call a rename a semantic fix. Before RELEASE-01,
every active row must resolve to one implementation and relevant callers must
pass their tests. `scripts/refactor/update_module_inventory.py` preserves the
150 original target rows and adds a separate factual implementation record.
A real whole-module alias, retained original implementation, absent destination
and noninstalled historical archive have distinct statuses. The existence of
an intended target does not prove that an old implementation delegates to it.

`scripts/refactor/check_architecture.py` inspects all static imports, including
function-local imports and literal dynamic imports. It detects engine cycles,
domain/contract reverse dependencies, optional consumer imports and duplicate
closure implementations. Migration mode reports legacy debt; `--release`
enforces it. Its mutation self-test proves the negative rules detect examples;
it does not substitute for the full Python dependency semantics or manual
review of nonliteral dynamic loading.

Native workers receive immutable decoded `SourceModule` snapshots before TAC
lowering. File encoding, source text, module name, package and path metadata are
therefore captured once; nested functions retain the original module snapshot.
The worker seeds declared finite scalar alternatives before constraints and
charges each actual publication to the shared work ledger. Native cannot
represent typed scalar top exactly, so it publishes a covering unknown and an
explicit projection obligation. A query with pending empty points-to facts
remains unavailable. A Native synthetic-method option expands only the Native
stage and records the actual roots and synthetic-self argument contract; it
does not establish coverage for arbitrary external requests or AI execution
of those extra methods.

Current author evidence is under `evidence/20261111/recovery-native`: the
source-stable 102-case gate covers all 33 Native declarations (31 historical
fields and two heap controls), 19 context policies, source input boundaries,
shared-budget intrinsics and architecture negative fixtures. The release
architecture checker reports zero findings without exemptions. These scoped
results remain separate from independent review, whole-repository release
acceptance and semantic coverage claims.
