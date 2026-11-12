"""Callable domain for function/method/bound-method tokens.

This domain tracks callable objects (functions, methods, bound methods)
for call dispatch precision.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, override

from pythonstan.analysis.contracts.budgets import AnalysisBudget
from pythonstan.analysis.domains.base import Domain, DomainRegistry
from pythonstan.analysis.pointer.kcfa.object import AbstractObject

if TYPE_CHECKING:
    from pythonstan.compat.legacy_ai.value_domain import AbsVal, AddrSet, CallableTarget

__all__ = [
    "CallableDomain",
    "CallableVal",
    "CallableKind",
    "FunctionRef",
    "BoundMethodRef",
]


class CallableKind(Enum):
    """Stratification for callable values."""

    BOTTOM = "bottom"
    SET = "set"
    TOP = "top"


@dataclass(frozen=True)
class FunctionRef:
    """Token for function/method objects."""

    obj: "AbstractObject"


@dataclass(frozen=True)
class BoundMethodRef:
    """Token for bound method objects."""

    obj: "AbstractObject"
    receiver_addrs: "AddrSet"


type CallableToken = FunctionRef | BoundMethodRef


@dataclass(frozen=True)
class CallableVal:
    """Abstract callable value."""

    kind: CallableKind = CallableKind.BOTTOM
    tokens: frozenset[CallableToken] = field(default_factory=frozenset)

    @classmethod
    def bottom(cls) -> "CallableVal":
        return cls(CallableKind.BOTTOM, frozenset())

    @classmethod
    def top(cls) -> "CallableVal":
        return cls(CallableKind.TOP, frozenset())

    @classmethod
    def from_tokens(cls, tokens: frozenset[CallableToken]) -> "CallableVal":
        if len(tokens) == 0:
            return cls.bottom()
        return cls(CallableKind.SET, tokens)

    def is_bottom(self) -> bool:
        return self.kind == CallableKind.BOTTOM

    def is_top(self) -> bool:
        return self.kind == CallableKind.TOP

    def join(self, other: "CallableVal") -> "CallableVal":
        if self.is_bottom():
            return other
        if other.is_bottom():
            return self
        if self.is_top() or other.is_top():
            return CallableVal.top()
        return CallableVal.from_tokens(self.tokens | other.tokens)

    def meet(self, other: "CallableVal") -> "CallableVal":
        if self.is_top():
            return other
        if other.is_top():
            return self
        if self.is_bottom() or other.is_bottom():
            return CallableVal.bottom()
        return CallableVal.from_tokens(self.tokens & other.tokens)

    def leq(self, other: "CallableVal") -> bool:
        if self.is_bottom():
            return True
        if other.is_top():
            return True
        if self.is_top():
            return other.is_top()
        if other.kind != CallableKind.SET:
            return False
        return self.tokens <= other.tokens

    def to_callable_targets(self) -> frozenset["CallableTarget"]:
        """Extract callables to feed call dispatch."""
        from pythonstan.compat.legacy_ai.value_domain import AbsVal, CallableTarget

        if self.is_top() or self.is_bottom():
            return frozenset()
        targets: set[CallableTarget] = set()
        for tok in self.tokens:
            if isinstance(tok, BoundMethodRef):
                receiver = AbsVal.from_addrs(tok.receiver_addrs)
                targets.add(CallableTarget(tok.obj, receiver))
            else:
                targets.add(CallableTarget(tok.obj))
        return frozenset(targets)

    def bind_receiver(
        self,
        owner: "AbstractObject",
        receiver: "AbsVal",
    ) -> "CallableVal":
        """Bind functions to a receiver to create bound methods."""
        if self.is_top() or self.is_bottom():
            return self
        if not receiver.addrs:
            return self
        from pythonstan.analysis.pointer.kcfa.object import AllocKind

        if getattr(owner, "kind", None) != AllocKind.CLASS:
            return self
        receiver_addrs = frozenset(
            obj for obj in receiver.addrs if getattr(obj, "kind", None) != AllocKind.CLASS
        )
        if not receiver_addrs:
            return self
        new_tokens: set[CallableToken] = set(self.tokens)
        for tok in self.tokens:
            if isinstance(tok, FunctionRef):
                new_tokens.add(BoundMethodRef(tok.obj, receiver_addrs))
        return CallableVal.from_tokens(frozenset(new_tokens))

    @override
    def __str__(self) -> str:
        if self.is_bottom():
            return "⊥_callable"
        if self.is_top():
            return "⊤_callable"
        return f"Callable({len(self.tokens)} tokens)"


class CallableDomain(Domain[CallableVal]):
    """Domain for callable tokens with bounded set widening."""

    def __init__(self, max_tokens: int = 20) -> None:
        self.max_tokens = max_tokens

    @property
    @override
    def name(self) -> str:
        return "callable"

    @override
    def bottom(self) -> CallableVal:
        return CallableVal.bottom()

    @override
    def top(self) -> CallableVal:
        return CallableVal.top()

    @override
    def join(self, a: CallableVal, b: CallableVal) -> CallableVal:
        return a.join(b)

    @override
    def meet(self, a: CallableVal, b: CallableVal) -> CallableVal:
        return a.meet(b)

    @override
    def leq(self, a: CallableVal, b: CallableVal) -> bool:
        return a.leq(b)

    @override
    def widen(self, old: CallableVal, new: CallableVal) -> CallableVal:
        joined = old.join(new)
        if joined.kind == CallableKind.SET and len(joined.tokens) > self.max_tokens:
            return CallableVal.top()
        return joined

    @override
    def narrow(self, old: CallableVal, new: CallableVal) -> CallableVal:
        return old.meet(new)

    @override
    def on_reduce_value(
        self,
        var: str,
        addrs: frozenset[object],
        elem: CallableVal,
        registry: DomainRegistry,
        budget: AnalysisBudget | None = None,
    ) -> CallableVal:
        """Derive callable tokens from the address set."""
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

        if combined.kind == CallableKind.SET and len(combined.tokens) > max_tokens:
            return CallableVal.top()

        return combined

    def _derive_from_addrs(self, addrs: frozenset[object]) -> CallableVal | None:
        """Derive callable tokens from KCFA address set."""
        from pythonstan.analysis.pointer.kcfa.object import AllocKind

        tokens: set[CallableToken] = set()
        for obj in addrs:
            if isinstance(obj, AbstractObject) and obj.kind in (
                AllocKind.FUNCTION,
                AllocKind.METHOD,
                AllocKind.BOUND_METHOD,
                AllocKind.BUILTIN,
            ):
                tokens.add(FunctionRef(obj))

        if not tokens:
            return None
        return CallableVal.from_tokens(frozenset(tokens))
