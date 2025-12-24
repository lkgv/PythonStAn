"""Summary accumulation during AI interpretation.

This module provides SummaryAccumulator which collects:
- Return values
- Field writes
- Field reads
- Unknown effects

The accumulated summary is converted to AISummary for PTA consumption.
"""

from dataclasses import dataclass, field
from typing import (
    TYPE_CHECKING,
    Dict,
    FrozenSet,
    List,
    Optional,
    Set,
    Tuple,
)

from .api import AISummary, FieldWrite, UnknownEffect

if TYPE_CHECKING:
    from pythonstan.analysis.pointer.kcfa.object import AbstractObject
    from pythonstan.analysis.pointer.kcfa.heap_model import Field

__all__ = ["SummaryAccumulator"]


# Type alias for address sets (compatible with PTA)
AddrSet = FrozenSet['AbstractObject']


@dataclass
class SummaryAccumulator:
    """Accumulates summary facts during AI interpretation.
    
    During intraprocedural analysis, the interpreter calls methods
    on this class to record effects. At the end, to_summary() produces
    the final AISummary.
    
    Attributes:
        ret_addrs: Return value address sets
        writes: List of (obj_addrs, field, value_addrs) tuples
        reads: List of (obj_addrs, field) tuples
        unknown_effects: List of scoped effects
        may_raise: Exception types that may be raised
    """
    ret_addrs: Set['AbstractObject'] = field(default_factory=set)
    writes: List[Tuple[AddrSet, 'Field', AddrSet]] = field(default_factory=list)
    reads: List[Tuple[AddrSet, 'Field']] = field(default_factory=list)
    unknown_effects: List[UnknownEffect] = field(default_factory=list)
    may_raise: Set[str] = field(default_factory=set)

    def add_return(self, addrs: AddrSet) -> None:
        """Record return value addresses."""
        self.ret_addrs.update(addrs)

    def add_write(
        self,
        obj_addrs: AddrSet,
        field_key: 'Field',
        value_addrs: AddrSet,
    ) -> None:
        """Record a field write.
        
        Args:
            obj_addrs: Objects being written to
            field_key: Field being written
            value_addrs: Values being written
        """
        if len(obj_addrs) > 0 and len(value_addrs) > 0:
            self.writes.append((obj_addrs, field_key, value_addrs))

    def add_read(
        self,
        obj_addrs: AddrSet,
        field_key: 'Field',
    ) -> None:
        """Record a field read (for dependency tracking)."""
        if len(obj_addrs) > 0:
            self.reads.append((obj_addrs, field_key))

    def add_unknown_effect(
        self,
        kind: str,
        target_addrs: AddrSet,
        field_key: Optional['Field'] = None,
    ) -> None:
        """Record an unknown/havoc effect.
        
        Args:
            kind: Effect type ("may_write", "may_read", "may_call")
            target_addrs: Affected objects
            field_key: Specific field affected (None = all)
        """
        if len(target_addrs) > 0:
            self.unknown_effects.append(UnknownEffect(
                kind=kind,
                target_addrs=frozenset(target_addrs),
                field_key=field_key,
            ))

    def add_may_raise(self, exn_type: str) -> None:
        """Record that an exception type may be raised."""
        self.may_raise.add(exn_type)

    def merge(self, other: 'SummaryAccumulator') -> 'SummaryAccumulator':
        """Merge another accumulator into this one."""
        new_acc = SummaryAccumulator()
        new_acc.ret_addrs = self.ret_addrs | other.ret_addrs
        new_acc.writes = self.writes + other.writes
        new_acc.reads = self.reads + other.reads
        new_acc.unknown_effects = self.unknown_effects + other.unknown_effects
        new_acc.may_raise = self.may_raise | other.may_raise
        return new_acc

    def to_summary(self) -> AISummary:
        """Convert accumulated facts to AISummary.
        
        Consolidates writes by (obj_addrs, field) key.
        """
        # Consolidate writes by (obj_set, field) -> value union
        consolidated_writes: Dict[Tuple[FrozenSet, 'Field'], Set['AbstractObject']] = {}
        for obj_addrs, field_key, value_addrs in self.writes:
            key = (frozenset(obj_addrs), field_key)
            if key not in consolidated_writes:
                consolidated_writes[key] = set()
            consolidated_writes[key].update(value_addrs)

        field_writes = tuple(
            FieldWrite(
                obj_addrs=obj_addrs,
                field_key=field_key,
                value_addrs=frozenset(value_addrs),
            )
            for (obj_addrs, field_key), value_addrs in consolidated_writes.items()
        )

        # Consolidate reads
        consolidated_reads: Set[Tuple[FrozenSet, 'Field']] = set()
        for obj_addrs, field_key in self.reads:
            consolidated_reads.add((frozenset(obj_addrs), field_key))

        reads_tuple = tuple(consolidated_reads)

        return AISummary(
            ret=frozenset(self.ret_addrs),
            writes=field_writes,
            reads=reads_tuple,
            calls=(),  # Not tracked in v1
            unknown_effects=tuple(self.unknown_effects),
            may_raise=frozenset(self.may_raise),
        )

    def is_empty(self) -> bool:
        """Check if no effects have been recorded."""
        return (
            len(self.ret_addrs) == 0 and
            len(self.writes) == 0 and
            len(self.unknown_effects) == 0
        )

    def __str__(self) -> str:
        parts = []
        if self.ret_addrs:
            parts.append(f"ret={len(self.ret_addrs)} objs")
        if self.writes:
            parts.append(f"writes={len(self.writes)}")
        if self.reads:
            parts.append(f"reads={len(self.reads)}")
        if self.unknown_effects:
            parts.append(f"effects={len(self.unknown_effects)}")
        return f"SummaryAcc({', '.join(parts)})"

