# Narrow retired-module removal review

Reviewer: requirements_audit. Reviewed operation: remove the 17 already-retired
duplicate installable source paths listed in `publication_repair.json` while retaining
their archived source. This is not REL-01 candidate qualification.

Independent checks found:

- Every removed path is absent from the current installable source tree.
- Every archived copy is byte-identical to `git show 37f9a93:<original-path>` and
  matches its recorded archive SHA256. None of the 17 archived originals was lost
  or changed by this operation.
- A new AST scan across production packages, scripts and tests found no direct
  import or literal `__import__`/`import_module` consumer of a retired module.
  Arbitrary computed dynamic import targets are outside this static observation.
- The wheel under `artifacts/next/evidence/N4-BOOT-01/retirement-wheels` contains none
  of the 17 forbidden member paths. Its Python-file count happens to equal the old
  count because concurrent W1 work added modules; member identities, not counts,
  establish this narrow absence check.

Disposition: **accepted for the declared retirement delta**. The wheel reflects an
in-progress W1 working tree, so this review does not qualify its semantic content,
installed API, strict typing, py.typed, full member hashes or a same-version formal
experiment identity. `release_qualified=false` remains the accurate status.
