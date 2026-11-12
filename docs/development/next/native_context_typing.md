# Finite Native context key contract

The actual `finite_context_atom`, `_source_identity`, `_origin_sources`,
`ordered_binding_token` and their finite validators now return concrete recursive
key types. `SourceIdentity` is the finite source vocabulary; `FiniteContextKey`
also admits flat `FiniteContextAtom`, `OrderedBindingToken`, tuple and frozenset
keys. Runtime conversion still forgets object, scope and recursive context
identities. Bounded source-shape fallback is retained.

Structural data carriers remain supported through explicit read capabilities
and one `_decode_native_metadata` compatibility boundary. Its fixed literal
field reads accept stable, readonly `__getattr__` allocation/context pairs,
named descriptors, field pairs, origin arrays/wrappers and keyword `.items()`
providers. Finite legacy indexed sequences are accepted alongside iterables.
The decoder emits only validated finite sources, atoms, origins or bindings;
raw attributes never become typed engine/Generic contracts. Malformed private
descriptor projections reject before returning nonfinite values; normal public
atom construction already rejected those values. Cyclic origin wrappers reject
explicitly. This compatibility contract requires stable readonly field values
and finite origin/entry sequences. Casts and `Any` return annotations are absent.

`AbstractContext` has an invariant, unbounded item parameter. The old three-way
constraint excluded actual union and tuple inputs. `SummaryContext[T]` and
`ProtocolKeyContext[T]` retain the actual inner context parameter. The Native
boundary is the six concrete base families plus summary and protocol wrappers;
each wrapper has six explicit supported item instantiations. No wildcard type
argument is used at that boundary. Protocol import validation remains local to
avoid a runtime import cycle. The guard checks each known wrapper's eventual
native base family and rejects wrapper cycles. Standalone generic wrapper APIs
remain general; a wrapper around a custom underlying context is now explicitly
outside Native bridge admission. `wrapper-admission-red.txt` records the prior
incorrect admission, and the ordinary nineteen-policy and custom-base controls
record the corrected boundary.

Frozen `Ctx` has a separate covariant content parameter; nullable scope and
context represent actual constant/field producers. `Scope.context` is the
canonical Native family. `Scope.obj` is the genuine `AbstractObject` base because
real producers include generators, coroutines and instances as well as the old
function/class/module declaration. `Scope.new` admits the actual nullable module
parent. These are annotation changes; constructor, hash and equality bodies are
unchanged. Historical context-module object-class imports remain one-way
reexports.

The nineteen selector policies and existing scheduler algorithms are unchanged.
`HybridContext.append` now accepts the genuine base-contract single tuple
`(CallSite, receiver_or_none)` as well as its legacy two arguments. This is an
explicit compatibility extension: previously `SummaryContext(HybridContext())`
accepted at the Native boundary but its canonical tuple append raised
`TypeError`. The new typed argument dispatch preserves the exact legacy
algorithm suffix, hash, finite keys and valid two-argument results. Base,
summary, protocol and nested forwarding are exercised across all sixteen
call/object depth pairs and both receiver forms; legacy wrong arities retain
`TypeError`. `ProtocolKeyContext.__hash__` explicitly implements the same tuple
hash its frozen dataclass previously generated. Other old scheduler typing
debt remains outside this slice; this is not a whole-module qualification.

Evidence is under `artifacts/next/evidence/N4-TYPE-01/native-context/`:

- `context-before.py` is the exact handed-off helper implementation.
- `differential.py` compares shared real IR identities using separately built
  old/current records: 35 atom outcomes, 210 bounded source projections,
  24 origin projections, 24 ordered binding projections and 784 equality pairs.
  Values, repr, hash, rejection class and within-implementation equality agree.
- `approved-final-tests.txt` records 191 passing core, Native nineteen-policy,
  independent finite-key/native-admission and formal protocol controls.
  This run binds pre-whole-format `c996714...`; `whole-format-proof.json`
  establishes executable AST equality to `c794b98...`. Only eight class
  docstrings changed whitespace, with identical normalized text. Root's final
  matrix binds the whole formatted candidate tree. Independent review also
  reran all 191 controls on final `c794b98...`/`787ba364...` bytes and accepted
  this bounded scope; see `context2_independent_review.json` and
  `reviews/TYPE-01-native-context.md`. Whole-module and parent TYPE/STATE
  acceptance remain open.
- `tests-final.txt` records the broader run: 172 pass, 9 preexisting optional
  skips, 3 framework query-precision failures. All three failures reproduce with
  only the old helper bodies restored in a separate process; see
  `old-helper-three-failures.txt`. This establishes helper-change parity rather
  than claiming an unexecuted historical-HEAD baseline.
- The strict invocation retains all imported diagnostics and process exit 1.
  Only changed helper methods, the actual native guard, summary append and the
  explicit protocol hash are the scoped qualification target. Existing
  scheduler diagnostics remain visible. The changed `Scope.new` factory is also
  included, as are the actual metadata decoder and Hybrid append overloads;
  these have zero strict diagnostics. The untouched abstract append's
  docstring-plus-pass lint is retained with exact old-body comparison; there are
  zero new lint diagnostics in the changed methods. No frozen acceptance
  denominator or gold fixture changed, and no LLM was called.

The benchmark direct consumer now invokes explicit `provider.prepare()` after
its measured timer starts. The initial NEW-provider failure is retained. This
exposed a separate repeated optional import cost (68 versus expected 17 in the
simulated clock). Root's neutral context DTO extraction restored the unchanged
timing test.
The final benchmark-family/provider run passes all 22 controls. The benchmark
assertions, timing and result fields were not changed, and both intermediate
failure receipts remain available.
