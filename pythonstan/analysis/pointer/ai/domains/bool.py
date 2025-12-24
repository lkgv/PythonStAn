"""Boolean domain for branch refinement.

From ai_spec.md §3.2.1:
- BoolLattice = {⊥, F, T, ⊤}
- Used for branch guards and descriptor predicates
"""

from dataclasses import dataclass
from enum import Enum
from typing import Any, List, Optional

from .base import Domain, Fact

__all__ = ["BoolDomain", "BoolLattice", "BoolVal"]


# =============================================================================
# BoolVal - Four-valued boolean
# =============================================================================

class BoolVal(Enum):
    """Four-valued boolean lattice."""
    BOTTOM = "bottom"  # Unreachable / contradiction
    FALSE = "false"    # Definitely False
    TRUE = "true"      # Definitely True
    TOP = "top"        # Unknown (may be True or False)


# =============================================================================
# BoolLattice - Boolean lattice element
# =============================================================================

@dataclass(frozen=True)
class BoolLattice:
    r"""Abstract boolean value.
    
    Four-valued lattice::
    
            ⊤ (top/unknown)
           / \
          T   F
           \ /
            ⊥ (bottom/unreachable)
    """
    val: BoolVal = BoolVal.BOTTOM

    @classmethod
    def bottom(cls) -> 'BoolLattice':
        """Bottom (unreachable)."""
        return cls(BoolVal.BOTTOM)

    @classmethod
    def true_(cls) -> 'BoolLattice':
        """Definitely True."""
        return cls(BoolVal.TRUE)

    @classmethod
    def false_(cls) -> 'BoolLattice':
        """Definitely False."""
        return cls(BoolVal.FALSE)

    @classmethod
    def top(cls) -> 'BoolLattice':
        """Unknown (may be True or False)."""
        return cls(BoolVal.TOP)

    @classmethod
    def from_bool(cls, b: bool) -> 'BoolLattice':
        """Create from Python bool."""
        return cls(BoolVal.TRUE if b else BoolVal.FALSE)

    def is_bottom(self) -> bool:
        return self.val == BoolVal.BOTTOM

    def is_top(self) -> bool:
        return self.val == BoolVal.TOP

    def is_true(self) -> bool:
        return self.val == BoolVal.TRUE

    def is_false(self) -> bool:
        return self.val == BoolVal.FALSE

    def is_definite(self) -> bool:
        """Check if definitely True or definitely False."""
        return self.val in (BoolVal.TRUE, BoolVal.FALSE)

    def may_be_true(self) -> bool:
        """Check if may be True."""
        return self.val in (BoolVal.TRUE, BoolVal.TOP)

    def may_be_false(self) -> bool:
        """Check if may be False."""
        return self.val in (BoolVal.FALSE, BoolVal.TOP)

    def negate(self) -> 'BoolLattice':
        """Logical negation."""
        if self.val == BoolVal.TRUE:
            return BoolLattice.false_()
        if self.val == BoolVal.FALSE:
            return BoolLattice.true_()
        return self  # bottom or top stay the same

    def __str__(self) -> str:
        if self.val == BoolVal.BOTTOM:
            return "⊥_bool"
        if self.val == BoolVal.TOP:
            return "⊤_bool"
        if self.val == BoolVal.TRUE:
            return "True"
        return "False"


# =============================================================================
# BoolDomain
# =============================================================================

class BoolDomain(Domain[BoolLattice]):
    """Boolean domain for branch refinement and descriptor predicates."""

    @property
    def name(self) -> str:
        return "bool"

    def bottom(self) -> BoolLattice:
        return BoolLattice.bottom()

    def top(self) -> BoolLattice:
        return BoolLattice.top()

    def join(self, a: BoolLattice, b: BoolLattice) -> BoolLattice:
        """Join two boolean values."""
        if a.is_bottom():
            return b
        if b.is_bottom():
            return a
        if a.val == b.val:
            return a
        return BoolLattice.top()

    def meet(self, a: BoolLattice, b: BoolLattice) -> BoolLattice:
        """Meet two boolean values."""
        if a.is_top():
            return b
        if b.is_top():
            return a
        if a.val == b.val:
            return a
        return BoolLattice.bottom()  # Contradiction

    def leq(self, a: BoolLattice, b: BoolLattice) -> bool:
        """Check a ⊑ b."""
        if a.is_bottom():
            return True
        if b.is_top():
            return True
        return a.val == b.val

    def abstract_const(self, value: Any) -> Optional[BoolLattice]:
        """Abstract a constant boolean."""
        if isinstance(value, bool):
            return BoolLattice.from_bool(value)
        return None

    def produce_facts(self, var: str, elem: BoolLattice) -> List[Fact]:
        """Produce facts about boolean values."""
        facts = []
        if elem.is_true():
            facts.append(Fact("is_true", var, True))
        elif elem.is_false():
            facts.append(Fact("is_false", var, False))
        elif elem.is_top():
            facts.append(Fact("is_bool", var, None))
        return facts

    def consume_fact(self, elem: BoolLattice, fact: Fact) -> BoolLattice:
        """Consume a fact to refine boolean abstraction."""
        if fact.kind == "is_true":
            return self.meet(elem, BoolLattice.true_())
        if fact.kind == "is_false":
            return self.meet(elem, BoolLattice.false_())
        return elem

    # -------------------------------------------------------------------------
    # Boolean operations
    # -------------------------------------------------------------------------

    def and_(self, a: BoolLattice, b: BoolLattice) -> BoolLattice:
        """Logical AND."""
        if a.is_bottom() or b.is_bottom():
            return BoolLattice.bottom()
        if a.is_false() or b.is_false():
            return BoolLattice.false_()
        if a.is_true() and b.is_true():
            return BoolLattice.true_()
        return BoolLattice.top()

    def or_(self, a: BoolLattice, b: BoolLattice) -> BoolLattice:
        """Logical OR."""
        if a.is_bottom() or b.is_bottom():
            return BoolLattice.bottom()
        if a.is_true() or b.is_true():
            return BoolLattice.true_()
        if a.is_false() and b.is_false():
            return BoolLattice.false_()
        return BoolLattice.top()

    def not_(self, a: BoolLattice) -> BoolLattice:
        """Logical NOT."""
        return a.negate()

