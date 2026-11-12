"""String domain for reflective attribute names.

Enhanced string lattice with:
- Bounded set of constant strings
- Bounded set of prefixes (any string starting with these)
- Bounded set of suffixes (any string ending with these)
- has_unknown flag (if True, includes all possible strings)

This is critical for getattr/setattr key reasoning and metaprogramming.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import (
    TypeGuard,
    override,
)

from .base import Domain, Fact, FactPayload

__all__ = ["StringDomain", "StrLattice"]


# =============================================================================
# StrLattice - String lattice element (enhanced)
# =============================================================================


def _string_set(value: FactPayload) -> TypeGuard[frozenset[str]]:
    return isinstance(value, frozenset) and all(isinstance(item, str) for item in value)


@dataclass(frozen=True, eq=True)
class StrLattice:
    """Abstract string value with rich filtering capabilities.

    Represents a set of possible strings as:
    - consts: Finite set of exact constant strings
    - prefixes: Set of prefixes (any string starting with these)
    - suffixes: Set of suffixes (any string ending with these)
    - has_unknown: If True, includes any string (Σ*)
    - conjunctions: Finite (prefix, suffix) pairs requiring both ends

    The concretization (γ) is:
        γ(elem) = consts ∪ {s | ∃p ∈ prefixes. s.startswith(p)}
                        ∪ {s | ∃s' ∈ suffixes. s.endswith(s')}
                        ∪ {s | ∃(p,t) ∈ conjunctions. s.startswith(p) and s.endswith(t)}
                        ∪ (Σ* if has_unknown else ∅)

    Prefixes and suffixes are union disjuncts; each conjunction requires both ends.
    """

    consts: frozenset[str]
    prefixes: frozenset[str]
    suffixes: frozenset[str]
    has_unknown: bool
    # Conjunctions retain exact intersections of prefix and suffix languages.
    conjunctions: frozenset[tuple[str, str]] = frozenset()

    # -------------------------------------------------------------------------
    # Factory methods
    # -------------------------------------------------------------------------

    @classmethod
    def bottom(cls) -> "StrLattice":
        """No strings (unreachable/not a string)."""
        return cls(
            consts=frozenset(),
            prefixes=frozenset(),
            suffixes=frozenset(),
            has_unknown=False,
        )

    @classmethod
    def const(cls, s: str) -> "StrLattice":
        """Single constant string."""
        return cls(
            consts=frozenset({s}),
            prefixes=frozenset(),
            suffixes=frozenset(),
            has_unknown=False,
        )

    @classmethod
    def from_consts(cls, strs: frozenset[str]) -> "StrLattice":
        """Set of constant strings."""
        if len(strs) == 0:
            return cls.bottom()
        return cls(
            consts=strs,
            prefixes=frozenset(),
            suffixes=frozenset(),
            has_unknown=False,
        )

    @classmethod
    def from_prefix(cls, prefix: str) -> "StrLattice":
        """Single prefix constraint."""
        # Empty prefix matches all strings
        if prefix == "":
            return cls.top()
        return cls(
            consts=frozenset(),
            prefixes=frozenset({prefix}),
            suffixes=frozenset(),
            has_unknown=False,
        )

    @classmethod
    def from_prefixes(cls, prefixes: frozenset[str]) -> "StrLattice":
        """Set of prefix constraints."""
        # Empty prefix matches all strings
        if "" in prefixes:
            return cls.top()
        if len(prefixes) == 0:
            return cls.bottom()
        return cls._normalize(
            consts=frozenset(),
            prefixes=prefixes,
            suffixes=frozenset(),
            has_unknown=False,
        )

    @classmethod
    def from_suffix(cls, suffix: str) -> "StrLattice":
        """Single suffix constraint."""
        # Empty suffix matches all strings
        if suffix == "":
            return cls.top()
        return cls(
            consts=frozenset(),
            prefixes=frozenset(),
            suffixes=frozenset({suffix}),
            has_unknown=False,
        )

    @classmethod
    def from_suffixes(cls, suffixes: frozenset[str]) -> "StrLattice":
        """Set of suffix constraints."""
        # Empty suffix matches all strings
        if "" in suffixes:
            return cls.top()
        if len(suffixes) == 0:
            return cls.bottom()
        return cls._normalize(
            consts=frozenset(),
            prefixes=frozenset(),
            suffixes=suffixes,
            has_unknown=False,
        )

    @classmethod
    def top(cls) -> "StrLattice":
        """Any string (Σ*)."""
        return cls(
            consts=frozenset(),
            prefixes=frozenset(),
            suffixes=frozenset(),
            has_unknown=True,
        )

    # -------------------------------------------------------------------------
    # Normalization
    # -------------------------------------------------------------------------

    @classmethod
    def _normalize(
        cls,
        consts: frozenset[str],
        prefixes: frozenset[str],
        suffixes: frozenset[str],
        has_unknown: bool,
        conjunctions: frozenset[tuple[str, str]] = frozenset(),
    ) -> "StrLattice":
        """Normalize the lattice element.

        Normalization rules:
        1. If has_unknown=True, clear all sets (top is canonical)
        2. If "" in prefixes or suffixes, normalize to top
        3. Deduplicate prefixes by keeping anti-chain (more general prefixes)
        4. Deduplicate suffixes similarly
        5. Remove constants covered by prefixes/suffixes
        """
        # Rule 1: Empty string in prefixes/suffixes means top
        if "" in prefixes or "" in suffixes:
            return cls(
                consts=frozenset(),
                prefixes=frozenset(),
                suffixes=frozenset(),
                has_unknown=True,
            )

        # Rule 2: has_unknown=True means top (clear sets)
        if has_unknown:
            return cls(
                consts=frozenset(),
                prefixes=frozenset(),
                suffixes=frozenset(),
                has_unknown=True,
            )

        # Empty sides are ordinary prefix/suffix constraints. Nonempty pairs
        # mean startswith(p) AND endswith(s), rather than their union.
        prefixes = prefixes | frozenset(p for p, t in conjunctions if not t)
        suffixes = suffixes | frozenset(t for p, t in conjunctions if not p)
        if "" in prefixes or "" in suffixes:
            return cls.top()
        conjunctions = frozenset((p, t) for p, t in conjunctions if p and t)

        # Rule 3: Anti-chain for prefixes (keep only most general)
        normalized_prefixes = cls._compute_prefix_antichain(prefixes)

        # Rule 4: Anti-chain for suffixes
        normalized_suffixes = cls._compute_suffix_antichain(suffixes)

        conjunctions = frozenset(
            (p, t)
            for p, t in conjunctions
            if not any(p.startswith(q) for q in normalized_prefixes)
            and not any(t.endswith(u) for u in normalized_suffixes)
        )
        conjunctions = frozenset(
            (p, t)
            for p, t in conjunctions
            if not any(
                (p, t) != (q, u) and p.startswith(q) and t.endswith(u) for q, u in conjunctions
            )
        )

        # Rule 5: Remove constants covered by any infinite component.
        normalized_consts = frozenset(
            c
            for c in consts
            if not any(c.startswith(p) for p in normalized_prefixes)
            and not any(c.endswith(s) for s in normalized_suffixes)
            and not any(c.startswith(p) and c.endswith(t) for p, t in conjunctions)
        )

        # Bottom check
        if (
            len(normalized_consts) == 0
            and len(normalized_prefixes) == 0
            and len(normalized_suffixes) == 0
            and not conjunctions
            and not has_unknown
        ):
            return cls(
                consts=frozenset(),
                prefixes=frozenset(),
                suffixes=frozenset(),
                has_unknown=False,
            )

        return cls(
            consts=normalized_consts,
            prefixes=normalized_prefixes,
            suffixes=normalized_suffixes,
            has_unknown=False,
            conjunctions=conjunctions,
        )

    @staticmethod
    def _compute_prefix_antichain(prefixes: frozenset[str]) -> frozenset[str]:
        """Compute anti-chain for prefixes.

        If p1 is a prefix of p2, keep only p1 (more general).
        Example: {"ab", "abc"} -> {"ab"}
        """
        if len(prefixes) <= 1:
            return prefixes

        result: set[str] = set()
        sorted_prefixes = sorted(prefixes, key=len)

        for p in sorted_prefixes:
            # Check if any existing prefix in result is a prefix of p
            if not any(p.startswith(existing) for existing in result):
                result.add(p)

        return frozenset(result)

    @staticmethod
    def _compute_suffix_antichain(suffixes: frozenset[str]) -> frozenset[str]:
        """Compute anti-chain for suffixes.

        If s1 is a suffix of s2, keep only s1 (more general).
        Example: {"bc", "abc"} -> {"bc"}
        """
        if len(suffixes) <= 1:
            return suffixes

        result: set[str] = set()
        sorted_suffixes = sorted(suffixes, key=len)

        for s in sorted_suffixes:
            # Check if any existing suffix in result is a suffix of s
            if not any(s.endswith(existing) for existing in result):
                result.add(s)

        return frozenset(result)

    # -------------------------------------------------------------------------
    # Predicates
    # -------------------------------------------------------------------------

    def is_bottom(self) -> bool:
        """Check if this is bottom (no strings)."""
        return (
            len(self.consts) == 0
            and len(self.prefixes) == 0
            and len(self.suffixes) == 0
            and not self.conjunctions
            and not self.has_unknown
        )

    def is_top(self) -> bool:
        """Check if this is top (any string)."""
        return self.has_unknown

    def is_const(self) -> bool:
        """Check if this is a finite const set only (no prefixes/suffixes)."""
        return (
            len(self.consts) > 0
            and len(self.prefixes) == 0
            and len(self.suffixes) == 0
            and not self.conjunctions
            and not self.has_unknown
        )

    def is_enumerable(self) -> bool:
        """Check if we can enumerate all possible strings.

        Only enumerable when we have exact constants only
        (no prefixes, no suffixes, no unknown).
        """
        return (
            len(self.prefixes) == 0
            and len(self.suffixes) == 0
            and not self.conjunctions
            and not self.has_unknown
        )

    def get_consts(self) -> frozenset[str] | None:
        """Get constant strings if enumerable, else None."""
        if self.is_enumerable():
            return self.consts
        return None

    # -------------------------------------------------------------------------
    # String filtering operations
    # -------------------------------------------------------------------------

    def may_equal(self, s: str) -> bool:
        """Check if this may equal a specific string.

        Returns True if:
        - has_unknown is True (any string possible), OR
        - s is in consts, OR
        - any prefix in prefixes is a prefix of s, OR
        - any suffix in suffixes is a suffix of s
        """
        if self.has_unknown:
            return True
        if s in self.consts:
            return True
        if any(s.startswith(p) for p in self.prefixes):
            return True
        if any(s.endswith(suf) for suf in self.suffixes):
            return True
        return any(s.startswith(p) and s.endswith(t) for p, t in self.conjunctions)

    def must_equal(self, s: str) -> bool:
        """Check if this must equal a specific string (singleton constant)."""
        return (
            self.consts == frozenset({s})
            and len(self.prefixes) == 0
            and len(self.suffixes) == 0
            and not self.conjunctions
            and not self.has_unknown
        )

    def filter_candidates(self, candidates: frozenset[str]) -> frozenset[str]:
        """Filter candidate strings that may match this lattice element.

        Returns the subset of candidates that satisfy may_equal.
        This is the key operation for filtering known attribute names.
        """
        if self.has_unknown:
            return candidates
        return frozenset(c for c in candidates if self.may_equal(c))

    # -------------------------------------------------------------------------
    # String representation
    # -------------------------------------------------------------------------

    @override
    def __str__(self) -> str:
        if self.is_bottom():
            return "⊥_str"
        if self.is_top():
            return "⊤_str"

        parts = []
        if self.consts:
            if len(self.consts) <= 3:
                parts.append("{" + ", ".join(repr(s) for s in sorted(self.consts)) + "}")
            else:
                parts.append(f"{{...{len(self.consts)} strs}}")
        if self.prefixes:
            if len(self.prefixes) <= 3:
                parts.append("prefix(" + ", ".join(repr(p) for p in sorted(self.prefixes)) + ")")
            else:
                parts.append(f"prefix(...{len(self.prefixes)})")
        if self.suffixes:
            if len(self.suffixes) <= 3:
                parts.append("suffix(" + ", ".join(repr(s) for s in sorted(self.suffixes)) + ")")
            else:
                parts.append(f"suffix(...{len(self.suffixes)})")

        if self.conjunctions:
            parts.append("patterns=" + repr(sorted(self.conjunctions)))
        return " ∪ ".join(parts) if parts else "⊥_str"


# =============================================================================
# StringDomain - Domain implementation
# =============================================================================


class StringDomain(Domain[StrLattice]):
    """String domain for reflective attribute names.

    Implements stratified widening with prefix/suffix compression:
    - Constants are maintained up to max_consts
    - When constants exceed cap, compress via LCP into prefixes/suffixes
    - When prefixes/suffixes exceed caps, widen to top
    """

    def __init__(
        self,
        max_consts: int = 10,
        max_prefixes: int = 5,
        max_suffixes: int = 5,
    ) -> None:
        """Initialize with widening caps.

        Args:
            max_consts: Maximum constant strings before compression
            max_prefixes: Maximum prefixes before widening to top
            max_suffixes: Maximum suffixes before widening to top
        """
        self.max_consts: int = max_consts
        self.max_prefixes: int = max_prefixes
        self.max_suffixes: int = max_suffixes

    @override
    def public_settings(self) -> Mapping[str, int]:
        return {
            "max_consts": self.max_consts,
            "max_prefixes": self.max_prefixes,
            "max_suffixes": self.max_suffixes,
        }

    @property
    @override
    def name(self) -> str:
        return "string"

    @override
    def bottom(self) -> StrLattice:
        return StrLattice.bottom()

    @override
    def top(self) -> StrLattice:
        return StrLattice.top()

    @override
    def join(self, a: StrLattice, b: StrLattice) -> StrLattice:
        """Join two string abstractions (⊔).

        Join is pointwise union of all components.
        """
        # Bottom is identity
        if a.is_bottom():
            return b
        if b.is_bottom():
            return a

        # Top absorbs
        if a.is_top() or b.is_top():
            return StrLattice.top()

        # Union all components and normalize
        return StrLattice._normalize(
            consts=a.consts | b.consts,
            prefixes=a.prefixes | b.prefixes,
            suffixes=a.suffixes | b.suffixes,
            has_unknown=a.has_unknown or b.has_unknown,
            conjunctions=a.conjunctions | b.conjunctions,
        )

    @override
    def meet(self, a: StrLattice, b: StrLattice) -> StrLattice:
        """Exact intersection of finite unions of prefix/suffix conjunctions.

        Each infinite disjunct is a pair (prefix, suffix), with an empty side
        unconstrained. Two prefix constraints intersect iff they are comparable;
        the same holds for suffixes. Thus this representation is meet-closed.
        """
        if a.is_top():
            return b
        if b.is_top():
            return a
        if a.is_bottom() or b.is_bottom():
            return StrLattice.bottom()
        consts = frozenset(c for c in a.consts if b.may_equal(c)) | frozenset(
            c for c in b.consts if a.may_equal(c)
        )
        patterns = set()
        for p, t in self._patterns(a):
            for q, u in self._patterns(b):
                if p.startswith(q):
                    prefix = p
                elif q.startswith(p):
                    prefix = q
                else:
                    continue
                if t.endswith(u):
                    suffix = t
                elif u.endswith(t):
                    suffix = u
                else:
                    continue
                patterns.add((prefix, suffix))
        return StrLattice._normalize(consts, frozenset(), frozenset(), False, frozenset(patterns))

    @staticmethod
    def _patterns(value: StrLattice) -> frozenset[tuple[str, str]]:
        return (
            value.conjunctions
            | frozenset((p, "") for p in value.prefixes)
            | frozenset(("", t) for t in value.suffixes)
        )

    @override
    def leq(self, a: StrLattice, b: StrLattice) -> bool:
        """Check a ⊑ b (subset of concretizations)."""
        if a.is_bottom():
            return True
        if b.is_top():
            return True
        if a.is_top():
            return b.is_top()

        # All constants in a must be in b's concretization
        for c in a.consts:
            if not b.may_equal(c):
                return False

        # A disjunct is covered when a more general target disjunct covers
        # both ends. This is sound (possibly incomplete) language inclusion.
        targets = self._patterns(b)
        for p, t in self._patterns(a):
            if not any(p.startswith(q) and t.endswith(u) for q, u in targets):
                return False
        return True

    @override
    def widen(self, old: StrLattice, new: StrLattice) -> StrLattice:
        """Widening with LCP-based compression.

        Strategy:
        1. Join old and new
        2. If too many constants, compress into prefixes/suffixes via LCP
        3. If too many prefixes/suffixes, widen to top
        """
        joined = self.join(old, new)

        if joined.is_top() or joined.is_bottom():
            return joined

        consts = joined.consts
        prefixes = joined.prefixes
        suffixes = joined.suffixes
        conjunctions = joined.conjunctions

        # Step 1: Compress constants if too many
        if len(consts) > self.max_consts:
            new_prefixes, new_suffixes = self._compress_consts(consts)
            prefixes = prefixes | new_prefixes
            suffixes = suffixes | new_suffixes
            # LCP/LCS only cover groups; isolated strings must survive.
            consts = frozenset(
                c
                for c in consts
                if not any(c.startswith(p) for p in prefixes)
                and not any(c.endswith(t) for t in suffixes)
            )
            if len(consts) > self.max_consts:
                return StrLattice.top()

        # Step 2: Check prefix/suffix caps
        if (
            len(prefixes) > self.max_prefixes
            or len(suffixes) > self.max_suffixes
            or len(conjunctions) > self.max_prefixes * self.max_suffixes
        ):
            return StrLattice.top()

        return StrLattice._normalize(
            consts=consts,
            prefixes=prefixes,
            suffixes=suffixes,
            has_unknown=False,
            conjunctions=conjunctions,
        )

    @override
    def narrow(self, old: StrLattice, new: StrLattice) -> StrLattice:
        """Narrowing: refine with meet."""
        return self.meet(old, new)

    @override
    def abstract_const(self, value: object) -> StrLattice | None:
        """Abstract a Python constant."""
        if isinstance(value, str):
            return StrLattice.const(value)
        return None

    @override
    def produce_facts(self, var: str, elem: StrLattice) -> list[Fact]:
        """Produce facts about string values."""
        facts: list[Fact] = []
        if elem.is_enumerable() and len(elem.consts) > 0:
            facts.append(Fact("is_const_str", var, elem.consts))
            if len(elem.consts) == 1:
                facts.append(Fact("is_singleton_str", var, next(iter(elem.consts))))
        elif elem.prefixes and not elem.suffixes and not elem.consts and not elem.conjunctions:
            facts.append(Fact("has_prefix", var, elem.prefixes))
        elif elem.suffixes and not elem.prefixes and not elem.consts and not elem.conjunctions:
            facts.append(Fact("has_suffix", var, elem.suffixes))
        elif elem.is_top():
            facts.append(Fact("is_unknown_str", var, None))
        return facts

    @override
    def consume_fact(self, elem: StrLattice, fact: Fact) -> StrLattice:
        """Consume a fact to refine string abstraction."""
        if fact.kind == "is_const_str" and _string_set(fact.data):
            # Refine with known constants
            const_set = fact.data
            if elem.is_top():
                return StrLattice.from_consts(const_set)
            if elem.is_enumerable():
                return StrLattice.from_consts(elem.consts & const_set)
            # Filter by both constraints
            filtered = elem.filter_candidates(const_set)
            return StrLattice.from_consts(filtered)
        if fact.kind == "is_none":
            return StrLattice.bottom()
        return elem

    # -------------------------------------------------------------------------
    # LCP-based compression
    # -------------------------------------------------------------------------

    def _compress_consts(
        self,
        consts: frozenset[str],
    ) -> tuple[frozenset[str], frozenset[str]]:
        """Compress constants into prefixes and suffixes using LCP/LCS.

        Uses Trie-based grouping:
        - Group strings by common prefixes using LCP
        - Group strings by common suffixes using LCS (LCP on reversed strings)

        Returns:
            (prefixes, suffixes) derived from the constants
        """
        if len(consts) == 0:
            return frozenset(), frozenset()

        const_list = list(consts)

        # Compute common prefixes via LCP grouping
        prefix_candidates = self._compute_lcp_prefixes(const_list)

        # Compute common suffixes via reversed-LCP
        suffix_candidates = self._compute_lcs_suffixes(const_list)

        # Keep only meaningful prefixes/suffixes (length >= 1)
        prefixes = frozenset(p for p in prefix_candidates if len(p) >= 1)
        suffixes = frozenset(s for s in suffix_candidates if len(s) >= 1)

        return prefixes, suffixes

    def _compute_lcp_prefixes(self, strings: list[str]) -> set[str]:
        """Compute common prefixes using LCP-based grouping.

        Groups strings by their common prefixes using a simplified trie approach.
        Returns prefixes that cover multiple strings.
        """
        if len(strings) <= 1:
            return set()

        # Sort strings to group similar ones
        sorted_strings = sorted(strings)
        prefixes: set[str] = set()

        # Compute LCP between adjacent pairs and collect meaningful prefixes
        for i in range(len(sorted_strings) - 1):
            lcp = self._lcp(sorted_strings[i], sorted_strings[i + 1])
            if len(lcp) >= 1:
                prefixes.add(lcp)

        # Keep anti-chain of prefixes
        return set(StrLattice._compute_prefix_antichain(frozenset(prefixes)))

    def _compute_lcs_suffixes(self, strings: list[str]) -> set[str]:
        """Compute common suffixes using LCP on reversed strings."""
        if len(strings) <= 1:
            return set()

        # Reverse strings and compute LCP
        reversed_strings = [s[::-1] for s in strings]
        sorted_reversed = sorted(reversed_strings)
        suffixes: set[str] = set()

        for i in range(len(sorted_reversed) - 1):
            lcp = self._lcp(sorted_reversed[i], sorted_reversed[i + 1])
            if len(lcp) >= 1:
                # Reverse back to get suffix
                suffixes.add(lcp[::-1])

        # Keep anti-chain of suffixes
        return set(StrLattice._compute_suffix_antichain(frozenset(suffixes)))

    @staticmethod
    def _lcp(s1: str, s2: str) -> str:
        """Compute longest common prefix of two strings."""
        min_len = min(len(s1), len(s2))
        for i in range(min_len):
            if s1[i] != s2[i]:
                return s1[:i]
        return s1[:min_len]
