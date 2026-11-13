# Rebuilt abstract value wire contract

The v1 record is a union: tagged constants, integer interval/parity, string constants together with prefix/suffix/conjunction patterns, typed tops, global top, possible unboundness, opaque reference IDs and may relation tokens. Global top does not erase reference, relation or boundness obligations in the internal Value order. Floats preserve a single zero sign; both zero signs and nonfinite factory constants use a covering float top.

For complete records produced from Value, decoding preserves the Value facets. The enclosing heap is a separate parameter of reference interpretation; exported reference metadata explicitly says presence and freshness were not exported. Decoding never creates a heap entry, alias certificate or analysis fact in a running store. Handler IDs and optional presentation names are separate fields, including the case where a presentation string equals the ID.

A partial record denotes all complete records compatible with its retained fields. It does not generally admit a single representable internal Value upper bound: arbitrary omitted object IDs and relation tokens cannot be reconstructed. Therefore restore_value returns a diagnostic retained_value with complete/losses/reference_info; its value property, require_value and decode_value reject every incomplete, legacy, unknown-extension or omitted-field record. Diagnostic fragments must not be treated as usable analysis Values. This replaces the initial rebuilt candidate that incorrectly used a scalar Top alone for projection loss.

Future major versions are rejected even in nested item and slot values. Contradictory raw/tagged constant presentations are rejected. A complete record whose bottom marker contradicts its reconstructed union is also rejected in both directions. Missing required fields and unknown nested union fields are explicit losses. This compatibility policy preserves inspection of older records without granting silent closed reconstruction.

The current finite controls are regression evidence, not a proof for arbitrary Python programs. The frozen domain/semantic oracle files and budgets remain unchanged.
