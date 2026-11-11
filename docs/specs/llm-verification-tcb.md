# LLM proposal and verification trust boundary

L1 and L2 providers are optional and default off. The shipped L2 providers are
offline deterministic, recorded or adversarial test implementations. They
return candidate data only. No external model is needed for CI or these
experiments, and no source comment grants tool or verification authority.

The unchanged `closed-call-effects-v2` checker now lives at
`analysis/verification/closed_effects/checker.py`. The old checker module is a
thin module alias so public and private test imports share one implementation.
Its source/profile version and finite call/raise-prefix/exit guarantee are
unchanged. It cannot prove scalar returns, arbitrary heap effects, event order,
termination or unrestricted Python. The old 38 offline probes are reclassified
against the new executable identity; their pass count is not a soundness score.
Its legacy inference and checking paths share `_Validator`, `_Transfer` and
the modeled event semantics. That shared implementation is explicitly inside
the TCB: agreement between the two paths is not an independent semantic proof.
The legacy driver keeps candidate-check status, fallback-used identity,
baseline-inference status and baseline-check status separate. Rejecting a
candidate and accepting a separately inferred fallback does not validate the
rejected candidate. The migration preserves those fields and their behavior.

For guarded-dispatch, trusted code consists of source parsing and identity
hashing, finite source-obligation generation, the source refinement checker,
sealed point-input coverage constructors, applicability checking, capture-free
fresh/frame application and the composition conversion into engine values and
stores. Python's parser/AST representation, cryptographic hash implementation,
immutable wire freezer and the stated builtin dict/string semantics are also
part of the host model. The core engine/store and its coverage of the modeled
pre-existing frame are required by the bridge; checker acceptance does not
repair an unsound arbitrary fallback engine.

Candidate parsing, identity metadata, JSON wire helpers and syntax node hashing
are shared data infrastructure. The L2 proposer and checker do not share an
abstract execution transfer: the proposer guesses input/box relations from
return syntax, while the checker symbolically checks every source statement,
binding, function and arm. The proposer may be wrong or malicious; source
refinement and guard/frame obligations still control acceptance.

Integrity stamps are in-process host authority, not portable proof objects.
They detect replacement of validated immutable records. They are not a
sandbox against arbitrary trusted-host Python execution. Source code and LLM
JSON have no access to these host objects. Portable caches store source-bound
candidate data and must invoke the checker again.

Independent tests cover wrong handlers, omitted callees, hidden writes,
recursion/loops, source/IR/model/policy/table/state changes, partial worklists,
unproved bottom, later keys/payloads, half-checked vectors, crafted authority
records, legal coarse summaries, allocator capture and missing frame bounds.
The independent fresh-allocation review found and repaired a real capture bug;
red and corrected probes are preserved. Passing these finite tests supports
the declared implementation scope and does not prove all Python sound.

Per-property verification records include their profile, point, input bound,
frame bound, versions and closed obligations. Runtime aggregation may say
verified only when at least one property is explicitly requested and every
requested property, including its requested module/point scope, is verified.
Convergence, semantic coverage and property proof remain separate axes.
