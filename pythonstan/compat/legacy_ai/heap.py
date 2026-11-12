"""Heap model for abstract interpretation.

This module defines:
- AIAddr: Abstract addresses (allocation site + context)
- SlotMap: Unified namespace/dict abstraction
- AbsObj: Abstract heap objects with kind, class pointer, slots
- FieldStore: DEPRECATED - kept for backward compatibility, use SlotMap instead
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Literal, override

from pythonstan.analysis.domains.ints import IntDomain, IntLattice
from pythonstan.analysis.pointer.kcfa.context import FiniteContextKey
from pythonstan.compat.legacy_ai.value_domain import AbsVal, LegacyDomainContractError

if TYPE_CHECKING:
    from pythonstan.analysis.pointer.kcfa.context import NativeContext
    from pythonstan.analysis.pointer.kcfa.heap_model import Field
    from pythonstan.analysis.pointer.kcfa.object import AbstractObject, AllocSite

# Import unified SlotMap from domains
from pythonstan.compat.legacy_ai.domains.mapslots import (
    KeySig as KeySig,
)
from pythonstan.compat.legacy_ai.domains.mapslots import (
    Presence,
    Slot,
    SlotMap,
    slotmap_from_fieldstore,
    slotmap_to_fieldstore,
)
from pythonstan.compat.legacy_ai.domains.mapslots import (
    field_to_keysig as field_to_keysig,
)
from pythonstan.compat.legacy_ai.domains.mapslots import (
    keysig_to_field as keysig_to_field,
)
from pythonstan.compat.legacy_ai.domains.mapslots import (
    make_str_keysig as make_str_keysig,
)

__all__ = [
    "AIAddr",
    "SlotMap",
    "Slot",
    "Presence",
    "AbsObj",
    "ObjKind",
    "ContainerSlots",
    # Backward compatibility
    "FieldStore",
]

# Type for address sets used by summaries and low-level compatibility.
AddrSet = frozenset["AbstractObject"]
type TrackedKey = None | bool | int | float | str
type AIAddrProjection = (
    tuple[()]
    | tuple[Literal["method"], FiniteContextKey, FiniteContextKey | None]
    | tuple[Literal["builtin_method"], str, FiniteContextKey]
)


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

    alloc_site: "AllocSite"
    context: "NativeContext"
    # Bound and unbound Native method objects can have the same allocation and
    # context while denoting different receiver-bearing representations. The
    # optional flat token preserves them without embedding contextual objects.
    projection: AIAddrProjection = ()

    @override
    def __str__(self) -> str:
        return f"{self.alloc_site}@{self.context}"

    @classmethod
    def from_kcfa_obj(cls, obj: "AbstractObject") -> "AIAddr":
        """Create AIAddr from kcfa AbstractObject."""
        from pythonstan.analysis.pointer.kcfa.context import finite_context_atom, is_native_context
        from pythonstan.analysis.pointer.kcfa.object import BuiltinMethodObject, MethodObject

        projection: AIAddrProjection = ()
        if isinstance(obj, MethodObject):
            projection = (
                "method",
                finite_context_atom(obj.class_obj),
                None if obj.instance_obj is None else finite_context_atom(obj.instance_obj),
            )
        elif isinstance(obj, BuiltinMethodObject):
            projection = ("builtin_method", obj.method_name, finite_context_atom(obj.receiver))
        context = obj.context
        if not is_native_context(context):
            raise LegacyDomainContractError("Native heap addresses require a registered context")
        return cls(obj.alloc_site, context, projection)


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
    NAMESPACE = "namespace"
    BOUND_METHOD = "bound_method"
    BUILTIN_METHOD = "builtin_method"
    STATICMETHOD = "staticmethod"
    CLASSMETHOD = "classmethod"
    PROPERTY = "property"
    EXCEPTION = "exception"
    FOREIGN = "foreign"
    UNKNOWN = "unknown"


# =============================================================================
# FieldStore - DEPRECATED: Kept for backward compatibility
# =============================================================================


@dataclass
class FieldStore:
    """Field store abstraction with bounded precise map + default bucket.

    DEPRECATED: Use SlotMap instead. This class is kept for backward compatibility
    and will be removed in a future version. Use slotmap_from_fieldstore() and
    slotmap_to_fieldstore() for conversion.

    From ai_spec.md §3.4:
    - M: Field -> AbsVal (finite partial map for tracked keys)
    - def: AbsVal (default bucket for "all other keys")

    This is the key mechanism for handling dynamic attribute names
    without immediately collapsing to top.

    Attributes:
        precise_map: Tracked field -> value mapping
        default_bucket: Value for unknown/untracked keys
        max_keys: Maximum number of tracked keys before spilling
    """

    precise_map: dict["Field", AbsVal] = field(default_factory=dict)
    default_bucket: AbsVal = field(default_factory=AbsVal.bottom)
    max_keys: int = 20

    def copy(self) -> "FieldStore":
        """Create a shallow copy."""
        return FieldStore(
            precise_map=dict(self.precise_map),
            default_bucket=self.default_bucket,
            max_keys=self.max_keys,
        )

    # -------------------------------------------------------------------------
    # Read operations
    # -------------------------------------------------------------------------

    def get_const(self, key: "Field") -> AbsVal:
        """Get value for a constant field key (FSGet# for const string).

        Returns M[key] ⊔ def if key in M, else just def.
        """
        if key in self.precise_map:
            return self.precise_map[key].join(self.default_bucket)
        return self.default_bucket

    def get_keys(self, keys: frozenset["Field"]) -> AbsVal:
        """Get value for a set of constant field keys."""
        result = AbsVal.bottom()
        for k in keys:
            result = result.join(self.get_const(k))
        return result

    def get_unknown(self) -> AbsVal:
        """Get value for unknown key (reads def ⊔ all tracked values)."""
        result = self.default_bucket
        for v in self.precise_map.values():
            result = result.join(v)
        return result

    # -------------------------------------------------------------------------
    # Write operations
    # -------------------------------------------------------------------------

    def weak_set_const(self, key: "Field", value: AbsVal) -> "FieldStore":
        """Weak update for a constant key (FSWeakSet#).

        M[key] := M[key] ⊔ value
        """
        new_fs = self.copy()
        if key in new_fs.precise_map:
            new_fs.precise_map[key] = new_fs.precise_map[key].join(value)
        elif len(new_fs.precise_map) < new_fs.max_keys:
            new_fs.precise_map[key] = value
        else:
            # Spill to default bucket
            new_fs.default_bucket = new_fs.default_bucket.join(value)
        return new_fs

    def weak_set_keys(self, keys: frozenset["Field"], value: AbsVal) -> "FieldStore":
        """Weak update for a set of constant keys."""
        new_fs = self.copy()
        for k in keys:
            new_fs = new_fs.weak_set_const(k, value)
        return new_fs

    def weak_set_unknown(self, value: AbsVal) -> "FieldStore":
        """Weak update for unknown key (updates def only).

        def := def ⊔ value
        """
        new_fs = self.copy()
        new_fs.default_bucket = new_fs.default_bucket.join(value)
        return new_fs

    def strong_set_const(self, key: "Field", value: AbsVal) -> "FieldStore":
        """Strong update for a constant key (FSStrongSet#).

        M[key] := value (replaces, doesn't join)
        Only sound when we know this is the only possible write.
        """
        new_fs = self.copy()
        if len(new_fs.precise_map) < new_fs.max_keys or key in new_fs.precise_map:
            new_fs.precise_map[key] = value
        else:
            # Spill to default bucket
            new_fs.default_bucket = new_fs.default_bucket.join(value)
        return new_fs

    # -------------------------------------------------------------------------
    # Lattice operations
    # -------------------------------------------------------------------------

    def join(self, other: "FieldStore") -> "FieldStore":
        """Join two field stores (⊔)."""
        new_map: dict[Field, AbsVal] = {}
        all_keys = set(self.precise_map.keys()) | set(other.precise_map.keys())

        for k in all_keys:
            v1 = self.precise_map.get(k, AbsVal.bottom())
            v2 = other.precise_map.get(k, AbsVal.bottom())
            new_map[k] = v1.join(v2)

        return FieldStore(
            precise_map=new_map,
            default_bucket=self.default_bucket.join(other.default_bucket),
            max_keys=min(self.max_keys, other.max_keys),
        )

    def leq(self, other: "FieldStore") -> bool:
        """Check if self ⊑ other."""
        if not self.default_bucket.leq(other.default_bucket):
            return False
        for k, v in self.precise_map.items():
            other_v = other.precise_map.get(k, AbsVal.bottom())
            if not v.leq(other_v.join(other.default_bucket)):
                return False
        return True

    def widen(self, other: "FieldStore") -> "FieldStore":
        """Widen field store (∇).

        Key explosion → spill to default bucket.
        """
        joined = self.join(other)

        if len(joined.precise_map) > joined.max_keys:
            items = sorted(
                joined.precise_map.items(),
                key=lambda x: len(x[1].addrs),
            )
            to_spill = len(joined.precise_map) - joined.max_keys

            spilled_values = AbsVal.bottom()
            new_map: dict[Field, AbsVal] = {}

            for i, (k, v) in enumerate(items):
                if i < to_spill:
                    spilled_values = spilled_values.join(v)
                else:
                    new_map[k] = v

            return FieldStore(
                precise_map=new_map,
                default_bucket=joined.default_bucket.join(spilled_values),
                max_keys=joined.max_keys,
            )

        return joined

    def narrow(self, other: "FieldStore") -> "FieldStore":
        """Narrow field store (△).

        Meet the effective read values, including default-bucket coverage.

        A previously spilled value may be precise in ``other``. Intersecting
        only matching stored keys/defaults would drop that reachable value.
        """
        new_map: dict[Field, AbsVal] = {}
        for k in self.precise_map.keys() | other.precise_map.keys():
            new_map[k] = self.get_const(k).meet(other.get_const(k))

        new_default = self.default_bucket.meet(other.default_bucket)

        return FieldStore(
            precise_map=new_map,
            default_bucket=new_default,
            max_keys=self.max_keys,
        )

    def is_bottom(self) -> bool:
        """Check if this is bottom (empty)."""
        return len(self.precise_map) == 0 and self.default_bucket.is_bottom()

    @classmethod
    def bottom(cls, max_keys: int = 20) -> "FieldStore":
        """Create bottom field store."""
        return cls(default_bucket=AbsVal.bottom(), max_keys=max_keys)

    @classmethod
    def top(cls, max_keys: int = 20) -> "FieldStore":
        """Create top field store (unknown value for all keys)."""
        return cls(default_bucket=AbsVal.top(), max_keys=max_keys)

    @override
    def __str__(self) -> str:
        parts = []
        for k, v in sorted(self.precise_map.items(), key=lambda x: str(x[0])):
            parts.append(f"{k}: {v}")
        parts.append(f"*: {self.default_bucket}")
        return "{" + ", ".join(parts) + "}"

    def to_slotmap(self) -> SlotMap:
        """Convert to unified SlotMap."""
        return slotmap_from_fieldstore(self)

    @classmethod
    def from_slotmap(cls, slots: SlotMap) -> "FieldStore":
        """Create from unified SlotMap."""
        return slotmap_to_fieldstore(slots)


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
    elem_bucket: AbsVal = field(default_factory=AbsVal.bottom)

    # Tracked indices/keys (optional precision)
    tracked_indices: dict[TrackedKey, AbsVal] = field(default_factory=dict)

    # Length abstraction (simplified: just may-be-empty flag for now)
    may_be_empty: bool = True

    # Maximum tracked indices
    max_tracked: int = 10

    # Nonnegative size, independent of which indices happen to be tracked.
    length: IntLattice = field(default_factory=IntLattice.nonneg)

    @classmethod
    def empty_sequence(cls) -> "ContainerSlots":
        return cls(length=IntLattice.const(0))

    def append(self, value: AbsVal) -> "ContainerSlots":
        """Append one element to every represented concrete sequence."""
        result = self.set_elem(value)
        lo, hi = self.length.lo, self.length.hi
        result.length = IntLattice.from_interval(
            None if lo is None else lo + 1, None if hi is None else hi + 1
        )
        return result

    def copy(self) -> "ContainerSlots":
        """Create a shallow copy."""
        return ContainerSlots(
            elem_bucket=self.elem_bucket,
            tracked_indices=dict(self.tracked_indices),
            may_be_empty=self.may_be_empty,
            max_tracked=self.max_tracked,
            length=self.length,
        )

    def get_elem(self) -> AbsVal:
        """Get all elements (elem bucket ⊔ all tracked)."""
        result = self.elem_bucket
        for v in self.tracked_indices.values():
            result = result.join(v)
        return result

    def get_index(self, idx: TrackedKey) -> AbsVal:
        """Get element at specific index."""
        if idx in self.tracked_indices:
            return self.tracked_indices[idx].join(self.elem_bucket)
        return self.elem_bucket

    def set_elem(self, value: AbsVal) -> "ContainerSlots":
        """Add to elem bucket."""
        new_slots = self.copy()
        new_slots.elem_bucket = new_slots.elem_bucket.join(value)
        new_slots.may_be_empty = False
        return new_slots

    def set_index(self, idx: TrackedKey, value: AbsVal) -> "ContainerSlots":
        """Set element at specific index."""
        new_slots = self.copy()
        if idx in new_slots.tracked_indices:
            new_slots.tracked_indices[idx] = new_slots.tracked_indices[idx].join(value)
        elif len(new_slots.tracked_indices) < new_slots.max_tracked:
            new_slots.tracked_indices[idx] = value
        else:
            new_slots.elem_bucket = new_slots.elem_bucket.join(value)
        new_slots.may_be_empty = False
        return new_slots

    def join(self, other: "ContainerSlots") -> "ContainerSlots":
        """Join two container slots."""
        new_tracked: dict[TrackedKey, AbsVal] = {}
        all_keys = set(self.tracked_indices.keys()) | set(other.tracked_indices.keys())

        for k in all_keys:
            v1 = self.tracked_indices.get(k, AbsVal.bottom())
            v2 = other.tracked_indices.get(k, AbsVal.bottom())
            new_tracked[k] = v1.join(v2)

        return ContainerSlots(
            elem_bucket=self.elem_bucket.join(other.elem_bucket),
            tracked_indices=new_tracked,
            may_be_empty=self.may_be_empty or other.may_be_empty,
            max_tracked=min(self.max_tracked, other.max_tracked),
            length=IntDomain().join(self.length, other.length),
        )

    def leq(self, other: "ContainerSlots") -> bool:
        """Compare every semantic component, including wildcard elements.

        The element bucket contributes to every index. A tracked value may
        therefore be covered by either the matching index or that bucket.
        ``may_be_empty`` is a may fact and is ordered by implication.
        """
        if self.may_be_empty and not other.may_be_empty:
            return False
        if not IntDomain().leq(self.length, other.length):
            return False
        if not self.elem_bucket.leq(other.elem_bucket):
            return False
        return all(
            value.leq(other.get_index(index)) for index, value in self.tracked_indices.items()
        )

    def widen(self, other: "ContainerSlots") -> "ContainerSlots":
        """Widen container slots (spill tracked to elem bucket)."""
        joined = self.join(other)
        integer = self.elem_bucket.registry.get("int") or IntDomain()
        length = integer.widen(self.length, other.length)
        if not isinstance(length, IntLattice):
            raise LegacyDomainContractError("Native container length requires IntLattice")
        joined.length = length
        joined.elem_bucket = self.elem_bucket.widen(other.elem_bucket)
        joined.tracked_indices = {
            key: self.get_index(key).widen(other.get_index(key)) for key in joined.tracked_indices
        }

        if len(joined.tracked_indices) > joined.max_tracked:
            spilled = AbsVal.bottom()
            for v in joined.tracked_indices.values():
                spilled = spilled.join(v)
            return ContainerSlots(
                elem_bucket=joined.elem_bucket.join(spilled),
                tracked_indices={},
                may_be_empty=joined.may_be_empty,
                max_tracked=joined.max_tracked,
                length=joined.length,
            )

        return joined

    def narrow(self, other: "ContainerSlots") -> "ContainerSlots":
        """Narrow container slots (△).

        Meet effective index values, recovering any prior spill when needed.
        """
        new_tracked: dict[TrackedKey, AbsVal] = {}
        for k in self.tracked_indices.keys() | other.tracked_indices.keys():
            new_tracked[k] = self.get_index(k).meet(other.get_index(k))

        new_elem = self.elem_bucket.meet(other.elem_bucket)

        return ContainerSlots(
            elem_bucket=new_elem,
            tracked_indices=new_tracked,
            may_be_empty=self.may_be_empty and other.may_be_empty,
            max_tracked=self.max_tracked,
            length=IntDomain().meet(self.length, other.length),
        )


# =============================================================================
# AbsObj - Abstract heap object
# =============================================================================


def _default_slotmap() -> SlotMap:
    """Create default SlotMap for AbsObj."""
    return SlotMap.for_namespace()


@dataclass
class AbsObj:
    """Abstract heap object.

    From ai_spec.md §3.5:
    - kind: Instance/Class/Function/Module/List/Dict/Set/Tuple/...
    - cls: class pointer(s)
    - slots: unified namespace/dict slots (SlotMap)
    - container_slots: kind-specific payload for containers
    - proto: protocol hooks summaries

    Attributes:
        addr: Abstract address identifying this object
        kind: Type of object
        cls_addrs: Class pointer(s) for instances
        slots: Unified namespace/dict SlotMap (replaces dict_store + dict_slots)
        container_slots: Container-specific slots (for List/Set/Tuple)
    """

    addr: AIAddr
    kind: ObjKind
    cls_addrs: AddrSet = field(default_factory=frozenset)
    slots: SlotMap = field(default_factory=_default_slotmap)
    container_slots: ContainerSlots | None = None

    def copy(self) -> "AbsObj":
        """Create a shallow copy."""
        return AbsObj(
            addr=self.addr,
            kind=self.kind,
            cls_addrs=self.cls_addrs,
            slots=self.slots.copy(),
            container_slots=self.container_slots.copy() if self.container_slots else None,
        )

    def with_slots(self, new_slots: SlotMap) -> "AbsObj":
        """Return copy with updated slots."""
        obj = self.copy()
        obj.slots = new_slots
        return obj

    # Backward compatibility aliases
    @property
    def dict_store(self) -> FieldStore:
        """DEPRECATED: Access slots as FieldStore for backward compatibility."""
        return slotmap_to_fieldstore(self.slots)

    @property
    def dict_slots(self) -> SlotMap:
        """DEPRECATED: dict_slots is now unified into slots."""
        return self.slots

    def with_dict_store(self, new_store: FieldStore) -> "AbsObj":
        """DEPRECATED: Use with_slots() instead. Updates slots from FieldStore."""
        return self.with_slots(slotmap_from_fieldstore(new_store))

    def with_dict_slots(self, new_slots: SlotMap) -> "AbsObj":
        """DEPRECATED: Use with_slots() instead."""
        return self.with_slots(new_slots)

    def with_container_slots(self, new_slots: ContainerSlots) -> "AbsObj":
        """Return copy with updated container slots."""
        obj = self.copy()
        obj.container_slots = new_slots
        return obj

    def join(self, other: "AbsObj") -> "AbsObj":
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
            slots=self.slots.join(other.slots),
            container_slots=container,
        )

    def widen(self, other: "AbsObj") -> "AbsObj":
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
            slots=self.slots.widen(other.slots),
            container_slots=container,
        )

    def narrow(self, other: "AbsObj") -> "AbsObj":
        """Narrow two abstract objects (△).

        Refines by meeting (intersecting) components.
        """
        assert self.addr == other.addr

        container = None
        if self.container_slots and other.container_slots:
            container = self.container_slots.narrow(other.container_slots)
        elif self.container_slots:
            container = self.container_slots
        # Note: if only other has container_slots, we keep None (self didn't have it)

        return AbsObj(
            addr=self.addr,
            kind=self.kind,
            cls_addrs=self.cls_addrs & other.cls_addrs,  # Meet = intersection
            slots=self.slots.narrow(other.slots),
            container_slots=container,
        )

    def leq(self, other: "AbsObj") -> bool:
        """Check if self ⊑ other.

        Component-wise partial order check.
        """
        # Check cls_addrs
        if not self.cls_addrs <= other.cls_addrs:
            return False

        # Check slots
        if not self.slots.leq(other.slots):
            return False

        # Check container_slots (if present in self, must be ⊑ other's)
        if self.container_slots is not None:
            if other.container_slots is None:
                return False
            if not self.container_slots.leq(other.container_slots):
                return False

        return True

    def is_container(self) -> bool:
        """Check if this is a container object."""
        return self.kind in (ObjKind.LIST, ObjKind.DICT, ObjKind.SET, ObjKind.TUPLE)

    @override
    def __str__(self) -> str:
        return f"AbsObj({self.addr}, {self.kind.value})"
