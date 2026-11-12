# Reviewed boundary follow-ups

Reviewer: requirements_audit. These are separate follow-on requirements; this file
does not promote the partial TYPE checkpoint or add a completed task label.

## Resolved narrow producer issue: unused prepared oracle update versus later fact growth

The previous standalone IncrementalCandidateOracle admitted this sequence:

1. Publish cell c = 1.
2. Prepare an unused detached update with an empty address substitution.
3. Publish c = 2; candidates contain both 1 and 2.
4. Commit the previously unused update; candidates now contain only 1.

The reviewer independently reproduced the fact loss. Issuer binding and at-most-once
publication prevent a foreign issuer or replay of an already consumed update; they
do not detect a never-consumed snapshot whose input facts have since advanced.
Preserve a producer/input generation in the prepared token and reject or explicitly
reprepare a stale update, with honest accounting, or state and enforce a no-mutation
transaction precondition. A new control should publish a late real fact between
preparation and commit rather than only recommitting an already consumed token.

This is a component lifecycle/version requirement. The currently serialized real
host transaction has no demonstrated interleaving, so it is not presented as an
ordinary Source-program failure or a reason to rewrite the accepted partial
checkpoint. The low-contract review explicitly excluded arbitrary out-of-order
uncommitted updates. Evidence is
artifacts/next/evidence/reviewer-W1/low-contracts-recovery/stale_unused_update_probe.json.

The separate finite record-field completeness concern is already guarded by
test_graph_transform_contracts.py::test_projection_covers_every_actual_record_field,
which passed in the independent 60-test run. The current 45 projections also match
the reviewer's independent audit. It is not an outstanding follow-up.

The producer-generation and actual host pre-publication correction has now passed
independent review for its stated narrow scope; see TYPE-01-oracle-coherence.md.
The historical red witness is retained. TYPE-01 remains unqualified.
