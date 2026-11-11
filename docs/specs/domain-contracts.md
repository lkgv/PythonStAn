# Domain contracts and compatibility projections

The target scalar domains are engine-neutral. New `Value` objects form a tagged
union of scalar alternatives and opaque object identities. Legacy `AbsVal`
objects now carry `scalar_kinds` and `boolean_values` so that a truthiness fact is
not exported as a Boolean return value.

## Scalars

`IntLattice` has four disjoint forms: Bottom, exact nonempty finite constants,
interval restricted by parity, and Top. A finite constant set is exactly its
members; cached bounds do not fill holes. An interval has no finite enumeration
flag. Its concretization is the integers within the bounds satisfying parity.
An unstable bound widened to infinity clears finite constants. Raw constructors
normalize the same representation invariants as factory methods.

`StrLattice` denotes the union of exact constants, prefix languages, suffix
languages, and conjunction languages. A conjunction `(p,s)` denotes strings
satisfying both `startswith(p)` and `endswith(s)`, including overlapping ends.
This representation is closed under finite intersection: comparable prefixes
and comparable suffixes select the stricter constraint. Incomparable prefixes
or suffixes intersect to the empty language. `meet` and `narrow` use that exact
intersection. `leq` is a sound, possibly incomplete inclusion decision.

String widening covers both operands. LCP/LCS groups never justify discarding
isolated constants: unmatched constants survive, or the operation returns
covering Top when their residual capacity is exceeded. Small exact sets and
representable common prefixes retain useful precision.

For tagged legacy values, actual Boolean values are returned only by
`get_bool_values()`. `get_bool_lattice()` is the historical truthiness facet.
For example, integer `7` has true truthiness and no Boolean-value alternative;
`7 | False` has unknown truthiness and actual Boolean projection `{False}`.
`None | 'x'` keeps both alternatives after reduction. A scalar fact may constrain
other facets only when it holds for the complete input alternative set; facts
from one arm of a mixed union cannot delete another arm.

A raw untagged legacy `AbsVal(...)` has `scalar_kinds=None`. Its missing facets
are a conservative compatibility projection of old constraint products. It
cannot establish an exact scalar kind. The new primary engine does not use
this constructor or the legacy product interpretation. No claim of complete
arbitrary-Python semantics follows from this compatibility layer.

## Run-local policy

`DomainPolicy` is immutable; `DomainBundle` binds it to one sealed registry and
policy digest. Domain capacities are distinct from transfer enumeration limits.
In particular `max_int_constants` / `int_domain_max_consts` control the integer
domain, whereas legacy `max_const_ints` controls bounded subscript enumeration.
Exact values retain a reference to their run-local registry through joins,
widening, heap slots, summaries and reabstraction. Shared compatibility defaults
are sealed; `configure_registry` returns an isolated registry rather than
mutating defaults. Serialized snapshots reconstruct their sealed registry.

## Slots and state

A tracked key uses its own slot; an untracked key uses the residual default.
Every slot carries independent presence. Joins use the same effective-slot
meaning on both branches. Unknown reads join all matching tracked partitions
and the default; unknown writes reach tracked partitions as well. A new weakly
tracked partition inherits its previous effective default. Boolean keys use
Python's integer-key equivalence. Missing keys are a separate `KeyError` outcome,
not a fabricated normal Top value.

Address coarsening is atomic over the full state: environment, local heap keys,
object class/reference payloads, tracked and residual slots, identity-bearing
keys, container contents, overlays, havoc, shared cells/globals and exceptions.
The retained address-remap ledger canonicalizes subsequent old-alias accesses.
Predicates whose heap justification changes are discarded conservatively.
A single abstract address does not establish concrete singleton cardinality.

The legacy state publishes `address_remaps` and `remap_generation`; migrating
host positive/negative dependency subscriptions is a separate obligation. The
state implementation and finite tests alone do not close that host obligation.

`AbsVal.with_addrs(new_witnesses)` preserves the address-Top flag. Deleting finite
witnesses does not prove arbitrary unknown addresses impossible. An internal
trusted transfer may explicitly request `with_addrs(..., addr_top=False)` when
it has an independent covering refinement justification; this is a deliberate
migration from the historical implicit flag clearing.

## Evidence

The executable tests in `tests/refactor/domains/` use independent finite string,
integer and slot concretizations, exact precision examples, mixed scalar
unions, capacity/interleaving checks, full-state cyclic/alias remapping, and
snapshot round trips. Finite witnesses refute violations and check the declared
examples; passing tests are not a universal soundness proof.
