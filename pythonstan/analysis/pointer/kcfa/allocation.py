"""One run-local policy boundary for every native allocation family."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import TYPE_CHECKING, overload

from .context_selector import ContextSelector
from .object import AbstractObject, AllocKind, AllocSite, ConstantObject, NativeConstantValue

if TYPE_CHECKING:
    from .context import NativeContext
    from .state import PointerAnalysisState


@dataclass(frozen=True)
class AllocationKindDescriptor:
    kind: str
    identity: str
    contract: str


ALLOCATION_KINDS = tuple(
    AllocationKindDescriptor(
        kind.value,
        "canonical" if kind in {AllocKind.CONSTANT, AllocKind.MODULE} else "selected",
        "Canonical source/model value, not fresh"
        if kind in {AllocKind.CONSTANT, AllocKind.MODULE}
        else "Heap context selected; capture/type/frame relations remain explicit",
    )
    for kind in AllocKind
)


class AllocationPolicyRegistry:
    """Selected fresh identities plus declared canonical/alias identities.

    Bound methods, namespace views and lexical rebindings are alias-derived
    views of existing identities, not independent fresh allocations. Consumers
    name that exception rather than silently bypassing the policy.
    """

    def __init__(self, selector: ContextSelector) -> None:
        self.selector = selector
        self.decisions: Counter[tuple[str, str]] = Counter()

    @overload
    def select(
        self,
        context: NativeContext,
        site: AllocSite,
        alloc_type: AbstractObject | None = None,
        *,
        identity: str = "fresh",
    ) -> NativeContext: ...

    @overload
    def select(
        self,
        context: None,
        site: AllocSite,
        alloc_type: AbstractObject | None = None,
        *,
        identity: str = "fresh",
    ) -> NativeContext | None: ...

    def select(
        self,
        context: NativeContext | None,
        site: AllocSite,
        alloc_type: AbstractObject | None = None,
        *,
        identity: str = "fresh",
    ) -> NativeContext | None:
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

    def statistics(self) -> dict[str, int]:
        return {
            f"{kind}:{identity}": count
            for (kind, identity), count in sorted(self.decisions.items())
        }


def canonical_constant(
    state: PointerAnalysisState,
    context: NativeContext | None,
    site: AllocSite,
    value: NativeConstantValue,
) -> ConstantObject:
    """Route synthetic scalar metadata through the same canonical policy."""

    return ConstantObject(
        state.select_allocation_context(context, site, identity="canonical"), site, value
    )


ALIAS_IDENTITY_CONTRACTS = {
    "bound_method": "Existing function plus receiver/class identity; method binding is not a fresh body allocation",
    "lexical_rebinding": "Reconstruct a capture-table key, retaining the original function identity",
    "instance_namespace": "Live alias of owner's namespace; owner relation is retained",
    "keyword_projection": "Intermediate relation view of mapping/receiver, not an independently allocated user dict",
    "mapping_item": "Weak key/value relation view of a mapping entry; no tuple singleton/freshness proof",
    "model_builtin_symbol": "Explicit model symbol identity; helper ObjectFactory accepts caller-owned context",
}
