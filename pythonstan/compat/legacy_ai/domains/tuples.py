"""Tuple shape service for abstract interpretation.

This module provides tuple modeling for metaprogramming use cases:
- TupleExact: Exactly known tuple elements
- TupleSummary: Prefix with summarized tail (for long tuples)
- TupleDisj: Disjunction of tuple shapes (for join/widen)

Key features:
- Materialization of tuple shapes from PTA heap (via key(i) fields)
- Join/widen with capped alternatives and lengths
- Support for precise tuple unpacking and *args expansion

Design notes:
- Tuples are indexed by key(i) where i is converted to string (matching PTA translator)
- elem() is always read for soundness (captures unknown-index access)
- Widening caps number of alternatives and max probed length
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING, FrozenSet, List, Optional, Tuple, Union

if TYPE_CHECKING:
    from pythonstan.analysis.pointer.kcfa.object import AbstractObject
    from pythonstan.analysis.pointer.kcfa.context import AbstractContext, Scope
    from pythonstan.compat.legacy_ai.state import AIState
    from pythonstan.compat.legacy_ai.value_domain import AbsVal

__all__ = [
    "TupleExact",
    "TupleSummary",
    "TupleDisj",
    "TupleShape",
    "materialize_tuple_shape",
    "materialize_tuple_disj",
]


# =============================================================================
# TupleExact - Exactly known tuple elements
# =============================================================================


@dataclass(frozen=True)
class TupleExact:
    """Exactly known tuple elements.

    Represents a tuple with precisely known length and element values.

    Attributes:
        elems: Tuple of abstract values for each element position
    """

    elems: Tuple["AbsVal", ...]

    @property
    def length(self) -> int:
        """Return the tuple length."""
        return len(self.elems)

    def get(self, index: int) -> Optional["AbsVal"]:
        """Get element at index, or None if out of bounds."""
        if 0 <= index < len(self.elems):
            return self.elems[index]
        return None

    def join(self, other: "TupleExact") -> Optional["TupleExact"]:
        """Join two exact tuples of same length, None if lengths differ."""
        if len(self.elems) != len(other.elems):
            return None
        joined_elems = tuple(
            a.join(b) for a, b in zip(self.elems, other.elems)
        )
        return TupleExact(elems=joined_elems)

    def __str__(self) -> str:
        return f"TupleExact({len(self.elems)} elems)"


# =============================================================================
# TupleSummary - Prefix with summarized tail
# =============================================================================


@dataclass(frozen=True)
class TupleSummary:
    """Tuple with known prefix and summarized tail.

    For long tuples, we keep precise elements for the prefix and
    summarize remaining elements into a single elem value.

    Attributes:
        prefix: Tuple of abstract values for first N elements
        elem: Summary value for remaining elements (joined)
        min_len: Minimum known length (>= len(prefix))
        max_len: Maximum known length (None = unbounded)
    """

    prefix: Tuple["AbsVal", ...]
    elem: "AbsVal"
    min_len: int
    max_len: Optional[int]

    @property
    def prefix_len(self) -> int:
        """Return prefix length."""
        return len(self.prefix)

    def get(self, index: int) -> Optional["AbsVal"]:
        """Get element at index.

        Returns precise value for prefix indices, elem for tail indices,
        or None if definitely out of bounds.
        """
        if index < 0:
            return None
        if index < len(self.prefix):
            return self.prefix[index]
        if self.max_len is not None and index >= self.max_len:
            return None
        return self.elem

    def join(self, other: "TupleSummary") -> "TupleSummary":
        """Join two tuple summaries."""
        # Use shorter prefix, join common elements
        common_len = min(len(self.prefix), len(other.prefix))
        new_prefix = tuple(
            self.prefix[i].join(other.prefix[i]) for i in range(common_len)
        )

        # Join tail elements including extra prefix elements
        new_elem = self.elem.join(other.elem)
        for i in range(common_len, len(self.prefix)):
            new_elem = new_elem.join(self.prefix[i])
        for i in range(common_len, len(other.prefix)):
            new_elem = new_elem.join(other.prefix[i])

        new_min_len = min(self.min_len, other.min_len)
        new_max_len: Optional[int] = None
        if self.max_len is not None and other.max_len is not None:
            new_max_len = max(self.max_len, other.max_len)

        return TupleSummary(
            prefix=new_prefix,
            elem=new_elem,
            min_len=new_min_len,
            max_len=new_max_len,
        )

    def __str__(self) -> str:
        return f"TupleSummary(prefix={len(self.prefix)}, len=[{self.min_len},{self.max_len}])"


# Union type for tuple shapes
TupleShape = Union[TupleExact, TupleSummary]


# =============================================================================
# TupleDisj - Disjunction of tuple shapes
# =============================================================================


@dataclass(frozen=True)
class TupleDisj:
    """Disjunction of tuple shapes.

    Represents multiple possible tuple shapes (e.g., from conditional branches
    or joins of different-length tuples).

    Attributes:
        alts: Tuple of alternative shapes (TupleExact or TupleSummary)
    """

    alts: Tuple[TupleShape, ...]

    @property
    def num_alts(self) -> int:
        """Number of alternatives."""
        return len(self.alts)

    def is_empty(self) -> bool:
        """Check if no alternatives (unknown tuple)."""
        return len(self.alts) == 0

    def is_exact_single(self) -> bool:
        """Check if single exact alternative."""
        return len(self.alts) == 1 and isinstance(self.alts[0], TupleExact)

    def get_exact_alts(self) -> List[TupleExact]:
        """Get all TupleExact alternatives."""
        return [alt for alt in self.alts if isinstance(alt, TupleExact)]

    def get_all_lengths(self) -> FrozenSet[int]:
        """Get all possible lengths from exact alternatives."""
        lengths: set = set()
        for alt in self.alts:
            if isinstance(alt, TupleExact):
                lengths.add(alt.length)
            elif isinstance(alt, TupleSummary):
                # Add known lengths from summary range
                if alt.max_len is not None:
                    for i in range(alt.min_len, alt.max_len + 1):
                        lengths.add(i)
        return frozenset(lengths)

    def get(self, index: int) -> Optional["AbsVal"]:
        """Get element at index (joined across all alternatives).

        Returns joined value from all alternatives where index is valid.
        """
        from pythonstan.compat.legacy_ai.value_domain import AbsVal

        result = AbsVal.bottom()
        for alt in self.alts:
            elem = alt.get(index)
            if elem is not None:
                result = result.join(elem)
        return result if not result.is_bottom() else None

    def join(self, other: "TupleDisj", max_alts: int = 4) -> "TupleDisj":
        """Join two TupleDisj, capping alternatives."""
        combined = list(self.alts) + list(other.alts)

        if len(combined) <= max_alts:
            return TupleDisj(alts=tuple(combined))

        # Need to merge: group by length for exact, then join element-wise
        return _merge_tuple_alts(combined, max_alts)

    def widen(self, other: "TupleDisj", max_alts: int = 4) -> "TupleDisj":
        """Widen: similar to join but more aggressive collapse."""
        return self.join(other, max_alts)

    @classmethod
    def empty(cls) -> "TupleDisj":
        """Create empty disjunction (unknown)."""
        return cls(alts=())

    @classmethod
    def from_exact(cls, exact: TupleExact) -> "TupleDisj":
        """Create from single exact tuple."""
        return cls(alts=(exact,))

    @classmethod
    def from_summary(cls, summary: TupleSummary) -> "TupleDisj":
        """Create from single summary tuple."""
        return cls(alts=(summary,))

    def __str__(self) -> str:
        return f"TupleDisj({len(self.alts)} alts)"


def _merge_tuple_alts(alts: List[TupleShape], max_alts: int) -> TupleDisj:
    """Merge tuple alternatives to stay within cap.

    Strategy:
    1. Group TupleExact by length
    2. Join elements within each length group
    3. If still over cap, convert longest groups to TupleSummary
    """
    from pythonstan.compat.legacy_ai.value_domain import AbsVal

    # Group exact tuples by length
    by_length: dict = {}
    summaries: List[TupleSummary] = []

    for alt in alts:
        if isinstance(alt, TupleExact):
            length = alt.length
            if length not in by_length:
                by_length[length] = []
            by_length[length].append(alt)
        elif isinstance(alt, TupleSummary):
            summaries.append(alt)

    # Join each length group into single TupleExact
    merged_exact: List[TupleExact] = []
    for length in sorted(by_length.keys()):
        group = by_length[length]
        if len(group) == 1:
            merged_exact.append(group[0])
        else:
            # Join all elements element-wise
            result_elems: List[AbsVal] = [AbsVal.bottom()] * length
            for exact in group:
                for i, elem in enumerate(exact.elems):
                    result_elems[i] = result_elems[i].join(elem)
            merged_exact.append(TupleExact(elems=tuple(result_elems)))

    # Merge summaries
    merged_summary: Optional[TupleSummary] = None
    if summaries:
        merged_summary = summaries[0]
        for s in summaries[1:]:
            merged_summary = merged_summary.join(s)

    # Build result
    result_alts: List[TupleShape] = list(merged_exact)
    if merged_summary is not None:
        result_alts.append(merged_summary)

    # If still over cap, collapse excess exact tuples to summary
    while len(result_alts) > max_alts:
        # Find shortest exact tuple to collapse
        exact_indices = [
            i for i, a in enumerate(result_alts) if isinstance(a, TupleExact)
        ]
        if len(exact_indices) < 2:
            break

        # Pick two shortest and merge to summary
        exact_indices.sort(
            key=lambda i: result_alts[i].length  # type: ignore
            if isinstance(result_alts[i], TupleExact)
            else 0
        )
        i1, i2 = exact_indices[0], exact_indices[1]
        alt1, alt2 = result_alts[i1], result_alts[i2]
        assert isinstance(alt1, TupleExact) and isinstance(alt2, TupleExact)

        # Merge into summary
        common_len = min(alt1.length, alt2.length)
        prefix = tuple(
            alt1.elems[i].join(alt2.elems[i]) for i in range(common_len)
        )
        elem = AbsVal.bottom()
        for i in range(common_len, alt1.length):
            elem = elem.join(alt1.elems[i])
        for i in range(common_len, alt2.length):
            elem = elem.join(alt2.elems[i])
        merged = TupleSummary(
            prefix=prefix,
            elem=elem,
            min_len=min(alt1.length, alt2.length),
            max_len=max(alt1.length, alt2.length),
        )

        # Remove originals, add merged
        result_alts = [a for i, a in enumerate(result_alts) if i not in (i1, i2)]
        result_alts.append(merged)

    return TupleDisj(alts=tuple(result_alts))


# =============================================================================
# Materialization from PTA heap
# =============================================================================


def materialize_tuple_shape(
    obj: "AbstractObject",
    state: "AIState",
    caller_scope: Optional["Scope"],
    call_context: Optional["AbstractContext"],
    max_len: int = 8,
) -> Optional[TupleShape]:
    """Materialize tuple shape from a single abstract object.

    Probes key(0), key(1), ... until getting bottom or reaching max_len.
    Uses the overlay and PTA heap to read element values.

    Args:
        obj: The abstract object representing a tuple/list
        state: Current AI state with overlay
        caller_scope: Scope for PTA queries
        call_context: Context for PTA queries
        max_len: Maximum length to probe

    Returns:
        TupleShape (TupleExact or TupleSummary) or None if not materializable
    """
    from pythonstan.analysis.pointer.kcfa.heap_model import key
    from pythonstan.compat.legacy_ai.value_domain import AbsVal

    elems: List[AbsVal] = []
    saw_havoc = False
    found_any = False

    for i in range(max_len):
        field_key = key(str(i))
        elem_val, havoc = state.read_field_value(
            obj, field_key, caller_scope, call_context
        )
        if havoc:
            saw_havoc = True

        if elem_val.is_bottom():
            # No more elements at this position
            break

        elems.append(elem_val)
        found_any = True

    if not found_any:
        return None

    if saw_havoc or len(elems) == max_len:
        # Can't be sure about length, use summary
        from pythonstan.analysis.pointer.kcfa.heap_model import elem as elem_field

        tail_val, _ = state.read_field_value(
            obj, elem_field(), caller_scope, call_context
        )
        return TupleSummary(
            prefix=tuple(elems),
            elem=tail_val,
            min_len=len(elems),
            max_len=None,
        )

    return TupleExact(elems=tuple(elems))


def materialize_tuple_disj(
    val: "AbsVal",
    state: "AIState",
    caller_scope: Optional["Scope"],
    call_context: Optional["AbstractContext"],
    max_len: int = 8,
    max_alts: int = 4,
) -> TupleDisj:
    """Materialize TupleDisj from abstract value.

    For each address in val.addrs, probe key(i) fields and build shapes.
    Group by length, join elements, and cap alternatives.

    Args:
        val: Abstract value (should point to tuple/list objects)
        state: Current AI state
        caller_scope: Scope for PTA queries
        call_context: Context for PTA queries
        max_len: Maximum tuple length to probe
        max_alts: Maximum alternatives before merging

    Returns:
        TupleDisj with materialized shapes (may be empty if not materializable)
    """
    shapes: List[TupleShape] = []

    for obj in val.addrs:
        shape = materialize_tuple_shape(
            obj, state, caller_scope, call_context, max_len
        )
        if shape is not None:
            shapes.append(shape)

    if not shapes:
        return TupleDisj.empty()

    if len(shapes) <= max_alts:
        return TupleDisj(alts=tuple(shapes))

    return _merge_tuple_alts(shapes, max_alts)


def expand_starred_arg(
    val: "AbsVal",
    state: "AIState",
    caller_scope: Optional["Scope"],
    call_context: Optional["AbstractContext"],
    max_expand_len: int = 8,
    max_expand_alts: int = 4,
) -> Optional[List[Tuple[Tuple["AbsVal", ...], int]]]:
    """Expand a starred argument value into possible element sequences.

    If the value can be materialized as small TupleExact alternatives,
    returns list of (elements, length) pairs for each alternative.
    Otherwise returns None (caller should treat as unknown).

    Args:
        val: Abstract value representing *args
        state: Current AI state
        caller_scope: Scope for PTA queries
        call_context: Context for PTA queries
        max_expand_len: Maximum length per alternative to expand
        max_expand_alts: Maximum alternatives to consider

    Returns:
        List of (element_tuple, length) pairs, or None if not expandable
    """
    disj = materialize_tuple_disj(
        val, state, caller_scope, call_context, max_expand_len, max_expand_alts
    )

    if disj.is_empty():
        return None

    exact_alts = disj.get_exact_alts()
    if len(exact_alts) == 0:
        # Only summaries - can't expand precisely
        return None

    if len(exact_alts) > max_expand_alts:
        return None

    # Check all lengths are within cap
    for alt in exact_alts:
        if alt.length > max_expand_len:
            return None

    return [(alt.elems, alt.length) for alt in exact_alts]
