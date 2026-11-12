# Checked summary provider runtime port

GuardedDispatchProvider now consumes SummaryApplicationContext. The actual driver
constructs it at each provider call. It exposes readonly live function/module/AST
views, stable policy/model/scenario identifiers, live epoch/remaining-work reads,
and narrow charge/event callbacks. It carries neither the solver nor
ValueOperations. Setup billing retains a private opaque marker rather than a
Python numeric id, preventing identity reuse without retaining the driver.

The provider constructor only assembles data. The sole production factory calls
prepare() explicitly after construction. Successful preparation is idempotent;
reentrant/running, failed and interrupted preparations are explicit states and
cannot silently publish a ready provider. Errors propagate. Repeated preparation
after epoch revocation does not resurrect authority.

Epoch preparation returns UnchangedSummaryEpoch or RevokedSummaryEpoch. It bills
and allocates detached records before publishing. Commit transfers the prepared
maps by assignment. The revoked variant is an internal ownership-transfer builder:
its outer dataclass is frozen, while its detached maps/statistics are not
claimed to be deeply immutable. Public proof records remain deeply frozen JSON.
The legacy standalone invalidation helper receives only a narrow charge port.

The actual candidate parser, independent source grammar, checker, evidence guard
and application implementations are typed at their source. Candidate data still
has no authority. Checker and coverage stamps are issued and verified by the
same independent implementations. DispatchApplication's explicit wire boundary
uses immutable JSON record mappings, with validation before composition consumes
text/object identities. Source grammar, byte/vector limits, checked denominators,
proof/coverage statuses and candidate wire formats are preserved.

Measured differential evidence compares isolated exact44d13b1 with the current
implementation: 48 candidate rows and 432 application rows match exactly across
four literal payloads, precise/coarse proposals, six proposal variants, three
work caps and four requested properties. Comparison retains status, diagnostics,
coverage, proof status, checked work, function denominator, certificate digest and
all application values/residual/frame facts. Private randomized stamps and wall
time are outside these deterministic records. This finite preservation check is
not a general semantic proof. No L2 semantic repair or real LLM execution is part
of this typing slice.

PartitionToken stores the actual ObjectId, with a separate optional display_name.
Public handler labels stay unchanged. Remapping transforms the actual reference
while retaining its display; overflow retains (handler, display) pairs. The opaque
token digest intentionally changes to include actual identity and display data.
The dictionary and checked-summary producers now pass real refs, without turning
qualified display names into fake ObjectIds.

The verification grammar/application functions and provider constructor/prepare
exceed readability review thresholds. They remain explicit finite syntax/vector
passes so coverage, seal issuance and resource ordering can be reviewed together.
Mechanical formatting of the three previously unformatted verification modules
is separately saved with executable AST equality; typing and the constructor
execution split are separately described here.

Current evidence is under `artifacts/next/evidence/N4-TYPE-01/recovery-20261112/`:
79 combined existing/context/relation integration controls; 49 provider controls including
six new purity/lifetime/budget/transaction tests; five owned provider/verifier
modules with zero strict/Any-expression diagnostics. Imported-tree debt remains
nonzero. Independent provider review and parent TYPE/COMP/verification gates are
pending; none of those parent cards is marked verified here.
