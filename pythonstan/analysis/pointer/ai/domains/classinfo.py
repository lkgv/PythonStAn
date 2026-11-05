"""ClassInfo: Enhanced class/type modeling for abstract interpretation.

This module extends TypeDomain with rich class information:
- ClassInfo: Detailed class representation with namespace, bases, metaclass
- ClassInfoCache: Service for managing class info during analysis
- MRO resolution with ClassInfo support

Key features:
- Class namespace: SlotMap representing class __dict__
- Bases: TupleDisj of base class tokens/addresses
- Metaclass: Address set for metaclass pointer(s)
- MRO shape: Cached linearized MRO for attribute lookup

Design notes:
- ClassInfo is created lazily when detailed class info is needed
- Integrates with existing TypeDomain (ClassRef tokens)
- Supports metaclass-aware attribute lookup (class as instance of metaclass)
- type(name, bases, ns) creates ClassInfo from tuple/dict shapes

Metaclass semantics:
- When looking up attributes on a class object C, we must consider:
  1. C.__dict__ (direct class attributes)
  2. MRO lookup through C's bases
  3. Metaclass attribute lookup (C is instance of metaclass M)
     - M.__dict__ lookup with descriptor semantics
     - MRO lookup through M's bases
- This enables proper modeling of classmethods, class properties, etc.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import (
    TYPE_CHECKING,
    Any,
    Dict,
    FrozenSet,
    List,
    Optional,
    Set,
    Tuple,
    Union,
)

if TYPE_CHECKING:
    from pythonstan.analysis.pointer.kcfa.object import AbstractObject
    from pythonstan.analysis.pointer.kcfa.context import AbstractContext, Scope
    from ..state import AIState
    from ..value_domain import AbsVal
    from ..api import PtaQuery, AnalysisBudget
    from .tuples import TupleDisj
    from .mapslots import SlotMap

__all__ = [
    "ClassInfo",
    "ClassInfoCache",
    "get_class_info",
    "create_dynamic_class_info",
]


# =============================================================================
# ClassInfo - Detailed class representation
# =============================================================================

@dataclass(frozen=True)
class ClassInfo:
    """Detailed information about a class/type object.
    
    Represents the abstract state of a class including:
    - namespace: The class __dict__ as SlotMap
    - bases: The __bases__ tuple as TupleDisj
    - metaclass: The __class__ (metaclass) as address set
    - mro_shape: Optional cached MRO information
    
    This enables:
    - Precise attribute lookup in class namespaces
    - MRO-aware inheritance resolution
    - Metaclass-aware class attribute lookup
    - Dynamic class creation via type(name, bases, ns)
    
    Attributes:
        class_obj: The AbstractObject for this class
        namespace: SlotMap for class __dict__
        bases: TupleDisj representing __bases__ (may be None if unknown)
        metaclass_addrs: Address set for metaclass (empty = unknown/type)
        mro: Cached MRO as list of class objects (may be None)
        is_dynamic: Whether this class was created dynamically
    """
    class_obj: "AbstractObject"
    namespace: Optional["SlotMap"] = None
    bases: Optional["TupleDisj"] = None
    metaclass_addrs: FrozenSet["AbstractObject"] = field(default_factory=frozenset)
    mro: Optional[Tuple["AbstractObject", ...]] = None
    is_dynamic: bool = False
    
    def has_namespace(self) -> bool:
        """Check if namespace info is available."""
        return self.namespace is not None
    
    def has_bases(self) -> bool:
        """Check if bases info is available."""
        return self.bases is not None
    
    def has_metaclass(self) -> bool:
        """Check if metaclass info is available."""
        return len(self.metaclass_addrs) > 0
    
    def has_mro(self) -> bool:
        """Check if MRO is cached."""
        return self.mro is not None
    
    def get_namespace_value(self, key: str) -> Optional["AbsVal"]:
        """Get attribute value from class namespace.
        
        Returns None if namespace unavailable or key not tracked.
        """
        if self.namespace is None:
            return None
        from .mapslots import Presence
        slot = self.namespace.get_str(key)
        if slot.pres.may_be_present():
            return slot.val
        return None
    
    def get_base_classes(self) -> List["AbstractObject"]:
        """Get list of base class objects from bases tuple.
        
        Returns empty list if bases unavailable or not exact.
        """
        if self.bases is None:
            return []
        
        result: List["AbstractObject"] = []
        for alt in self.bases.get_exact_alts():
            for elem in alt.elems:
                result.extend(elem.addrs)
        
        return result
    
    def with_namespace(self, ns: "SlotMap") -> "ClassInfo":
        """Return copy with updated namespace."""
        return ClassInfo(
            class_obj=self.class_obj,
            namespace=ns,
            bases=self.bases,
            metaclass_addrs=self.metaclass_addrs,
            mro=self.mro,
            is_dynamic=self.is_dynamic,
        )
    
    def with_bases(self, bases: "TupleDisj") -> "ClassInfo":
        """Return copy with updated bases."""
        return ClassInfo(
            class_obj=self.class_obj,
            namespace=self.namespace,
            bases=bases,
            metaclass_addrs=self.metaclass_addrs,
            mro=None,  # Invalidate MRO cache
            is_dynamic=self.is_dynamic,
        )
    
    def with_metaclass(self, meta_addrs: FrozenSet["AbstractObject"]) -> "ClassInfo":
        """Return copy with updated metaclass."""
        return ClassInfo(
            class_obj=self.class_obj,
            namespace=self.namespace,
            bases=self.bases,
            metaclass_addrs=meta_addrs,
            mro=self.mro,
            is_dynamic=self.is_dynamic,
        )
    
    def with_mro(self, mro: Tuple["AbstractObject", ...]) -> "ClassInfo":
        """Return copy with cached MRO."""
        return ClassInfo(
            class_obj=self.class_obj,
            namespace=self.namespace,
            bases=self.bases,
            metaclass_addrs=self.metaclass_addrs,
            mro=mro,
            is_dynamic=self.is_dynamic,
        )
    
    def join(self, other: "ClassInfo") -> "ClassInfo":
        """Join two ClassInfo (must be for same class_obj)."""
        assert self.class_obj == other.class_obj
        
        # Join namespaces
        new_ns = None
        if self.namespace is not None and other.namespace is not None:
            new_ns = self.namespace.join(other.namespace)
        elif self.namespace is not None:
            new_ns = self.namespace
        elif other.namespace is not None:
            new_ns = other.namespace
        
        # Join bases
        new_bases = None
        if self.bases is not None and other.bases is not None:
            new_bases = self.bases.join(other.bases)
        elif self.bases is not None:
            new_bases = self.bases
        elif other.bases is not None:
            new_bases = other.bases
        
        # Union metaclass addrs
        new_meta = self.metaclass_addrs | other.metaclass_addrs
        
        # MRO is invalidated on join (may differ across branches)
        
        return ClassInfo(
            class_obj=self.class_obj,
            namespace=new_ns,
            bases=new_bases,
            metaclass_addrs=new_meta,
            mro=None,
            is_dynamic=self.is_dynamic or other.is_dynamic,
        )
    
    def __str__(self) -> str:
        parts = [f"ClassInfo({self.class_obj})"]
        if self.namespace is not None:
            parts.append(f"ns={len(self.namespace.partitions)} keys")
        if self.bases is not None:
            parts.append(f"bases={self.bases.num_alts} alts")
        if self.metaclass_addrs:
            parts.append(f"meta={len(self.metaclass_addrs)} objs")
        return ", ".join(parts)


# =============================================================================
# ClassInfoCache - Service for managing class info
# =============================================================================

class ClassInfoCache:
    """Cache and factory for ClassInfo objects.
    
    Manages creation and caching of ClassInfo during analysis.
    Integrates with PTA for initial class info and AI state for refinements.
    
    Usage:
        cache = ClassInfoCache(pta_query, budget)
        info = cache.get_info(class_obj, state)
        info = cache.update_namespace(class_obj, key, value)
    """
    
    def __init__(
        self,
        pta_query: Optional["PtaQuery"],
        budget: Optional["AnalysisBudget"],
    ):
        """Initialize class info cache.
        
        Args:
            pta_query: PTA query interface for MRO and field lookups
            budget: Analysis budget for caps
        """
        self.pta_query = pta_query
        self.budget = budget
        
        # Cache: class_obj -> ClassInfo
        self._cache: Dict["AbstractObject", ClassInfo] = {}
        
        # Budget caps
        self.max_namespace_keys = getattr(budget, "max_class_namespace_keys", 30)
        self.max_mro_depth = getattr(budget, "max_mro_depth", 10)
    
    def get_info(
        self,
        class_obj: "AbstractObject",
        state: Optional["AIState"] = None,
    ) -> ClassInfo:
        """Get or create ClassInfo for a class object.
        
        First checks cache, then creates from PTA/state if needed.
        
        Args:
            class_obj: The class object to get info for
            state: Optional AI state for overlay reads
            
        Returns:
            ClassInfo for the class (may be minimal if no info available)
        """
        # Check cache
        if class_obj in self._cache:
            cached = self._cache[class_obj]
            # If we have state, we might need to update from overlay
            # For now, return cached (could add overlay integration later)
            return cached
        
        # Create new ClassInfo
        info = self._create_from_pta(class_obj, state)
        self._cache[class_obj] = info
        return info
    
    def _create_from_pta(
        self,
        class_obj: "AbstractObject",
        state: Optional["AIState"],
    ) -> ClassInfo:
        """Create ClassInfo from PTA information.
        
        Extracts namespace, bases, metaclass from PTA heap.
        """
        from .mapslots import SlotMap, Slot, Presence
        from .tuples import TupleDisj, TupleExact
        from ..value_domain import AbsVal
        
        namespace: Optional[SlotMap] = None
        bases: Optional[TupleDisj] = None
        metaclass_addrs: FrozenSet["AbstractObject"] = frozenset()
        mro: Optional[Tuple["AbstractObject", ...]] = None
        
        # Get MRO from PTA
        if self.pta_query is not None:
            try:
                mro_list = self.pta_query.get_class_mro(class_obj)
                if mro_list:
                    mro = tuple(mro_list[:self.max_mro_depth])
            except Exception:
                pass
        
        # Extract namespace from PTA/state
        if state is not None:
            ns = self._extract_namespace(class_obj, state)
            if ns is not None:
                namespace = ns
        
        # Try to extract bases
        if self.pta_query is not None:
            try:
                bases_tuple = self._extract_bases(class_obj, state)
                if bases_tuple is not None:
                    bases = bases_tuple
            except Exception:
                pass
        
        # Try to extract metaclass
        if self.pta_query is not None:
            try:
                meta = self._extract_metaclass(class_obj, state)
                if meta:
                    metaclass_addrs = meta
            except Exception:
                pass
        
        return ClassInfo(
            class_obj=class_obj,
            namespace=namespace,
            bases=bases,
            metaclass_addrs=metaclass_addrs,
            mro=mro,
            is_dynamic=False,
        )
    
    def _extract_namespace(
        self,
        class_obj: "AbstractObject",
        state: "AIState",
    ) -> Optional["SlotMap"]:
        """Extract class namespace from AI state/PTA."""
        from .mapslots import SlotMap, Slot, Presence
        from ..value_domain import AbsVal
        from pythonstan.analysis.pointer.kcfa.heap_model import attr, unknown
        
        default_val = AbsVal.bottom()
        
        # Read known attributes from overlay/PTA
        # For now, we just create an empty namespace; it will be populated
        # via store operations
        
        # Read unknown() for default bucket
        try:
            unknown_val, _ = state.read_field_value(
                class_obj, unknown(), None, None
            )
            if not unknown_val.is_bottom():
                default_val = unknown_val
        except Exception:
            pass
        
        # Create SlotMap for namespace (string keys, no key_rep tracking)
        return SlotMap(
            partitions={},
            default=Slot(val=default_val, pres=Presence.MAYBE, key_rep=None),
            max_partitions=self.max_namespace_keys,
            track_key_rep=False,
        )
    
    def _extract_bases(
        self,
        class_obj: "AbstractObject",
        state: Optional["AIState"],
    ) -> Optional["TupleDisj"]:
        """Extract bases tuple from PTA."""
        from .tuples import TupleDisj, TupleExact, materialize_tuple_disj
        from ..value_domain import AbsVal
        from pythonstan.analysis.pointer.kcfa.heap_model import attr
        
        if self.pta_query is None:
            return None
        
        # Try to read __bases__ attribute
        try:
            bases_field = attr("__bases__")
            bases_pts = self.pta_query.field_points_to(
                class_obj, bases_field, None, None
            )
            if bases_pts:
                bases_val = AbsVal.from_addrs(frozenset(bases_pts))
                if state is not None:
                    return materialize_tuple_disj(
                        bases_val, state, None, None,
                        max_len=8, max_alts=4
                    )
        except Exception:
            pass
        
        return None
    
    def _extract_metaclass(
        self,
        class_obj: "AbstractObject",
        state: Optional["AIState"],
    ) -> FrozenSet["AbstractObject"]:
        """Extract metaclass from PTA."""
        if self.pta_query is None:
            return frozenset()
        
        # Try to read __class__ attribute
        try:
            from pythonstan.analysis.pointer.kcfa.heap_model import attr
            class_field = attr("__class__")
            meta_pts = self.pta_query.field_points_to(
                class_obj, class_field, None, None
            )
            return frozenset(meta_pts)
        except Exception:
            return frozenset()
    
    def update_namespace(
        self,
        class_obj: "AbstractObject",
        key: str,
        value: "AbsVal",
        state: Optional["AIState"] = None,
    ) -> ClassInfo:
        """Update class namespace with a new attribute.
        
        Creates ClassInfo if not cached, then updates namespace.
        """
        from .mapslots import SlotMap, Presence
        
        info = self.get_info(class_obj, state)
        
        if info.namespace is None:
            ns = SlotMap.for_namespace(self.max_namespace_keys)
        else:
            ns = info.namespace
        
        new_ns = ns.weak_update_str(key, value, Presence.PRESENT)
        new_info = info.with_namespace(new_ns)
        
        self._cache[class_obj] = new_info
        return new_info
    
    def get_mro(
        self,
        class_obj: "AbstractObject",
        state: Optional["AIState"] = None,
    ) -> List["AbstractObject"]:
        """Get MRO for a class, using cache or computing from bases.
        
        Falls back to PTA MRO if ClassInfo doesn't have computed MRO.
        """
        info = self.get_info(class_obj, state)
        
        if info.mro is not None:
            return list(info.mro)
        
        # Fall back to PTA MRO
        if self.pta_query is not None:
            try:
                return self.pta_query.get_class_mro(class_obj)
            except Exception:
                pass
        
        return [class_obj]
    
    def invalidate(self, class_obj: "AbstractObject") -> None:
        """Invalidate cached info for a class."""
        self._cache.pop(class_obj, None)
    
    def clear(self) -> None:
        """Clear all cached info."""
        self._cache.clear()


# =============================================================================
# Helper functions
# =============================================================================

def get_class_info(
    class_obj: "AbstractObject",
    pta_query: Optional["PtaQuery"],
    state: Optional["AIState"],
    budget: Optional["AnalysisBudget"],
) -> ClassInfo:
    """Convenience function to get ClassInfo without explicit cache.
    
    Creates a temporary cache and retrieves info.
    For repeated use, prefer ClassInfoCache directly.
    """
    cache = ClassInfoCache(pta_query, budget)
    return cache.get_info(class_obj, state)


def create_dynamic_class_info(
    class_obj: "AbstractObject",
    name_val: "AbsVal",
    bases_val: "AbsVal",
    namespace_val: "AbsVal",
    state: "AIState",
) -> ClassInfo:
    """Create ClassInfo for dynamically created class (type(name, bases, ns)).
    
    This supports patterns like:
        MyClass = type('MyClass', (Base,), {'method': func})
    
    Args:
        class_obj: The AbstractObject for the new class
        name_val: Abstract value for class name (string)
        bases_val: Abstract value for bases tuple
        namespace_val: Abstract value for namespace dict
        state: Current AI state for materialization
        
    Returns:
        ClassInfo for the dynamic class
    """
    from .mapslots import SlotMap, Slot, Presence, keysig_kind
    from .tuples import TupleDisj, materialize_tuple_disj
    from ..value_domain import AbsVal
    
    # Materialize bases tuple
    bases: Optional[TupleDisj] = None
    try:
        bases = materialize_tuple_disj(
            bases_val, state, None, None,
            max_len=8, max_alts=4
        )
    except Exception:
        pass
    
    # Extract namespace from dict value
    namespace: Optional[SlotMap] = None
    try:
        # Create empty namespace - dict contents are complex to extract
        namespace = SlotMap.for_namespace(max_partitions=30)
        
        # If we can identify the dict object, try to get its slots
        for dict_obj in namespace_val.addrs:
            from ..heap import AIAddr
            ai_addr = AIAddr.from_kcfa_obj(dict_obj)
            ai_obj = state.get_obj(ai_addr)
            if ai_obj is not None:
                # Use unified slots from AbsObj
                for sig, slot in ai_obj.slots.partitions.items():
                    if keysig_kind(sig) == "str":  # String key
                        key = sig[1]
                        namespace = namespace.weak_update_str(
                            key, slot.val, 
                            Presence.PRESENT if slot.pres.is_definitely_present() else Presence.MAYBE
                        )
    except Exception:
        pass
    
    return ClassInfo(
        class_obj=class_obj,
        namespace=namespace,
        bases=bases,
        metaclass_addrs=frozenset(),  # Default metaclass is type
        mro=None,
        is_dynamic=True,
    )


def compute_mro_from_bases(
    class_obj: "AbstractObject",
    bases_info: List[ClassInfo],
    max_depth: int = 10,
) -> List["AbstractObject"]:
    """Compute C3 linearization from base class infos.
    
    This is a simplified MRO computation that works with ClassInfo.
    Falls back to simple concatenation if C3 fails.
    
    Args:
        class_obj: The class to compute MRO for
        bases_info: ClassInfo for each base class
        max_depth: Maximum MRO depth
        
    Returns:
        List of class objects in MRO order
    """
    # Simplified: just concatenate [class_obj] + base MROs
    # Full C3 implementation would be more complex
    
    mro: List["AbstractObject"] = [class_obj]
    seen: Set["AbstractObject"] = {class_obj}
    
    for base_info in bases_info:
        if base_info.mro is not None:
            for cls in base_info.mro:
                if cls not in seen and len(mro) < max_depth:
                    mro.append(cls)
                    seen.add(cls)
        elif base_info.class_obj not in seen:
            mro.append(base_info.class_obj)
            seen.add(base_info.class_obj)
    
    return mro
