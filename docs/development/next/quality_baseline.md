# N4-QUAL-01: initial executable quality baseline

The baseline is commit `e8d9e0d3d7cdf540622537f5af0490522edde91c`, tree
`13a79f550daf75445f9529e5d5a89d70adabb1c6`. Production source was unchanged
when the baseline was captured. This delivers Q0 tool adoption and debt control;
it does not establish the final runtime typed frontier.

| Scope | Actual result |
| --- | --- |
| Production inventory | All 305 Python files in `pythonstan` and `pythonstan_inspector` |
| Ruff formatter | 270 files require formatting; underlying tool exit 1 |
| Ruff lint | 12,477 existing diagnostics; underlying tool exit 1 |
| mypy strict | 10,349 existing error diagnostics; underlying tool exit 1 |
| Import Linter | 2 contracts kept, 0 broken; underlying tool exit 0 |
| Initial strict frontier | 13 clean package-marker modules; no claim of core strict adoption |
| Gate implementation | 11 quality modules/tests have zero Ruff/mypy diagnostics |
| Positive/negative tool controls | All 14 decisions match their independent expected result |
| Diagnostic/policy regression suite | 12 tests pass |

The aggregate quality command returns zero because no unapproved diagnostic was
added and the declared frontier is clean. Its JSON report explicitly retains
`whole_tree_mypy_clean=false`, all underlying exits, scan paths, source digests,
configuration digest, timings and tool versions. It never calls mypy failure
"all green."

`quality_debt.json` contains every counted diagnostic, grouped by logical module,
with path, qualname, code, stable AST anchor, message, owner and removal task.
Navigation line numbers do not participate in diagnostic identity. Repeated
definitions sharing a qualname are aggregated; an earlier definition cannot be
hidden by a later one. `quality_summary.json` is the smaller per-module inventory.

The debt file is frozen against the review base; the check command never rewrites
it. Resolved errors are reported separately. Baseline source/unit metadata,
duplicate diagnostic counts and tool identities cannot be reset during checking.
The versioned frontier can only expand. A semantic unit changed from its frozen
AST must have no diagnostics, and an edited file with formatter debt must be
fully formatted. Pure relocation needs a separately reviewed module mapping;
this initial implementation conservatively rejects unmapped moves with debt.

The initial capture receipt recorded 17 candidate frontier modules. Before the
first reviewed freeze, the four already-planned retired ICFG modules were excluded
from that candidate set; the final frontier is 13. The original raw receipt is
retained, together with the publication retirement decisions. All 305 files,
including those retired paths, remain in the diagnostic baseline.

The pinned tools are Ruff 0.17.4, mypy 2.4.1 and Import Linter 2.16. Poetry 2.2.0
generated the lock. `types-PyYAML` 6.0.12.20261111 supplies the existing YAML
boundary's third-party stubs. The existing mirror is retained; the four new direct
tool/stub dependencies use an explicit PyPI source because the mirror did not
resolve the new stub version. The lock pruned previously orphaned coverage,
pytest-cov, exceptiongroup and tomli entries and updated typing-extensions from
4.13.2 to 4.16.0 to satisfy the resolved toolchain. Runtime PyYAML is unchanged.

Run the gate from the checkout with the locked development environment:

```bash
poetry install --with dev --no-root --no-interaction
poetry check --lock
poetry run python scripts/check_quality.py --comparison-ref HEAD
poetry run python -m pytest --import-mode=importlib -p no:cacheprovider -q tests/regression/audit3/test_quality*.py
```

The CI workflow uses the pull request base or previous push commit to detect debt
resets and frontier retreat. It does not regenerate the baseline. Unknown tool
configuration, unmatched mypy overrides, reduced import protection, empty contract
execution, per-source mypy configuration, frontier suppressions and implementation
shadowing by `.pyi` cause failure. Mechanical formatter changes belong in their own
reviewable commit; this task does not mass-format production source.

`quality_review.json` records independent inspection and six deliberate bypasses
that failed before repair and pass after repair. The fixture suite proves the
tools reject ID swaps, raw requests, incomplete Flow handling, core Any, unchecked
summaries, reverse imports, missing public export/type markers and retired wheel
members. These are tool-effectiveness controls. Actual runtime adoption and its
negative tests remain N4-TYPE-01 obligations; actual candidate wheel qualification
remains REL-01. The current source wheel still lacks `py.typed` and contains the
17 retired modules, as recorded separately by BOOT-01.

New direct-SUT regression tests currently expose 29 strict call diagnostics at
legacy untyped production boundaries. Those diagnostics are separately retained
under BOOT-02 evidence and are an explicit N4-TYPE-01 bootstrap obligation. They
are not included in the 305-production-file debt denominator or described as
strict clean. No ignore or untyped wrapper was added to hide them.

Raw commands, outputs, receipts and red/green mutants are in the ignored
`artifacts/next/evidence/N4-QUAL-01` tree. Only reviewed summaries and the exact
debt/frontier data are versioned. This evidence does not prove analyzer soundness
or declare G0/G6 complete automatically.
