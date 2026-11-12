"""Integer domain for numeric precision in abstract interpretation.

Enhanced integer lattice with:
- Bounded set of constant integers (enumerable form)
- Interval bounds [lo, hi] with optional infinite endpoints
- Parity tracking (even/odd/unknown)

This improves precision for:
- range loops and integer-based indexing
- Metaprogramming patterns where ints become strings (f-strings/str/format)
- Branching on integer comparisons (x == 0, x < k, etc.)

Design notes:
- bottom means "definitely not an int / unreachable for int component"
- top means "any int" (unbounded interval, unknown parity, no consts)
- Widening caps control const-set size; overflow triggers interval collapse
"""

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from typing import (
    TypedDict,
    TypeGuard,
    override,
)

from .base import Domain, Fact, FactPayload

__all__ = ["IntDomain", "IntLattice", "Parity"]


# =============================================================================
# Parity - Even/Odd tracking
# =============================================================================


class Parity(Enum):
    """Parity lattice for integers.

    Lattice structure::

            TOP (unknown)
           /   \\
        EVEN   ODD
           \\   /
           BOTTOM
    """

    _value_: str

    BOTTOM = "bottom"
    EVEN = "even"
    ODD = "odd"
    TOP = "top"


class _IntFields(TypedDict):
    consts: frozenset[int]
    lo: int | None
    hi: int | None
    parity: Parity
    is_bottom: bool
    is_top: bool


def _integer_set(value: FactPayload) -> TypeGuard[frozenset[int]]:
    return isinstance(value, frozenset) and all(isinstance(item, int) for item in value)


def _parity_join(a: Parity, b: Parity) -> Parity:
    """Join two parity values."""
    if a == Parity.BOTTOM:
        return b
    if b == Parity.BOTTOM:
        return a
    if a == b:
        return a
    return Parity.TOP


def _parity_meet(a: Parity, b: Parity) -> Parity:
    """Meet two parity values."""
    if a == Parity.TOP:
        return b
    if b == Parity.TOP:
        return a
    if a == b:
        return a
    return Parity.BOTTOM


def _parity_of(n: int) -> Parity:
    """Get parity of an integer."""
    return Parity.EVEN if n % 2 == 0 else Parity.ODD


# =============================================================================
# IntLattice - Integer lattice element
# =============================================================================


