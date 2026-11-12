# N4-BOOT-01 independent review

Reviewer: requirements_audit (not the baseline author). Scope: source/package identity,
original stage cohort, finding provenance and traceability. This review does not qualify
semantic repairs, the whole W0 gate, formal performance results, or a release candidate.

## Verified observations

- The authoritative checkout is commit `e8d9e0d3d7cdf540622537f5af0490522edde91c`,
  tree `13a79f550daf75445f9529e5d5a89d70adabb1c6`. The committed delta from the
  design baseline `b19d98e0cdca8fd23d49ec82a526f814309edf75` contains only
  `docs/refactor/old_to_new_options.json`. The current canonical metadata and its
  documentation mirror are byte-identical.
- All 37 input handoff-manifest entries match their recorded sizes and SHA256 values.
  The separately supplied prompt, task board and code standard are byte-identical to
  their corresponding handoff members.
- The recorded stage command uses exactly the historical 14 path selections, including
  the three selected certified checker files. Its raw stdout reports `1001 passed in
  40.62s`; the receipt records exit 0 and total elapsed 41.2649 seconds. Stderr is empty.
  The receipt explicitly distinguishes its isolated pytest 7.4.4 environment from the
  earlier historical pytest 9.2.0 run. No historical green status was reused.
- The rebuilt baseline wheel SHA256 is
  `c3754da3f9d41975cb9aa72753299de3179a8fa6ce81e9323d52a7fdf62e374f`.
  Its full member set matches the raw publication receipt, contains 305 Python files,
  contains all 17 retired paths, and has no `py.typed` marker. Every wheel production
  Python/JSON/YAML member was independently compared byte-for-byte with `git show`
  at the recorded commit; none differed.
- All 17 source originals are byte-identical to their existing
  `legacy/prototypes/20261111` copies. A separate AST scan of 695 non-retired files
  under production packages, scripts and tests found no direct import consumer of
  the retired modules. This is a direct-import observation, not proof that arbitrary
  dynamic imports cannot exist.
- The original 46 and prior 22 task identities are mapped exactly once, all mappings
  have nonempty targets, and every target exists in the 43-card plan. Original cards
  remain pending current reacceptance.
- The output Mapping failure, unknown output-key `KeyError`, and silently ignored
  extra value-query field were independently reproduced. These agree with
  `artifacts/next/evidence/N4-BOOT-01/public_boundary.json`.
- EXP-01 through EXP-09 are explicitly marked as historical observations with current
  status inconclusive. They are not presented as rerun current-candidate experiments.

## Changes requested before BOOT-01 is verified

1. Correct or explain the configuration identity in `source_identity.json`.
   Its `pyproject_sha256` is
   `36469655bed978607b8682b6286137f3d0ddba35d197e3bf85a8aa450a38b01c`,
   while the recorded commit's `pyproject.toml` hashes to
   `eb3ef2d2d3869a822079bb9e393e8a3d76728094dc563150fd0e49dd17fd4221`.
   The lockfile hash does match the commit. Preserve the observed value and explain
   whether it denotes a later quality configuration; do not silently rewrite an
   observed receipt as if that configuration had been pristine committed source.
2. Finish finding-to-task traceability. MAINT-01 through MAINT-06 and EXP-01 through
   EXP-09 currently have empty `reacceptance_tasks` because their source finding IDs
   differ from the plan's umbrella labels. Add explicit alias mappings and responsible
   reacceptance tasks. Historical EXP rows may remain inconclusive and not rerun.
3. Merge the current semantic/LLM classifications before claiming all prior defects
   have current probe status. At review time AS-01 through AS-08 and LV-01 through
   LV-03 still say only that classification is pending. Update MAINT-02 by reference
   to the canonical AS-05 result rather than counting it as a second root cause.
4. Tighten source citations for structural findings. In particular MAINT-05 should
   cite the semantic service's unrestricted owner/driver access and forwarding
   implementation (for example `attributes.py`), not only `state.py` and a passing
   stage-test receipt. The stage suite does not itself prove those structural facts.

Disposition: **review / changes requested**. Source and package baseline observations
above are supported; BOOT-01 awaits the listed provenance/ledger corrections.
BOOT-02, QUAL-01, EVAL-01 and G0 require their own independent acceptance review.

Reproduction methods used: `git rev-parse`, `git diff --name-only`, `git show`, direct
SHA256 and ZIP member comparison, AST import scan, input-manifest comparison, exact
task-ID/set validation, and the public boundary probes. No production code was changed
by this reviewer.

## Re-review after corrections

The author retained the original configuration observation and documented its timing:
the initial empty dirty state was captured at clone, whereas the first stage run used
a tooling-only pyproject delta. A separate pristine detached worktree now has a clean
before/after receipt, committed pyproject/lock hashes, exit 0 and `1001 passed in 40.75s`
raw stdout (41.2496 seconds total). Its rebuilt wheel has the same SHA256 and member
set as the earlier baseline. `baseline.json` selects this pristine run as primary.

All 34 findings now have explicit reacceptance tasks. AS/LV entries point to existing
current `analyzer_red.json`/`concrete.json` evidence; MAINT-02 references the same AS-05
root cause and is not double-counted. MAINT-05 cites the actual unrestricted semantic
service implementation. Historical experiment rows remain explicitly not rerun.

Updated disposition: **N4-BOOT-01 baseline accepted**. The four changes requested above
are resolved. This accepts a reproducible baseline and its defect ledger; it does not
close any semantic defect, qualify the baseline wheel, or promote BOOT-02/QUAL-01/
EVAL-01 or G0 without their separate reviews. Reviewed identity remains the committed
`e8d9e0d` source; new implementation changes require their own evidence.
