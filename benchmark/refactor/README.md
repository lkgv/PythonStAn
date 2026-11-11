# Refactor A–E experiments

Run the same frozen input, entry contract, profile, queries, and resource limits
under five actual modes: A Native; B Native with AI intrinsics; C Native followed
by AI; D independent AI-primary; E AI-primary with the incremental inclusion
oracle. D and E install failure sentinels for the legacy full Native predecessor.
E intentionally measures its incremental oracle work.

The manifest contains controlled source/client fixtures, a generated 25-module
package, and the original 14 pinned published runtime-package trees. The small
generated package has 73 source lines and is a plumbing/control fixture, not an
industrial-scale project. Corpus package trees retain bundled tests and
migrations. Distribution files outside the declared runtime packages are not
application roots. External dependencies are never installed, imported, built,
or executed by the experiment tools.

Distribution exclusions are rooted glob patterns, so an excluded distribution
`tasks.py` or `tests/` does not drop a package's own same-named files. Default
component exclusions are disabled for this frozen corpus protocol. The final
public API inventory must equal every pinned runtime Python source byte.

Prepare the original pinned corpus archives in a directory and run:

```bash
python benchmark/refactor/runner.py \
  --stage preliminary --case tiny_identity --case medium_static_package \
  --archives /path/to/corpus_archives --cache /path/to/cache \
  --output docs/refactor/evidence/preliminary
```

Every run requires a new empty output directory. An official run also requires a
clean committed executable tree and its exact commit:

```bash
python benchmark/refactor/runner.py \
  --stage official --expected-commit COMMIT_SHA \
  --archives /path/to/corpus_archives --cache /path/to/cache \
  --output docs/refactor/evidence/official
python benchmark/refactor/aggregate.py docs/refactor/evidence/official
```

The archive digest and every selected runtime Python file are checked against
the actual archive. Each completed public result is also checked against the
frozen input inventory. A changed cached file is reported; it is not silently
overwritten. Each row retains its request, complete or partial stdout, stderr,
process receipt, actual results/error and executable identity before and after.
All 90 expected rows remain in the denominator, including preparation failures,
unsupported inputs, timeouts, memory stops, internal errors and missing data.

The fixed protocol allows 10,000 charged engine work units, an eight-second
global analysis timer, 1,536 MiB address-space/tree-RSS limits and a 15-second
outer process safeguard, with at most two analysis workers. The outer inclusive
clock includes process start and JSON export in addition to analysis. RSS is
sampled every 50 ms over the process tree; brief unsampled peaks remain a
measurement limitation. Kernel self/child peak RSS and measured worker/child CPU
are retained separately. CPU is sampled before final result JSON serialization;
complete export-inclusive CPU remains unavailable/null. Source preparation,
driver construction, solving, oracle and adapter
clocks are recorded; nested phase times must not be added twice. Work units are
engine-specific and do not independently establish speed or precision gains.
Missing counters remain null rather than becoming zero.

Queries and literal reference facts are selected from pinned source before
predictions. Pure-module literal hits are a bounded sanity/usefulness measure.
Other lexical literals may depend on source execution paths or external
behavior and are labeled separately. They are not whole-project call-graph
gold, arbitrary application-request coverage or a soundness proof. Parsed files,
declared initialization roots, analyzed units, published exits and query answers
are separate fields. `REPORT.md` and `summary.json` are regenerated from raw
records, and version/input/denominator checks are recomputed by the aggregator.
