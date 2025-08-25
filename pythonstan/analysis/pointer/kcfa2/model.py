"""Core data model for k-CFA pointer analysis.

This module defines the fundamental abstractions used in the k-CFA pointer analysis:
- Abstract locations and objects
- Field keys for attribute and container access
- Points-to sets and lattice structures
- Environment, store, and heap abstractions
"""

from dataclasses import dataclass
from typing import Optional, FrozenSet, Final, Dict, Any, TYPE_CHECKING
# from frozendict import frozendict  # Optional dependency

if TYPE_CHECKING:
    from .context import Context

__all__ = [
    "AbstractLocation", 
    "FieldKey", 
    "AbstractObject", 
    "PointsToSet",
    "Env",
    "Store", 
    "Heap"
]


@dataclass(frozen=True)
class AbstractLocation:
    """An abstract location in the pointer analysis.
    
    Abstract locations represent program variables and temporary locations
    in a specific calling context.
    
    Attributes:
        fn: Function name containing this location
        name: Variable or location name
        ctx: Calling context for this location
    """
    fn: str
    name: str
    ctx: "Context"
    
    def __str__(self) -> str:
        return f"{self.fn}.{self.name}@{self.ctx}"


@dataclass(frozen=True) 
class FieldKey:
    """A field key for object field access.
    
    Field keys abstract over different kinds of field access:
    - Named attributes (obj.attr)
    - Container elements (list[i], set elements) 
    - Dictionary values (dict[key])
    - Unknown/dynamic attributes
    
    Attributes:
        kind: Type of field access
        name: Field name for named attributes (None for element/value access)
    """
    kind: str  # Literal["attr", "elem", "value", "unknown"]
    name: Optional[str] = None
    
    def __post_init__(self) -> None:
        """Validate field key constraints."""
        if self.kind == "attr" and self.name is None:
            raise ValueError("Named attributes must have a name")
        if self.kind in ("elem", "value") and self.name is not None:
            raise ValueError(f"{self.kind} access should not have a name")
    
    def __str__(self) -> str:
        if self.kind == "attr":
            return f".{self.name}"
        elif self.kind == "elem":
            return ".elem"
        elif self.kind == "value":
            return ".value"
        else:
            return ".?"


@dataclass(frozen=True)
class AbstractObject:
    """An abstract object in the heap.
    
    Abstract objects represent allocations in specific contexts.
    For 2-object sensitivity, recv_ctx_fingerprint captures the
    allocation context of receiver objects up to depth obj_depth.
    
    Attributes:
        alloc_id: Unique identifier for the allocation site
        alloc_ctx: Context where this object was allocated
        recv_ctx_fingerprint: Receiver context fingerprint for 2-obj sensitivity
    """
    alloc_id: str
    alloc_ctx: "Context"
    recv_ctx_fingerprint: Optional[tuple] = None
    
    def __str__(self) -> str:
        recv_suffix = f"[{self.recv_ctx_fingerprint}]" if self.recv_ctx_fingerprint else ""
        return f"{self.alloc_id}@{self.alloc_ctx}{recv_suffix}"


class PointsToSet:
    """A points-to set containing abstract objects.
    
    This class wraps a frozen set of abstract objects and provides
    lattice operations for the pointer analysis.
    """
    
    def __init__(self, objects: FrozenSet[AbstractObject] = frozenset()):
        """Initialize points-to set.
        
        Args:
            objects: Set of abstract objects this location points to
        """
        self._objects: Final[FrozenSet[AbstractObject]] = objects
        
    @property 
    def objects(self) -> FrozenSet[AbstractObject]:
        """Get the underlying set of objects."""
        return self._objects
        
    def join(self, other: "PointsToSet") -> "PointsToSet":
        """Join with another points-to set (set union).
        
        Args:
            other: Another points-to set
            
        Returns:
            New points-to set containing union of both sets
        """
        return PointsToSet(self._objects | other._objects)
        
    def is_empty(self) -> bool:
        """Check if points-to set is empty."""
        return len(self._objects) == 0
        
    def is_top(self) -> bool:
        """Check if points-to set represents top (currently always False)."""
        # TODO: Implement top representation for widening
        return False
        
    def __len__(self) -> int:
        return len(self._objects)
        
    def __iter__(self):
        return iter(self._objects)
        
    def __contains__(self, obj: AbstractObject) -> bool:
        return obj in self._objects
        
    def __eq__(self, other: object) -> bool:
        if not isinstance(other, PointsToSet):
            return False
        return self._objects == other._objects
        
    def __hash__(self) -> int:
        return hash(self._objects)
        
    def __str__(self) -> str:
        if self.is_empty():
            return "{}"
        return "{" + ", ".join(str(obj) for obj in sorted(self._objects, key=str)) + "}"


