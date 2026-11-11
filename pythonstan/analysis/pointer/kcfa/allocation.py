"""One run-local policy boundary for every native allocation family."""
from collections import Counter
from dataclasses import dataclass
from .object import AllocKind


@dataclass(frozen=True)
class AllocationKindDescriptor:
    kind: str
    identity: str
    contract: str


ALLOCATION_KINDS = tuple(
    AllocationKindDescriptor(kind.value,
        "canonical" if kind in {AllocKind.CONSTANT, AllocKind.MODULE} else "selected",
        "Canonical source/model value, not fresh" if kind in {AllocKind.CONSTANT, AllocKind.MODULE}
        else "Heap context selected; capture/type/frame relations remain explicit")
    for kind in AllocKind
)


class AllocationPolicyRegistry:
    """Selected fresh identities plus declared canonical/alias identities.

    Bound methods, namespace views and lexical rebindings are alias-derived
    views of existing identities, not independent fresh allocations. Consumers
    name that exception rather than silently bypassing the policy.
    """
    def __init__(self, selector):
        self.selector = selector
        self.decisions = Counter()

    def select(self, context, site, alloc_type=None, *, identity="fresh"):
        if identity not in {"fresh", "canonical", "alias"}:
            raise ValueError("Unknown allocation identity contract")
        if site.kind in {AllocKind.CONSTANT, AllocKind.MODULE}:
            identity = "canonical"
        self.decisions[(site.kind.value, identity)] += 1
        if identity == "canonical":
            return self.selector.empty_context()
        if identity == "alias":
            return context
        return self.selector.select_alloc_context(context, site, alloc_type)

    def statistics(self):
        return {f"{kind}:{identity}": count
                for (kind, identity), count in sorted(self.decisions.items())}


def canonical_constant(state, context, site, value):
    """Route synthetic scalar metadata through the same canonical policy."""
    from .object import ConstantObject
    return ConstantObject(state.select_allocation_context(context, site, identity="canonical"),
                          site, value)


ALIAS_IDENTITY_CONTRACTS = {
    "bound_method": "Existing function plus receiver/class identity; method binding is not a fresh body allocation",
    "lexical_rebinding": "Reconstruct a capture-table key, retaining the original function identity",
    "instance_namespace": "Live alias of owner's namespace; owner relation is retained",
    "keyword_projection": "Intermediate relation view of mapping/receiver, not an independently allocated user dict",
    "mapping_item": "Weak key/value relation view of a mapping entry; no tuple singleton/freshness proof",
    "model_builtin_symbol": "Explicit model symbol identity; helper ObjectFactory accepts caller-owned context",
}
