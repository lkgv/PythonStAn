"""Analysis state for pointer analysis.

This module defines the state maintained during pointer analysis including
the environment (variable points-to sets) and heap (object field points-to sets).
"""

from dataclasses import dataclass
from typing import Dict, FrozenSet, Tuple, Set, Optional, Iterable, Any, TYPE_CHECKING
from collections import defaultdict

from pythonstan.ir.ir_statements import IRModule, IRStatement
from .object import (FunctionObject, ModuleObject, ClassObject, MethodObject, InstanceObject,
                     ListObject, TupleObject, DictObject, SetObject)
from .variable import VariableFactory, VariableKind
from pythonstan.graph.call_graph import AbstractCallGraph, CallEdge
from .context import CallSite, Ctx, AbstractContext, Scope
from .constraints import ConstraintManager
from .heap_model import FieldKind

if TYPE_CHECKING:
    from .object import AbstractObject, AllocSite
    from .variable import Variable, FieldAccess    
    from .heap_model import Field
    from pythonstan.world.scope_manager import ScopeManager

__all__ = ["PointsToSet", "PointerAnalysisState"]


# TODO to be done 
class PointerCallGraph(AbstractCallGraph[Ctx[IRStatement], Scope]):

    def __init__(self):
        super().__init__()
        self.plain_edges = set()
    
    def add_edge(self, edge: CallEdge[Ctx[IRStatement], Scope]):
        super().add_edge(edge)
        self.plain_edges.add((edge.callsite.content, edge.callee.stmt))
    
    def has_edge(self, edge: CallEdge[Ctx[IRStatement], Scope]):
        return (edge.callsite.content, edge.callee.stmt) in self.plain_edges
    
    def num_plain_edges(self):
        return len(self.plain_edges)
    


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

    def inherit_to(self, new_cls: ClassObject) -> 'PointsToSet':
        cms = [cm.inherit_into(new_cls) for cm in self.classmethods]
        return PointsToSet(self.objects, frozenset(cms), self.instancemethods)
    
    def deliver_into(self, new_inst: InstanceObject) -> 'PointsToSet':
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
    
    
class HeapModel:
    """Heap model for pointer analysis.
    
    Maintains the heap and field accesses.
    """
    
    heap: Dict[Tuple['Scope', 'AbstractContext'], Dict[Tuple[str, VariableKind], 'Ctx[Variable]']]
    prev_scope: Dict['Scope', 'Scope']
    objects: Dict[Tuple['Scope', 'AbstractContext', 'AllocSite'], 'AbstractObject']
    cell_vars: 'Dict[FunctionObject, Dict[str, Set[Ctx[Variable]]]]'
    global_vars: 'Dict[FunctionObject, Dict[str, Set[Ctx[Variable]]]]'
    nonlocal_vars: 'Dict[FunctionObject, Dict[str, Set[Ctx[Variable]]]]'
    
    def __init__(self):
        self.heap = {}
        self.field_accesses = {}
        self.objects = {}
        self.cell_vars = {}
        self.global_vars = {}
        self.nonlocal_vars = {}

    def get_variable(self, scope: 'Scope', context: 'AbstractContext', var: 'Variable') -> Optional['Ctx[Variable]']:
        ctx_key = (scope, )  # (scope, context)
        registers = self.heap.get(ctx_key, {})
        return registers.get(var.name, None)

    def set_variable(self, scope: 'Scope', context: 'AbstractContext', var: 'Variable', ctx_var: 'Ctx[Variable]'):
        ctx_key = (scope, )  # (scope, context)
        registers = self.heap.get(ctx_key, None)
        if registers is None:
            registers = {}
            self.heap[ctx_key] = registers
        registers[var.name] = ctx_var  # TODO whether use context or scope.context?
    
    def get_field(self, scope: 'Scope', context: 'AbstractContext', obj: 'AbstractObject', field: 'Field') -> 'Ctx[FieldAccess]':
        ...

    def get_all_variables(self, scope: 'Scope', context: 'AbstractContext') -> Set['Ctx[Variable]']:
        ctx_key = (scope, )
        return self.heap.get(ctx_key, {}).values()
    
    def get_all_fields(self, scope: 'Scope', context: 'AbstractContext') -> Set['Ctx[Field]']:
        ...
    
    def set_obj(self, scope: 'Scope', context: 'AbstractContext', c: 'AllocSite', o: "AbstractObject"):
        # print(f"New object: {o}")
        self.objects[(scope, c.stmt, c.kind)] = o
    
    def get_obj(self, scope: 'Scope', context: 'AbstractContext', c: 'AllocSite') -> Optional['AbstractObject']:
        return self.objects.get((scope, c.stmt, c.kind), None)
    
    def get_cell_vars(self, obj: FunctionObject) -> Dict[str, Ctx['Variable']]:
        return self.cell_vars.get(obj, {})
    
    def get_global_vars(self, obj: FunctionObject) -> Dict[str, Ctx['Variable']]:
        return self.global_vars.get(obj, {})
    
    def get_nonlocal_vars(self, obj: FunctionObject) -> Dict[str, Ctx['Variable']]:
        return self.nonlocal_vars.get(obj, {})


