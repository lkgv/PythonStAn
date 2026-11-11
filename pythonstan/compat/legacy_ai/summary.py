"""Summary accumulation during AI interpretation.

This module provides SummaryAccumulator which collects:
- Return values
- Field writes
- Field reads
- Unknown effects

The accumulated summary is converted to AISummary for PTA consumption.
"""

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Dict, FrozenSet, List, Optional, Set, Tuple

from pythonstan.compat.legacy_ai.api import AISummary, FieldWrite, UnknownEffect
from pythonstan.compat.legacy_ai.value_domain import AbsVal

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
        calls: List of (callsite_id, callee_addrs) tuples
        unknown_effects: List of scoped effects
        may_raise: Exception types that may be raised
    """
    ret_addrs: Set['AbstractObject'] = field(default_factory=set)
    ret_value: AbsVal = field(default_factory=AbsVal.bottom)
    normal_exit: bool = False
    exit_state: Optional['AIState'] = None
    prefix_exceptions: Set[str] = field(default_factory=set)
    exceptional_exit_states: List[Tuple[str, 'AIState']] = field(default_factory=list)
    writes: List[Tuple[AddrSet, 'Field', AddrSet]] = field(default_factory=list)
    reads: List[Tuple[AddrSet, 'Field']] = field(default_factory=list)
    calls: List[Tuple[str, AddrSet]] = field(default_factory=list)
    unknown_effects: List[UnknownEffect] = field(default_factory=list)
    may_raise: Set[str] = field(default_factory=set)
    incomplete_reasons: Set[str] = field(default_factory=set)

    def mark_incomplete(self, reason: str) -> None:
        """Record that observed effects cannot be used as a complete summary."""
        self.incomplete_reasons.add(reason)

    def include_effects(
        self,
        other: 'SummaryAccumulator',
        suppress_exceptions: FrozenSet[str] = frozenset(),
    ) -> None:
        """Include an operation's effects without importing its return values.

        Exception suppression is local to that operation: catching an
        AttributeError from ``hasattr`` must not erase an earlier exception.
        """
        self.writes.extend(other.writes)
        self.reads.extend(other.reads)
        self.calls.extend(other.calls)
        self.unknown_effects.extend(other.unknown_effects)
        self.may_raise.update(other.may_raise - suppress_exceptions)
        self.prefix_exceptions.update(other.prefix_exceptions)
        self.exceptional_exit_states.extend((kind, state) for kind, state in other.exceptional_exit_states
                                             if kind not in suppress_exceptions)
        self.incomplete_reasons.update(other.incomplete_reasons)

    def add_return(self, addrs: AddrSet) -> None:
        """Record return value addresses."""
        self.ret_addrs.update(addrs)
        self.ret_value = self.ret_value.join(AbsVal.from_addrs(addrs))
        self.normal_exit = True

    def add_return_value(self, value: AbsVal, state: Optional['AIState'] = None) -> None:
        """Preserve the complete value and correlated outgoing memory."""
        self.ret_value = self.ret_value.join(value)
        self.ret_addrs.update(value.addrs)
        self.normal_exit = True
        if state is not None:
            self.exit_state = state.copy() if self.exit_state is None else self.exit_state.join(state)

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

    def add_call(self, callsite_id: str, callee_addrs: AddrSet) -> None:
        """Record a call edge discovered during AI."""
        if callsite_id and len(callee_addrs) > 0:
            self.calls.append((callsite_id, frozenset(callee_addrs)))

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

    def add_may_raise(self, exn_type: str, state: Optional['AIState'] = None) -> None:
        """Record that an exception type may be raised."""
        self.may_raise.add(exn_type)
        self.prefix_exceptions.add(exn_type)
        if state is not None:
            self.exceptional_exit_states.append((exn_type, state.copy()))

    def merge(self, other: 'SummaryAccumulator') -> 'SummaryAccumulator':
        """Merge another accumulator into this one."""
        new_acc = SummaryAccumulator()
        new_acc.ret_addrs = self.ret_addrs | other.ret_addrs
        new_acc.ret_value = self.ret_value.join(other.ret_value)
        new_acc.normal_exit = self.normal_exit or other.normal_exit
        new_acc.prefix_exceptions = self.prefix_exceptions | other.prefix_exceptions
        new_acc.exceptional_exit_states = self.exceptional_exit_states + other.exceptional_exit_states
        if self.exit_state is None:
            new_acc.exit_state = other.exit_state
        elif other.exit_state is None:
            new_acc.exit_state = self.exit_state
        else:
            new_acc.exit_state = self.exit_state.join(other.exit_state)
        new_acc.writes = self.writes + other.writes
        new_acc.reads = self.reads + other.reads
        new_acc.calls = self.calls + other.calls
        new_acc.unknown_effects = self.unknown_effects + other.unknown_effects
        new_acc.may_raise = self.may_raise | other.may_raise
        new_acc.incomplete_reasons = self.incomplete_reasons | other.incomplete_reasons
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

        # Consolidate calls by callsite_id
        consolidated_calls: Dict[str, Set['AbstractObject']] = {}
        for callsite_id, callee_addrs in self.calls:
            if callsite_id not in consolidated_calls:
                consolidated_calls[callsite_id] = set()
            consolidated_calls[callsite_id].update(callee_addrs)

        calls_tuple = tuple(
            (callsite_id, frozenset(addrs))
            for callsite_id, addrs in consolidated_calls.items()
        )

        return AISummary(
            ret=frozenset(self.ret_addrs),
            ret_value=self.ret_value,
            normal_exit=self.normal_exit,
            exit_state=self.exit_state.copy() if self.exit_state is not None else None,
            prefix_exceptions=frozenset(self.prefix_exceptions),
            exceptional_exit_states=tuple(self.exceptional_exit_states),
            writes=field_writes,
            reads=reads_tuple,
            calls=calls_tuple,
            unknown_effects=tuple(self.unknown_effects),
            may_raise=frozenset(self.may_raise),
            is_complete=not self.incomplete_reasons,
            incomplete_reasons=tuple(sorted(self.incomplete_reasons)),
        )

    def is_empty(self) -> bool:
        """Check if no effects have been recorded."""
        return (
            len(self.ret_addrs) == 0 and
            self.ret_value.is_bottom() and
            not self.normal_exit and
            not self.prefix_exceptions and
            len(self.writes) == 0 and
            len(self.unknown_effects) == 0 and
            len(self.calls) == 0 and
            len(self.reads) == 0 and
            len(self.may_raise) == 0 and
            len(self.incomplete_reasons) == 0
        )

    def __str__(self) -> str:
        parts = []
        if self.ret_addrs:
            parts.append(f"ret={len(self.ret_addrs)} objs")
        if self.writes:
            parts.append(f"writes={len(self.writes)}")
        if self.reads:
            parts.append(f"reads={len(self.reads)}")
        if self.calls:
            parts.append(f"calls={len(self.calls)}")
        if self.unknown_effects:
            parts.append(f"effects={len(self.unknown_effects)}")
        return f"SummaryAcc({', '.join(parts)})"

