# N4-DOM-01 scalar contract, 2026-11-13

The implementation at `pythonstan/analysis/domains/values.py` uses one facet decomposition before scalar transfer and truth evaluation. `max_scalar_pairs` now limits enumeration within a facet pair. It cannot suppress a different pair's normal result or exception.

## Value interpretation

The bound scalar payload is the union of the integer lattice, string language, finite booleans, finite floats, `None`, and each named sort top. Integer membership is the finite set when present, otherwise the interval restricted by parity. String membership is the union of constants, prefix languages, suffix languages, and prefix/suffix conjunction languages. Finite floats retain their signed-zero representation; nonfinite floats and conflicting zero signs use the existing float cover.

`ValueOperations.facets` provides the sole decomposition interface for these transfers. Bottom contributes no facet. The unbound marker is separate from runtime values and contributes no successful truth result. Tracked references and unknown payloads remain separate facets and retain engine obligations; relation tokens annotate payloads and do not create additional scalar members. Facet values need not carry those tokens to compute a builtin scalar result.

## Transfers

| Inputs | Normal cover when enumeration is unavailable | Exceptions or obligation |
| --- | --- | --- |
| Numeric +, -, * | Integer or float sort, according to the pair | Float conversion may overflow |
| Numeric /, //, % | Integer or float sort, according to the operator and pair | Division by zero; float conversion may overflow |
| Numeric power | Integer/float/complex covers as applicable | Zero division, overflow, value errors |
| Integral bit operations | Integer; bool for bool/bool bitwise operations | Shifts may raise value/overflow errors |
| str + str, str repetition | String language | Oversized repetition may overflow |
| String percent formatting | String language | Type, value, overflow errors |
| Equality / identity | Booleans | Opaque equality retains an operator obligation |
| Ordered comparison | Booleans for numeric/numeric or str/str | Known incompatible scalar pairs raise TypeError |
| str membership | Booleans | Other known scalar pairs raise TypeError |
| Opaque named sorts, references, unknown payload | Existing unknown payload | `unknown_operator` obligation; no fabricated definite TypeError |

Finite pairs use CPython builtin operators under the existing allocation guards. Resource guards append a cover for that pair and continue processing the remaining pairs. They do not return from the entire union transfer. Every pair's normal value is joined, and exception names are unioned. Known builtin failures stay explicit exceptions; unknown protocols remain engine obligations.

`scalar_unary` preserves numeric normal sorts together with errors from invalid scalar facets. String top produces TypeError without a fabricated normal value. Complex unary +/- retains a complex cover. The engine must keep its protocol-aware `not` path: the scalar API reports an `unknown_truth_protocol` obligation for opaque/reference truth rather than executing it. Non-`not` engine integration belongs to the SEM task.

Truth does not use whole-value enumeration. It examines each facet, including finite floats even when an integer interval or string pattern cannot be enumerated. The empty string is false; a nonempty prefix language is true. Reference protocols and their effects remain the engine's responsibility.

Wire string export always emits residual exact constants, including when prefix or suffix patterns coexist. Dropping those constants changes the represented union.

## Evidence and limits

The frozen domain subset had 8 failures and 3 passes at fed9659; it now has 11 passes. Generator and graph cases in that file belong to STATE and were not counted in this subset. The new 55-case scalar suite checks CPython-derived finite normal and exception witnesses, cap 1 and cap 8, default-cap exhaustion, unary errors, truth, resource fallback, Py_ssize_t repetition overflow, opaque sorts, and join/widen membership. The existing domain suite contributes 15 controls. These finite checks do not establish language-wide soundness or formal monotonicity for all abstract values.

Author strict typing, lint, source hashes and exact evidence paths are recorded separately. Independent domain witnesses are owned by the recovery reviewer. DOM-01 is not accepted by this document: shared graph-wide widening (cells, heap fields/defaults, and metadata Value leaves) remains with STATE, and public unary integration remains with SEM. Full quality and integration gates are owned by the root agent.
