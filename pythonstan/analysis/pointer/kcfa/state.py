"""Analysis state for pointer analysis.

This module defines the state maintained during pointer analysis including
the environment (variable points-to sets) and heap (object field points-to sets).
"""

from dataclasses import dataclass
from typing import Dict, FrozenSet, Tuple, Set, Optional, Iterable, Any, TYPE_CHECKING
from collections import defaultdict

from pythonstan.ir.ir_statements import IRModule
from .object import FunctionObject, ModuleObject, ClassObject
from .variable import VariableFactory, VariableKind
from pythonstan.graph.call_graph import AbstractCallGraph
from .context import CallSite, Ctx, AbstractContext, Scope
from .constraints import ConstraintManager

if TYPE_CHECKING:
    from .object import AbstractObject, AllocSite
    from .variable import Variable, FieldAccess    
    from .heap_model import Field
    from pythonstan.world.scope_manager import ScopeManager

__all__ = ["PointsToSet", "PointerAnalysisState"]


class PointerCallGraph(AbstractCallGraph[CallSite, 'Variable']):
    def __init__(self):
        super().__init__()


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
    
    @staticmethod
    def from_objects(objs: Iterable['AbstractObject']) -> 'PointsToSet':
        """Create points-to set from a set of objects."""
        return PointsToSet(frozenset(objs))
    
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
    
    
class HeapModel:
    """Heap model for pointer analysis.
    
    Maintains the heap and field accesses.
    """
    
    heap: Dict[Tuple['Scope', 'AbstractContext'], Dict[Tuple[str, VariableKind], 'Ctx[Variable]']]
    prev_scope: Dict['Scope', 'Scope']
    objects: Dict[Tuple['Scope', 'AbstractContext', 'AllocSite'], 'AbstractObject']
    
    def __init__(self):
        self.heap = {}
        self.field_accesses = {}
        self.objects = {}

    def get_variable(self, scope: 'Scope', context: 'AbstractContext', var: 'Variable') -> 'Ctx[Variable]':
        if self.heap.get((scope, context)) is None:
            self.heap[(scope, context)] = {}
        registers = self.heap[(scope, context)]
        if var.name not in registers:
            registers[(var.name, var.kind)] = Ctx(scope.context, scope, var)  # TODO whether use context or scope.context?
        return registers[(var.name, var.kind)]
    
    def get_field(self, scope: 'Scope', context: 'AbstractContext', obj: 'AbstractObject', field: 'Field') -> 'Ctx[FieldAccess]':
        ...

    def get_all_variables(self, scope: 'Scope', context: 'AbstractContext') -> Set['Ctx[Variable]']:
        return self.heap.get((scope, context), {}).values()
    
    def get_all_fields(self, scope: 'Scope', context: 'AbstractContext') -> Set['Ctx[Field]']:
        ...
    
    def set_obj(self, scope: 'Scope', context: 'AbstractContext', c: 'AllocSite', o: "AbstractObject"):
        self.objects[(scope, context, c)] = o
    
    def get_obj(self, scope: 'Scope', context: 'AbstractContext', c: 'AllocSite') -> Optional['AbstractObject']:
        return self.objects.get((scope, context, c), None)


class PointerFlowGraph:
    """Represents pointer flow graph in context-sensitive pointer analysis.
    """
    succs: Dict[Ctx[Any], Set[Ctx[Any]]]
    preds: Dict[Ctx[Any], Set[Ctx[Any]]]
    nodes: Set[Ctx[Any]]
    
    def __init__(self):
        self.succs = {}
        self.preds = {}
        self.nodes = {*()}
    
    def add_edge(self, src: Ctx[Any], tgt: Ctx[Any]):
        assert isinstance(src, Ctx)
        assert isinstance(tgt, Ctx)
        if src not in self.succs:
            self.succs[src] = {*()}
        if tgt not in self.preds:
            self.preds[tgt] = {*()}
        self.succs[src].add(tgt)
        self.preds[tgt].add(src)
        self.nodes.add(src)
        self.nodes.add(tgt)

    def get_succs(self, var: Ctx[Any]) -> Set[Ctx[Any]]:
        return self.succs.get(var, {*()})
    
    def get_preds(self, var: Ctx[Any]) -> Set[Ctx[Any]]:
        return self.preds.get(var, {*()})
    
    def get_nodes(self) -> Set[Ctx[Any]]:
        return self.nodes
    
    def get_edges(self) -> Set[Tuple[Ctx[Any], Ctx[Any]]]:
        return {(src, tgt) for src in self.nodes for tgt in self.succs.get(src, {*()})}
    
    def get_reverse_edges(self) -> Set[Tuple[Ctx[Any], Ctx[Any]]]:
        return {(tgt, src) for src in self.nodes for tgt in self.preds.get(src, {*()})}    


