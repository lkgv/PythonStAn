"""None domain for tracking None-ness.

Provides a small lattice to indicate whether a value may be None.
"""

from dataclasses import dataclass
from enum import Enum
from typing import Any, List, Optional

from pythonstan.analysis.domains.base import Domain, Fact

__all__ = ["NoneDomain", "NoneLattice", "NoneVal"]


class NoneVal(Enum):
    """None-ness lattice values."""
    BOTTOM = "bottom"     # Unreachable / contradiction
    NONE = "none"         # Definitely None
    NOT_NONE = "not_none" # Definitely not None
    TOP = "top"           # Unknown (may be None or not)


@dataclass(frozen=True)
class NoneLattice:
    """Abstract None-ness value."""
    val: NoneVal = NoneVal.BOTTOM

    @classmethod
    def bottom(cls) -> "NoneLattice":
        return cls(NoneVal.BOTTOM)

    @classmethod
    def none_(cls) -> "NoneLattice":
        return cls(NoneVal.NONE)

    @classmethod
    def not_none(cls) -> "NoneLattice":
        return cls(NoneVal.NOT_NONE)

    @classmethod
    def top(cls) -> "NoneLattice":
        return cls(NoneVal.TOP)

    def is_bottom(self) -> bool:
        return self.val == NoneVal.BOTTOM

    def is_top(self) -> bool:
        return self.val == NoneVal.TOP

    def is_none(self) -> bool:
        return self.val == NoneVal.NONE

    def is_not_none(self) -> bool:
        return self.val == NoneVal.NOT_NONE

    def __str__(self) -> str:
        if self.val == NoneVal.BOTTOM:
            return "⊥_none"
        if self.val == NoneVal.TOP:
            return "⊤_none"
        if self.val == NoneVal.NONE:
            return "None"
        return "NotNone"


class NoneDomain(Domain[NoneLattice]):
    """Domain for None-ness refinement."""

    @property
    def name(self) -> str:
        return "none"

    def bottom(self) -> NoneLattice:
        return NoneLattice.bottom()

    def top(self) -> NoneLattice:
        return NoneLattice.top()

    def join(self, a: NoneLattice, b: NoneLattice) -> NoneLattice:
        if a.is_bottom():
            return b
        if b.is_bottom():
            return a
        if a.val == b.val:
            return a
        if a.is_top() or b.is_top():
            return NoneLattice.top()
        # NONE ⊔ NOT_NONE = TOP
        return NoneLattice.top()

    def meet(self, a: NoneLattice, b: NoneLattice) -> NoneLattice:
        if a.is_top():
            return b
        if b.is_top():
            return a
        if a.is_bottom() or b.is_bottom():
            return NoneLattice.bottom()
        if a.val == b.val:
            return a
        # NONE ⊓ NOT_NONE = BOTTOM
        return NoneLattice.bottom()

    def leq(self, a: NoneLattice, b: NoneLattice) -> bool:
        if a.is_bottom():
            return True
        if b.is_top():
            return True
        return a.val == b.val

    def abstract_const(self, value: Any) -> Optional[NoneLattice]:
        if value is None:
            return NoneLattice.none_()
        return None

    def produce_facts(self, var: str, elem: NoneLattice) -> List[Fact]:
        facts: List[Fact] = []
        if elem.is_none():
            facts.append(Fact("is_none", var, True))
        elif elem.is_not_none():
            facts.append(Fact("is_not_none", var, True))
        return facts

    def consume_fact(self, elem: NoneLattice, fact: Fact) -> NoneLattice:
        if fact.kind == "is_none":
            return self.meet(elem, NoneLattice.none_())
        if fact.kind == "is_not_none":
            return self.meet(elem, NoneLattice.not_none())
        if fact.kind in {"is_const_str", "is_singleton_str", "has_prefix"}:
            return self.meet(elem, NoneLattice.not_none())
        return elem
