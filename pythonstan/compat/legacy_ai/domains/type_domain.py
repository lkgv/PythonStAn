"""Type domain for class/type tokens.

This domain tracks class/type objects for type-aware analysis.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import override

from pythonstan.analysis.contracts.budgets import AnalysisBudget
from pythonstan.analysis.domains.base import Domain, DomainRegistry
from pythonstan.analysis.pointer.kcfa.object import AbstractObject

__all__ = [
    "TypeDomain",
    "TypeVal",
    "TypeKind",
    "ClassRef",
]


class TypeKind(Enum):
    """Stratification for type values."""

    BOTTOM = "bottom"
    SET = "set"
    TOP = "top"


@dataclass(frozen=True)
class ClassRef:
    """Token for class objects."""

    obj: "AbstractObject"


@dataclass(frozen=True)
class TypeVal:
    """Abstract type value."""

    kind: TypeKind = TypeKind.BOTTOM
    tokens: frozenset[ClassRef] = field(default_factory=frozenset)

    @classmethod
    def bottom(cls) -> "TypeVal":
        return cls(TypeKind.BOTTOM, frozenset())

    @classmethod
    def top(cls) -> "TypeVal":
        return cls(TypeKind.TOP, frozenset())

    @classmethod
    def from_tokens(cls, tokens: frozenset[ClassRef]) -> "TypeVal":
        if len(tokens) == 0:
            return cls.bottom()
        return cls(TypeKind.SET, tokens)

    def is_bottom(self) -> bool:
        return self.kind == TypeKind.BOTTOM

    def is_top(self) -> bool:
        return self.kind == TypeKind.TOP

    def join(self, other: "TypeVal") -> "TypeVal":
        if self.is_bottom():
            return other
        if other.is_bottom():
            return self
        if self.is_top() or other.is_top():
            return TypeVal.top()
        return TypeVal.from_tokens(self.tokens | other.tokens)

    def meet(self, other: "TypeVal") -> "TypeVal":
        if self.is_top():
            return other
        if other.is_top():
            return self
        if self.is_bottom() or other.is_bottom():
            return TypeVal.bottom()
        return TypeVal.from_tokens(self.tokens & other.tokens)

    def leq(self, other: "TypeVal") -> bool:
        if self.is_bottom():
            return True
        if other.is_top():
            return True
        if self.is_top():
            return other.is_top()
        if other.kind != TypeKind.SET:
            return False
        return self.tokens <= other.tokens

    @override
    def __str__(self) -> str:
        if self.is_bottom():
            return "⊥_type"
        if self.is_top():
            return "⊤_type"
        return f"Type({len(self.tokens)} classes)"


class TypeDomain(Domain[TypeVal]):
    """Domain for type/class tokens with bounded set widening."""

    def __init__(self, max_tokens: int = 20) -> None:
        self.max_tokens = max_tokens

    @property
    @override
    def name(self) -> str:
        return "type"

    @override
    def bottom(self) -> TypeVal:
        return TypeVal.bottom()

    @override
    def top(self) -> TypeVal:
        return TypeVal.top()

    @override
    def join(self, a: TypeVal, b: TypeVal) -> TypeVal:
        return a.join(b)

    @override
    def meet(self, a: TypeVal, b: TypeVal) -> TypeVal:
        return a.meet(b)

    @override
    def leq(self, a: TypeVal, b: TypeVal) -> bool:
        return a.leq(b)

    @override
    def widen(self, old: TypeVal, new: TypeVal) -> TypeVal:
        joined = old.join(new)
        if joined.kind == TypeKind.SET and len(joined.tokens) > self.max_tokens:
            return TypeVal.top()
        return joined

    @override
    def narrow(self, old: TypeVal, new: TypeVal) -> TypeVal:
        return old.meet(new)

    @override
    def on_reduce_value(
        self,
        var: str,
        addrs: frozenset[object],
        elem: TypeVal,
        registry: DomainRegistry,
        budget: AnalysisBudget | None = None,
    ) -> TypeVal:
        """Derive type tokens from the address set."""
        if not addrs:
            return elem

        derived = self._derive_from_addrs(addrs)
        if derived is None:
            return elem

        # Merge with existing element
        combined = derived if elem.is_top() or elem.is_bottom() else elem.join(derived)

        # Apply max_tokens cap
        max_tokens = self.max_tokens
        if budget is not None and hasattr(budget, "max_ho_tokens"):
            max_tokens = budget.max_ho_tokens

        if combined.kind == TypeKind.SET and len(combined.tokens) > max_tokens:
            return TypeVal.top()

        return combined

    def _derive_from_addrs(self, addrs: frozenset[object]) -> TypeVal | None:
        """Derive type tokens from KCFA address set."""
        from pythonstan.analysis.pointer.kcfa.object import AllocKind

        tokens: set[ClassRef] = set()
        for obj in addrs:
            if isinstance(obj, AbstractObject) and obj.kind == AllocKind.CLASS:
                tokens.add(ClassRef(obj))

        if not tokens:
            return None
        return TypeVal.from_tokens(frozenset(tokens))
