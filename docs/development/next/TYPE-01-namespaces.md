# Actual namespace service contracts

This N4-TYPE-01 supporting slice types the complete existing
`NamespaceOperations` implementation. Its constructor receives the actual
`Transfer`, and its state, value, frame and metadata dependencies are the real
implementations. It adds no solver-shaped protocol, cast, unchecked getter or
parallel namespace model.

The key protocol returns
`tuple[list[tuple[Store, list[JsonScalar] | None]], list[Flow]]` through the
`NamespaceKeyCases` alias. A key list containing Python `None` is a finite
literal alternative; `None` in the key-list position is the existing non-finite
key set. `bindings` returns `dict[FieldKey, Value]`; read/write/contains and
namespace builtins/methods return the actual completion union. Object and cell
identities use their existing nominal ID constructors without changing display
strings.

Namespace owners are read through the real `NamespaceMeta` record or the
existing `Metadata.namespace_owner` accessor. This distinguishes an object ID
from a dictionary-view owner's `Value`, which shares the historical `owner`
field spelling. Current-state normalization calls the actual Store and value
operations directly. Oracle publications use the driver's explicitly installed
lifecycle channel, shared with the other typed engine consumers, rather than
guessing optional methods on a candidate-only provider.

Every former live Flow constructor now uses the real variants or the typed
`replace_flow` handler. The old four-argument constructor in `setdefault`
cleared continuation, cleanups and resume target. The replacement explicitly
resets those fields and recaptures the same Store handle; it does not silently
retain a suspended tail. Expression-only custom-hash results use the actual
`require_payload` invariant instead of reading `.value` from statement flows.

The focused validation consists of 66 existing protocol/namespace/external
module metadata controls and four new source controls. The new controls cover
missing and existing `setdefault` keys for globals and object dictionaries in
both `ai_primary` and `hybrid`, checking exact retained results, publication of
the new binding and closed-profile coverage. No target source or LLM is
executed by the new controls.

Normal-import strict checking includes the namespace implementation, its real
attribute and dictionary consumers, and the new source controls. Scoped zero
diagnostics do not qualify the unfinished whole-import graph or all of
N4-TYPE-01. Raw evidence, source digests and the formatter-equivalent pre-edit
baseline are under `artifacts/next/evidence/N4-TYPE-01/namespace-recovery/`.
