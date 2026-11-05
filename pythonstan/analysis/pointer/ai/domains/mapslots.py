"""SlotMap: Unified namespace/mapping abstraction for abstract interpretation.

This module provides a generalized map abstraction that unifies:
- Attribute namespaces (instance/class/module __dict__)
- Dict objects (string and non-string keys)
- Kwargs shape modeling (**kwargs)
- Intermediate locals/globals when available

Key features:
- KeySig-based keys for both strings and complex keys (ints, tuples, tokens)
- Bounded precise partitions for tracked keys + default bucket (spill)
- Presence lattice per tracked key (ABSENT/MAYBE/PRESENT)
- Optional key_rep tracking for dict key queries
- Branch-sensitive refinement hooks (assume_contains/assume_not_contains)
- Compatibility with PTA field model (attr(name) and unknown() for attributes,
  key(s) and elem() for dicts)

Design notes:
- KeySig is the unified key type: ("str", "foo"), ("int", 42), etc.
- String-only namespaces use string KeySigs for efficiency
- Default bucket captures all untracked keys soundly
- Widening spills excess partitions to default to ensure termination

Lattice structure:
- SlotMap = { partitions: Dict[KeySig, Slot], default: Slot }
- Slot = { val: AbsVal, pres: Presence, key_rep: Optional[AbsVal] }
- Presence = ABSENT | MAYBE | PRESENT (ordered: ABSENT ⊑ MAYBE, PRESENT ⊑ MAYBE)

Join/Widen/Narrow:
- join: union partitions, join values/presence, join defaults
- widen: join + spill excess partitions to default
- narrow: intersect partitions, meet values/presence (refine from conditions)
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import (
    TYPE_CHECKING,
    Any,
    Callable,
    Dict,
    FrozenSet,
    Iterator,
    List,
    Optional,
    Set,
    Tuple,
    Union,
)

if TYPE_CHECKING:
    from pythonstan.analysis.pointer.kcfa.object import AbstractObject, AllocKind
    from pythonstan.analysis.pointer.kcfa.context import AbstractContext, Scope
    from pythonstan.analysis.pointer.kcfa.heap_model import Field
    from ..state import AIState
    from ..value_domain import AbsVal
    from ..heap import FieldStore
    from ..summary import SummaryAccumulator
    from ..api import AnalysisBudget
    from .strings import StrLattice

__all__ = [
    # Core types
    "Presence",
    "Slot",
    "SlotMap",
    # KeySig types and helpers
    "KeySig",
    "make_str_keysig",
    "make_int_keysig",
    "make_type_keysig",
    "make_callable_keysig",
    "make_module_keysig",
    "make_tuple_keysig",
    "make_id_keysig",
    "keysig_kind",
    "keysig_value",
    "keysig_to_field",
    "field_to_keysig",
    # Services
    "SlotMapSemantics",
    "DictSemantics",
    "KeyDecomposer",
    "LoadResult",
]


# =============================================================================
# Presence Lattice
# =============================================================================

class Presence(Enum):
    """Key presence lattice element.
    
    Tracks whether a key is definitely present, definitely absent, or may be either.
    
    Lattice: ABSENT ⊑ MAYBE, PRESENT ⊑ MAYBE (MAYBE is top)
    
    This is a three-point lattice:
        MAYBE (top)
        /    \\
    ABSENT  PRESENT
        \\    /
         ⊥ (no explicit bottom; empty SlotMap represents no keys)
    """
    ABSENT = "absent"    # Key is definitely not present
    MAYBE = "maybe"      # Key may or may not be present
    PRESENT = "present"  # Key is definitely present
    
    def join(self, other: "Presence") -> "Presence":
        """Join (⊔) two presence elements."""
        if self == other:
            return self
        # Any different values join to MAYBE
        return Presence.MAYBE
    
    def meet(self, other: "Presence") -> "Presence":
        """Meet (⊓) two presence elements."""
        if self == other:
            return self
        if self == Presence.MAYBE:
            return other
        if other == Presence.MAYBE:
            return self
        # ABSENT ⊓ PRESENT = inconsistent, return ABSENT conservatively
        return Presence.ABSENT
    
    def leq(self, other: "Presence") -> bool:
        """Check if self ⊑ other."""
        if self == other:
            return True
        if other == Presence.MAYBE:
            return True
        return False
    
    def may_be_present(self) -> bool:
        """Check if key may be present."""
        return self in (Presence.MAYBE, Presence.PRESENT)
    
    def may_be_absent(self) -> bool:
        """Check if key may be absent."""
        return self in (Presence.MAYBE, Presence.ABSENT)
    
    def is_definitely_present(self) -> bool:
        """Check if key is definitely present."""
        return self == Presence.PRESENT
    
    def is_definitely_absent(self) -> bool:
        """Check if key is definitely absent."""
        return self == Presence.ABSENT


# =============================================================================
# KeySig - Hashable finite key signature
# =============================================================================

# KeySig is a tuple representing a hashable key signature
# Format: (kind, value) where kind identifies the key type
# Examples:
#   ("str", "foo")       - constant string key
#   ("int", 42)          - constant int key
#   ("type", ClassRef)   - type/class token key
#   ("callable", FuncRef) - callable token key
#   ("module", ModRef)   - module token key
#   ("tuple", ((kind1, val1), (kind2, val2), ...)) - tuple key (recursive)
#   ("id", AbstractObject) - identity key (for whitelisted kinds)

KeySig = Tuple[str, Any]


def make_str_keysig(s: str) -> KeySig:
    """Create a KeySig for a constant string."""
    return ("str", s)


def make_int_keysig(n: int) -> KeySig:
    """Create a KeySig for a constant int."""
    return ("int", n)


def make_type_keysig(cls_obj: "AbstractObject") -> KeySig:
    """Create a KeySig for a type/class token."""
    return ("type", cls_obj)


def make_callable_keysig(func_obj: "AbstractObject") -> KeySig:
    """Create a KeySig for a callable token."""
    return ("callable", func_obj)


def make_module_keysig(mod_obj: "AbstractObject") -> KeySig:
    """Create a KeySig for a module token."""
    return ("module", mod_obj)


def make_tuple_keysig(elem_sigs: Tuple[KeySig, ...]) -> KeySig:
    """Create a KeySig for a tuple key."""
    return ("tuple", elem_sigs)


def make_id_keysig(obj: "AbstractObject") -> KeySig:
    """Create a KeySig for an identity key."""
    return ("id", obj)


def keysig_kind(sig: KeySig) -> str:
    """Get the kind of a KeySig."""
    return sig[0]


def keysig_value(sig: KeySig) -> Any:
    """Extract value from KeySig."""
    return sig[1]


def keysig_to_field(sig: KeySig, for_dict: bool = False) -> "Field":
    """Convert a KeySig to a KCFA Field.
    
    Args:
        sig: KeySig to convert
        for_dict: If True, use key() for strings, else use attr()
        
    Returns:
        KCFA Field (attr, key, or elem)
    """
    from pythonstan.analysis.pointer.kcfa.heap_model import attr, key, elem
    
    kind = keysig_kind(sig)
    if kind == "str":
        s = sig[1]
        if for_dict:
            return key(s)
        else:
            return attr(s)
    else:
        # Non-string keys degrade to elem()
        return elem()


def field_to_keysig(field_key: "Field") -> Optional[KeySig]:
    """Convert a KCFA Field to a KeySig.
    
    Args:
        field_key: KCFA Field to convert
        
    Returns:
        KeySig if convertible, else None
    """
    from pythonstan.analysis.pointer.kcfa.heap_model import FieldKind
    
    if hasattr(field_key, 'kind') and hasattr(field_key, 'name'):
        if field_key.kind == FieldKind.ATTRIBUTE and isinstance(field_key.name, str):
            return make_str_keysig(field_key.name)
        elif field_key.kind == FieldKind.KEY and isinstance(field_key.name, str):
            return make_str_keysig(field_key.name)
    return None


# =============================================================================
# Slot - Single key's abstract state (unified Partition + Part)
# =============================================================================

@dataclass
class Slot:
    """A slot entry representing a key's value and presence.
    
    Unified abstraction that merges the old Partition (MapSlots) and Part (DictSlots).
    
    Attributes:
        val: AbsVal representing values for this key
        pres: Presence lattice element for key presence
        key_rep: Optional AbsVal representing the key itself (for dict key queries)
    """
    val: "AbsVal"
    pres: Presence
    key_rep: Optional["AbsVal"] = None
    
    def copy(self) -> "Slot":
        """Create a copy of this slot."""
        return Slot(val=self.val, pres=self.pres, key_rep=self.key_rep)
    
    def join(self, other: "Slot") -> "Slot":
        """Join two slots."""
        key_rep = None
        if self.key_rep is not None and other.key_rep is not None:
            key_rep = self.key_rep.join(other.key_rep)
        elif self.key_rep is not None:
            key_rep = self.key_rep
        elif other.key_rep is not None:
            key_rep = other.key_rep
        
        return Slot(
            val=self.val.join(other.val),
            pres=self.pres.join(other.pres),
            key_rep=key_rep,
        )
    
    def widen(self, other: "Slot", max_addrs: int = 50) -> "Slot":
        """Widen two slots."""
        key_rep = None
        if self.key_rep is not None and other.key_rep is not None:
            key_rep = self.key_rep.widen(other.key_rep, max_addrs=max_addrs)
        elif self.key_rep is not None:
            key_rep = self.key_rep
        elif other.key_rep is not None:
            key_rep = other.key_rep
        
        return Slot(
            val=self.val.widen(other.val, max_addrs=max_addrs),
            pres=self.pres.join(other.pres),  # No widen for Presence
            key_rep=key_rep,
        )
    
    def narrow(self, other: "Slot") -> "Slot":
        """Narrow two slots (refine from branch condition)."""
        key_rep = None
        if self.key_rep is not None and other.key_rep is not None:
            key_rep = self.key_rep.meet(other.key_rep)
        elif self.key_rep is not None:
            key_rep = self.key_rep
        
        return Slot(
            val=self.val.meet(other.val),
            pres=self.pres.meet(other.pres),
            key_rep=key_rep,
        )
    
    def leq(self, other: "Slot") -> bool:
        """Check if self ⊑ other."""
        if not self.val.leq(other.val):
            return False
        if not self.pres.leq(other.pres):
            return False
        # key_rep comparison: if self has key_rep, other should too and self ⊑ other
        if self.key_rep is not None:
            if other.key_rep is None:
                return False
            if not self.key_rep.leq(other.key_rep):
                return False
        return True
    
    @classmethod
    def bottom(cls, track_key_rep: bool = False) -> "Slot":
        """Create bottom slot (absent key with no value)."""
        from ..value_domain import AbsVal
        key_rep = AbsVal.bottom() if track_key_rep else None
        return cls(val=AbsVal.bottom(), pres=Presence.ABSENT, key_rep=key_rep)
    
    @classmethod
    def present(cls, val: "AbsVal", key_rep: Optional["AbsVal"] = None) -> "Slot":
        """Create slot for definitely present key."""
        return cls(val=val, pres=Presence.PRESENT, key_rep=key_rep)
    
    @classmethod
    def maybe(cls, val: "AbsVal", key_rep: Optional["AbsVal"] = None) -> "Slot":
        """Create slot for maybe-present key."""
        return cls(val=val, pres=Presence.MAYBE, key_rep=key_rep)
    
    def __str__(self) -> str:
        if self.key_rep is not None:
            return f"({self.val}, {self.pres.value}, key={self.key_rep})"
        return f"({self.val}, {self.pres.value})"


# =============================================================================
# SlotMap - Unified map/namespace lattice element
# =============================================================================

@dataclass
class SlotMap:
    """Unified map/namespace lattice element.
    
    Represents namespace/dict state with finite partitions + default bucket + presence.
    Unifies the old MapSlots (string-keyed) and DictSlots (KeySig-keyed).
    
    Attributes:
        partitions: Finite map from KeySig to Slots
        default: Slot for all untracked keys (keys not in partitions)
        max_partitions: Budget cap for tracked keys (spill on widen)
        track_key_rep: Whether to track key AbsVals (for dict key queries)
    
    Soundness invariant:
        For any concrete key k:
        - If k in partitions: concrete_value(k) ∈ γ(partitions[k].val)
        - If k not in partitions: concrete_value(k) ∈ γ(default.val)
        - Presence correctly tracks whether k is in the concrete map
    """
    partitions: Dict[KeySig, Slot] = field(default_factory=dict)
    default: Slot = field(default_factory=lambda: Slot.bottom())
    max_partitions: int = 20
    track_key_rep: bool = False
    
    def copy(self) -> "SlotMap":
        """Create a shallow copy."""
        return SlotMap(
            partitions={k: v.copy() for k, v in self.partitions.items()},
            default=self.default.copy(),
            max_partitions=self.max_partitions,
            track_key_rep=self.track_key_rep,
        )
    
    # -------------------------------------------------------------------------
    # Factory methods
    # -------------------------------------------------------------------------
    
    @classmethod
    def for_namespace(cls, max_partitions: int = 20) -> "SlotMap":
        """Create SlotMap for attribute namespaces (string keys, no key_rep)."""
        from ..value_domain import AbsVal
        return cls(
            partitions={},
            default=Slot(val=AbsVal.bottom(), pres=Presence.ABSENT, key_rep=None),
            max_partitions=max_partitions,
            track_key_rep=False,
        )
    
    @classmethod
    def for_dict(cls, max_partitions: int = 20) -> "SlotMap":
        """Create SlotMap for dict objects (full KeySig, with key_rep)."""
        from ..value_domain import AbsVal
        return cls(
            partitions={},
            default=Slot(val=AbsVal.bottom(), pres=Presence.ABSENT, key_rep=AbsVal.bottom()),
            max_partitions=max_partitions,
            track_key_rep=True,
        )
    
    @classmethod
    def bottom(cls, max_partitions: int = 20, track_key_rep: bool = False) -> "SlotMap":
        """Create bottom SlotMap (empty namespace)."""
        from ..value_domain import AbsVal
        key_rep = AbsVal.bottom() if track_key_rep else None
        return cls(
            partitions={},
            default=Slot(val=AbsVal.bottom(), pres=Presence.ABSENT, key_rep=key_rep),
            max_partitions=max_partitions,
            track_key_rep=track_key_rep,
        )
    
    @classmethod
    def top(cls, max_partitions: int = 20, track_key_rep: bool = False) -> "SlotMap":
        """Create top SlotMap (unknown namespace)."""
        from ..value_domain import AbsVal
        key_rep = AbsVal.top() if track_key_rep else None
        return cls(
            partitions={},
            default=Slot(val=AbsVal.top(), pres=Presence.MAYBE, key_rep=key_rep),
            max_partitions=max_partitions,
            track_key_rep=track_key_rep,
        )
    
    # -------------------------------------------------------------------------
    # Read operations
    # -------------------------------------------------------------------------
    
    def get(self, sig: KeySig) -> Slot:
        """Get slot for a specific KeySig.
        
        Returns the tracked slot if key is tracked, otherwise default.
        """
        if sig in self.partitions:
            return self.partitions[sig]
        return self.default
    
    def get_str(self, key: str) -> Slot:
        """Get slot for a string key (convenience method)."""
        return self.get(make_str_keysig(key))
    
    def get_value(self, sig: KeySig) -> "AbsVal":
        """Get value for a specific key (convenience method)."""
        return self.get(sig).val
    
    def get_str_value(self, key: str) -> "AbsVal":
        """Get value for a string key (convenience method)."""
        return self.get_str(key).val
    
    def get_presence(self, sig: KeySig) -> Presence:
        """Get presence for a specific key."""
        return self.get(sig).pres
    
    def get_str_presence(self, key: str) -> Presence:
        """Get presence for a string key."""
        return self.get_str(key).pres
    
    def get_keys_matching_str(self, str_lattice: "StrLattice") -> Tuple[FrozenSet[KeySig], bool]:
        """Get tracked keys matching a string lattice + whether default may match.
        
        Args:
            str_lattice: String lattice to filter against
            
        Returns:
            Tuple of (matching tracked KeySigs, default_may_match)
        """
        matching: Set[KeySig] = set()
        for sig in self.partitions:
            if keysig_kind(sig) == "str":
                s = sig[1]
                if str_lattice.may_equal(s):
                    matching.add(sig)
        
        # Default matches if str_lattice has non-tracked possibilities
        tracked_strs = {sig[1] for sig in self.partitions if keysig_kind(sig) == "str"}
        default_matches = not str_lattice.is_enumerable() or any(
            s not in tracked_strs for s in str_lattice.consts
        )
        
        return frozenset(matching), default_matches
    
    def get_all_values(self) -> "AbsVal":
        """Get joined value from all partitions and default."""
        from ..value_domain import AbsVal
        result = self.default.val
        for slot in self.partitions.values():
            result = result.join(slot.val)
        return result
    
    def get_all_keys(self) -> "AbsVal":
        """Get joined keys from all partitions and default (for dict key queries)."""
        from ..value_domain import AbsVal
        result = self.default.key_rep if self.default.key_rep is not None else AbsVal.bottom()
        for slot in self.partitions.values():
            if slot.key_rep is not None:
                result = result.join(slot.key_rep)
        return result
    
    def get_all_keysigs(self) -> FrozenSet[KeySig]:
        """Get all tracked KeySigs."""
        return frozenset(self.partitions.keys())
    
    def get_all_str_keys(self) -> FrozenSet[str]:
        """Get all tracked string keys."""
        return frozenset(
            sig[1] for sig in self.partitions if keysig_kind(sig) == "str"
        )
    
    def get_present_keysigs(self) -> FrozenSet[KeySig]:
        """Get tracked KeySigs that may be present."""
        return frozenset(
            k for k, s in self.partitions.items() if s.pres.may_be_present()
        )
    
    def get_definitely_present_keysigs(self) -> FrozenSet[KeySig]:
        """Get tracked KeySigs that are definitely present."""
        return frozenset(
            k for k, s in self.partitions.items() if s.pres.is_definitely_present()
        )
    
    # Backward compatibility
    def get_present_keys(self) -> FrozenSet[str]:
        """Get tracked string keys that may be present (backward compat)."""
        return frozenset(
            sig[1] for sig, s in self.partitions.items()
            if keysig_kind(sig) == "str" and s.pres.may_be_present()
        )
    
    def get_definitely_present_keys(self) -> FrozenSet[str]:
        """Get tracked string keys that are definitely present (backward compat)."""
        return frozenset(
            sig[1] for sig, s in self.partitions.items()
            if keysig_kind(sig) == "str" and s.pres.is_definitely_present()
        )
    
    # -------------------------------------------------------------------------
    # Write operations
    # -------------------------------------------------------------------------
    
    def weak_update(
        self,
        sig: KeySig,
        val: "AbsVal",
        pres: Presence = Presence.PRESENT,
        key_rep: Optional["AbsVal"] = None,
    ) -> "SlotMap":
        """Weak update for a tracked key.
        
        If key exists: join values and presence.
        If key count < max: create new partition.
        Otherwise: spill to default bucket.
        
        Args:
            sig: KeySig to update
            val: Value to write
            pres: Presence of the key (default PRESENT for definite writes)
            key_rep: Optional key representation (for dict key tracking)
        """
        new_slots = self.copy()
        
        if sig in new_slots.partitions:
            existing = new_slots.partitions[sig]
            new_key_rep = None
            if self.track_key_rep:
                if existing.key_rep is not None and key_rep is not None:
                    new_key_rep = existing.key_rep.join(key_rep)
                elif existing.key_rep is not None:
                    new_key_rep = existing.key_rep
                elif key_rep is not None:
                    new_key_rep = key_rep
            new_slots.partitions[sig] = Slot(
                val=existing.val.join(val),
                pres=existing.pres.join(pres),
                key_rep=new_key_rep,
            )
        elif len(new_slots.partitions) < new_slots.max_partitions:
            new_slots.partitions[sig] = Slot(
                val=val,
                pres=pres,
                key_rep=key_rep if self.track_key_rep else None,
            )
        else:
            # Spill to default bucket
            new_key_rep = None
            if self.track_key_rep:
                if new_slots.default.key_rep is not None and key_rep is not None:
                    new_key_rep = new_slots.default.key_rep.join(key_rep)
                elif new_slots.default.key_rep is not None:
                    new_key_rep = new_slots.default.key_rep
                elif key_rep is not None:
                    new_key_rep = key_rep
            new_slots.default = Slot(
                val=new_slots.default.val.join(val),
                pres=new_slots.default.pres.join(pres),
                key_rep=new_key_rep,
            )
        
        return new_slots
    
    def weak_update_str(
        self,
        key: str,
        val: "AbsVal",
        pres: Presence = Presence.PRESENT,
    ) -> "SlotMap":
        """Weak update for a string key (convenience method)."""
        key_rep = None
        if self.track_key_rep:
            from ..value_domain import AbsVal
            key_rep = AbsVal.from_str(key)
        return self.weak_update(make_str_keysig(key), val, pres, key_rep)
    
    def strong_update(
        self,
        sig: KeySig,
        val: "AbsVal",
        pres: Presence = Presence.PRESENT,
        key_rep: Optional["AbsVal"] = None,
    ) -> "SlotMap":
        """Strong update for a tracked key (replaces, doesn't join).
        
        Only sound when we know this is the only possible write path
        (single concrete receiver, single key).
        """
        new_slots = self.copy()
        
        if len(new_slots.partitions) < new_slots.max_partitions or sig in new_slots.partitions:
            new_slots.partitions[sig] = Slot(
                val=val,
                pres=pres,
                key_rep=key_rep if self.track_key_rep else None,
            )
        else:
            # Spill to default bucket
            new_key_rep = None
            if self.track_key_rep:
                if new_slots.default.key_rep is not None and key_rep is not None:
                    new_key_rep = new_slots.default.key_rep.join(key_rep)
                elif new_slots.default.key_rep is not None:
                    new_key_rep = new_slots.default.key_rep
                elif key_rep is not None:
                    new_key_rep = key_rep
            new_slots.default = Slot(
                val=new_slots.default.val.join(val),
                pres=new_slots.default.pres.join(pres),
                key_rep=new_key_rep,
            )
        
        return new_slots
    
    def strong_update_str(
        self,
        key: str,
        val: "AbsVal",
        pres: Presence = Presence.PRESENT,
    ) -> "SlotMap":
        """Strong update for a string key (convenience method)."""
        key_rep = None
        if self.track_key_rep:
            from ..value_domain import AbsVal
            key_rep = AbsVal.from_str(key)
        return self.strong_update(make_str_keysig(key), val, pres, key_rep)
    
    def weak_update_default(
        self,
        val: "AbsVal",
        pres: Presence = Presence.PRESENT,
        key_rep: Optional["AbsVal"] = None,
    ) -> "SlotMap":
        """Weak update for the default bucket (unknown key)."""
        new_slots = self.copy()
        new_key_rep = None
        if self.track_key_rep:
            if new_slots.default.key_rep is not None and key_rep is not None:
                new_key_rep = new_slots.default.key_rep.join(key_rep)
            elif new_slots.default.key_rep is not None:
                new_key_rep = new_slots.default.key_rep
            elif key_rep is not None:
                new_key_rep = key_rep
        new_slots.default = Slot(
            val=new_slots.default.val.join(val),
            pres=new_slots.default.pres.join(pres),
            key_rep=new_key_rep,
        )
        return new_slots
    
    def mark_absent(self, sig: KeySig) -> "SlotMap":
        """Mark a key as potentially absent (for del operations).
        
        Shifts presence from PRESENT to MAYBE (can't go to ABSENT
        since the key might be re-added).
        """
        new_slots = self.copy()
        if sig in new_slots.partitions:
            slot = new_slots.partitions[sig]
            if slot.pres == Presence.PRESENT:
                new_slots.partitions[sig] = Slot(
                    val=slot.val,
                    pres=Presence.MAYBE,
                    key_rep=slot.key_rep,
                )
        return new_slots
    
    def mark_absent_str(self, key: str) -> "SlotMap":
        """Mark a string key as potentially absent (convenience method)."""
        return self.mark_absent(make_str_keysig(key))
    
    # -------------------------------------------------------------------------
    # Refinement operations (for branch conditions)
    # -------------------------------------------------------------------------
    
    def assume_contains(self, sig: KeySig, key_rep: Optional["AbsVal"] = None) -> "SlotMap":
        """Refine assuming key is present (true branch of 'k in map').
        
        Strengthens presence to PRESENT for the key.
        """
        new_slots = self.copy()
        
        if sig in new_slots.partitions:
            slot = new_slots.partitions[sig]
            if slot.pres == Presence.MAYBE:
                new_slots.partitions[sig] = Slot(
                    val=slot.val,
                    pres=Presence.PRESENT,
                    key_rep=slot.key_rep,
                )
        elif len(new_slots.partitions) < new_slots.max_partitions:
            # Key not tracked but we know it's present - add with default value
            new_slots.partitions[sig] = Slot(
                val=new_slots.default.val,  # May have value from default
                pres=Presence.PRESENT,
                key_rep=key_rep if self.track_key_rep else None,
            )
        
        return new_slots
    
    def assume_contains_str(self, key: str) -> "SlotMap":
        """Refine assuming string key is present (convenience method)."""
        key_rep = None
        if self.track_key_rep:
            from ..value_domain import AbsVal
            key_rep = AbsVal.from_str(key)
        return self.assume_contains(make_str_keysig(key), key_rep)
    
    def assume_not_contains(self, sig: KeySig) -> "SlotMap":
        """Refine assuming key is absent (false branch of 'k in map').
        
        Strengthens presence to ABSENT for the key.
        """
        new_slots = self.copy()
        
        if sig in new_slots.partitions:
            slot = new_slots.partitions[sig]
            if slot.pres == Presence.MAYBE:
                new_slots.partitions[sig] = Slot(
                    val=slot.val,
                    pres=Presence.ABSENT,
                    key_rep=slot.key_rep,
                )
        
        return new_slots
    
    def assume_not_contains_str(self, key: str) -> "SlotMap":
        """Refine assuming string key is absent (convenience method)."""
        return self.assume_not_contains(make_str_keysig(key))
    
    # -------------------------------------------------------------------------
    # Lattice operations
    # -------------------------------------------------------------------------
    
    def join(self, other: "SlotMap") -> "SlotMap":
        """Join two SlotMaps (⊔).
        
        Union of partition keys, join values and presence element-wise,
        join defaults.
        """
        new_partitions: Dict[KeySig, Slot] = {}
        all_keys = set(self.partitions.keys()) | set(other.partitions.keys())
        track_key_rep = self.track_key_rep or other.track_key_rep
        
        for sig in all_keys:
            s1 = self.partitions.get(sig)
            s2 = other.partitions.get(sig)
            
            if s1 is not None and s2 is not None:
                # Both have this key - join
                new_partitions[sig] = s1.join(s2)
            elif s1 is not None:
                # Only self has key - might not be present in other branch
                # Join with other's default for the value, presence goes to MAYBE
                new_key_rep = s1.key_rep
                if s1.pres == Presence.PRESENT:
                    new_partitions[sig] = Slot(
                        val=s1.val.join(other.default.val),
                        pres=Presence.MAYBE,
                        key_rep=new_key_rep,
                    )
                else:
                    new_partitions[sig] = Slot(
                        val=s1.val.join(other.default.val),
                        pres=s1.pres,
                        key_rep=new_key_rep,
                    )
            else:
                assert s2 is not None
                # Only other has key
                new_key_rep = s2.key_rep
                if s2.pres == Presence.PRESENT:
                    new_partitions[sig] = Slot(
                        val=s2.val.join(self.default.val),
                        pres=Presence.MAYBE,
                        key_rep=new_key_rep,
                    )
                else:
                    new_partitions[sig] = Slot(
                        val=s2.val.join(self.default.val),
                        pres=s2.pres,
                        key_rep=new_key_rep,
                    )
        
        return SlotMap(
            partitions=new_partitions,
            default=self.default.join(other.default),
            max_partitions=min(self.max_partitions, other.max_partitions),
            track_key_rep=track_key_rep,
        )
    
    def leq(self, other: "SlotMap") -> bool:
        """Check if self ⊑ other."""
        # Check default bucket
        if not self.default.leq(other.default):
            return False
        
        # Check all partitions in self
        for sig, slot in self.partitions.items():
            if sig in other.partitions:
                if not slot.leq(other.partitions[sig]):
                    return False
            else:
                # Self has key not in other - must be ⊑ other's default
                if not slot.leq(other.default):
                    return False
        
        return True
    
    def widen(self, other: "SlotMap", max_addrs: int = 50) -> "SlotMap":
        """Widen SlotMap (∇).
        
        Key explosion → spill to default bucket.
        """
        joined = self.join(other)
        
        if len(joined.partitions) > joined.max_partitions:
            # Spill excess partitions to default bucket
            # Strategy: keep most "valuable" partitions (those with PRESENT status)
            # and spill others
            
            present_keys = [
                k for k, s in joined.partitions.items()
                if s.pres == Presence.PRESENT
            ]
            maybe_keys = [
                k for k, s in joined.partitions.items()
                if s.pres == Presence.MAYBE
            ]
            absent_keys = [
                k for k, s in joined.partitions.items()
                if s.pres == Presence.ABSENT
            ]
            
            # Keep present keys first, then maybe, spill absent and excess
            to_keep: List[KeySig] = []
            to_spill: List[KeySig] = []
            
            for k in present_keys:
                if len(to_keep) < joined.max_partitions:
                    to_keep.append(k)
                else:
                    to_spill.append(k)
            for k in maybe_keys:
                if len(to_keep) < joined.max_partitions:
                    to_keep.append(k)
                else:
                    to_spill.append(k)
            for k in absent_keys:
                if len(to_keep) < joined.max_partitions:
                    to_keep.append(k)
                else:
                    to_spill.append(k)
            
            # Build new default from spilled partitions
            spilled_val = joined.default.val
            spilled_pres = joined.default.pres
            spilled_key_rep = joined.default.key_rep
            for k in to_spill:
                slot = joined.partitions[k]
                spilled_val = spilled_val.join(slot.val)
                spilled_pres = spilled_pres.join(slot.pres)
                if joined.track_key_rep and slot.key_rep is not None:
                    if spilled_key_rep is not None:
                        spilled_key_rep = spilled_key_rep.join(slot.key_rep)
                    else:
                        spilled_key_rep = slot.key_rep
            
            new_partitions = {k: joined.partitions[k] for k in to_keep}
            
            # Widen individual partitions
            for k in new_partitions:
                new_partitions[k] = new_partitions[k].widen(
                    new_partitions[k], max_addrs
                )
            
            new_default = Slot(
                val=spilled_val,
                pres=spilled_pres,
                key_rep=spilled_key_rep,
            ).widen(
                Slot(val=spilled_val, pres=spilled_pres, key_rep=spilled_key_rep),
                max_addrs
            )
            
            return SlotMap(
                partitions=new_partitions,
                default=new_default,
                max_partitions=joined.max_partitions,
                track_key_rep=joined.track_key_rep,
            )
        
        return joined
    
    def narrow(self, other: "SlotMap") -> "SlotMap":
        """Narrow SlotMap (△).
        
        Refines by meeting (intersecting) partitions.
        """
        new_partitions: Dict[KeySig, Slot] = {}
        for sig in self.partitions:
            if sig in other.partitions:
                new_partitions[sig] = self.partitions[sig].narrow(other.partitions[sig])
            else:
                new_partitions[sig] = self.partitions[sig]
        
        return SlotMap(
            partitions=new_partitions,
            default=self.default.narrow(other.default),
            max_partitions=self.max_partitions,
            track_key_rep=self.track_key_rep,
        )
    
    def is_bottom(self) -> bool:
        """Check if this is bottom (no keys, empty default)."""
        if len(self.partitions) > 0:
            return False
        if not self.default.val.is_bottom():
            return False
        return True
    
    def __str__(self) -> str:
        present_keys = sorted(
            (str(k) for k in self.get_present_keysigs()),
            key=str
        )[:5]
        parts_str = ", ".join(present_keys)
        if len(self.partitions) > 5:
            parts_str += f", ... ({len(self.partitions) - 5} more)"
        mode = "dict" if self.track_key_rep else "ns"
        return f"SlotMap[{mode}]({{{parts_str}}}, default={self.default})"


# =============================================================================
# LoadResult - Result of a load operation
# =============================================================================

@dataclass
class LoadResult:
    """Result of a load operation from SlotMap.
    
    Attributes:
        result_val: The loaded value
        may_be_missing: Whether the key may be absent
        saw_havoc: Whether havoc was encountered
    """
    result_val: "AbsVal"
    may_be_missing: bool
    saw_havoc: bool = False


# =============================================================================
# KeyDecomposer - Decompose AbsVal keys into bounded partitions
# =============================================================================


class KeyDecomposer:
    """Service to decompose AbsVal keys into bounded KeySig partitions.
    
    Strategy (priority order):
    1. Enumerable constant strings (up to cap)
    2. Enumerable constant ints (up to cap)
    3. Token domains (type/callable/module) (up to cap)
    4. Tuple keys (small TupleExact only)
    5. Identity keys (whitelisted kinds only)
    6. Everything else → default bucket
    """
    
    # Whitelisted AllocKinds for identity key partitioning
    # These are kinds where equality/hash is reliably identity-like
    IDENTITY_KEY_WHITELIST = frozenset([
        "class",    # AllocKind.CLASS
        "func",     # AllocKind.FUNCTION
        "module",   # AllocKind.MODULE
        "builtin",  # AllocKind.BUILTIN
    ])
    
    def __init__(self, budget: Optional["AnalysisBudget"] = None):
        """Initialize decomposer with budget caps.
        
        Args:
            budget: Analysis budget with dict-specific caps
        """
        self.budget = budget
        
        # Get caps from budget or use defaults
        self.max_str_keys = getattr(budget, "max_dict_key_const_strs", 10)
        self.max_int_keys = getattr(budget, "max_dict_key_const_ints", 10)
        self.max_tuple_len = getattr(budget, "max_dict_key_tuple_len", 4)
        self.max_tuple_alts = getattr(budget, "max_dict_key_tuple_alts", 2)
        self.enable_identity_keys = getattr(budget, "enable_dict_identity_keys", True)
        self.max_identity_keys = getattr(budget, "max_dict_identity_keys", 10)
    
    def decompose(
        self,
        key_val: "AbsVal",
        state: "AIState",
    ) -> Tuple[List[Tuple[KeySig, "AbsVal"]], bool, "AbsVal"]:
        """Decompose an AbsVal key into bounded partitions.
        
        Args:
            key_val: Abstract value representing the key
            state: Current AI state (for tuple materialization)
            
        Returns:
            Tuple of:
            - tracked: List of (KeySig, key_rep AbsVal) pairs for tracked keys
            - has_default: Whether there are keys falling into default bucket
            - default_key_rep: AbsVal for keys in default bucket
        """
        from ..value_domain import AbsVal
        from .tuples import materialize_tuple_disj, TupleExact
        from pythonstan.analysis.pointer.kcfa.object import AllocKind
        
        tracked: List[Tuple[KeySig, AbsVal]] = []
        has_default = False
        default_key_rep = AbsVal.bottom()
        
        # 1. Enumerable constant strings
        str_lattice = key_val.get_string_lattice()
        if str_lattice.is_enumerable() and len(str_lattice.consts) <= self.max_str_keys:
            for s in str_lattice.consts:
                sig = make_str_keysig(s)
                tracked.append((sig, AbsVal.from_str(s)))
        elif not str_lattice.is_bottom():
            has_default = True
            default_key_rep = default_key_rep.join(key_val)
        
        # 2. Enumerable constant ints
        int_lattice = key_val.get_int_lattice()
        int_consts = int_lattice.get_consts()
        if int_consts is not None and len(int_consts) <= self.max_int_keys:
            for n in int_consts:
                sig = make_int_keysig(n)
                tracked.append((sig, AbsVal.from_int(n)))
        elif not int_lattice.is_bottom:  # is_bottom is a field, not a method
            has_default = True
            default_key_rep = default_key_rep.join(key_val)
        
        # 3. Token domains (type/callable/module) from addrs
        token_count = 0
        remaining_addrs: Set["AbstractObject"] = set()
        
        for obj in key_val.addrs:
            kind = getattr(obj, "kind", None)
            if kind is None:
                remaining_addrs.add(obj)
                continue
            
            kind_val = kind.value if hasattr(kind, "value") else str(kind)
            
            if kind_val == "class" or kind == AllocKind.CLASS:
                sig = make_type_keysig(obj)
                tracked.append((sig, AbsVal.from_addrs(frozenset({obj}))))
                token_count += 1
            elif kind_val in ("func", "method", "builtin") or kind in (
                AllocKind.FUNCTION, AllocKind.METHOD, AllocKind.BUILTIN
            ):
                sig = make_callable_keysig(obj)
                tracked.append((sig, AbsVal.from_addrs(frozenset({obj}))))
                token_count += 1
            elif kind_val == "module" or kind == AllocKind.MODULE:
                sig = make_module_keysig(obj)
                tracked.append((sig, AbsVal.from_addrs(frozenset({obj}))))
                token_count += 1
            elif kind_val == "tuple" or kind == AllocKind.TUPLE:
                # 4. Tuple keys - try to materialize
                tuple_disj = materialize_tuple_disj(
                    AbsVal.from_addrs(frozenset({obj})),
                    state,
                    None,  # caller_scope
                    None,  # call_context
                    max_len=self.max_tuple_len,
                    max_alts=self.max_tuple_alts,
                )
                
                for alt in tuple_disj.get_exact_alts():
                    if alt.length <= self.max_tuple_len:
                        # Try to decompose each element
                        elem_sigs: List[KeySig] = []
                        decomposable = True
                        for elem in alt.elems:
                            elem_tracked, elem_has_def, _ = self.decompose(elem, state)
                            if len(elem_tracked) == 1 and not elem_has_def:
                                elem_sigs.append(elem_tracked[0][0])
                            else:
                                decomposable = False
                                break
                        
                        if decomposable:
                            sig = make_tuple_keysig(tuple(elem_sigs))
                            tracked.append((sig, AbsVal.from_addrs(frozenset({obj}))))
                            token_count += 1
                        else:
                            remaining_addrs.add(obj)
                    else:
                        remaining_addrs.add(obj)
            elif self.enable_identity_keys and kind_val in self.IDENTITY_KEY_WHITELIST:
                # 5. Identity keys (whitelisted)
                if token_count < self.max_identity_keys:
                    sig = make_id_keysig(obj)
                    tracked.append((sig, AbsVal.from_addrs(frozenset({obj}))))
                    token_count += 1
                else:
                    remaining_addrs.add(obj)
            else:
                remaining_addrs.add(obj)
        
        # Remaining addresses go to default
        if remaining_addrs:
            has_default = True
            default_key_rep = default_key_rep.join(
                AbsVal.from_addrs(frozenset(remaining_addrs))
            )
        
        return tracked, has_default, default_key_rep


# =============================================================================
# SlotMapSemantics - Unified service for namespace/dict operations
# =============================================================================


class SlotMapSemantics:
    """Unified service for namespace load/store operations with presence refinement.
    
    Provides a unified interface for namespace operations that can be used
    for both attribute namespaces and dict-like objects. Handles:
    - Load with presence-aware missing checks
    - Store with strong/weak update depending on receiver count
    - Delete with presence update
    - Containment checks with refinement
    - Dict-specific operations (with key decomposition)
    
    This is the unified service that replaces both NamespaceSemantics and DictSemantics.
    """
    
    def __init__(
        self,
        caller_scope: Optional["Scope"],
        call_context: Optional["AbstractContext"],
        budget: Optional["AnalysisBudget"],
    ):
        """Initialize slot map semantics.
        
        Args:
            caller_scope: Scope context for field reads
            call_context: Call context for field reads
            budget: Analysis budget for caps
        """
        self.caller_scope = caller_scope
        self.call_context = call_context
        self.budget = budget
        self.max_partitions = getattr(budget, "max_namespace_partitions", 20)
        self.decomposer = KeyDecomposer(budget)
    
    # -------------------------------------------------------------------------
    # Basic operations (string keys for namespaces)
    # -------------------------------------------------------------------------
    
    def load(
        self,
        slots: SlotMap,
        key: str,
    ) -> Tuple["AbsVal", bool]:
        """Load value from namespace by string key.
        
        Args:
            slots: SlotMap representing the namespace
            key: String key to load
            
        Returns:
            Tuple of (value, may_be_missing)
        """
        slot = slots.get_str(key)
        may_be_missing = slot.pres.may_be_absent()
        return slot.val, may_be_missing
    
    def load_dynamic(
        self,
        slots: SlotMap,
        str_lattice: "StrLattice",
    ) -> Tuple["AbsVal", bool]:
        """Load value for dynamic/unknown string key.
        
        Uses string lattice to filter candidate keys.
        
        Args:
            slots: SlotMap representing the namespace
            str_lattice: String lattice constraining the key
            
        Returns:
            Tuple of (joined value, may_be_missing)
        """
        from ..value_domain import AbsVal
        
        matching_sigs, default_matches = slots.get_keys_matching_str(str_lattice)
        
        result = AbsVal.bottom()
        may_be_missing = False
        
        for sig in matching_sigs:
            slot = slots.partitions[sig]
            result = result.join(slot.val)
            if slot.pres.may_be_absent():
                may_be_missing = True
        
        if default_matches:
            result = result.join(slots.default.val)
            if slots.default.pres.may_be_absent():
                may_be_missing = True
        
        return result, may_be_missing
    
    def store(
        self,
        slots: SlotMap,
        key: str,
        val: "AbsVal",
        single_receiver: bool = False,
    ) -> SlotMap:
        """Store value to namespace by string key.
        
        Uses strong update if single_receiver is True (only one concrete receiver),
        otherwise weak update.
        
        Args:
            slots: SlotMap to update
            key: String key to store
            val: Value to store
            single_receiver: Whether there's only one possible receiver
            
        Returns:
            Updated SlotMap
        """
        if single_receiver:
            return slots.strong_update_str(key, val, Presence.PRESENT)
        else:
            return slots.weak_update_str(key, val, Presence.PRESENT)
    
    def store_dynamic(
        self,
        slots: SlotMap,
        str_lattice: "StrLattice",
        val: "AbsVal",
    ) -> SlotMap:
        """Store value for dynamic/unknown string key.
        
        Updates all matching tracked keys + default if needed.
        
        Args:
            slots: SlotMap to update
            str_lattice: String lattice constraining the key
            val: Value to store
            
        Returns:
            Updated SlotMap
        """
        new_slots = slots.copy()
        matching_sigs, default_matches = slots.get_keys_matching_str(str_lattice)
        
        # Update matching tracked keys
        for sig in matching_sigs:
            key_rep = None
            if slots.track_key_rep:
                from ..value_domain import AbsVal
                key_rep = AbsVal.from_str(sig[1])
            new_slots = new_slots.weak_update(sig, val, Presence.PRESENT, key_rep)
        
        # Update default if needed
        if default_matches:
            new_slots = new_slots.weak_update_default(val, Presence.PRESENT)
        
        return new_slots
    
    def delete(
        self,
        slots: SlotMap,
        key: str,
    ) -> SlotMap:
        """Delete key from namespace.
        
        Marks key as potentially absent.
        """
        return slots.mark_absent_str(key)
    
    def contains_str(
        self,
        slots: SlotMap,
        key: str,
    ) -> Tuple[bool, bool]:
        """Check if string key may be in namespace (namespace-only API).
        
        For dict membership checks, use DictSemantics.contains() instead.
        
        Args:
            slots: SlotMap to check
            key: String key to check
            
        Returns:
            Tuple of (may_be_true, may_be_false) for "k in ns"
        """
        pres = slots.get_str_presence(key)
        return pres.may_be_present(), pres.may_be_absent()
    
    def assume_contains_str(
        self,
        slots: SlotMap,
        key: str,
    ) -> SlotMap:
        """Refine namespace assuming string key is present (namespace-only API)."""
        return slots.assume_contains_str(key)
    
    def assume_not_contains_str(
        self,
        slots: SlotMap,
        key: str,
    ) -> SlotMap:
        """Refine namespace assuming string key is absent (namespace-only API)."""
        return slots.assume_not_contains_str(key)
    
    # -------------------------------------------------------------------------
    # Dict operations (full KeySig keys via decomposer)
    # -------------------------------------------------------------------------
    
    def load_item(
        self,
        slots: SlotMap,
        key_val: "AbsVal",
        state: "AIState",
    ) -> LoadResult:
        """Load value from dict: result = d[k].
        
        Args:
            slots: SlotMap representing the dict
            key_val: Abstract value representing the key
            state: Current AI state
            
        Returns:
            LoadResult with loaded value and error status
        """
        from ..value_domain import AbsVal
        
        result_val = AbsVal.bottom()
        may_be_missing = False
        
        # Decompose key
        tracked, has_default, default_key_rep = self.decomposer.decompose(key_val, state)
        
        # Read from tracked partitions
        for sig, key_rep in tracked:
            slot = slots.get(sig)
            # If the slot is from the partition, use its value
            if sig in slots.partitions:
                result_val = result_val.join(slot.val)
                if slot.pres.may_be_absent():
                    may_be_missing = True
            else:
                # Key not in tracked partitions, might be in default or missing
                result_val = result_val.join(slots.default.val)
                may_be_missing = True
        
        # Read from default bucket if needed
        if has_default:
            result_val = result_val.join(slots.default.val)
            may_be_missing = True
        
        return LoadResult(
            result_val=result_val,
            may_be_missing=may_be_missing,
            saw_havoc=False,
        )
    
    def store_item(
        self,
        slots: SlotMap,
        key_val: "AbsVal",
        value_val: "AbsVal",
        state: "AIState",
    ) -> SlotMap:
        """Store value to dict: d[k] = v.
        
        Args:
            slots: SlotMap to update
            key_val: Abstract value representing the key
            value_val: Abstract value to store
            state: Current AI state
            
        Returns:
            Updated SlotMap
        """
        # Decompose key
        tracked, has_default, default_key_rep = self.decomposer.decompose(key_val, state)
        
        # Update tracked partitions
        for sig, key_rep in tracked:
            slots = slots.weak_update(sig, value_val, Presence.PRESENT, key_rep)
        
        # Update default bucket if needed
        if has_default:
            slots = slots.weak_update_default(value_val, Presence.PRESENT, default_key_rep)
        
        return slots
    
    def del_item(
        self,
        slots: SlotMap,
        key_val: "AbsVal",
        state: "AIState",
    ) -> SlotMap:
        """Delete key from dict: del d[k].
        
        Args:
            slots: SlotMap to update
            key_val: Abstract value representing the key
            state: Current AI state
            
        Returns:
            Updated SlotMap
        """
        # Decompose key
        tracked, _, _ = self.decomposer.decompose(key_val, state)
        
        # Mark tracked keys as potentially absent
        for sig, _ in tracked:
            slots = slots.mark_absent(sig)
        
        return slots
    
    def contains_item(
        self,
        slots: SlotMap,
        key_val: "AbsVal",
        state: "AIState",
    ) -> Tuple[bool, bool]:
        """Check if key may be in dict.
        
        Args:
            slots: SlotMap to check
            key_val: Abstract value representing the key
            state: Current AI state
            
        Returns:
            Tuple of (may_be_true, may_be_false) for "k in d"
        """
        may_be_true = False
        may_be_false = False
        
        # Decompose key
        tracked, has_default, _ = self.decomposer.decompose(key_val, state)
        
        # Check tracked partitions
        for sig, _ in tracked:
            slot = slots.get(sig)
            if sig in slots.partitions:
                if slot.pres.may_be_present():
                    may_be_true = True
                if slot.pres.may_be_absent():
                    may_be_false = True
            else:
                # Not in tracked, check default
                if slots.default.pres.may_be_present():
                    may_be_true = True
                may_be_false = True
        
        # Check default bucket
        if has_default:
            if slots.default.pres.may_be_present():
                may_be_true = True
            may_be_false = True
        
        # If no tracked keys and no default, key is definitely not present
        if not tracked and not has_default:
            may_be_false = True
        
        return may_be_true, may_be_false
    
    def assume_contains_item(
        self,
        slots: SlotMap,
        key_val: "AbsVal",
        state: "AIState",
    ) -> SlotMap:
        """Refine SlotMap assuming key is in dict.
        
        Args:
            slots: SlotMap to refine
            key_val: Abstract value representing the key
            state: Current AI state
            
        Returns:
            Refined SlotMap
        """
        # Decompose key
        tracked, _, _ = self.decomposer.decompose(key_val, state)
        
        # Strengthen presence for tracked keys
        for sig, key_rep in tracked:
            slots = slots.assume_contains(sig, key_rep)
        
        return slots
    
    def assume_not_contains_item(
        self,
        slots: SlotMap,
        key_val: "AbsVal",
        state: "AIState",
    ) -> SlotMap:
        """Refine SlotMap assuming key is not in dict.
        
        Args:
            slots: SlotMap to refine
            key_val: Abstract value representing the key
            state: Current AI state
            
        Returns:
            Refined SlotMap
        """
        # Decompose key
        tracked, _, _ = self.decomposer.decompose(key_val, state)
        
        # Weaken presence for tracked keys
        for sig, _ in tracked:
            slots = slots.assume_not_contains(sig)
        
        return slots


    # -------------------------------------------------------------------------
    # Dict operations with AbsVal containers (for DictSemantics compatibility)
    # -------------------------------------------------------------------------
    
    def _get_or_create_slots(
        self,
        obj: "AbstractObject",
        state: "AIState",
    ) -> Tuple["AIState", SlotMap]:
        """Get or create SlotMap for a dict object.
        
        Args:
            obj: The dict object
            state: Current AI state
            
        Returns:
            Tuple of (potentially updated state, SlotMap)
        """
        from ..heap import AIAddr, AbsObj, ObjKind
        
        ai_addr = AIAddr.from_kcfa_obj(obj)
        ai_obj = state.get_obj(ai_addr)
        
        if ai_obj is not None:
            return state, ai_obj.slots
        
        # Create new SlotMap for dict
        max_parts = getattr(self.budget, "max_dict_parts", 20)
        new_slots = SlotMap.for_dict(max_partitions=max_parts)
        
        # Create new AbsObj
        ai_obj = AbsObj(
            addr=ai_addr,
            kind=ObjKind.DICT,
            slots=new_slots,
        )
        state = state.set_obj(ai_addr, ai_obj)
        
        return state, new_slots
    
    def _update_slots(
        self,
        obj: "AbstractObject",
        state: "AIState",
        new_slots: SlotMap,
    ) -> "AIState":
        """Update SlotMap for a dict object.
        
        Args:
            obj: The dict object
            state: Current AI state
            new_slots: New SlotMap to set
            
        Returns:
            Updated AI state
        """
        from ..heap import AIAddr, AbsObj, ObjKind
        
        ai_addr = AIAddr.from_kcfa_obj(obj)
        ai_obj = state.get_obj(ai_addr)
        
        if ai_obj is None:
            ai_obj = AbsObj(
                addr=ai_addr,
                kind=ObjKind.DICT,
                slots=new_slots,
            )
        else:
            ai_obj = ai_obj.with_slots(new_slots)
        
        return state.set_obj(ai_addr, ai_obj)
    
    def load_item_dict(
        self,
        dict_val: "AbsVal",
        key_val: "AbsVal",
        state: "AIState",
        summary: "SummaryAccumulator",
    ) -> LoadResult:
        """Load value from dict objects: result = d[k].
        
        DictSemantics-compatible interface that takes AbsVal for dict.
        
        Args:
            dict_val: Abstract value representing the dict
            key_val: Abstract value representing the key
            state: Current AI state
            summary: Summary accumulator for effects
            
        Returns:
            LoadResult with loaded value and error status
        """
        from ..value_domain import AbsVal
        from pythonstan.analysis.pointer.kcfa.heap_model import elem, key
        from pythonstan.analysis.pointer.kcfa.object import AllocKind
        
        result_val = AbsVal.bottom()
        may_raise_key_error = False
        saw_havoc = False
        
        # Decompose key
        tracked, has_default, default_key_rep = self.decomposer.decompose(key_val, state)
        
        for obj in dict_val.addrs:
            # Check if this is a dict object
            kind = getattr(obj, "kind", None)
            is_dict = (
                kind == AllocKind.DICT or
                (hasattr(kind, "value") and kind.value == "dict")
            )
            
            if not is_dict:
                # Not a dict, fall back to PTA/overlay
                if obj in state.overlay_havoc:
                    saw_havoc = True
                continue
            
            # Get slots
            _, slots = self._get_or_create_slots(obj, state)
            
            # Read from tracked partitions
            for sig, key_rep in tracked:
                if sig in slots.partitions:
                    slot = slots.partitions[sig]
                    result_val = result_val.join(slot.val)
                    if slot.pres.may_be_absent():
                        may_raise_key_error = True
                else:
                    # Key not in tracked partitions, might be in default or missing
                    result_val = result_val.join(slots.default.val)
                    may_raise_key_error = True
            
            # Read from default bucket if needed
            if has_default:
                result_val = result_val.join(slots.default.val)
                may_raise_key_error = True
            
            # Also record reads for PTA summary (string keys only)
            for sig, _ in tracked:
                if keysig_kind(sig) == "str":
                    s = sig[1]
                    summary.add_read(frozenset({obj}), key(s))
            summary.add_read(frozenset({obj}), elem())
        
        # If result is bottom (nothing read), or havoc, join with top
        if saw_havoc:
            result_val = result_val.join(AbsVal.top())
        
        return LoadResult(
            result_val=result_val,
            may_be_missing=may_raise_key_error,
            saw_havoc=saw_havoc,
        )
    
    def store_item_dict(
        self,
        dict_val: "AbsVal",
        key_val: "AbsVal",
        value_val: "AbsVal",
        state: "AIState",
        summary: "SummaryAccumulator",
    ) -> "AIState":
        """Store value to dict objects: d[k] = v.
        
        DictSemantics-compatible interface that takes AbsVal for dict.
        
        Args:
            dict_val: Abstract value representing the dict
            key_val: Abstract value representing the key
            value_val: Abstract value to store
            state: Current AI state
            summary: Summary accumulator for effects
            
        Returns:
            Updated AI state
        """
        from pythonstan.analysis.pointer.kcfa.heap_model import elem, key
        from pythonstan.analysis.pointer.kcfa.object import AllocKind
        
        # Decompose key
        tracked, has_default, default_key_rep = self.decomposer.decompose(key_val, state)
        
        for obj in dict_val.addrs:
            kind = getattr(obj, "kind", None)
            is_dict = (
                kind == AllocKind.DICT or
                (hasattr(kind, "value") and kind.value == "dict")
            )
            
            if not is_dict:
                continue
            
            # Get or create slots
            state, slots = self._get_or_create_slots(obj, state)
            
            # Update tracked partitions
            for sig, key_rep in tracked:
                slots = slots.weak_update(sig, value_val, Presence.PRESENT, key_rep)
            
            # Update default bucket if needed
            if has_default:
                slots = slots.weak_update_default(value_val, Presence.PRESENT, default_key_rep)
            
            # Update state with new slots
            state = self._update_slots(obj, state, slots)
            
            # Record writes for PTA summary
            # String keys: emit key(s) writes
            for sig, _ in tracked:
                if keysig_kind(sig) == "str":
                    s = sig[1]
                    if value_val.addrs:
                        summary.add_write(frozenset({obj}), key(s), value_val.addrs)
                    # Also update overlay for read-after-write
                    state = state.add_overlay_write(obj, key(s), value_val)
            
            # Non-string keys or default: emit elem() write (conservative)
            non_str_tracked = any(keysig_kind(sig) != "str" for sig, _ in tracked)
            if has_default or non_str_tracked:
                if value_val.addrs:
                    summary.add_write(frozenset({obj}), elem(), value_val.addrs)
                state = state.add_overlay_write(obj, elem(), value_val)
        
        return state


# Backward compatibility: DictSemantics class that wraps SlotMapSemantics
class DictSemantics(SlotMapSemantics):
    """Backward-compatible DictSemantics wrapper.
    
    This class provides the old DictSemantics interface for code that
    expects the old method signatures.
    """
    
    def load_item(
        self,
        dict_val: "AbsVal",
        key_val: "AbsVal",
        state: "AIState",
        summary: "SummaryAccumulator",
    ) -> LoadResult:
        """Load value from dict (DictSemantics-compatible)."""
        return self.load_item_dict(dict_val, key_val, state, summary)
    
    def store_item(
        self,
        dict_val: "AbsVal",
        key_val: "AbsVal",
        value_val: "AbsVal",
        state: "AIState",
        summary: "SummaryAccumulator",
    ) -> "AIState":
        """Store value to dict (DictSemantics-compatible)."""
        return self.store_item_dict(dict_val, key_val, value_val, state, summary)
    
    def dict_get(
        self,
        dict_val: "AbsVal",
        key_val: "AbsVal",
        default_val: "AbsVal",
        state: "AIState",
        summary: "SummaryAccumulator",
    ) -> "AbsVal":
        """Handle dict.get(k, default=None).
        
        Returns d[k] if present, else default.
        """
        load_result = self.load_item(dict_val, key_val, state, summary)
        
        result = load_result.result_val
        if load_result.may_be_missing:
            # Key might not be present, join with default
            result = result.join(default_val)
        
        return result
    
    def dict_setdefault(
        self,
        dict_val: "AbsVal",
        key_val: "AbsVal",
        default_val: "AbsVal",
        state: "AIState",
        summary: "SummaryAccumulator",
    ) -> Tuple["AIState", "AbsVal"]:
        """Handle dict.setdefault(k, default=None).
        
        If key exists, returns d[k].
        Otherwise, sets d[k] = default and returns default.
        """
        load_result = self.load_item(dict_val, key_val, state, summary)
        
        result = load_result.result_val
        if load_result.may_be_missing:
            # Key might not be present, write default
            state = self.store_item(dict_val, key_val, default_val, state, summary)
            result = result.join(default_val)
        
        return state, result
    
    def dict_update(
        self,
        dict_val: "AbsVal",
        other_val: "AbsVal",
        state: "AIState",
        summary: "SummaryAccumulator",
    ) -> "AIState":
        """Handle dict.update(other).
        
        Copies all key-value pairs from other into self.
        """
        from pythonstan.analysis.pointer.kcfa.object import AllocKind
        from ..value_domain import AbsVal
        
        for other_obj in other_val.addrs:
            kind = getattr(other_obj, "kind", None)
            is_dict = (
                kind == AllocKind.DICT or
                (hasattr(kind, "value") and kind.value == "dict")
            )
            
            if not is_dict:
                continue
            
            _, other_slots = self._get_or_create_slots(other_obj, state)
            
            # Copy all partitions from other
            for sig, slot in other_slots.partitions.items():
                if slot.pres.may_be_present():
                    # Use key_rep if available, otherwise create from keysig
                    if slot.key_rep is not None:
                        key_rep_val = slot.key_rep
                    elif keysig_kind(sig) == "str":
                        key_rep_val = AbsVal.from_str(sig[1])
                    elif keysig_kind(sig) == "int":
                        key_rep_val = AbsVal.from_int(sig[1])
                    else:
                        key_rep_val = AbsVal.top()
                    
                    state = self.store_item(dict_val, key_rep_val, slot.val, state, summary)
            
            # Copy default bucket
            if not other_slots.default.val.is_bottom():
                state = self.store_item(dict_val, AbsVal.top(), other_slots.default.val, state, summary)
        
        return state
    
    def dict_keys(
        self,
        dict_val: "AbsVal",
        state: "AIState",
    ) -> "AbsVal":
        """Handle dict.keys().
        
        Returns joined keys from all partitions.
        """
        from pythonstan.analysis.pointer.kcfa.object import AllocKind
        from ..value_domain import AbsVal
        
        result = AbsVal.bottom()
        
        for obj in dict_val.addrs:
            kind = getattr(obj, "kind", None)
            is_dict = (
                kind == AllocKind.DICT or
                (hasattr(kind, "value") and kind.value == "dict")
            )
            
            if not is_dict:
                continue
            
            _, slots = self._get_or_create_slots(obj, state)
            result = result.join(slots.get_all_keys())
        
        return result
    
    def dict_values(
        self,
        dict_val: "AbsVal",
        state: "AIState",
    ) -> "AbsVal":
        """Handle dict.values().
        
        Returns joined values from all partitions.
        """
        from pythonstan.analysis.pointer.kcfa.object import AllocKind
        from ..value_domain import AbsVal
        
        result = AbsVal.bottom()
        
        for obj in dict_val.addrs:
            kind = getattr(obj, "kind", None)
            is_dict = (
                kind == AllocKind.DICT or
                (hasattr(kind, "value") and kind.value == "dict")
            )
            
            if not is_dict:
                continue
            
            _, slots = self._get_or_create_slots(obj, state)
            result = result.join(slots.get_all_values())
        
        return result
    
    def assume_contains(
        self,
        dict_val: "AbsVal",
        key_val: "AbsVal",
        state: "AIState",
    ) -> "AIState":
        """Refine state assuming key is in dict.
        
        Used for value partition refinement in true branch of "k in d".
        
        Args:
            dict_val: Abstract value representing the dict
            key_val: Abstract value representing the key
            state: Current AI state
            
        Returns:
            Refined AI state
        """
        from pythonstan.analysis.pointer.kcfa.object import AllocKind
        
        # Decompose key
        tracked, _, _ = self.decomposer.decompose(key_val, state)
        
        for obj in dict_val.addrs:
            kind = getattr(obj, "kind", None)
            is_dict = (
                kind == AllocKind.DICT or
                (hasattr(kind, "value") and kind.value == "dict")
            )
            
            if not is_dict:
                continue
            
            state, slots = self._get_or_create_slots(obj, state)
            
            # Strengthen presence for tracked keys
            for sig, key_rep in tracked:
                slots = slots.assume_contains(sig, key_rep)
            
            state = self._update_slots(obj, state, slots)
        
        return state
    
    def assume_not_contains(
        self,
        dict_val: "AbsVal",
        key_val: "AbsVal",
        state: "AIState",
    ) -> "AIState":
        """Refine state assuming key is not in dict.
        
        Used for value partition refinement in false branch of "k in d".
        
        Args:
            dict_val: Abstract value representing the dict
            key_val: Abstract value representing the key
            state: Current AI state
            
        Returns:
            Refined AI state
        """
        from pythonstan.analysis.pointer.kcfa.object import AllocKind
        
        # Decompose key
        tracked, _, _ = self.decomposer.decompose(key_val, state)
        
        for obj in dict_val.addrs:
            kind = getattr(obj, "kind", None)
            is_dict = (
                kind == AllocKind.DICT or
                (hasattr(kind, "value") and kind.value == "dict")
            )
            
            if not is_dict:
                continue
            
            state, slots = self._get_or_create_slots(obj, state)
            
            # Weaken presence for tracked keys
            for sig, _ in tracked:
                slots = slots.assume_not_contains(sig)
            
            state = self._update_slots(obj, state, slots)
        
        return state
    
    def contains(
        self,
        dict_val: "AbsVal",
        key_val: "AbsVal",
        state: "AIState",
    ) -> Tuple[bool, bool]:
        """Check if key may be in dict objects.
        
        Args:
            dict_val: Abstract value representing the dict
            key_val: Abstract value representing the key
            state: Current AI state
            
        Returns:
            Tuple of (may_be_true, may_be_false) for "k in d"
        """
        from pythonstan.analysis.pointer.kcfa.object import AllocKind
        
        may_be_true = False
        may_be_false = False
        
        for obj in dict_val.addrs:
            kind = getattr(obj, "kind", None)
            is_dict = (
                kind == AllocKind.DICT or
                (hasattr(kind, "value") and kind.value == "dict")
            )
            
            if not is_dict:
                may_be_false = True
                continue
            
            _, slots = self._get_or_create_slots(obj, state)
            
            obj_may_be_true, obj_may_be_false = self.contains_item(slots, key_val, state)
            may_be_true = may_be_true or obj_may_be_true
            may_be_false = may_be_false or obj_may_be_false
        
        # If no dict objects, key is definitely not present
        if not dict_val.addrs:
            may_be_false = True
        
        return may_be_true, may_be_false


# =============================================================================
# Helpers for integration with existing code (FieldStore conversion)
# =============================================================================

def slotmap_from_fieldstore(fieldstore: "FieldStore") -> SlotMap:
    """Convert a FieldStore to SlotMap.
    
    Used for gradual migration and interop.
    """
    from pythonstan.analysis.pointer.kcfa.heap_model import FieldKind
    
    partitions: Dict[KeySig, Slot] = {}
    
    for field_key, val in fieldstore.precise_map.items():
        # Extract attr name from field
        if hasattr(field_key, 'kind') and hasattr(field_key, 'name'):
            if field_key.kind == FieldKind.ATTR and isinstance(field_key.name, str):
                sig = make_str_keysig(field_key.name)
                partitions[sig] = Slot(
                    val=val,
                    pres=Presence.MAYBE,  # FieldStore doesn't track presence, so MAYBE
                    key_rep=None,
                )
    
    return SlotMap(
        partitions=partitions,
        default=Slot(
            val=fieldstore.default_bucket,
            pres=Presence.MAYBE,
            key_rep=None,
        ),
        max_partitions=fieldstore.max_keys,
        track_key_rep=False,
    )


def slotmap_to_fieldstore(slots: SlotMap) -> "FieldStore":
    """Convert SlotMap to FieldStore.
    
    Used for backward compatibility with existing heap model.
    """
    from ..heap import FieldStore
    from pythonstan.analysis.pointer.kcfa.heap_model import attr
    
    precise_map: Dict["Field", "AbsVal"] = {}
    
    for sig, slot in slots.partitions.items():
        if slot.pres.may_be_present():  # Only include keys that may exist
            if keysig_kind(sig) == "str":
                field_key = attr(sig[1])
                precise_map[field_key] = slot.val
    
    return FieldStore(
        precise_map=precise_map,
        default_bucket=slots.default.val,
        max_keys=slots.max_partitions,
    )
