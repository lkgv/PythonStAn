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

from dataclasses import dataclass
from enum import Enum
from typing import (
    Any,
    FrozenSet,
    List,
    Optional,
    Set,
)

from .base import Domain, Fact

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

    BOTTOM = "bottom"
    EVEN = "even"
    ODD = "odd"
    TOP = "top"


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
    - Otherwise: represents union of consts and interval [lo, hi]

    Concretization γ(elem):
        If is_bottom: ∅
        If is_top: ℤ
        Otherwise: consts ∪ {x | lo ≤ x ≤ hi}

    Note: lo=None means -∞, hi=None means +∞.
    """

    # Finite set of exact constants (only populated in const-only form)
    consts: FrozenSet[int]

    # Interval bounds (None = unbounded)
    lo: Optional[int]
    hi: Optional[int]

    # Parity constraint
    parity: Parity

    # Explicit bottom/top flags for canonical form
    is_bottom: bool
    is_top: bool

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
    def from_consts(cls, ints: FrozenSet[int]) -> "IntLattice":
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
        lo: Optional[int],
        hi: Optional[int],
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
        # Empty interval check
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
        consts: FrozenSet[int],
        lo: Optional[int],
        hi: Optional[int],
        parity: Parity,
    ) -> "IntLattice":
        """Normalize the lattice element.

        Normalization rules:
        1. Empty consts + empty/invalid interval = bottom
        2. Unbounded interval + TOP parity + empty consts = top
        3. Consts within interval are redundant; keep only consts if small
        4. Parity must be consistent with consts if enumerable
        """
        # Rule 1: Check for bottom
        has_interval = not (lo is not None and hi is not None and lo > hi)
        if len(consts) == 0 and not has_interval:
            return cls.bottom()

        # Rule 2: Check for top
        if (
            len(consts) == 0
            and lo is None
            and hi is None
            and parity == Parity.TOP
        ):
            return cls.top()

        # Compute effective bounds including consts
        all_values: Set[int] = set(consts)
        if lo is not None and hi is not None:
            # Only enumerate small intervals
            if hi - lo <= 100:
                all_values.update(range(lo, hi + 1))
            else:
                # Large interval: cannot enumerate
                new_lo = lo if lo is not None else (min(consts) if consts else None)
                new_hi = hi if hi is not None else (max(consts) if consts else None)
                if consts:
                    new_lo = min(new_lo, min(consts)) if new_lo is not None else min(consts)
                    new_hi = max(new_hi, max(consts)) if new_hi is not None else max(consts)
                return cls(
                    consts=frozenset(),
                    lo=new_lo,
                    hi=new_hi,
                    parity=parity,
                    is_bottom=False,
                    is_top=False,
                )

        if len(all_values) == 0:
            return cls.bottom()

        # Compute parity from values if enumerable
        computed_parity = Parity.BOTTOM
        for n in all_values:
            computed_parity = _parity_join(computed_parity, _parity_of(n))

        return cls(
            consts=frozenset(all_values),
            lo=min(all_values),
            hi=max(all_values),
            parity=computed_parity,
            is_bottom=False,
            is_top=False,
        )

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

    def get_singleton(self) -> Optional[int]:
        """Get singleton value if this is a single constant, else None."""
        if self.is_singleton():
            return next(iter(self.consts))
        return None

    def get_consts(self) -> Optional[FrozenSet[int]]:
        """Get constant integers if enumerable, else None."""
        if self.is_enumerable():
            return self.consts
        return None

    def is_zero(self) -> bool:
        """Check if this is definitely zero."""
        return self.consts == frozenset({0})

    def may_be_zero(self) -> bool:
        """Check if this may be zero."""
        if self.is_bottom:
            return False
        if self.is_top:
            return True
        if 0 in self.consts:
            return True
        if self.lo is not None and self.hi is not None:
            return self.lo <= 0 <= self.hi
        if self.lo is None and self.hi is not None:
            return 0 <= self.hi
        if self.lo is not None and self.hi is None:
            return self.lo <= 0
        return True

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
        """Check if n is definitely in this abstract value."""
        if self.is_bottom:
            return False
        if self.is_top:
            return True
        if n in self.consts:
            return True
        if self.lo is not None and self.hi is not None:
            if self.lo <= n <= self.hi:
                return len(self.consts) == 0 or self.hi - self.lo <= 100
        return False

    def may_contain(self, n: int) -> bool:
        """Check if n may be in this abstract value."""
        if self.is_bottom:
            return False
        if self.is_top:
            return True
        if n in self.consts:
            return True
        # Check interval
        lo_ok = self.lo is None or n >= self.lo
        hi_ok = self.hi is None or n <= self.hi
        return lo_ok and hi_ok

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
            parts.append(f"({self.parity.value})")

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

    def __init__(self, max_consts: int = 10):
        """Initialize with widening caps.

        Args:
            max_consts: Maximum constant integers before compression
        """
        self.max_consts = max_consts

    @property
    def name(self) -> str:
        return "int"

    def bottom(self) -> IntLattice:
        return IntLattice.bottom()

    def top(self) -> IntLattice:
        return IntLattice.top()

    def join(self, a: IntLattice, b: IntLattice) -> IntLattice:
        """Join two integer abstractions (⊔).

        Join is union of information:
        - Consts: union
        - Interval: hull (min lo, max hi)
        - Parity: join
        """
        # Bottom is identity
        if a.is_bottom:
            return b
        if b.is_bottom:
            return a

        # Top absorbs
        if a.is_top or b.is_top:
            return IntLattice.top()

        # Union consts
        new_consts = a.consts | b.consts

        # Interval hull
        if a.lo is None or b.lo is None:
            new_lo = None
        else:
            new_lo = min(a.lo, b.lo)

        if a.hi is None or b.hi is None:
            new_hi = None
        else:
            new_hi = max(a.hi, b.hi)

        # Parity join
        new_parity = _parity_join(a.parity, b.parity)

        return IntLattice._normalize(new_consts, new_lo, new_hi, new_parity)

    def meet(self, a: IntLattice, b: IntLattice) -> IntLattice:
        """Meet two integer abstractions (⊓).

        Meet is intersection of constraints:
        - Consts: intersection (if both enumerable)
        - Interval: intersection (max lo, min hi)
        - Parity: meet
        """
        # Top is identity
        if a.is_top:
            return b
        if b.is_top:
            return a

        # Bottom absorbs
        if a.is_bottom or b.is_bottom:
            return IntLattice.bottom()

        # Interval intersection
        new_lo: Optional[int] = None
        if a.lo is not None and b.lo is not None:
            new_lo = max(a.lo, b.lo)
        elif a.lo is not None:
            new_lo = a.lo
        elif b.lo is not None:
            new_lo = b.lo

        new_hi: Optional[int] = None
        if a.hi is not None and b.hi is not None:
            new_hi = min(a.hi, b.hi)
        elif a.hi is not None:
            new_hi = a.hi
        elif b.hi is not None:
            new_hi = b.hi

        # Check for empty interval
        if new_lo is not None and new_hi is not None and new_lo > new_hi:
            return IntLattice.bottom()

        # Consts: filter by the other's containment
        new_consts: FrozenSet[int] = frozenset()
        if a.is_enumerable() and b.is_enumerable():
            new_consts = a.consts & b.consts
        elif a.is_enumerable():
            new_consts = frozenset(c for c in a.consts if b.may_contain(c))
        elif b.is_enumerable():
            new_consts = frozenset(c for c in b.consts if a.may_contain(c))

        # Filter by interval
        if new_lo is not None or new_hi is not None:
            new_consts = frozenset(
                c for c in new_consts
                if (new_lo is None or c >= new_lo) and (new_hi is None or c <= new_hi)
            )

        # Parity meet
        new_parity = _parity_meet(a.parity, b.parity)

        # Filter consts by parity
        if new_parity == Parity.EVEN:
            new_consts = frozenset(c for c in new_consts if c % 2 == 0)
        elif new_parity == Parity.ODD:
            new_consts = frozenset(c for c in new_consts if c % 2 == 1)

        if not new_consts and new_parity == Parity.BOTTOM:
            return IntLattice.bottom()

        return IntLattice._normalize(new_consts, new_lo, new_hi, new_parity)

    def leq(self, a: IntLattice, b: IntLattice) -> bool:
        """Check a ⊑ b (subset of concretizations)."""
        if a.is_bottom:
            return True
        if b.is_top:
            return True
        if a.is_top:
            return b.is_top
        if b.is_bottom:
            return a.is_bottom

        # All constants in a must be containable by b
        for c in a.consts:
            if not b.may_contain(c):
                return False

        # a's interval must be contained in b's interval
        if a.lo is not None:
            if b.lo is not None and a.lo < b.lo:
                return False
        elif b.lo is not None:
            # a.lo is -∞ but b.lo is bounded
            return False

        if a.hi is not None:
            if b.hi is not None and a.hi > b.hi:
                return False
        elif b.hi is not None:
            # a.hi is +∞ but b.hi is bounded
            return False

        # Parity: a.parity must be <= b.parity
        if a.parity == Parity.BOTTOM:
            return True
        if b.parity == Parity.TOP:
            return True
        if a.parity == Parity.TOP:
            return b.parity == Parity.TOP
        return a.parity == b.parity

    def widen(self, old: IntLattice, new: IntLattice) -> IntLattice:
        """Widening with bounded constants and interval widening.

        Strategy:
        1. Join old and new
        2. If too many constants, compress into interval hull
        3. If interval bounds are unstable, widen to ±∞
        """
        joined = self.join(old, new)

        if joined.is_top or joined.is_bottom:
            return joined

        consts = joined.consts
        lo = joined.lo
        hi = joined.hi
        parity = joined.parity

        # Step 1: Compress constants if too many
        if len(consts) > self.max_consts:
            # Keep interval hull, drop consts
            if consts:
                new_lo = min(consts) if lo is None else min(lo, min(consts))
                new_hi = max(consts) if hi is None else max(hi, max(consts))
                lo = new_lo
                hi = new_hi
            consts = frozenset()

        # Step 2: Widen unstable bounds to infinity
        if old.lo is not None and lo is not None and lo < old.lo:
            lo = None  # Lower bound decreasing -> widen to -∞
        if old.hi is not None and hi is not None and hi > old.hi:
            hi = None  # Upper bound increasing -> widen to +∞

        # Check if we're now effectively top
        if len(consts) == 0 and lo is None and hi is None and parity == Parity.TOP:
            return IntLattice.top()

        return IntLattice(
            consts=consts,
            lo=lo,
            hi=hi,
            parity=parity,
            is_bottom=False,
            is_top=False,
        )

    def narrow(self, old: IntLattice, new: IntLattice) -> IntLattice:
        """Narrowing: refine with meet."""
        return self.meet(old, new)

    def abstract_const(self, value: Any) -> Optional[IntLattice]:
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

    def produce_facts(self, var: str, elem: IntLattice) -> List[Fact]:
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
        facts: List[Fact] = []

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

    def consume_fact(self, elem: IntLattice, fact: Fact) -> IntLattice:
        """Consume a fact to refine integer abstraction."""
        if elem.is_bottom:
            return elem

        if fact.kind == "is_singleton_int" and isinstance(fact.data, int):
            return self.meet(elem, IntLattice.const(fact.data))

        if fact.kind == "is_const_int_set" and isinstance(fact.data, frozenset):
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
            return IntLattice(
                consts=elem.consts,
                lo=elem.lo,
                hi=elem.hi,
                parity=_parity_meet(elem.parity, Parity.EVEN),
                is_bottom=False,
                is_top=False,
            )

        if fact.kind == "is_odd":
            if elem.is_top:
                return IntLattice.from_interval(None, None, Parity.ODD)
            if elem.is_enumerable():
                new_consts = frozenset(c for c in elem.consts if c % 2 == 1)
                return IntLattice.from_consts(new_consts)
            return IntLattice(
                consts=elem.consts,
                lo=elem.lo,
                hi=elem.hi,
                parity=_parity_meet(elem.parity, Parity.ODD),
                is_bottom=False,
                is_top=False,
            )

        if fact.kind == "is_none":
            # Not an int if it's None
            return IntLattice.bottom()

        return elem
