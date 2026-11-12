# Independent prepared-oracle coherence review

Reviewer: requirements_audit. Disposition: accepted for the actual incremental
oracle producer API and serialized host object-address transaction. This is separate
from the earlier low-contract and dictionary reviews, and does not qualify TYPE-01.

The reviewer previously reproduced a real component fact-loss sequence: prepare an
unused detached update, publish a newer fact, then commit the unused old update. The
old token replaced the live kernel and discarded the late fact. That historical
witness remains preserved; at-most-once consumption alone did not prevent it.

The current producer captures its actual kernel identity/revision, bind/subscription/
call generation, source/profile/policy/model identity, operations/policy/arena
identities and source epochs before invoking a charging callback. Owned address
substitution and arena-alias snapshots are charged before cloning. Validation after
each callback rejects live producer growth before another clone/iterator advance.
The detached update rejects changed source inputs, including topology-only mutations
which leave the public fact version unchanged.

Pre-publication validate requires the original object-address arena aliases and
epoch. Commit permits the unchanged arena or the exact prepared object-alias
quotient at the next epoch, while still checking non-arena source stamps. The
actual driver invokes validate immediately before publishing the arena or any new
host roots; its publication path consists of prepared assignments before token
commit. The control interleaving late real fact growth with host preparation now
rejects without changing the host arena epoch or units identity.

The independent combined run passed 361 tests in 18.51 seconds, including all fifteen
new coherence controls and the existing epoch/semantics/composition controls. The
source and caller hashes were stable for that execution. An independent ordinary-
import strict scan reports zero diagnostics in the two oracle modules and new
test module; 311 imported diagnostic records remain visible. Ruff passes.

This is not a proof of arbitrary concurrent publication, direct mutation of exposed
internal collections, every IdentityArena field, or unrelated producer transactions.
The accepted behavior is the real producer methods and current serialized host
object-address publication channel exercised here. Candidate coverage remains
partial and does not grant exclusion or verification authority.

Evidence:

- artifacts/next/evidence/reviewer-W1/low-contracts-recovery/stale_unused_update_probe.json (historical red witness)
- artifacts/next/evidence/reviewer-W1/namespace-dictionary/caller-fixed-receipt.json (361 runtime controls and caller hashes)
- artifacts/next/evidence/reviewer-W1/oracle-coherence/strict-receipt.json

Reviewed token source hashes:

| File | SHA-256 |
| --- | --- |
| pythonstan/analysis/composition/oracle.py | 8640c5aed5d31f3209c180701185ebbb10248f9a34daee6a3cb5fc1a2f6ef722 |
| pythonstan/analysis/contracts/oracle.py | 22258ab654bb7da049258d302312a5fd2860827dda4e757b343ef713ab42412c |
| tests/regression/audit3/test_oracle_preparation_coherence.py | 5b4fa15798eceaad8363418dc8fd1aec75f90a92f413a8c8c745a8db2071bfa0 |

Actual driver tested SHA-256: 246e8f38890cb6368cb098c8c1c1a6ff395f79a0c58148d821c250f788c47b00.