class PointerAnalysisState:
    """Unified analysis state container.
    
    Maintains the environment (variable points-to information), heap (object
    field points-to information), call graph, and constraint manager.
    """
    
    def __init__(self):
        """Initialize empty analysis state."""
        self._env: Dict['Variable', PointsToSet] = {}
        self._heap = HeapModel()
        self._call_graph: 'AbstractCallGraph' = PointerCallGraph()
        self._constraints: ConstraintManager = ConstraintManager()
        self._call_edges = []  # List of CallEdge objects tracked during analysis
        self._pointer_flow_graph: PointerFlowGraph = PointerFlowGraph()
        self._field_accesses: Dict[Tuple['AbstractObject', 'Field'], FieldAccess] = {}
        self._variable_factory: VariableFactory = VariableFactory()

        from pythonstan.world import World
        self._scope_manager = World().scope_manager
    
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
    
    def get_field(self, scope: 'Scope', context: 'AbstractContext', obj: 'AbstractObject', field: 'Field') -> Optional['FieldAccess']:
        """Get field access for object field.
        
        Args:
            obj: Object to query
            field: Field to query
        
        Returns:
            Points-to set for field (empty if not found)
        """
        # TODO here is just a trivial mock and did not consider the complex features such as inheritance and MRO.
        field_access = self._field_accesses.get((obj, field), None)
        if field_access is None:
            from .variable import FieldAccess
            field_access = self._variable_factory.make_field_access(obj, field)
            self.set_field(scope, context, obj, field, field_access)
        assert field_access is None
        obj = Ctx(obj.context, scope, field_access)
        return obj

    
    def set_field(
        self,
        scope: 'Scope',
        context: 'AbstractContext',
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
    def scope_manager(self) -> 'ScopeManager':
        return self._scope_manager
    
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

    def get_variable(self, scope: 'Scope', context: 'AbstractContext', var: 'Variable') -> 'Ctx[Variable]':
        container_obj = scope.obj
        if isinstance(container_obj, ModuleObject):
            cvar = self._get_variable_direct(scope, context, var.name, VariableKind.GLOBAL)
        elif isinstance(container_obj, FunctionObject):
            if var.is_cell and var.name in container_obj.cell_vars:
                cvar = container_obj.cell_vars[var.name]
                target_var = self._get_variable_direct(scope, context, var.name, VariableKind.LOCAL)
                self.pointer_flow_graph.add_edge(cvar, target_var)
            elif var.is_nonlocal and var.name in container_obj.nonlocal_vars:
                cvar = container_obj.nonlocal_vars[var.name]
                # in a multi-level clocure, nonlocal appears as a writable cell var in the parent scope
            elif var.is_nonlocal and var.name in container_obj.cell_vars:
                cvar = container_obj.cell_vars[var.name]
            elif var.is_global and var.name in container_obj.global_vars:
                cvar = container_obj.global_vars[var.name]
            else:
                cvar = self._get_variable_direct(scope, context, var.name, VariableKind.LOCAL)
        # TODO add logic for class
        # elif isinstance(container_obj, ClassObject):
        else:
            # TODO add option between global_var and local_var to make over-approximate memory
            cvar = self._get_variable_direct(scope.module, context, var.name, VariableKind.GLOBAL)
        return cvar
        
    def _get_variable_direct(self, scope: 'Scope', context: 'AbstractContext', var_name: str, var_kind: VariableKind) -> 'Ctx[Variable]':
        var = self._variable_factory.make_variable(var_name, var_kind)
        return self._heap.get_variable(scope, context, var)
    
    def get_statistics(self) -> Dict[str, int]:
        """Get state statistics.
        
        Returns:
            Dictionary with statistics:
            - num_variables: Number of variables tracked
            - num_objects: Number of unique objects
            - num_heap_locations: Number of heap locations
        """
        objects = set()
        '''
        for pts in self._env.values():
            objects.update(pts.objects)
        for pts in self._heap.values():
            objects.update(pts.objects)
        '''
        
        return {
            "num_variables": len(self._env),
            "num_objects": len(objects),
            "num_heap_locations": len(self._heap.objects),
            "num_call_edges": self._call_graph.get_number_of_edges()
        }

