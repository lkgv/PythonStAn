# N4-API-01: actual resolved request path

The public boundary now resolves configuration, queries and output before
constructing a session. An output Mapping and the corresponding `OutputSpec`
select the same answers. Unknown output/query fields raise
`ConfigurationError`; value and return queries no longer discard extra fields.

```python
from pythonstan import Source, ValueQuery, analyze

result = analyze(
    Source("answer = 7", module="example"),
    config={"engine": "ai_primary"},
    queries=[ValueQuery("example", "answer")],
    output={"views": ["values"]},
)
answer = result.query(ValueQuery("example", "answer"))
```

`analyze` performs no printing or file writes. `Source` always means memory
source and does not inherit the current directory's configuration. `File` and
`Project` retain their documented discovery and initialization/explicit-entry
rules. Config-file relative paths, presets, overrides, empty explicit query
lists and output precedence remain covered by the API regression matrix.

The direct session constructor now accepts a `ResolvedRequest`:

```python
from pythonstan import AnalysisSession, File, resolve_request

request = resolve_request(File("app.py"), config="pythonstan.yaml")
with AnalysisSession(request) as session:
    result = session.execute()
```

Session construction only stores the resolved request and run identity.
`prepare()` inventories and parses source, validates input bindings, constructs
the source program and optional region plan, and orders independent scenarios.
`execute()` invokes preparation lazily, then the selected engine. The run clock
starts before request resolution, so configuration and source preparation use
the same global deadline. Actual configuration-resolution, source-preparation,
frozen-source validation and engine-pipeline durations are recorded separately;
idle between explicit preparation and execution only affects overall elapsed
time and the deadline. Timing scopes distinguish the engine pipeline from its
nested solver/provider spans and identify the pre-publication wall prefix.
Explicit preparation does not execute target code.

`ResolvedRequest` delegates its queries/output to the immutable effective config
so there is one canonical owner. `QuerySpec` is a closed union of value,
property and view records. The old `results.schema` path only reexports the
canonical classes/functions. The actual result path stores canonical records;
external `query`, output Mapping and legacy direct-result construction remain
checked adapters. Serialization reads detached facts and never starts analysis.

`AnalysisSession.from_snapshot(request, snapshot)` retains frozen source bytes
and discards the old parser cache. Source/module/path, requested entry sequence,
and retained inventory selectors must agree; changed roots or exclusions are
rejected rather than silently ignored. Explicit abstract bindings and domain
policy can change on a clean rerun. A matching file snapshot still works after
the original file is deleted. Its L1 consumer now constructs a resolved
rerun request, uses the remaining shared budget and closes the rerun session.
Changing L1 adoption policy remains a separate task; this migration preserves
the existing adoption behavior.

New-code JSON checks share one low-level codec and the existing snapshot
freeze/thaw semantics. It rejects nonfinite values, nonstring object keys,
reference cycles and excessive external nesting; shared acyclic aliases remain
valid. Published snapshots and query answers own immutable containers.

Validation in progress includes the existing API/L1/config/input matrix,
constructor source-I/O sentinels, output/query negative cases, result isolation,
read-only event serialization, and actual Session consumer type checks. The
positive consumer has no own diagnostic; the raw-request negative produces an
`arg-type` error requiring `ResolvedRequest`. Normal imports are retained and
all transitive debt is recorded separately.

Review triggers were considered explicitly. The abstract-run orchestration and
L1 rerun loop keep their shared deadline/work/reservation ownership in one
scope; further splitting is deferred to the corresponding control/verification
tasks. `OutputSpec` retains its existing constructor shape for compatibility,
while the new CLI argument record uses keyword-only construction. These are
review decisions, not linter exemptions.

Raw receipts are under `artifacts/next/evidence/N4-API-01`. Independent review
accepted this API checkpoint in `reviews/API-01-integration.md`: the API matrix
has 186 passing cases, Ruff format/lint pass, and all 36 checked API/port source
files have zero scoped strict diagnostics with normal imports. The full mypy
process still exits 1 for 1,782 imported errors plus one note; this is
not a whole-tree strict-clean claim. `API-01-acceptance.json` binds the exact
source identity and final receipt. Current code does not implement the separate finite prepared-state
client-entry executor, query-ledger closure, full-project usefulness or live LLM
evaluation, and does not claim those tasks complete.