class Env:
    """Environment mapping variables to points-to sets.
    
    The environment tracks what each program variable points to
    in the current analysis state.
    """
    
    def __init__(self, mapping: Optional[Dict[str, PointsToSet]] = None):
        """Initialize environment.
        
        Args:
            mapping: Variable name to points-to set mapping
        """
        self._mapping: Dict[str, PointsToSet] = mapping or {}
        
    def get(self, var: str) -> PointsToSet:
        """Get points-to set for a variable.
        
        Args:
            var: Variable name
            
        Returns:
            Points-to set for the variable (empty if not found)
        """
        return self._mapping.get(var, PointsToSet())
        
    def set(self, var: str, pts: PointsToSet) -> "Env":
        """Set points-to set for a variable.
        
        Args:
            var: Variable name
            pts: New points-to set
            
        Returns:
            New environment with updated binding
        """
        new_mapping = self._mapping.copy()
        new_mapping[var] = pts
        return Env(new_mapping)
        
    def join(self, other: "Env") -> "Env":
        """Join with another environment.
        
        Args:
            other: Another environment
            
        Returns:
            New environment with joined points-to sets
        """
        all_vars = set(self._mapping.keys()) | set(other._mapping.keys())
        new_mapping = {}
        
        for var in all_vars:
            self_pts = self.get(var)
            other_pts = other.get(var) 
            new_mapping[var] = self_pts.join(other_pts)
            
        return Env(new_mapping)


class Store:
    """Store mapping abstract locations to points-to sets.
    
    The store tracks heap locations and their contents.
    """
    
    def __init__(self, mapping: Optional[Dict[AbstractLocation, PointsToSet]] = None):
        """Initialize store.
        
        Args:
            mapping: Location to points-to set mapping
        """
        self._mapping: Dict[AbstractLocation, PointsToSet] = mapping or {}
        
    def get(self, loc: AbstractLocation) -> PointsToSet:
        """Get points-to set for a location.
        
        Args:
            loc: Abstract location
            
        Returns:
            Points-to set for the location (empty if not found)
        """
        return self._mapping.get(loc, PointsToSet())
        
    def set(self, loc: AbstractLocation, pts: PointsToSet) -> "Store":
        """Set points-to set for a location.
        
        Args:
            loc: Abstract location
            pts: New points-to set
            
        Returns:
            New store with updated binding
        """
        new_mapping = self._mapping.copy()
        new_mapping[loc] = pts
        return Store(new_mapping)
        
    def join(self, other: "Store") -> "Store":
        """Join with another store.
        
        Args:
            other: Another store
            
        Returns:
            New store with joined points-to sets
        """
        all_locs = set(self._mapping.keys()) | set(other._mapping.keys())
        new_mapping = {}
        
        for loc in all_locs:
            self_pts = self.get(loc)
            other_pts = other.get(loc)
            new_mapping[loc] = self_pts.join(other_pts)
            
        return Store(new_mapping)


class Heap:
    """Heap mapping object fields to points-to sets.
    
    The heap tracks object field contents using abstract objects
    and field keys.
    """
    
    def __init__(self, mapping: Optional[Dict[tuple, PointsToSet]] = None):
        """Initialize heap.
        
        Args:
            mapping: (object, field) to points-to set mapping
        """
        self._mapping: Dict[tuple, PointsToSet] = mapping or {}
        
    def get(self, obj: AbstractObject, field: FieldKey) -> PointsToSet:
        """Get points-to set for an object field.
        
        Args:
            obj: Abstract object
            field: Field key
            
        Returns:
            Points-to set for the field (empty if not found)
        """
        return self._mapping.get((obj, field), PointsToSet())
        
    def set(self, obj: AbstractObject, field: FieldKey, pts: PointsToSet) -> "Heap":
        """Set points-to set for an object field.
        
        Args:
            obj: Abstract object
            field: Field key  
            pts: New points-to set
            
        Returns:
            New heap with updated binding
        """
        new_mapping = self._mapping.copy()
        new_mapping[(obj, field)] = pts
        return Heap(new_mapping)
        
    def join(self, other: "Heap") -> "Heap":
        """Join with another heap.
        
        Args:
            other: Another heap
            
        Returns:
            New heap with joined points-to sets
        """
        all_keys = set(self._mapping.keys()) | set(other._mapping.keys())
        new_mapping = {}
        
        for key in all_keys:
            obj, field = key
            self_pts = self.get(obj, field)
            other_pts = other.get(obj, field)
            new_mapping[key] = self_pts.join(other_pts)
            
        return Heap(new_mapping)