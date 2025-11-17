"""Analysis state for pointer analysis.

This module defines the state maintained during pointer analysis including
the environment (variable points-to sets) and heap (object field points-to sets).
"""

from dataclasses import dataclass
from typing import Dict, FrozenSet, Tuple, Set, Optional, Iterable, Any, TYPE_CHECKING
from collections import defaultdict

from pythonstan.ir.ir_statements import IRModule, IRStatement
from pythonstan.graph.call_graph import AbstractCallGraph, CallEdge
from .object import *
from .variable import VariableFactory, VariableKind, FieldAccess, Variable
from .context import CallSite, Ctx, AbstractContext, Scope
from .constraints import ConstraintManager, Constraint, InheritanceConstraint
from .heap_model import HeapModel, Field, FieldKind
from .pointer_flow_graph import PointerFlowGraph, NormalNode, GuardNode, SelectorNode, PointerFlowEdge, PointerFlowNode, PointerFlowKind
from .points_to_set import PointsToSet

if TYPE_CHECKING:
    from pythonstan.world.scope_manager import ScopeManager

__all__ = ["PointerAnalysisState"]


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


class Worklist:
    items: Dict[PointerFlowNode, Tuple[Scope, PointsToSet]]

    def __init__(self):
        self.items = {}

    def add(self, content: Tuple[Scope, PointerFlowNode, PointsToSet]):
        scope, node, pts = content
        assert isinstance(node, PointerFlowNode), f"node must be a PFNode, but got {type(node)}"
        if isinstance(node, NormalNode):
            assert isinstance(node.var, Ctx), f"node.var must be a Ctx, but got {type(node.var)}"
        if node in self.items:
            _, orig_pts = self.items.pop(node)
            self.items[node] = (scope, orig_pts.union(pts))
        else:
            self.items[node] = (scope, pts)
    
    def pop(self) -> Tuple[Scope, PointerFlowNode, PointsToSet]:
        node, (scope, pts) = self.items.popitem()
        return scope, node, pts
    
    def empty(self) -> bool:
        return len(self.items) == 0
    
    def __len__(self) -> int:
        return len(self.items)
    

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
        self._worklist: Worklist = Worklist()
        self._static_constraints: Set[Tuple['Scope', 'AbstractContext', 'Constraint']] = set()
        self._internal_scope = {}
        self.obj_scope = {}

        # Note: scope_manager is set lazily when needed
        self._scope_manager = None
    
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
    
    def get_field(self, scope: 'Scope', context: 'AbstractContext', obj: 'AbstractObject', field: 'Field') -> 'Ctx[FieldAccess]':
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
        
        cfield: Ctx[FieldAccess] = Ctx(obj.context, None, field_access)

        if not exists:
            if isinstance(obj, ModuleObject):
                if field.name is None:
                    return cfield
                
                internal_scope = self.get_internal_scope(obj)
                var = self._variable_factory.make_variable(field.name, VariableKind.GLOBAL)
                cvar = self.get_variable(internal_scope, internal_scope.context, var)
                self._add_var_points_flow(cvar, cfield)
            
            elif isinstance(obj, InstanceObject):
                # print(f"get field {field.name} for instance {obj}")
                cls_obj = obj.class_obj
                assert isinstance(cls_obj, ClassObject), f"cls_obj must be a ClassObject, but got {type(cls_obj)}"
                cls_scope = self.get_internal_scope(cls_obj)
                cls_field = self.get_field(cls_scope, cls_scope.context, cls_obj, field)
                assert cls_field != cfield, f"cls_field {cls_field} and cfield {cfield} should not be the same"
                if cls_field != cfield:
                    edge = PointerFlowEdge(NormalNode(cls_field), NormalNode(cfield), PointerFlowKind.INSTANCE)
                    self._add_points_flow_edge(edge)
            
            elif isinstance(obj, ClassObject):
                bases = obj.alloc_site.stmt.get_bases()
                scope = obj.container_scope

                if len(bases) > 0:
                    # TODO here can use the class hierarchy manager to get the base class and the index of the base class.
                    selector = SelectorNode()
                    inherit_edge = PointerFlowEdge(selector, NormalNode(cfield), PointerFlowKind.INHERIT)
                    self._add_points_flow_edge(inherit_edge)

                    for idx, base in enumerate(bases):
                        base_var = self._variable_factory.make_variable(base.id)
                        inherit_constraint = InheritanceConstraint(base=base_var, field=field, target=selector, index=idx)
                        self._constraints.add(scope, base_var, inherit_constraint)
            
            # Handle builtin instance objects - create builtin method objects on-demand
            from .object import BuiltinInstanceObject, BuiltinMethodObject, ObjectFactory
            if isinstance(obj, BuiltinInstanceObject) and field.kind == FieldKind.ATTRIBUTE and field.name:
                # Check if this is a known builtin method
                method_name = field.name
                builtin_methods = self._get_builtin_methods_for_type(obj.builtin_type)
                
                if method_name in builtin_methods:
                    # Create a builtin method object bound to this instance
                    method_obj = ObjectFactory.create_builtin_method(
                        method_name=method_name,
                        receiver=obj,
                        context=obj.context
                    )
                    
                    # Add the method object to the field's points-to set
                    self._worklist.add((scope, NormalNode(cfield), PointsToSet.singleton(method_obj)))

        return cfield
    
    def _get_builtin_methods_for_type(self, builtin_type: str) -> Set[str]:
        """Get the set of known methods for a builtin type.
        
        Args:
            builtin_type: Type name (e.g., "list", "dict", "set")
        
        Returns:
            Set of method names
        """
        methods = {
            "list": {
                "append", "extend", "insert", "remove", "pop", "clear",
                "index", "count", "sort", "reverse", "copy",
                "__getitem__", "__setitem__", "__iter__", "__len__"
            },
            "dict": {
                "get", "pop", "popitem", "clear", "update", "setdefault",
                "keys", "values", "items", "copy",
                "__getitem__", "__setitem__", "__iter__", "__len__", "__contains__"
            },
            "set": {
                "add", "remove", "discard", "pop", "clear", "copy",
                "union", "intersection", "difference", "symmetric_difference",
                "update", "intersection_update", "difference_update",
                "__iter__", "__len__", "__contains__"
            },
            "tuple": {
                "count", "index",
                "__getitem__", "__iter__", "__len__"
            },
            "str": {
                "upper", "lower", "strip", "split", "join", "replace",
                "startswith", "endswith", "find", "index", "format",
                "__getitem__", "__iter__", "__len__", "__contains__"
            },
        }
        return methods.get(builtin_type, set())

    
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
        assert isinstance(field_access, FieldAccess), f"field_access must be a FieldAccess, but got {type(field_access)}"
        self._field_accesses[(obj, field)] = field_access
    
    @property
    def scope_manager(self) -> 'ScopeManager':
        if self._scope_manager is None:
            from pythonstan.world import World
            self._scope_manager = World().scope_manager
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
        assert isinstance(var_name, str), f"var_name must be a string, but got {type(var_name)}"
        
        var = self._variable_factory.make_variable(var_name, var_kind)
        return self._heap.get_variable(scope, context, var)
        
    def _add_var_points_flow(self, src: Ctx[Any], tgt: Ctx[Any]):
        assert isinstance(src, Ctx), f"src must be a Ctx, but got {type(src)}"
        assert isinstance(tgt, Ctx), f"tgt must be a Ctx, but got {type(tgt)}"
        if src != tgt:
            self._add_points_flow_edge(PointerFlowEdge(NormalNode(src), NormalNode(tgt), PointerFlowKind.NORMAL))
    
    def _add_points_flow_edge(self, edge: PointerFlowEdge):
        if self.pointer_flow_graph.add_edge(edge):
            src = edge.source
            tgt = edge.target
            pts = self.pointer_flow_graph.flow_through_edge(edge, self.get_points_to(src)) - self.get_points_to(tgt)
            if not pts.is_empty():
                scope = None
                if isinstance(tgt, NormalNode):
                    scope = tgt.var.scope
                self._worklist.add((scope, tgt, pts))
    
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

