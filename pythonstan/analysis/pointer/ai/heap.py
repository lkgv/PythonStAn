"""Heap model for abstract interpretation.

This module defines:
- AIAddr: Abstract addresses (allocation site + context)
- FieldStore: Bounded precise map + default bucket for field abstraction
- AbsObj: Abstract heap objects with kind, class pointer, dict, slots
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import (
    TYPE_CHECKING,
    Any,
    Dict,
    FrozenSet,
    Generic,
    Iterator,
    List,
    Mapping,
    Optional,
    Set,
    Tuple,
    TypeVar,
    Union,
)

if TYPE_CHECKING:
    from pythonstan.analysis.pointer.kcfa.object import AbstractObject, AllocSite
    from pythonstan.analysis.pointer.kcfa.context import AbstractContext
    from pythonstan.analysis.pointer.kcfa.heap_model import Field

__all__ = [
    "AIAddr",
    "FieldStore",
    "AbsObj",
    "ObjKind",
    "ContainerSlots",
]


# =============================================================================
# AIAddr - Abstract addresses
# =============================================================================

@dataclass(frozen=True)
class AIAddr:
    """Abstract address = (AllocSite, Context).
    
    This is compatible with kcfa's AbstractObject but used internally
    by AI for its heap abstraction. We can convert between them.
    
    Attributes:
        alloc_site: Allocation site (static identity)
        context: Analysis context (dynamic identity)
    """
    alloc_site: 'AllocSite'
    context: 'AbstractContext'

    def __str__(self) -> str:
        return f"{self.alloc_site}@{self.context}"

    @classmethod
    def from_kcfa_obj(cls, obj: 'AbstractObject') -> 'AIAddr':
        """Create AIAddr from kcfa AbstractObject."""
        return cls(obj.alloc_site, obj.context)


# =============================================================================
# ObjKind - Types of heap objects
# =============================================================================

class ObjKind(Enum):
    """Kinds of abstract heap objects."""
    INSTANCE = "instance"
    CLASS = "class"
    FUNCTION = "function"
    METHOD = "method"
    MODULE = "module"
    LIST = "list"
    DICT = "dict"
    SET = "set"
    TUPLE = "tuple"
    ITERATOR = "iterator"
    FOREIGN = "foreign"
    UNKNOWN = "unknown"


# =============================================================================
# FieldStore - Bounded precise map + default bucket
# =============================================================================

# Type for abstract values in field store (just address sets for now)
AddrSet = FrozenSet['AbstractObject']


@dataclass
class FieldStore:
    """Field store abstraction with bounded precise map + default bucket.
    
    From ai_spec.md §3.4:
    - M: ConstKey -> AbsVal (finite partial map for tracked keys)
    - def: AbsVal (default bucket for "all other keys")
    
    This is the key mechanism for handling dynamic attribute names
    without immediately collapsing to top.
    
    Attributes:
        precise_map: Tracked key -> value mapping
        default_bucket: Value for unknown/untracked keys
        max_keys: Maximum number of tracked keys before spilling
    """
    precise_map: Dict[str, AddrSet] = field(default_factory=dict)
    default_bucket: AddrSet = field(default_factory=frozenset)
    max_keys: int = 20

    def copy(self) -> 'FieldStore':
        """Create a shallow copy."""
        return FieldStore(
            precise_map=dict(self.precise_map),
            default_bucket=self.default_bucket,
            max_keys=self.max_keys,
        )

    # -------------------------------------------------------------------------
    # Read operations
    # -------------------------------------------------------------------------

    def get_const(self, key: str) -> AddrSet:
        """Get value for a constant key (FSGet# for const string).
        
        Returns M[key] ⊔ def if key in M, else just def.
        """
        if key in self.precise_map:
            return self.precise_map[key] | self.default_bucket
        return self.default_bucket

    def get_keys(self, keys: FrozenSet[str]) -> AddrSet:
        """Get value for a set of constant keys.
        
        Returns ⊔_{k ∈ keys} (M[k] ⊔ def).
        """
        result: AddrSet = frozenset()
        for k in keys:
            result = result | self.get_const(k)
        return result

    def get_unknown(self) -> AddrSet:
        """Get value for unknown key (reads def ⊔ all tracked values).
        
        When key is not enumerable, we must include all possibilities.
        """
        result = self.default_bucket
        for v in self.precise_map.values():
            result = result | v
        return result

    # -------------------------------------------------------------------------
    # Write operations
    # -------------------------------------------------------------------------

    def weak_set_const(self, key: str, value: AddrSet) -> 'FieldStore':
        """Weak update for a constant key (FSWeakSet#).
        
        M[key] := M[key] ⊔ value
        """
        new_fs = self.copy()
        if key in new_fs.precise_map:
            new_fs.precise_map[key] = new_fs.precise_map[key] | value
        elif len(new_fs.precise_map) < new_fs.max_keys:
            new_fs.precise_map[key] = value
        else:
            # Spill to default bucket
            new_fs.default_bucket = new_fs.default_bucket | value
        return new_fs

    def weak_set_keys(self, keys: FrozenSet[str], value: AddrSet) -> 'FieldStore':
        """Weak update for a set of constant keys."""
        new_fs = self.copy()
        for k in keys:
            new_fs = new_fs.weak_set_const(k, value)
        return new_fs

    def weak_set_unknown(self, value: AddrSet) -> 'FieldStore':
        """Weak update for unknown key (updates def only).
        
        def := def ⊔ value
        """
        new_fs = self.copy()
        new_fs.default_bucket = new_fs.default_bucket | value
        return new_fs

    def strong_set_const(self, key: str, value: AddrSet) -> 'FieldStore':
        """Strong update for a constant key (FSStrongSet#).
        
        M[key] := value (replaces, doesn't join)
        Only sound when we know this is the only possible write.
        """
        new_fs = self.copy()
        if len(new_fs.precise_map) < new_fs.max_keys or key in new_fs.precise_map:
            new_fs.precise_map[key] = value
        else:
            # Spill to default bucket
            new_fs.default_bucket = new_fs.default_bucket | value
        return new_fs

    # -------------------------------------------------------------------------
    # Lattice operations
    # -------------------------------------------------------------------------

    def join(self, other: 'FieldStore') -> 'FieldStore':
        """Join two field stores (⊔)."""
        new_map: Dict[str, AddrSet] = {}
        all_keys = set(self.precise_map.keys()) | set(other.precise_map.keys())
        
        for k in all_keys:
            v1 = self.precise_map.get(k, frozenset())
            v2 = other.precise_map.get(k, frozenset())
            new_map[k] = v1 | v2

        return FieldStore(
            precise_map=new_map,
            default_bucket=self.default_bucket | other.default_bucket,
            max_keys=min(self.max_keys, other.max_keys),
        )

    def leq(self, other: 'FieldStore') -> bool:
        """Check if self ⊑ other."""
        # Check default bucket
        if not self.default_bucket <= other.default_bucket:
            return False
        # Check all tracked keys
        for k, v in self.precise_map.items():
            other_v = other.precise_map.get(k, frozenset())
            if not v <= (other_v | other.default_bucket):
                return False
        return True

    def widen(self, other: 'FieldStore') -> 'FieldStore':
        """Widen field store (∇).
        
        Key explosion → spill to default bucket.
        """
        joined = self.join(other)
        
        # If too many keys, spill some to default
        if len(joined.precise_map) > joined.max_keys:
            # Sort by size (smallest first) and spill the smallest
            items = sorted(joined.precise_map.items(), key=lambda x: len(x[1]))
            to_spill = len(joined.precise_map) - joined.max_keys
            
            spilled_values: AddrSet = frozenset()
            new_map: Dict[str, AddrSet] = {}
            
            for i, (k, v) in enumerate(items):
                if i < to_spill:
                    spilled_values = spilled_values | v
                else:
                    new_map[k] = v
            
            return FieldStore(
                precise_map=new_map,
                default_bucket=joined.default_bucket | spilled_values,
                max_keys=joined.max_keys,
            )
        
        return joined

    def is_bottom(self) -> bool:
        """Check if this is bottom (empty)."""
        return len(self.precise_map) == 0 and len(self.default_bucket) == 0

    @classmethod
    def bottom(cls, max_keys: int = 20) -> 'FieldStore':
        """Create bottom field store."""
        return cls(max_keys=max_keys)

    @classmethod
    def top(cls, max_keys: int = 20) -> 'FieldStore':
        """Create top field store (not really possible without top AddrSet)."""
        # In practice we don't have a concrete "top" for address sets
        # This is a placeholder
        return cls(max_keys=max_keys)

    def __str__(self) -> str:
        parts = []
        for k, v in sorted(self.precise_map.items()):
            v_str = ", ".join(str(obj) for obj in v) if v else "∅"
            parts.append(f"{k}: {{{v_str}}}")
        def_str = ", ".join(str(obj) for obj in self.default_bucket) if self.default_bucket else "∅"
        parts.append(f"*: {{{def_str}}}")
        return "{" + ", ".join(parts) + "}"


# =============================================================================
# ContainerSlots - Container-specific abstract storage
# =============================================================================

@dataclass
class ContainerSlots:
    """Container-specific slot abstraction.
    
    From ai_spec.md §3.6:
    - List: ⟨len, idxStore, elems⟩
    - Dict: ⟨len, kvStore, keys, vals⟩
    - Set: ⟨len, elems⟩
    - Tuple: fixed-arity vector or summary
    
    For v1 we use a simplified model with elem bucket + optional tracked indices.
    """
    # Element summary (all elements)
    elem_bucket: AddrSet = field(default_factory=frozenset)
    
    # Tracked indices/keys (optional precision)
    tracked_indices: Dict[Union[int, str], AddrSet] = field(default_factory=dict)
    
    # Length abstraction (simplified: just may-be-empty flag for now)
    may_be_empty: bool = True
    
    # Maximum tracked indices
    max_tracked: int = 10

    def copy(self) -> 'ContainerSlots':
        """Create a shallow copy."""
        return ContainerSlots(
            elem_bucket=self.elem_bucket,
            tracked_indices=dict(self.tracked_indices),
            may_be_empty=self.may_be_empty,
            max_tracked=self.max_tracked,
        )

    def get_elem(self) -> AddrSet:
        """Get all elements (elem bucket ⊔ all tracked)."""
        result = self.elem_bucket
        for v in self.tracked_indices.values():
            result = result | v
        return result

    def get_index(self, idx: Union[int, str]) -> AddrSet:
        """Get element at specific index."""
        if idx in self.tracked_indices:
            return self.tracked_indices[idx] | self.elem_bucket
        return self.elem_bucket

    def set_elem(self, value: AddrSet) -> 'ContainerSlots':
        """Add to elem bucket."""
        new_slots = self.copy()
        new_slots.elem_bucket = new_slots.elem_bucket | value
        new_slots.may_be_empty = False
        return new_slots

    def set_index(self, idx: Union[int, str], value: AddrSet) -> 'ContainerSlots':
        """Set element at specific index."""
        new_slots = self.copy()
        if idx in new_slots.tracked_indices:
            new_slots.tracked_indices[idx] = new_slots.tracked_indices[idx] | value
        elif len(new_slots.tracked_indices) < new_slots.max_tracked:
            new_slots.tracked_indices[idx] = value
        else:
            new_slots.elem_bucket = new_slots.elem_bucket | value
        new_slots.may_be_empty = False
        return new_slots

    def join(self, other: 'ContainerSlots') -> 'ContainerSlots':
        """Join two container slots."""
        new_tracked: Dict[Union[int, str], AddrSet] = {}
        all_keys = set(self.tracked_indices.keys()) | set(other.tracked_indices.keys())
        
        for k in all_keys:
            v1 = self.tracked_indices.get(k, frozenset())
            v2 = other.tracked_indices.get(k, frozenset())
            new_tracked[k] = v1 | v2

        return ContainerSlots(
            elem_bucket=self.elem_bucket | other.elem_bucket,
            tracked_indices=new_tracked,
            may_be_empty=self.may_be_empty and other.may_be_empty,
            max_tracked=min(self.max_tracked, other.max_tracked),
        )

    def widen(self, other: 'ContainerSlots') -> 'ContainerSlots':
        """Widen container slots (spill tracked to elem bucket)."""
        joined = self.join(other)
        
        if len(joined.tracked_indices) > joined.max_tracked:
            # Spill all tracked to elem bucket
            spilled = frozenset().union(*joined.tracked_indices.values())
            return ContainerSlots(
                elem_bucket=joined.elem_bucket | spilled,
                tracked_indices={},
                may_be_empty=joined.may_be_empty,
                max_tracked=joined.max_tracked,
            )
        
        return joined


# =============================================================================
# AbsObj - Abstract heap object
# =============================================================================

@dataclass
class AbsObj:
    """Abstract heap object.
    
    From ai_spec.md §3.5:
    - kind: Instance/Class/Function/Module/List/Dict/Set/Tuple/...
    - cls: class pointer(s)
    - dict: instance/class/module namespace (FieldStore)
    - slots: kind-specific payload
    - proto: protocol hooks summaries
    
    Attributes:
        addr: Abstract address identifying this object
        kind: Type of object
        cls_addrs: Class pointer(s) for instances
        dict_store: Namespace/attribute field store
        container_slots: Container-specific slots (for List/Dict/Set/Tuple)
    """
    addr: AIAddr
    kind: ObjKind
    cls_addrs: AddrSet = field(default_factory=frozenset)
    dict_store: FieldStore = field(default_factory=FieldStore)
    container_slots: Optional[ContainerSlots] = None

    def copy(self) -> 'AbsObj':
        """Create a shallow copy."""
        return AbsObj(
            addr=self.addr,
            kind=self.kind,
            cls_addrs=self.cls_addrs,
            dict_store=self.dict_store.copy(),
            container_slots=self.container_slots.copy() if self.container_slots else None,
        )

    def with_dict_store(self, new_store: FieldStore) -> 'AbsObj':
        """Return copy with updated dict store."""
        obj = self.copy()
        obj.dict_store = new_store
        return obj

    def with_container_slots(self, new_slots: ContainerSlots) -> 'AbsObj':
        """Return copy with updated container slots."""
        obj = self.copy()
        obj.container_slots = new_slots
        return obj

    def join(self, other: 'AbsObj') -> 'AbsObj':
        """Join two abstract objects (must have same addr)."""
        assert self.addr == other.addr
        
        container = None
        if self.container_slots and other.container_slots:
            container = self.container_slots.join(other.container_slots)
        elif self.container_slots:
            container = self.container_slots
        elif other.container_slots:
            container = other.container_slots

        return AbsObj(
            addr=self.addr,
            kind=self.kind,  # Assume same kind
            cls_addrs=self.cls_addrs | other.cls_addrs,
            dict_store=self.dict_store.join(other.dict_store),
            container_slots=container,
        )

    def widen(self, other: 'AbsObj') -> 'AbsObj':
        """Widen two abstract objects."""
        assert self.addr == other.addr
        
        container = None
        if self.container_slots and other.container_slots:
            container = self.container_slots.widen(other.container_slots)
        elif self.container_slots:
            container = self.container_slots
        elif other.container_slots:
            container = other.container_slots

        return AbsObj(
            addr=self.addr,
            kind=self.kind,
            cls_addrs=self.cls_addrs | other.cls_addrs,
            dict_store=self.dict_store.widen(other.dict_store),
            container_slots=container,
        )

    def is_container(self) -> bool:
        """Check if this is a container object."""
        return self.kind in (ObjKind.LIST, ObjKind.DICT, ObjKind.SET, ObjKind.TUPLE)

    def __str__(self) -> str:
        return f"AbsObj({self.addr}, {self.kind.value})"

