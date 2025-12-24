"""String domain for reflective attribute names.

From ai_spec.md §3.2.3:
- Stratified domain: P_≤N(ConstStr) ⊔ PrefixLang_≤B ⊔ ⊤
- Finite constants up to cap N
- Then prefix-language (simplified to prefix set) up to cap B
- Then ⊤ (any string)

This is critical for getattr/setattr key reasoning.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import (
    Any,
    FrozenSet,
    List,
    Optional,
    Set,
    Tuple,
    Union,
)

from .base import Domain, Fact

__all__ = ["StringDomain", "StrLattice", "StrKind"]


# =============================================================================
# StrKind - Stratification levels
# =============================================================================

class StrKind(Enum):
    """Stratification levels for string abstraction."""
    BOTTOM = "bottom"    # No strings (unreachable)
    CONST = "const"      # Finite set of constant strings
    PREFIX = "prefix"    # Set of prefixes (any string with these prefixes)
    TOP = "top"          # Any string


# =============================================================================
# StrLattice - String lattice element
# =============================================================================

@dataclass(frozen=True)
class StrLattice:
    """Abstract string value.
    
    Represents either:
    - bottom: no strings
    - const: finite set of constant strings
    - prefix: set of prefixes (strings starting with any prefix)
    - top: any string
    
    Attributes:
        kind: Stratification level
        values: Constant strings (for CONST) or prefixes (for PREFIX)
    """
    kind: StrKind = StrKind.BOTTOM
    values: FrozenSet[str] = field(default_factory=frozenset)

    @classmethod
    def bottom(cls) -> 'StrLattice':
        """No strings (unreachable/not a string)."""
        return cls(StrKind.BOTTOM, frozenset())

    @classmethod
    def const(cls, s: str) -> 'StrLattice':
        """Single constant string."""
        return cls(StrKind.CONST, frozenset({s}))

    @classmethod
    def consts(cls, strs: FrozenSet[str]) -> 'StrLattice':
        """Set of constant strings."""
        if len(strs) == 0:
            return cls.bottom()
        return cls(StrKind.CONST, strs)

    @classmethod
    def prefix(cls, prefixes: FrozenSet[str]) -> 'StrLattice':
        """Prefix set (any string starting with these)."""
        return cls(StrKind.PREFIX, prefixes)

    @classmethod
    def top(cls) -> 'StrLattice':
        """Any string."""
        return cls(StrKind.TOP, frozenset())

    def is_bottom(self) -> bool:
        """Check if this is bottom."""
        return self.kind == StrKind.BOTTOM

    def is_top(self) -> bool:
        """Check if this is top."""
        return self.kind == StrKind.TOP

    def is_const(self) -> bool:
        """Check if this is a finite const set."""
        return self.kind == StrKind.CONST

    def is_enumerable(self) -> bool:
        """Check if we can enumerate all possible strings."""
        return self.kind == StrKind.CONST

    def get_consts(self) -> Optional[FrozenSet[str]]:
        """Get constant strings if enumerable, else None."""
        if self.kind == StrKind.CONST:
            return self.values
        return None

    def may_equal(self, s: str) -> bool:
        """Check if this may equal a specific string."""
        if self.kind == StrKind.BOTTOM:
            return False
        if self.kind == StrKind.TOP:
            return True
        if self.kind == StrKind.CONST:
            return s in self.values
        if self.kind == StrKind.PREFIX:
            return any(s.startswith(p) for p in self.values)
        return True

    def must_equal(self, s: str) -> bool:
        """Check if this must equal a specific string (singleton)."""
        return self.kind == StrKind.CONST and self.values == frozenset({s})

    def __str__(self) -> str:
        if self.kind == StrKind.BOTTOM:
            return "⊥_str"
        if self.kind == StrKind.TOP:
            return "⊤_str"
        if self.kind == StrKind.CONST:
            if len(self.values) <= 3:
                return "{" + ", ".join(repr(s) for s in sorted(self.values)) + "}"
            return f"{{...{len(self.values)} strs}}"
        if self.kind == StrKind.PREFIX:
            return f"prefix({self.values})"
        return f"StrLattice({self.kind}, {self.values})"


# =============================================================================
# StringDomain - Domain implementation
# =============================================================================

class StringDomain(Domain[StrLattice]):
    """String domain for reflective attribute names.
    
    Implements stratified widening:
    - const -> prefix (when too many constants)
    - prefix -> top (when too many prefixes)
    """

    def __init__(self, max_consts: int = 10, max_prefixes: int = 5):
        """Initialize with widening caps.
        
        Args:
            max_consts: Maximum constant strings before widening to prefix
            max_prefixes: Maximum prefixes before widening to top
        """
        self.max_consts = max_consts
        self.max_prefixes = max_prefixes

    @property
    def name(self) -> str:
        return "string"

    def bottom(self) -> StrLattice:
        return StrLattice.bottom()

    def top(self) -> StrLattice:
        return StrLattice.top()

    def join(self, a: StrLattice, b: StrLattice) -> StrLattice:
        """Join two string abstractions."""
        # Bottom is identity
        if a.is_bottom():
            return b
        if b.is_bottom():
            return a
        
        # Top absorbs
        if a.is_top() or b.is_top():
            return StrLattice.top()

        # Same kind: union values
        if a.kind == b.kind:
            return StrLattice(a.kind, a.values | b.values)

        # Different kinds: lift to higher level
        if a.kind == StrKind.CONST and b.kind == StrKind.PREFIX:
            # Lift consts to prefixes
            prefixes = self._consts_to_prefixes(a.values) | b.values
            return StrLattice(StrKind.PREFIX, prefixes)
        if a.kind == StrKind.PREFIX and b.kind == StrKind.CONST:
            prefixes = a.values | self._consts_to_prefixes(b.values)
            return StrLattice(StrKind.PREFIX, prefixes)

        return StrLattice.top()

    def meet(self, a: StrLattice, b: StrLattice) -> StrLattice:
        """Meet two string abstractions."""
        # Top is identity
        if a.is_top():
            return b
        if b.is_top():
            return a
        
        # Bottom absorbs
        if a.is_bottom() or b.is_bottom():
            return StrLattice.bottom()

        # Same kind: intersect values
        if a.kind == b.kind == StrKind.CONST:
            intersection = a.values & b.values
            if len(intersection) == 0:
                return StrLattice.bottom()
            return StrLattice(StrKind.CONST, intersection)

        # Const meets prefix: filter consts by prefix
        if a.kind == StrKind.CONST and b.kind == StrKind.PREFIX:
            filtered = frozenset(
                s for s in a.values
                if any(s.startswith(p) for p in b.values)
            )
            if len(filtered) == 0:
                return StrLattice.bottom()
            return StrLattice(StrKind.CONST, filtered)
        
        if a.kind == StrKind.PREFIX and b.kind == StrKind.CONST:
            return self.meet(b, a)

        # Prefix meets prefix: keep common refinable prefixes
        if a.kind == StrKind.PREFIX and b.kind == StrKind.PREFIX:
            # Intersect prefix sets (simplified)
            return StrLattice(StrKind.PREFIX, a.values & b.values)

        return StrLattice.bottom()

    def leq(self, a: StrLattice, b: StrLattice) -> bool:
        """Check a ⊑ b."""
        if a.is_bottom():
            return True
        if b.is_top():
            return True
        if a.is_top():
            return b.is_top()

        # Same kind: subset check
        if a.kind == b.kind:
            return a.values <= b.values

        # Const ⊑ prefix if all consts match some prefix
        if a.kind == StrKind.CONST and b.kind == StrKind.PREFIX:
            return all(
                any(s.startswith(p) for p in b.values)
                for s in a.values
            )

        # Prefix ⊑ const only if prefix set is empty (bottom)
        if a.kind == StrKind.PREFIX and b.kind == StrKind.CONST:
            return len(a.values) == 0

        return False

    def widen(self, old: StrLattice, new: StrLattice) -> StrLattice:
        """Widening with stratified caps.
        
        const -> prefix when |consts| > max_consts
        prefix -> top when |prefixes| > max_prefixes
        """
        joined = self.join(old, new)

        if joined.kind == StrKind.CONST and len(joined.values) > self.max_consts:
            # Widen consts to prefixes
            prefixes = self._consts_to_prefixes(joined.values)
            if len(prefixes) <= self.max_prefixes:
                return StrLattice(StrKind.PREFIX, prefixes)
            else:
                return StrLattice.top()

        if joined.kind == StrKind.PREFIX and len(joined.values) > self.max_prefixes:
            return StrLattice.top()

        return joined

    def narrow(self, old: StrLattice, new: StrLattice) -> StrLattice:
        """Narrowing: refine with meet."""
        return self.meet(old, new)

    def abstract_const(self, value: Any) -> Optional[StrLattice]:
        """Abstract a Python constant."""
        if isinstance(value, str):
            return StrLattice.const(value)
        return None

    def produce_facts(self, var: str, elem: StrLattice) -> List[Fact]:
        """Produce facts about string values."""
        facts = []
        if elem.kind == StrKind.CONST:
            facts.append(Fact("is_const_str", var, elem.values))
            if len(elem.values) == 1:
                facts.append(Fact("is_singleton_str", var, next(iter(elem.values))))
        elif elem.kind == StrKind.PREFIX:
            facts.append(Fact("has_prefix", var, elem.values))
        elif elem.is_top():
            facts.append(Fact("is_unknown_str", var, None))
        return facts

    def consume_fact(self, elem: StrLattice, fact: Fact) -> StrLattice:
        """Consume a fact to refine string abstraction."""
        if fact.kind == "is_const_str" and fact.data is not None:
            # Refine with known constants
            consts = fact.data
            if elem.kind == StrKind.TOP:
                return StrLattice.consts(consts)
            elif elem.kind == StrKind.CONST:
                return StrLattice.consts(elem.values & consts)
        return elem

    def _consts_to_prefixes(self, consts: FrozenSet[str]) -> FrozenSet[str]:
        """Convert constant strings to common prefixes.
        
        Simple strategy: use first character as prefix.
        More sophisticated: compute LCP of groups.
        """
        if len(consts) == 0:
            return frozenset()
        
        # Group by first character
        prefixes: Set[str] = set()
        for s in consts:
            if s:
                prefixes.add(s[0])
            else:
                prefixes.add("")  # Empty string prefix
        
        return frozenset(prefixes)

