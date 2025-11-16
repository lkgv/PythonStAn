from dataclasses import dataclass
from typing import FrozenSet, Iterable, TYPE_CHECKING

from .object import AbstractObject, ClassObject, MethodObject, InstanceObject

__all__ = ["PointsToSet"]


@dataclass(frozen=True)
class PointsToSet:
    """Immutable set of abstract objects.
    
    Represents the set of objects that a variable or field may point to.
    Immutability ensures points-to sets can be used as dictionary keys.
    
    Attributes:
        objects: Frozen set of abstract objects
    """
    
    objects: FrozenSet['AbstractObject']
    classmethods: FrozenSet['MethodObject']  # for the convenience of processing inheritance of class methods
    instancemethods: FrozenSet['MethodObject']  # # for the convenience of processing propagation of instance methods
    
    @staticmethod
    def empty() -> 'PointsToSet':
        """Create empty points-to set.
        
        Returns:
            Empty points-to set
        """
        return PointsToSet(frozenset(), frozenset(), frozenset())
    
    @staticmethod
    def singleton(obj: 'AbstractObject') -> 'PointsToSet':
        """Create singleton points-to set.
        
        Args:
            obj: Single object
        
        Returns:
            Points-to set containing only obj
        """
        if isinstance(obj, MethodObject):
            if obj.alloc_site.stmt.is_class_method:
                return PointsToSet(frozenset(), frozenset([obj]), frozenset())
            else:
                return PointsToSet(frozenset(), frozenset(), frozenset([obj]))
        return PointsToSet(frozenset([obj]), frozenset(), frozenset())
    
    @staticmethod
    def from_objects(objs: Iterable['AbstractObject']) -> 'PointsToSet':
        """Create points-to set from a set of objects."""
        # return PointsToSet(frozenset(objs))
        os, cms, ims = [], [], []
        for obj in objs:
            if isinstance(obj, MethodObject):
                if obj.alloc_site.stmt.is_class_method:
                    cms.append(obj)
                else:
                    ims.append(obj)
            else:
                os.append(obj)
        return PointsToSet(frozenset(os), frozenset(cms), frozenset(ims))

    def inherit_to(self, new_cls: 'ClassObject') -> 'PointsToSet':
        cms = [cm.inherit_into(new_cls) for cm in self.classmethods]
        return PointsToSet(self.objects, frozenset(cms), self.instancemethods)
    
    def deliver_into(self, new_inst: 'InstanceObject') -> 'PointsToSet':
        ims = [im.deliver_into(new_inst) for im in self.instancemethods]
        return PointsToSet(self.objects, self.classmethods, frozenset(ims))
    
    def union(self, other: 'PointsToSet') -> 'PointsToSet':
        """Union with another points-to set.
        
        Args:
            other: Another points-to set
        
        Returns:
            New points-to set with objects from both sets
        """
        return PointsToSet(self.objects | other.objects, 
                           self.classmethods | other.classmethods,
                           self.instancemethods | other.instancemethods)
    
    def intersection(self, other: 'PointsToSet') -> 'PointsToSet':
        """Intersection with another points-to set.
        
        Args:
            other: Another points-to set
        
        Returns:
            New points-to set with objects from both sets
        """
        return PointsToSet(self.objects & other.objects, 
                           self.classmethods & other.classmethods,
                           self.instancemethods & other.instancemethods)
    
    def is_empty(self) -> bool:
        """Check if set is empty.
        
        Returns:
            True if set contains no objects
        """
        return len(self.objects) == 0 and len(self.classmethods) == 0 and len(self.instancemethods) == 0
    
    def __len__(self) -> int:
        """Get number of objects in set."""
        return len(self.objects) + len(self.classmethods) + len(self.instancemethods)
    
    def __iter__(self):
        """Iterate over objects in set."""
        return iter(self.classmethods | self.instancemethods | self.objects)
    
    def __contains__(self, obj: 'AbstractObject') -> bool:
        """Check if object is in set."""
        return obj in self.objects or obj in self.classmethods or obj in self.instancemethods
    
    def __sub__(self, other: 'PointsToSet') -> 'PointsToSet':
        """Subtract another points-to set."""
        return PointsToSet(self.objects - other.objects, self.classmethods - other.classmethods, self.instancemethods - other.instancemethods)
    
    def __str__(self) -> str:
        """String representation for debugging."""
        if self.is_empty():
            return "{}"
        objs = ", ".join(str(o) for o in sorted(self.objects | self.instancemethods | self.classmethods, key=str))
        return f"{{{objs}}}"
