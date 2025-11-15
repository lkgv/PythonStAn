from dataclasses import dataclass
from typing import Dict, FrozenSet, Tuple, Set, Optional, Iterable, Any, TYPE_CHECKING, List, Callable
from enum import Enum
from abc import ABC, abstractmethod

from .context import Ctx
from .object import InstanceObject, ClassObject
if TYPE_CHECKING:
    from .object import AbstractObject, AllocSite
    from .variable import Variable, FieldAccess    
    from .heap_model import Field
    from pythonstan.world.scope_manager import ScopeManager
    from .constraints import ConstraintManager
    from pythonstan.ir.ir_statements import IRModule, IRStatement
    from .variable import VariableFactory, VariableKind
    from pythonstan.graph.call_graph import AbstractCallGraph, CallEdge
    from .context import CallSite, AbstractContext, Scope
    from .points_to_set import PointsToSet

__all__ = ["PointerFlowGraph", "PointerFlowEdge", "PointerFlowNode", "NormalNode", "GuardNode", "SelectorNode", "PointerFlowKind"]


class PointerFlowKind(Enum):
    """Kinds of points-to flow in pointer flow graph."""
    
    NORMAL = "normal"
    INHERIT = "inherit"
    INSTANCE = "instance"


@dataclass(frozen=True)
class PointerFlowEdge:
    source: 'PointerFlowNode'
    target: 'PointerFlowNode'
    kind: PointerFlowKind
    
    def __post_init__(self):
        assert isinstance(self.source, PointerFlowNode)
        assert isinstance(self.target, PointerFlowNode)
        assert self.source != self.target
        if self.kind == PointerFlowKind.INHERIT:
            assert isinstance(self.target, NormalNode) and isinstance(self.target.var.content.obj, ClassObject)
        if self.kind == PointerFlowKind.INSTANCE:
            assert isinstance(self.target, NormalNode) and isinstance(self.target.var.content.obj, InstanceObject)

    
    def flow_through(self, pts: 'PointsToSet') -> 'PointsToSet':
        if self.kind == PointerFlowKind.INHERIT:
            return pts.inherit_to(self.target.var.content.obj)
        elif self.kind == PointerFlowKind.INSTANCE:
            return pts.deliver_into(self.source.var.content.obj)
        else:
            return pts


class PointerFlowNode(ABC):
    @abstractmethod
    def flow_through(self, edge: PointerFlowEdge, pts: 'PointsToSet') -> 'PointsToSet':
        pass


@dataclass(frozen=True)
class NormalNode(PointerFlowNode):
    """
    Normal node in pointer flow graph, just make all objects flowed through.
    var: Ctx[Any] - the variable of the node.
    """
    var: Ctx[Any]
    
    def __post_init__(self):
        assert isinstance(self.var, Ctx), f"var must be a Ctx, but got {type(self.var)}"
    
    def flow_through(self, edge: PointerFlowEdge, pts: 'PointsToSet') -> 'PointsToSet':
        return pts
    

class GuardNode(PointerFlowNode):
    """
    Guardian node in pointer flow graph, only allow the objects that are allowed by the guard condition to be flowed through.
    guard: Callable[[PointerFlowEdge, PointsToSet], PointsToSet] - the guard condition.
    """
    
    def __init__(self, guard: 'Callable[[PointerFlowEdge, PointsToSet], PointsToSet]'):
        self.guard = guard
    
    def __hash__(self):
        return id(self)

    def __eq__(self, other):
        return self is other

    def flow_through(self, edge: PointerFlowEdge, pts: 'PointsToSet') -> 'PointsToSet':
        return self.guard(edge, pts)
    

class SelectorNode:
    """Selector node in pointer flow graph, only the edge with the least index will be flowed through.
    edges: Dict[PointerFlowEdge, int] - the edges and their indices.
    least_index: int - the least index of the edges.
    """
    edges: Dict[PointerFlowEdge, int]
    least_index: int
    
    def __init__(self):
        self.edges = {}
        self.least_index = -1
    
    def __hash__(self):
        return id(self)

    def __eq__(self, other):
        return self is other
    
    def add_edge(self, edge: PointerFlowEdge, index: int):
        self.edges[edge] = index
    
    def flow_through(self, edge: PointerFlowEdge, pts: 'PointsToSet') -> 'PointsToSet':
        assert edge in self.edges, f"edge {edge} not found in selector node"
        index = self.edges[edge]
        if self.least_index == -1 or index <= self.least_index:
            self.least_index = index
            return pts
        else:
            return PointsToSet.empty()


class PointerFlowGraph:
    """Represents pointer flow graph in context-sensitive pointer analysis.
    """
    # TODO maybe object can be grouped by age, and too old objects should not be propagated.
    
    succs: Dict[PointerFlowNode, Set[PointerFlowEdge]]
    preds: Dict[PointerFlowNode, Set[PointerFlowEdge]]
    nodes: Set[PointerFlowNode]    
    edges: Set[PointerFlowEdge]
    
    def __init__(self):
        self.succs = {}
        self.preds = {}
        self.nodes = set()
        self.edges = set()
    
    def propagate(self, node: PointerFlowNode, pts: 'PointsToSet') -> 'List[Tuple[PointerFlowNode, PointsToSet]]':
        assert isinstance(node, PointerFlowNode), f"node must be a PFNode, but got {type(node)}"
        result = []
        for succ_edge in self.succs.get(node, frozenset()):
            succ_pts = succ_edge.flow_through(pts)
            if succ_pts.is_empty():
                continue
            succ_pts = succ_edge.target.flow_through(succ_edge, succ_pts)
            if succ_pts.is_empty():
                continue
            result.append([succ_edge.target, succ_pts])
        return result

    def add_edge(self, edge: PointerFlowEdge) -> bool:
        if edge not in self.edges:
            self.succs.setdefault(edge.source, {*()}).add(edge)
            self.preds.setdefault(edge.target, {*()}).add(edge)
            self.nodes.add(edge.source)
            self.nodes.add(edge.target)
            self.edges.add(edge)
            return True
        else:
            return False
    
    def flow_through_edge(self, edge: PointerFlowEdge, pts: 'PointsToSet') -> 'PointsToSet':
        return edge.target.flow_through(edge, edge.flow_through(pts))

    def get_succs(self, var: PointerFlowNode) -> Set[PointerFlowEdge]:
        return self.succs.get(var, {*()})
    
    def get_preds(self, var: PointerFlowNode) -> Set[PointerFlowEdge]:
        return self.preds.get(var, {*()})
    
    def get_nodes(self) -> Set[PointerFlowNode]:
        return self.nodes
    
    def get_edges(self) -> Set[PointerFlowEdge]:
        return self.edges
