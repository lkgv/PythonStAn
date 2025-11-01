"""Analysis state for pointer analysis.

This module defines the state maintained during pointer analysis including
the environment (variable points-to sets) and heap (object field points-to sets).
"""

from dataclasses import dataclass
from typing import Dict, FrozenSet, Tuple, Set, Optional, TYPE_CHECKING
from collections import defaultdict

# if TYPE_CHECKING:
from .object import AbstractObject
from .variable import Variable, FieldAccess, VariableFactory
from .heap_model import Field
from .constraints import ConstraintManager
from pythonstan.graph.call_graph import AbstractCallGraph

__all__ = ["PointsToSet", "PointerAnalysisState"]


@dataclass(frozen=True)
class PointsToSet:
    """Immutable set of abstract objects.
    
    Represents the set of objects that a variable or field may point to.
    Immutability ensures points-to sets can be used as dictionary keys.
    
    Attributes:
        objects: Frozen set of abstract objects
    """
    
    objects: FrozenSet['AbstractObject']
    
    @staticmethod
    def empty() -> 'PointsToSet':
        """Create empty points-to set.
        
        Returns:
            Empty points-to set
        """
        return PointsToSet(frozenset())
    
    @staticmethod
    def singleton(obj: 'AbstractObject') -> 'PointsToSet':
        """Create singleton points-to set.
        
        Args:
            obj: Single object
        
        Returns:
            Points-to set containing only obj
        """
        return PointsToSet(frozenset([obj]))
    
    def union(self, other: 'PointsToSet') -> 'PointsToSet':
        """Union with another points-to set.
        
        Args:
            other: Another points-to set
        
        Returns:
            New points-to set with objects from both sets
        """
        return PointsToSet(self.objects | other.objects)
    
    def is_empty(self) -> bool:
        """Check if set is empty.
        
        Returns:
            True if set contains no objects
        """
        return len(self.objects) == 0
    
    def __len__(self) -> int:
        """Get number of objects in set."""
        return len(self.objects)
    
    def __iter__(self):
        """Iterate over objects in set."""
        return iter(self.objects)
    
    def __contains__(self, obj: 'AbstractObject') -> bool:
        """Check if object is in set."""
        return obj in self.objects
    
    def __sub__(self, other: 'PointsToSet') -> 'PointsToSet':
        """Subtract another points-to set."""
        return PointsToSet(self.objects - other.objects)
    
    def __str__(self) -> str:
        """String representation for debugging."""
        if self.is_empty():
            return "{}"
        objs = ", ".join(str(o) for o in sorted(self.objects, key=str))
        return f"{{{objs}}}"


class PointerFlowGraph:
    """Represents pointer flow graph in context-sensitive pointer analysis.
    """
    succs: Dict[Variable, Set[Variable]]
    preds: Dict[Variable, Set[Variable]]
    nodes: Set[Variable]
    
    def __init__(self):
        self.succs = {}
        self.preds = {}
        self.nodes = {*()}
    
    def add_edge(self, src: Variable, tgt: Variable):
        if src not in self.succs:
            self.succs[src] = {*()}
        if tgt not in self.preds:
            self.preds[tgt] = {*()}
        self.succs[src].add(tgt)
        self.preds[tgt].add(src)
        self.nodes.add(src)
        self.nodes.add(tgt)

    def get_succs(self, var: Variable) -> Set[Variable]:
        return self.succs.get(var, {*()})
    
    def get_preds(self, var: Variable) -> Set[Variable]:
        return self.preds.get(var, {*()})
    
    def get_nodes(self) -> Set[Variable]:
        return self.nodes
    
    def get_edges(self) -> Set[Tuple[Variable, Variable]]:
        return {(src, tgt) for src in self.nodes for tgt in self.succs[src]}
    
    def get_reverse_edges(self) -> Set[Tuple[Variable, Variable]]:
        return {(tgt, src) for src in self.nodes for tgt in self.preds[src]}    


class PointerAnalysisState:
    """Unified analysis state container.
    
    Maintains the environment (variable points-to information), heap (object
    field points-to information), call graph, and constraint manager.
    """
    
    def __init__(self):
        """Initialize empty analysis state."""
        self._env: Dict['Variable', PointsToSet] = {}
        self._heap: Dict[Tuple['AbstractObject', 'Field'], PointsToSet] = {}
        self._call_graph: 'AbstractCallGraph' = AbstractCallGraph()
        self._constraints: 'ConstraintManager' = ConstraintManager()
        self._call_edges = []  # List of CallEdge objects tracked during analysis
        self._pointer_flow_graph: PointerFlowGraph = PointerFlowGraph()
        self._field_accesses: Dict[Tuple['AbstractObject', 'Field'], FieldAccess] = {}
    
    def get_points_to(self, var: 'Variable') -> PointsToSet:
        """Get points-to set for variable.
        
        Args:
            var: Variable to query
        
        Returns:
            Points-to set for variable (empty if not found)
        """
        return self._env.get(var, PointsToSet.empty())
    
    def set_points_to(self, var: 'Variable', pts: PointsToSet) -> bool:
        """Set points-to set for variable.
        
        Performs union with existing points-to set.
        
        Args:
            var: Variable to update
            pts: Points-to set to add
        
        Returns:
            True if points-to set changed
        """
        old_pts = self._env.get(var, PointsToSet.empty())
        new_pts = old_pts.union(pts)
        
        if new_pts != old_pts:
            self._env[var] = new_pts
            return True
        return False
    
    def get_field(self, obj: 'AbstractObject', field: 'Field') -> Optional['FieldAccess']:
        """Get field access for object field.
        
        Args:
            obj: Object to query
            field: Field to query
        
        Returns:
            Points-to set for field (empty if not found)
        """
        # TODO here is just a trivial mock and did not consider the complex features such as inheritance and MRO.
        return self._field_accesses.get((obj, field), None)
    
    def set_field(
        self,
        obj: 'AbstractObject',
        field: 'Field',
        field_access: 'FieldAccess'
    ) -> None:
        """Set field access for object field.
        
        Performs union with existing points-to set.
        
        Args:
            obj: Object to update
            field: Field to update
        
        Returns:
            Field access for object field
        """
        self._field_accesses[(obj, field)] = field_access
    
    @property
    def constraints(self) -> 'ConstraintManager':
        """Get constraint manager.
        
        Returns:
            Constraint manager
        
        Raises:
            RuntimeError: If constraints not initialized
        """
        if self._constraints is None:
            raise RuntimeError("Constraints not initialized")
        return self._constraints
    
    @property
    def pointer_flow_graph(self) -> 'PointerFlowGraph':
        """Get pointer flow graph.
        
        Returns:
            Pointer flow graph
        """
        return self._pointer_flow_graph
    
    @property
    def call_graph(self) -> 'AbstractCallGraph':
        """Get call graph.
        
        Returns:
            Call graph
        
        Raises:
            RuntimeError: If call graph not initialized
        """
        if self._call_graph is None:
            raise RuntimeError("Call graph not initialized")
        return self._call_graph
    
    def get_statistics(self) -> Dict[str, int]:
        """Get state statistics.
        
        Returns:
            Dictionary with statistics:
            - num_variables: Number of variables tracked
            - num_objects: Number of unique objects
            - num_heap_locations: Number of heap locations
        """
        objects = set()
        for pts in self._env.values():
            objects.update(pts.objects)
        for pts in self._heap.values():
            objects.update(pts.objects)
        
        return {
            "num_variables": len(self._env),
            "num_objects": len(objects),
            "num_heap_locations": len(self._heap)
        }