@dataclass(frozen=True, eq=True)
class IntLattice:
    """Abstract integer value with bounded constants and interval.

    Representation invariants:
    - If is_bottom=True, represents no integers (unreachable)
    - If is_top=True, represents any integer
    - If consts is nonempty: exactly that finite set; bounds/parity are cached facts
    - Otherwise: the interval [lo, hi] restricted by parity

    Concretization γ(elem):
        If is_bottom: ∅
        If is_top: ℤ
        If consts: consts
        Otherwise: {x | lo ≤ x ≤ hi and parity(x) matches parity}

    Note: lo=None means -∞, hi=None means +∞.
    """

    # Finite set of exact constants (only populated in const-only form)
    consts: frozenset[int]

    # Interval bounds (None = unbounded)
    lo: int | None
    hi: int | None

    # Parity constraint
    parity: Parity

    # Explicit bottom/top flags for canonical form
    is_bottom: bool
    is_top: bool

    def __post_init__(self) -> None:
        # The public dataclass constructor obeys the same invariants as factory
        # methods; raw finite values cannot carry contradictory cached bounds.
        values: _IntFields
        if self.is_bottom:
            values = {
                "consts": frozenset(),
                "lo": None,
                "hi": None,
                "parity": Parity.BOTTOM,
                "is_bottom": True,
                "is_top": False,
            }
        elif self.is_top:
            values = {
                "consts": frozenset(),
                "lo": None,
                "hi": None,
                "parity": Parity.TOP,
                "is_bottom": False,
                "is_top": True,
            }
        elif self.consts:
            consts = frozenset(self.consts)
            parity = Parity.BOTTOM
            for value in consts:
                parity = _parity_join(parity, _parity_of(value))
            values = {
                "consts": consts,
                "lo": min(consts),
                "hi": max(consts),
                "parity": parity,
                "is_bottom": False,
                "is_top": False,
            }
        else:
            lo, hi, parity = self.lo, self.hi, self.parity
            if parity in (Parity.EVEN, Parity.ODD):
                wanted = 0 if parity == Parity.EVEN else 1
                if lo is not None:
                    lo += (wanted - lo) % 2
                if hi is not None:
                    hi -= (hi - wanted) % 2
            bottom = parity == Parity.BOTTOM or (lo is not None and hi is not None and lo > hi)
            if bottom:
                values = {
                    "consts": frozenset(),
                    "lo": None,
                    "hi": None,
                    "parity": Parity.BOTTOM,
                    "is_bottom": True,
                    "is_top": False,
                }
            else:
                values = {
                    "consts": frozenset(),
                    "lo": lo,
                    "hi": hi,
                    "parity": parity,
                    "is_bottom": False,
                    "is_top": lo is None and hi is None and (parity == Parity.TOP),
                }
        for name, field_value in values.items():
            object.__setattr__(self, name, field_value)

    # -------------------------------------------------------------------------
    # Factory methods
    # -------------------------------------------------------------------------

    @classmethod
    def bottom(cls) -> "IntLattice":
        """No integers (unreachable / not an int)."""
        return cls(
            consts=frozenset(),
            lo=None,
            hi=None,
            parity=Parity.BOTTOM,
            is_bottom=True,
            is_top=False,
        )

    @classmethod
    def top(cls) -> "IntLattice":
        """Any integer (ℤ)."""
        return cls(
            consts=frozenset(),
            lo=None,
            hi=None,
            parity=Parity.TOP,
            is_bottom=False,
            is_top=True,
        )

    @classmethod
    def const(cls, n: int) -> "IntLattice":
        """Single constant integer."""
        return cls(
            consts=frozenset({n}),
            lo=n,
            hi=n,
            parity=_parity_of(n),
            is_bottom=False,
            is_top=False,
        )

    @classmethod
    def from_consts(cls, ints: frozenset[int]) -> "IntLattice":
        """Set of constant integers."""
        if len(ints) == 0:
            return cls.bottom()
        lo = min(ints)
        hi = max(ints)
        parity = Parity.BOTTOM
        for n in ints:
            parity = _parity_join(parity, _parity_of(n))
        return cls(
            consts=ints,
            lo=lo,
            hi=hi,
            parity=parity,
            is_bottom=False,
            is_top=False,
        )

    @classmethod
    def from_interval(
        cls,
        lo: int | None,
        hi: int | None,
        parity: Parity = Parity.TOP,
    ) -> "IntLattice":
        """Create from interval [lo, hi] with optional parity.

        Args:
            lo: Lower bound (None = -∞)
            hi: Upper bound (None = +∞)
            parity: Parity constraint

        Returns:
            IntLattice representing the interval
        """
        if parity == Parity.BOTTOM:
            return cls.bottom()
        # Empty interval check
        if lo is not None and hi is not None and lo > hi:
            return cls.bottom()

        if parity in (Parity.EVEN, Parity.ODD):
            wanted = 0 if parity == Parity.EVEN else 1
            if lo is not None:
                lo += (wanted - lo) % 2
            if hi is not None:
                hi -= (hi - wanted) % 2
            if lo is not None and hi is not None and lo > hi:
                return cls.bottom()
        # Check if this is effectively top
        if lo is None and hi is None and parity == Parity.TOP:
            return cls.top()

        return cls(
            consts=frozenset(),
            lo=lo,
            hi=hi,
            parity=parity,
            is_bottom=False,
            is_top=False,
        )

    @classmethod
    def zero(cls) -> "IntLattice":
        """Singleton zero."""
        return cls.const(0)

    @classmethod
    def nonzero(cls) -> "IntLattice":
        """Any nonzero integer.

        Represented as interval union: (-∞, -1] ∪ [1, +∞).
        Simplified to top interval with exclusion of 0 is tricky,
        so we use top with a note that this is an approximation.
        For better precision in practice, we rely on refinement.
        """
        # Approximate: return top for now
        # More precise: track excluded values (out of scope for MVP)
        return cls.top()

    @classmethod
    def nonneg(cls) -> "IntLattice":
        """Non-negative integers [0, +∞)."""
        return cls.from_interval(0, None)

    @classmethod
    def positive(cls) -> "IntLattice":
        """Positive integers [1, +∞)."""
        return cls.from_interval(1, None)

    @classmethod
    def negative(cls) -> "IntLattice":
        """Negative integers (-∞, -1]."""
        return cls.from_interval(None, -1)

    @classmethod
    def nonpos(cls) -> "IntLattice":
        """Non-positive integers (-∞, 0]."""
        return cls.from_interval(None, 0)

    # -------------------------------------------------------------------------
    # Normalization
    # -------------------------------------------------------------------------

    @classmethod
    def _normalize(
        cls,
        consts: frozenset[int],
        lo: int | None,
        hi: int | None,
        parity: Parity,
    ) -> "IntLattice":
        """Normalize an exact finite set OR an interval, never their union.

        This compatibility helper interprets nonempty consts as exact. Transfers
        that need an interval hull call from_interval explicitly.
        """
        if consts:
            return cls.from_consts(consts)
        return cls.from_interval(lo, hi, parity)

    # -------------------------------------------------------------------------
    # Predicates
    # -------------------------------------------------------------------------

    def is_const(self) -> bool:
        """Check if this is a finite const set (no interval extension)."""
        return not self.is_bottom and not self.is_top and len(self.consts) > 0

    def is_singleton(self) -> bool:
        """Check if this is exactly one integer."""
        return self.is_const() and len(self.consts) == 1

    def is_enumerable(self) -> bool:
        """Check if we can enumerate all possible integers.

        Only enumerable when we have exact constants.
        """
        return self.is_const()

    def get_singleton(self) -> int | None:
        """Get singleton value if this is a single constant, else None."""
        if self.is_singleton():
            return next(iter(self.consts))
        return None

    def get_consts(self) -> frozenset[int] | None:
        """Get constant integers if enumerable, else None."""
        if self.is_enumerable():
            return self.consts
        return None

    def is_zero(self) -> bool:
        """Check if this is definitely zero."""
        return self.consts == frozenset({0})

    def may_be_zero(self) -> bool:
        """Whether zero belongs to this element's concretization."""
        return self.may_contain(0)

    def may_be_negative(self) -> bool:
        """Check if this may be negative."""
        if self.is_bottom:
            return False
        if self.is_top:
            return True
        if any(n < 0 for n in self.consts):
            return True
        if self.lo is None:
            return True
        return self.lo < 0

    def may_be_positive(self) -> bool:
        """Check if this may be positive."""
        if self.is_bottom:
            return False
        if self.is_top:
            return True
        if any(n > 0 for n in self.consts):
            return True
        if self.hi is None:
            return True
        return self.hi > 0

    def is_nonneg(self) -> bool:
        """Check if definitely non-negative."""
        if self.is_bottom or self.is_top:
            return False
        if self.lo is not None and self.lo >= 0:
            return all(n >= 0 for n in self.consts)
        return False

    def is_nonpos(self) -> bool:
        """Check if definitely non-positive."""
        if self.is_bottom or self.is_top:
            return False
        if self.hi is not None and self.hi <= 0:
            return all(n <= 0 for n in self.consts)
        return False

    def is_even(self) -> bool:
        """Check if definitely even."""
        return self.parity == Parity.EVEN

    def is_odd(self) -> bool:
        """Check if definitely odd."""
        return self.parity == Parity.ODD

    # -------------------------------------------------------------------------
    # Filtering / containment
    # -------------------------------------------------------------------------

    def contains(self, n: int) -> bool:
        """Membership in this domain element's explicitly defined concretization."""
        return self.may_contain(n)

    def may_contain(self, n: int) -> bool:
        if self.is_bottom:
            return False
        if self.is_top:
            return True
        if self.consts:
            return n in self.consts
        return (
            (self.lo is None or n >= self.lo)
            and (self.hi is None or n <= self.hi)
            and self.parity != Parity.BOTTOM
            and (self.parity == Parity.TOP or n % 2 == (self.parity == Parity.ODD))
        )

    # -------------------------------------------------------------------------
    # Comparison helpers for condition refinement
    # -------------------------------------------------------------------------

    def refine_eq(self, n: int) -> "IntLattice":
        """Refine to x == n."""
        if not self.may_contain(n):
            return IntLattice.bottom()
        return IntLattice.const(n)

    def refine_neq(self, n: int) -> "IntLattice":
        """Refine to x != n."""
        if self.is_bottom:
            return self
        if self.is_top:
            # Can't precisely represent "all except n" without exclusion sets
            return self
        if self.is_singleton():
            if self.get_singleton() == n:
                return IntLattice.bottom()
            return self
        if self.is_enumerable():
            new_consts = self.consts - {n}
            if not new_consts:
                return IntLattice.bottom()
            return IntLattice.from_consts(new_consts)
        return self

    def refine_lt(self, n: int) -> "IntLattice":
        """Refine to x < n (i.e., x <= n-1)."""
        if self.is_bottom:
            return self
        if self.is_top:
            return IntLattice.from_interval(None, n - 1)
        new_hi = n - 1
        if self.hi is not None:
            new_hi = min(self.hi, n - 1)
        if self.lo is not None and new_hi < self.lo:
            return IntLattice.bottom()
        if self.is_enumerable():
            new_consts = frozenset(c for c in self.consts if c < n)
            return IntLattice.from_consts(new_consts)
        return IntLattice.from_interval(self.lo, new_hi, self.parity)

    def refine_le(self, n: int) -> "IntLattice":
        """Refine to x <= n."""
        if self.is_bottom:
            return self
        if self.is_top:
            return IntLattice.from_interval(None, n)
        new_hi = n
        if self.hi is not None:
            new_hi = min(self.hi, n)
        if self.lo is not None and new_hi < self.lo:
            return IntLattice.bottom()
        if self.is_enumerable():
            new_consts = frozenset(c for c in self.consts if c <= n)
            return IntLattice.from_consts(new_consts)
        return IntLattice.from_interval(self.lo, new_hi, self.parity)

    def refine_gt(self, n: int) -> "IntLattice":
        """Refine to x > n (i.e., x >= n+1)."""
        if self.is_bottom:
            return self
        if self.is_top:
            return IntLattice.from_interval(n + 1, None)
        new_lo = n + 1
        if self.lo is not None:
            new_lo = max(self.lo, n + 1)
        if self.hi is not None and new_lo > self.hi:
            return IntLattice.bottom()
        if self.is_enumerable():
            new_consts = frozenset(c for c in self.consts if c > n)
            return IntLattice.from_consts(new_consts)
        return IntLattice.from_interval(new_lo, self.hi, self.parity)

    def refine_ge(self, n: int) -> "IntLattice":
        """Refine to x >= n."""
        if self.is_bottom:
            return self
        if self.is_top:
            return IntLattice.from_interval(n, None)
        new_lo = n
        if self.lo is not None:
            new_lo = max(self.lo, n)
        if self.hi is not None and new_lo > self.hi:
            return IntLattice.bottom()
        if self.is_enumerable():
            new_consts = frozenset(c for c in self.consts if c >= n)
            return IntLattice.from_consts(new_consts)
        return IntLattice.from_interval(new_lo, self.hi, self.parity)

    # -------------------------------------------------------------------------
    # String representation
    # -------------------------------------------------------------------------

    @override
    def __str__(self) -> str:
        if self.is_bottom:
            return "⊥_int"
        if self.is_top:
            return "⊤_int"

        parts = []

        if self.consts:
            if len(self.consts) <= 5:
                parts.append("{" + ", ".join(str(n) for n in sorted(self.consts)) + "}")
            else:
                parts.append(f"{{...{len(self.consts)} ints}}")
        elif self.lo is not None or self.hi is not None:
            lo_str = str(self.lo) if self.lo is not None else "-∞"
            hi_str = str(self.hi) if self.hi is not None else "+∞"
            parts.append(f"[{lo_str}, {hi_str}]")

        if self.parity not in (Parity.TOP, Parity.BOTTOM):
            parts.append("(even)" if self.parity == Parity.EVEN else "(odd)")

        return " ".join(parts) if parts else "⊥_int"


