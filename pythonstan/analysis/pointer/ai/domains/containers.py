"""Container domain for list/dict/set/tuple abstractions.

From ai_spec.md §3.6:
- List: ⟨len, idxStore, elems⟩
- Dict: ⟨len, kvStore, keys, vals⟩
- Set: ⟨len, elems⟩
- Tuple: fixed-arity or summary

This domain tracks container element abstractions with
elem bucket + optional tracked indices for precision.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import (
    Any,
    Dict,
    FrozenSet,
    List,
    Optional,
    Set,
    Tuple,
    Union,
)

from .base import Domain, Fact

__all__ = ["ContainerDomain", "ContainerKind", "ContainerAbstraction"]


# =============================================================================
# ContainerKind
# =============================================================================

class ContainerKind(Enum):
    """Types of containers."""
    LIST = "list"
    DICT = "dict"
    SET = "set"
    TUPLE = "tuple"
    UNKNOWN = "unknown"


# =============================================================================
# ContainerAbstraction
# =============================================================================

@dataclass(frozen=True)
class ContainerAbstraction:
    """Abstract container value.
    
    Combines element summary with optional tracked indices.
    Uses FieldStore-style default bucket semantics.
    
    Attributes:
        kind: Container type
        elem_bucket: Summary of all elements (default bucket)
        tracked: Tracked indices/keys to element sets
        may_be_empty: Whether container may be empty
    """
    kind: ContainerKind = ContainerKind.UNKNOWN
    elem_bucket: FrozenSet[str] = field(default_factory=frozenset)  # Simplified: just track keys/indices
    tracked: Tuple[Tuple[Union[int, str], FrozenSet[str]], ...] = field(default_factory=tuple)
    may_be_empty: bool = True

    @classmethod
    def bottom(cls) -> 'ContainerAbstraction':
        """Bottom (not a container)."""
        return cls()

    @classmethod
    def empty(cls, kind: ContainerKind) -> 'ContainerAbstraction':
        """Empty container of given kind."""
        return cls(kind=kind, may_be_empty=True)

    @classmethod
    def unknown(cls, kind: ContainerKind = ContainerKind.UNKNOWN) -> 'ContainerAbstraction':
        """Unknown container contents."""
        return cls(kind=kind, elem_bucket=frozenset({"*"}), may_be_empty=True)

    def is_bottom(self) -> bool:
        """Check if bottom (not a container)."""
        return self.kind == ContainerKind.UNKNOWN and len(self.elem_bucket) == 0 and len(self.tracked) == 0

    def is_unknown(self) -> bool:
        """Check if contents are unknown."""
        return "*" in self.elem_bucket

    def get_elem(self) -> FrozenSet[str]:
        """Get all possible element keys/indices."""
        result = set(self.elem_bucket)
        for idx, keys in self.tracked:
            result.update(keys)
        return frozenset(result)

    def get_tracked_keys(self) -> FrozenSet[Union[int, str]]:
        """Get all tracked indices/keys."""
        return frozenset(idx for idx, _ in self.tracked)

    def __str__(self) -> str:
        if self.is_bottom():
            return "⊥_container"
        parts = [self.kind.value]
        if self.tracked:
            tracked_str = ", ".join(f"{k}:{v}" for k, v in self.tracked[:3])
            parts.append(f"[{tracked_str}]")
        if self.elem_bucket:
            parts.append(f"elem={self.elem_bucket}")
        return f"Container({', '.join(parts)})"


# =============================================================================
# ContainerDomain
# =============================================================================

class ContainerDomain(Domain[ContainerAbstraction]):
    """Domain for container abstractions.
    
    Provides abstract operations for list/dict/set/tuple with
    tracked indices and elem bucket widening.
    """

    def __init__(self, max_tracked: int = 10):
        """Initialize with tracking limit.
        
        Args:
            max_tracked: Maximum indices/keys to track before spilling
        """
        self.max_tracked = max_tracked

    @property
    def name(self) -> str:
        return "container"

    def bottom(self) -> ContainerAbstraction:
        return ContainerAbstraction.bottom()

    def top(self) -> ContainerAbstraction:
        return ContainerAbstraction.unknown()

    def join(self, a: ContainerAbstraction, b: ContainerAbstraction) -> ContainerAbstraction:
        """Join two container abstractions."""
        if a.is_bottom():
            return b
        if b.is_bottom():
            return a

        # Unify kind
        if a.kind != b.kind:
            kind = ContainerKind.UNKNOWN
        else:
            kind = a.kind

        # Join elem buckets
        elem_bucket = a.elem_bucket | b.elem_bucket

        # Join tracked
        tracked_dict: Dict[Union[int, str], FrozenSet[str]] = {}
        for idx, keys in a.tracked:
            tracked_dict[idx] = keys
        for idx, keys in b.tracked:
            if idx in tracked_dict:
                tracked_dict[idx] = tracked_dict[idx] | keys
            else:
                tracked_dict[idx] = keys

        tracked = tuple(sorted(tracked_dict.items(), key=lambda x: str(x[0])))

        return ContainerAbstraction(
            kind=kind,
            elem_bucket=elem_bucket,
            tracked=tracked,
            may_be_empty=a.may_be_empty and b.may_be_empty,
        )

    def meet(self, a: ContainerAbstraction, b: ContainerAbstraction) -> ContainerAbstraction:
        """Meet two container abstractions."""
        if a.is_bottom() or b.is_bottom():
            return ContainerAbstraction.bottom()

        # Must have same kind
        if a.kind != b.kind and a.kind != ContainerKind.UNKNOWN and b.kind != ContainerKind.UNKNOWN:
            return ContainerAbstraction.bottom()

        kind = a.kind if a.kind != ContainerKind.UNKNOWN else b.kind

        # Meet elem buckets
        elem_bucket = a.elem_bucket & b.elem_bucket

        # Meet tracked (intersection)
        a_tracked = dict(a.tracked)
        b_tracked = dict(b.tracked)
        common_keys = set(a_tracked.keys()) & set(b_tracked.keys())
        tracked = tuple(
            (k, a_tracked[k] & b_tracked[k])
            for k in sorted(common_keys, key=str)
        )

        return ContainerAbstraction(
            kind=kind,
            elem_bucket=elem_bucket,
            tracked=tracked,
            may_be_empty=a.may_be_empty or b.may_be_empty,
        )

    def leq(self, a: ContainerAbstraction, b: ContainerAbstraction) -> bool:
        """Check a ⊑ b."""
        if a.is_bottom():
            return True
        if b.is_unknown():
            return True

        if a.kind != b.kind and b.kind != ContainerKind.UNKNOWN:
            return False

        # Check elem bucket
        if not a.elem_bucket <= b.elem_bucket:
            return False

        # Check tracked
        a_tracked = dict(a.tracked)
        b_tracked = dict(b.tracked)
        for k, v in a_tracked.items():
            if k in b_tracked:
                if not v <= b_tracked[k]:
                    return False
            # If not in b, it should be in b's elem_bucket (handled above)

        return True

    def widen(self, old: ContainerAbstraction, new: ContainerAbstraction) -> ContainerAbstraction:
        """Widen: spill tracked to elem bucket when too many."""
        joined = self.join(old, new)

        if len(joined.tracked) > self.max_tracked:
            # Spill all tracked to elem bucket
            spilled = set(joined.elem_bucket)
            for _, keys in joined.tracked:
                spilled.update(keys)
            
            return ContainerAbstraction(
                kind=joined.kind,
                elem_bucket=frozenset(spilled),
                tracked=(),
                may_be_empty=joined.may_be_empty,
            )

        return joined

    def narrow(self, old: ContainerAbstraction, new: ContainerAbstraction) -> ContainerAbstraction:
        """Narrow: refine with meet."""
        return self.meet(old, new)

    def abstract_const(self, value: Any) -> Optional[ContainerAbstraction]:
        """Abstract a constant container (simplified: just identify kind)."""
        if isinstance(value, list):
            return ContainerAbstraction.empty(ContainerKind.LIST)
        if isinstance(value, dict):
            return ContainerAbstraction.empty(ContainerKind.DICT)
        if isinstance(value, set):
            return ContainerAbstraction.empty(ContainerKind.SET)
        if isinstance(value, tuple):
            return ContainerAbstraction.empty(ContainerKind.TUPLE)
        return None

    def produce_facts(self, var: str, elem: ContainerAbstraction) -> List[Fact]:
        """Produce facts about container."""
        facts = []
        if not elem.is_bottom():
            facts.append(Fact("is_container", var, elem.kind.value))
            if elem.may_be_empty:
                facts.append(Fact("may_be_empty", var, True))
            tracked_keys = elem.get_tracked_keys()
            if tracked_keys:
                facts.append(Fact("has_keys", var, tracked_keys))
        return facts

    def consume_fact(self, elem: ContainerAbstraction, fact: Fact) -> ContainerAbstraction:
        """Consume a fact to refine container abstraction."""
        # Currently no cross-domain refinement implemented
        return elem

