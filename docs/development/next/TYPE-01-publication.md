# Typed snapshot publication

This is a supporting N4-TYPE-01 slice, not whole-card acceptance. The source was
reconstructed after recovery to `735b9ff`. Checks from the lost working tree are
historical and do not qualify this reconstruction.

`analysis/engines/abstract/publication.py` consumes actual Flow/Store/Value
records through named publication inputs. It does not receive a driver or a
driver-shaped protocol. Its heap merge uses the genuine state copy/join methods
and run-local `ValueOperations`. Module-exit and procedure-return heap views,
per-outcome export timing, namespace-havoc query materialization, object
identities, calls, effects and ordered prefix events retain their prior scope.
The extraction retains the previous creation-only suspended-function summary
overwrite and dictionary-key wire scope; neither is a new semantic fix.

The actual caller must preserve this order:

1. Prepare the publication inputs and detached heap views.
2. Record each returned warning through the real driver. Duplicate warnings
   remain duplicate observation events even though obligations deduplicate.
3. Compute `publication_statistics(prepared, base_runtime)` and update the
   existing `driver.stats` map.
4. Invoke the actual candidate oracle's `statistics()`. It can observe those
   published base counters.
5. Capture the final runtime data and call `finalize_publication` with the
   actual post-oracle map as its required `published_statistics` argument.
6. Update the same `driver.stats` map with the final published statistics.

Finalization never recomputes base counters. Provider changes to those counters
and additional statistics survive; the original oracle/pta-work envelope is
still applied. Export/freeze happens once, after this statistics ordering.

The live Flow union deliberately distinguishes a statement/break/continue with
no value from an expression whose value is bottom and from Python `None`.
No-value outcomes expose `value_present: false` and `value: null`; valued
outcomes expose `value_present: true` and their existing value record. Outcome
field projection retains `value_present` with kind, exception and context.
The legacy module statement exit used a serialized placeholder (sometimes a
`None` scalar, sometimes bottom). Fresh replay parity permits that placeholder
to become explicit absence only for statement completions; a real return of
Python `None` remains a present scalar value and has its own control. The first
replay normalization admitted only bottom and failed on the legacy `None`
placeholder; that failed receipt is retained beside the corrected explicit rule.
Physical state owners/mutation revisions are absent from semantic wire
identity, while the existing logical unit summary revision remains visible.

The test scope includes seven completion variants, scope-specific heaps,
cyclic references, exact sequence shape restrictions, explicit havoc queries,
suspended creation summaries, warning multiplicity, effects, work/phase
statistics, root multiplicity, ownership independence and read-only output
projection. Actual driver controls separately check a candidate-only readonly
statistics observer, a provider that mutates the host statistics, and real
warning observation counts. No target Python execution or LLM call is used.

Fresh capture and check evidence lives under
`artifacts/next/evidence/N4-TYPE-01/publication-recovery/`. The pre-extraction
snapshots are parity evidence, not independent semantic gold. Final receipts
must retain whole-import diagnostic debt and identify the exact owned source
hashes; an owned zero-diagnostic scope is not a whole-tree clean claim.
