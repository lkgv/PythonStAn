# W0 independent semantic baseline

Task: N4-BOOT-02. Implementation complete; independent review accepted in
`reviews/BOOT-02-semantic.md`. The coordinator records the central BOOT-01
dependency before changing the task to verified.

The source baseline is `e8d9e0d3d7cdf540622537f5af0490522edde91c`, tree
`13a79f550daf75445f9529e5d5a89d70adabb1c6`. No production semantics were changed
to establish this baseline. The frozen registry is
`tests/regression/audit3/semantic_manifest.json`, SHA-256
`16771ac12bb1267a032fb004c98b47680ee938fcf95a5f639eac5549f159e66b`.

The 52 handcrafted source cases include the previous 36 audit probes and 16
independent variants or positive controls. Six additional offline L1/L2 cases
cover multiple source call occurrences, function exits, heap slots and source
reads, a dominated policy proposal, and the useful policy control. The original
A01–A18 references and source gold remain in force. Their old status strings
are retained only as historical labels; the central stage rerun supplies the
current nonregression evidence. Domain/native obligations have their own
component/API registry and remain separately attributable.

CPython 3.12.14 verifies all 52 independent source expectations. The strict
analyzer gate has 19 passing and 39 failing rows across 58 cases, with exit 1.
This freezes the current red denominator. The separately green oracle tests
check concrete expectations, stable source/gold identities, nominal scalar
sorts and rejection of missing, Top or weakened mandatory results. They are
not a passing product semantic gate. Neither suite establishes arbitrary
Python soundness.

Independent review tightened the checker before approval: exact function scope
sets, all bulk outcome scopes, useful scalar/fresh-box alternatives and bulk
returns must agree; L1 checks the two independently stated key/handler pairs
and typed decision fields, rather than a row count or text substring. The
required-open bytes case checks propagated normal/raise alternatives,
obligations, havoc, callback and exception effects. Wrong two-pair relations,
vacuous function exits, and Open without residual effects are negative guards.
Published Open residuals are checked here. Actual downstream heap/cell havoc
propagation remains protected by the retained
`test_unknown_dynamic_code_havoc_is_consumed` and
`test_unknown_callback_havocs_reachable_closure_cells` tests in
`tests/refactor/semantics/test_gold.py`, plus the Native unknown-effects suite.
The integrity/oracle suite now contains 69 passing tests. These changes did not
alter the 52+6 source/input/gold denominator.

| Finding | Current disposition | Scope of evidence |
| --- | --- | --- |
| AS-01 | still_present | Keyword/starred order; expansion and error-prefix variants; positional control preserved |
| AS-02 | still_present | Missing local/module/nonlocal and repeated deletion; bound-delete control preserved |
| AS-03 | still_present | Sequential effects, nested iterables, false filter; pure and scope controls preserved |
| AS-04 | still_present | Typed string unary exception; literal and integer controls preserved |
| AS-05 | still_present | Scalar iter/list/dict/next/star errors and iteration prefix; valid list control preserved |
| AS-06 | still_present | Builtin/tuple/invalid classinfo and ordered invalid tuple tail; source-class control preserved |
| AS-07 | still_present | Default four-level sum and heap recursion targets fail; loop and constant-recursion controls preserved |
| AS-08 | still_present | Repeated truth protocol loses useful precision; exception-prefix control preserved |
| LV-01 | still_present | Two/three handler callsite occurrences lost with L2; one-call occurrence control preserved |
| LV-02 | still_present | Requested function exits and bulk query scopes disappear; baseline contrast retained |
| LV-03 | still_present | Two-pair useful baseline replaced by four-pair, costlier candidate; four-to-two gain control preserved |

AS-07 is a useful convergence/precision obligation. AS-08 and LV-03 are precision
or usefulness defects, not newly asserted concrete underapproximations.
LV-02's explicit unavailable answers are honest but lose requested capability;
an answered bulk empty collection does not explain the lost function scope.
LV-01 does not disprove the separate return-relation certificate.

The starred-expression versus iterable-expansion and `**` error timing cases
are explicit CPython 3.12 order-profile targets. Context-zero recursion requires
finite non-Top inclusion instead of imposing the exactness of the default
policy. These distinctions were made before any production repair or policy
tuning. Future changes to the frozen denominator require an explicit reviewed
contract revision; deleting or downgrading a case fails the runner's hash gate.

Run from the checkout using the locked development environment:

```bash
python -m pytest --import-mode=importlib -p no:cacheprovider -q \
  tests/regression/audit3/test_semantic_baseline.py
python tests/regression/audit3/semantic_oracle.py --check concrete \
  --output artifacts/next/evidence/N4-BOOT-02/concrete.json
python tests/regression/audit3/semantic_oracle.py --check analyzer \
  --output artifacts/next/evidence/N4-BOOT-02/analyzer_red.json
```

The final command intentionally fails until mandatory repairs pass. It is not
an xfail, skip, accepted unsupported case, or expected-failure wrapper. Raw
stdout/stderr, per-case structured results, source/interpreter identities,
commands and exit codes live in the ignored evidence directory. The two new
Python helpers pass Ruff and mypy strict with explicit-package-bases and the
additional explicit/unimported Any checks. No real LLM or credential is used.

The helper exceeds the module-size review trigger because it includes the
three isolated subprocess programs and the finite acceptance predicates in one
auditable test-only file. It contains no production transfer implementation;
splitting it solely to reduce its line count would add import/launch paths
without reducing the trusted test surface. This is a review exception, not a
general exception for production modules.
