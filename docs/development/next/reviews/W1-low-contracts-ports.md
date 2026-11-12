# Actual low-contract ports

The recovered implementation uses one finite budget description, one checked engine snapshot, and one issuer-bound prepared oracle token. The historical 25 budget defaults and accepted ranges are unchanged. The snapshot owns its immutable JSON once; exported JSON is independent. `ValueFacts` resolves the historical `values` attribute collision while preserving key access and implementing genuine `Mapping.values()` without copying the record.

The old unused generic Engine/WorkLedger/Outcome/ProcedureSummary family was retired; the canonical state Flow union carries actual completions. `GuaranteeLedger` now stores the real snapshot status dimensions; metadata construction does not establish property verification. The new external `EngineSnapshot.from_wire` rejects incomplete or malformed snapshot records.

The real inclusion oracle prepares a detached token with read-only work accounting. The host publishes its address epoch before the same token commits. A committed token cannot overwrite a newer update when retried, and the compatibility commit entry point rejects another producer's token. Actual copy/load/store edges remain on the explicit incremental channel. A candidate-only provider remains valid through address coarsening.

The 10-file normal-import strict scan has zero owned diagnostics and exposes 1,004 imported diagnostics in 19 files (underlying exit 1). Ruff and formatter checks pass. The focused existing/new boundary matrix passes 110 tests. Owned hashes stayed stable during all checks. Exact commands, hashes and raw outputs are under `artifacts/next/evidence/N4-TYPE-01/low-contracts-recovery/`; the tracked summary is `low_contracts_ports.json`.

This is a dependency-port handoff for independent review. It does not verify TYPE01/TYPE02, resolve frozen semantic audit reds, or qualify a release. Root owns the broader driver lifecycle/publication integration; the API owner installs the known Native snapshot producer explicitly.