# =============================================================================
# IntDomain - Domain implementation
# =============================================================================


class IntDomain(Domain[IntLattice]):
    """Integer domain for numeric precision.

    Implements widening with:
    - Constant cap: when const set exceeds max_consts, compress to interval
    - Interval widening: when bounds keep changing, widen to ±∞

    Widening caps (from budget):
    - budget.max_const_ints: max constant set size
    - budget.value_partition_int_cap: max ints for branch partitioning
    """

    def __init__(self, max_consts: int = 10) -> None:
        """Initialize with widening caps.

        Args:
            max_consts: Maximum constant integers before compression
        """
        self.max_consts: int = max_consts

    @override
    def public_settings(self) -> Mapping[str, int]:
        return {"max_consts": self.max_consts}

    @property
    @override
    def name(self) -> str:
        return "int"

    @override
    def bottom(self) -> IntLattice:
        return IntLattice.bottom()

    @override
    def top(self) -> IntLattice:
        return IntLattice.top()

    @override
    def join(self, a: IntLattice, b: IntLattice) -> IntLattice:
        """Union of exact finite sets, otherwise a parity-restricted hull."""
        if a.is_bottom:
            return b
        if b.is_bottom:
            return a
        if a.is_top or b.is_top:
            return IntLattice.top()
        if a.is_enumerable() and b.is_enumerable():
            return IntLattice.from_consts(a.consts | b.consts)
        lo = None if a.lo is None or b.lo is None else min(a.lo, b.lo)
        hi = None if a.hi is None or b.hi is None else max(a.hi, b.hi)
        return IntLattice.from_interval(lo, hi, _parity_join(a.parity, b.parity))

    @override
    def meet(self, a: IntLattice, b: IntLattice) -> IntLattice:
        """Exact intersection; finite sets are never replaced by their hull."""
        if a.is_bottom or b.is_bottom:
            return IntLattice.bottom()
        if a.is_top:
            return b
        if b.is_top:
            return a
        if a.is_enumerable():
            return IntLattice.from_consts(frozenset(n for n in a.consts if b.may_contain(n)))
        if b.is_enumerable():
            return IntLattice.from_consts(frozenset(n for n in b.consts if a.may_contain(n)))
        lo = b.lo if a.lo is None else a.lo if b.lo is None else max(a.lo, b.lo)
        hi = b.hi if a.hi is None else a.hi if b.hi is None else min(a.hi, b.hi)
        return IntLattice.from_interval(lo, hi, _parity_meet(a.parity, b.parity))

    @override
    def leq(self, a: IntLattice, b: IntLattice) -> bool:
        """Concretization inclusion for the disjoint finite/interval forms."""
        if a.is_bottom or b.is_top:
            return True
        if b.is_bottom:
            return False
        if a.is_enumerable():
            return all(b.may_contain(n) for n in a.consts)
        if b.is_enumerable():
            if a.lo is None or a.hi is None:
                return False
            step = 1 if a.parity == Parity.TOP else 2
            # Avoid enumerating a wide interval merely to refute containment.
            if (a.hi - a.lo) // step + 1 > len(b.consts):
                return False
            return all(n in b.consts for n in range(a.lo, a.hi + 1, step))
        if a.is_top:
            return b.is_top
        if b.lo is not None and (a.lo is None or a.lo < b.lo):
            return False
        if b.hi is not None and (a.hi is None or a.hi > b.hi):
            return False
        return b.parity == Parity.TOP or a.parity == b.parity

    @override
    def widen(self, old: IntLattice, new: IntLattice) -> IntLattice:
        """Cover the union, dropping exact enumeration once bounds widen."""
        joined = self.join(old, new)
        if joined.is_bottom or joined.is_top:
            return joined
        if old.is_bottom:
            return (
                joined
                if len(joined.consts) <= self.max_consts
                else IntLattice.from_interval(joined.lo, joined.hi, joined.parity)
            )
        if (
            joined.is_enumerable()
            and len(joined.consts) <= self.max_consts
            and self.leq(joined, old)
        ):
            return joined
        lo, hi = joined.lo, joined.hi
        changed = False
        if old.lo is not None and (lo is None or lo < old.lo):
            lo = None
            changed = True
        if old.hi is not None and (hi is None or hi > old.hi):
            hi = None
            changed = True
        if joined.is_enumerable() and not changed and len(joined.consts) <= self.max_consts:
            return joined
        return IntLattice.from_interval(lo, hi, joined.parity)

    @override
    def narrow(self, old: IntLattice, new: IntLattice) -> IntLattice:
        """Narrowing: refine with meet."""
        return self.meet(old, new)

    @override
    def abstract_const(self, value: object) -> IntLattice | None:
        """Abstract a Python constant.

        Important: bool is a subclass of int in Python, but we do NOT
        treat True/False as integers here - the bool domain handles them.
        """
        # Exclude bools (they go to bool domain)
        if isinstance(value, bool):
            return None
        if isinstance(value, int):
            return IntLattice.const(value)
        return None

    @override
    def produce_facts(self, var: str, elem: IntLattice) -> list[Fact]:
        """Produce facts about integer values.

        Fact kinds:
        - "is_singleton_int": data=int (exact value)
        - "is_const_int_set": data=FrozenSet[int] (enumerable)
        - "in_int_range": data=(lo, hi)
        - "is_zero": data=None
        - "is_nonzero": data=None (when 0 not in range)
        - "is_pos" / "is_nonneg" / "is_neg" / "is_nonpos": data=None
        - "is_even" / "is_odd": data=None
        """
        facts: list[Fact] = []

        if elem.is_bottom or elem.is_top:
            return facts

        # Singleton
        if elem.is_singleton():
            n = elem.get_singleton()
            assert n is not None
            facts.append(Fact("is_singleton_int", var, n))
            if n == 0:
                facts.append(Fact("is_zero", var, None))
            else:
                facts.append(Fact("is_nonzero", var, None))

        # Enumerable const set
        elif elem.is_enumerable():
            facts.append(Fact("is_const_int_set", var, elem.consts))
            if 0 not in elem.consts:
                facts.append(Fact("is_nonzero", var, None))

        # Interval bounds
        if elem.lo is not None or elem.hi is not None:
            facts.append(Fact("in_int_range", var, (elem.lo, elem.hi)))

        # Sign facts
        if elem.is_nonneg():
            facts.append(Fact("is_nonneg", var, None))
            if elem.lo is not None and elem.lo > 0:
                facts.append(Fact("is_pos", var, None))
        if elem.is_nonpos():
            facts.append(Fact("is_nonpos", var, None))
            if elem.hi is not None and elem.hi < 0:
                facts.append(Fact("is_neg", var, None))

        # Zero check from interval
        if not elem.may_be_zero() and not elem.is_singleton():
            facts.append(Fact("is_nonzero", var, None))

        # Parity facts
        if elem.parity == Parity.EVEN:
            facts.append(Fact("is_even", var, None))
        elif elem.parity == Parity.ODD:
            facts.append(Fact("is_odd", var, None))

        return facts

    @override
    def consume_fact(self, elem: IntLattice, fact: Fact) -> IntLattice:
        """Consume a fact to refine integer abstraction."""
        if elem.is_bottom:
            return elem

        if fact.kind == "is_singleton_int" and isinstance(fact.data, int):
            return self.meet(elem, IntLattice.const(fact.data))

        if fact.kind == "is_const_int_set" and _integer_set(fact.data):
            return self.meet(elem, IntLattice.from_consts(fact.data))

        if fact.kind == "in_int_range" and isinstance(fact.data, tuple):
            lo, hi = fact.data
            return self.meet(elem, IntLattice.from_interval(lo, hi))

        if fact.kind == "is_zero":
            return self.meet(elem, IntLattice.zero())

        if fact.kind == "is_nonneg":
            return self.meet(elem, IntLattice.nonneg())

        if fact.kind == "is_pos":
            return self.meet(elem, IntLattice.positive())

        if fact.kind == "is_neg":
            return self.meet(elem, IntLattice.negative())

        if fact.kind == "is_nonpos":
            return self.meet(elem, IntLattice.nonpos())

        if fact.kind == "is_even":
            if elem.is_top:
                return IntLattice.from_interval(None, None, Parity.EVEN)
            if elem.is_enumerable():
                new_consts = frozenset(c for c in elem.consts if c % 2 == 0)
                return IntLattice.from_consts(new_consts)
            return IntLattice.from_interval(
                elem.lo, elem.hi, _parity_meet(elem.parity, Parity.EVEN)
            )

        if fact.kind == "is_odd":
            if elem.is_top:
                return IntLattice.from_interval(None, None, Parity.ODD)
            if elem.is_enumerable():
                new_consts = frozenset(c for c in elem.consts if c % 2 == 1)
                return IntLattice.from_consts(new_consts)
            return IntLattice.from_interval(elem.lo, elem.hi, _parity_meet(elem.parity, Parity.ODD))

        if fact.kind == "is_none":
            # Not an int if it's None
            return IntLattice.bottom()

        return elem