class GuardNode:
    def __init__(self):
        ...


class SelectorNode:
    def __init(self):
        ...


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
        self._internal_scope = {}
        self.obj_scope = {}

        from pythonstan.world import World
        self._scope_manager = World().scope_manager
    
    def set_internal_scope(self, obj, scope):
        self._internal_scope[obj] = scope
    
    def get_internal_scope(self, obj) -> Scope:
        return self._internal_scope.get(obj, None)
    
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

    def has_field(self, scope: 'Scope', context: 'AbstractContext', obj: 'AbstractObject', field: 'Field') -> Optional['FieldAccess']:
        """Check if the field access exists in the object.
        
        Args:
            obj: Object to query
            field: Field to query
        
        Returns:
            Points-to set for field (empty if not found)
        """
        # TODO here is just a trivial mock and did not consider the complex features such as inheritance and MRO.

        field_access = self._field_accesses.get((obj, field), None)
        return field_access
    
    def get_field(self, scope: 'Scope', context: 'AbstractContext', obj: 'AbstractObject', field: 'Field') -> 'FieldAccess':
        """Get field access for object field.
        
        Handles container-specific field resolution:
        - TupleObject: Supports position(i) fields for precise tracking
        - DictObject: Supports key(k) fields for precise tracking
        - ListObject/SetObject: Uses elem() field for all elements
        
        Args:
            obj: Object to query
            field: Field to query
        
        Returns:
            Contextualized field access for the specified field
        """

        field_access = self._field_accesses.get((obj, field), None)
        exists = True
        if field_access is None:
            field_access = self._variable_factory.make_field_access(obj, field)
            self.set_field(scope, context, obj, field, field_access)
            exists = False
        assert field_access is None
        
        cfield = Ctx(obj.context, None, field_access)

        if not exists:
            if isinstance(obj, ModuleObject):
                internal_scope = self.get_internal_scope(obj)
                var = self._variable_factory.make_variable(field.name, VariableKind.GLOBAL)
                cvar = self.get_variable(internal_scope, internal_scope.context, var)
                self.pointer_flow_graph.add_edge(cvar, cfield)
            
            elif isinstance(obj, InstanceObject):
                cls_obj = obj.class_obj
                cls_scope = self.get_internal_scope(cls_obj)
                cls_field = self.get_field(cls_scope, cls_scope.context, cls_obj, field)
                self.pointer_flow_graph.add_edge(cls_field, cfield)
            
            elif isinstance(obj, ClassObject):
                # TODO add inheritance
                ...
            
            # Container objects (ListObject, TupleObject, DictObject, SetObject)
            # are handled directly through their fields (position, key, elem, value)
            # The sophisticated field tracking is implemented in the solver's
            # _apply_load and _apply_store methods

        return cfield

    
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
        # TODO change active retriving into passive retriving
        cvar = self._get_variable_direct(scope.module, scope.module.context, var.name, VariableKind.LOCAL)
        if cvar is None:
            kind = VariableKind.GLOBAL if isinstance(scope, ModuleObject) else VariableKind.LOCAL
            cvar = Ctx(scope.context, scope, self._variable_factory.make_variable(var.name, kind))
            self.set_variable(scope, context, var, cvar)
        return cvar
    
    def set_variable(self, scope: 'Scope', context: 'AbstractContext', var: 'Variable', ctx_var: 'Ctx[Variable]'):
        self._heap.set_variable(scope, context, var, ctx_var)
        
    def get_cell_var(self, obj: FunctionObject, name: str) -> Optional[Ctx['Variable']]:
        return self._heap.get_cell_vars(obj).get(name, None)
    
    def get_nonlocal_var(self, obj: FunctionObject, name: str) -> Optional[Ctx['Variable']]:
        return self._heap.get_nonlocal_vars(obj).get(name, None)
    
    def get_global_var(self, obj: FunctionObject, name: str) -> Optional[Ctx['Variable']]:
        return self._heap.get_global_vars(obj).get(name, None)
    
    def get_cell_vars(self, obj: FunctionObject) -> Dict[str, Ctx['Variable']]:
        return self._heap.get_cell_vars(obj)
    
    def get_nonlocal_vars(self, obj: FunctionObject) -> Dict[str, Ctx['Variable']]:
        return self._heap.get_nonlocal_vars(obj)
    
    def get_global_vars(self, obj: FunctionObject) -> Dict[str, Ctx['Variable']]:
        return self._heap.get_global_vars(obj)
    
    def set_cell_vars(self, obj: FunctionObject, vars):
        self._heap.cell_vars[obj] = vars
    
    def set_nonlocal_vars(self, obj: FunctionObject, vars):
        self._heap.nonlocal_vars[obj] = vars

    def set_global_vars(self, obj: FunctionObject, vars):
        self._heap.global_vars[obj] = vars
        
    def _get_variable_direct(self, scope: 'Scope', context: 'AbstractContext', var_name: str, var_kind: VariableKind) -> Optional['Ctx[Variable]']:
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

