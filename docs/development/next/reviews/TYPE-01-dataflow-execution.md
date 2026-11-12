# Independent dataflow execution review

Reviewer: integration_review. Accepted narrowly for the registry-to-execution
boundary, opaque fact publication, compact solver injection and cooperative
subclass registration. This does not qualify unrelated legacy dataflow domains
or close TYPE-01.

The registry identifies an implementation without proving its generic fact
parameter. `DataflowExecution` is therefore an actual executable nominal
interface, while each concrete `DataflowAnalysis[Fact]` retains its own typed
worklist solve. A selected external solver is called at the compatibility
boundary; its two mappings are validated without copying their maps or facts.
The public generic driver subscription remains compatibility syntax and makes
no promise that arbitrary registered facts have that parameter's type.

Review found one new compatibility regression: an `Iterable` ABC guard rejected
a custom solver returning an integer-`__getitem__`-only pair, which Python's
original unpacking accepts. An independent actual-driver witness failed before
the owner reused the validated host iteration boundary and passed afterward.
The retained red is `dataflow-pair-red.txt` under
`artifacts/next/evidence/resume-integration-review/`.

The final independent selection passes 24 tests, covering that legacy pair,
ordinary/custom solver registration, generic subclass initialization, malformed
fact shapes, retained ordinary mutable facts, compact decoded views, custom
transfer fallback and failure/retry injection. Exact incoming/outgoing mappings
and their fact objects remain the published objects. `clear_results` replaces
owned publication containers while existing observers retain the old maps.

One inherited initialization issue remains outside this acceptance:
`DataflowAnalysis.__init__` still uses the historical class-based
`super(Analysis, self.__class__)` call and does not establish an instance
`config` field through `Analysis.__init__`. No new initialization guarantee is
claimed for that path. This was reported to the owner; it is not represented as
a new regression or silently corrected during the source freeze.

The final full frozen selections independently pass the original 1001 stage
tests and 4114 audit3 controls. Both receipts retain the complete Python source,
Inspector, scripts, tests and benchmark hashes and report no changed paths:
`artifacts/next/evidence/resume-current-final-20261112/`. They describe the same
795-file snapshot, with canonical manifest SHA-256
`1adcbbda27d36a8822178462ad8ed4343818e4166ed4ed695c3c02682db28d31`.
