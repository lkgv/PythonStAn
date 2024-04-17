from typing import Dict, Set
from enum import Enum
from abc import ABC, abstractmethod

from .elements import Pointer
from .points_to_set import PointsToSet


class FlowKind(Enum):
    LOCAL_ASSIGN = 0
    INSTANCE_STORE = 1
    INSTANCE_LOAD = 2
    STATIC_STORE = 3
    STATIC_LOAD = 4


class PointerFlowEdge:
    _src: Pointer
    _tgt: Pointer
    _kind: FlowKind
    _transfers: Set['EdgeTransfer']

    def __init__(self, kind, src, tgt):
        self._kind = kind
        self._src = src
        self._tgt = tgt
        self._transfers = set()

    def add_transfer(self, transfer: 'EdgeTransfer'):
        self._transfers.add(transfer)

    def get_transfers(self) -> Set['EdgeTransfer']:
        return self._transfers

    def __hash__(self):
        return hash((self._src, self._tgt, self._kind))

    def __eq__(self, other):
        if self == other:
            return True
        if other is None or not isinstance(other, PointerFlowEdge):
            return False
        return (self._kind, self._src, self._tgt) == (other._kind, other._src, other._tgt)

    def __str__(self):
        return f"<pointer flow edge [{self._kind}]: {self._src} -> {self._tgt}>"


class EdgeTransfer(ABC):
    @abstractmethod
    def apply(self, edge: PointerFlowEdge, input: PointsToSet) -> PointsToSet:
        ...


class IdentityEdgeTransfer(EdgeTransfer):
    def apply(self, edge: PointerFlowEdge, input: PointsToSet) -> PointsToSet:
        return input


class PointerFlowGraph:
    in_edges: Dict[Pointer, Set[PointerFlowEdge]]
    out_edges: Dict[Pointer, Set[PointerFlowEdge]]
    edges: Set[PointerFlowEdge]
    nodes: Set[Pointer]

    def __init__(self):
        self.in_edges = {}
        self.out_edges = {}
        self.edges = set()
        self.nodes = set()

    def add_node(self, node: Pointer):
        self.nodes.add(node)
        if node not in self.in_edges:
            self.in_edges[node] = set()
        if node not in self.out_edges:
            self.out_edges[node] = set()

    def add_edge(self, kind: FlowKind, src: Pointer, tgt: Pointer) -> PointerFlowEdge:
        edge = PointerFlowEdge(kind, src, tgt)
        if edge.src not in self.nodes:
            self.add_node(edge.src)
        if edge.tgt not in self.nodes:
            self.add_node(edge.tgt)
        self.edges.add(edge)
        self.in_edges[edge.tgt].add(edge)
        self.out_edges[edge.src].add(edge)
        return edge

    def has_edge(self, kind: FlowKind, src: Pointer, tgt: Pointer) -> bool:
        edge = PointerFlowEdge(kind, src, tgt)
        return edge in self.edges

    def get_in_edges_of(self, node: Pointer) -> Set[PointerFlowEdge]:
        assert node in self.in_edges, f"PointerFlowGraph does not have node {node}"
        return self.in_edges[node]

    def get_out_edges_of(self, node: Pointer) -> Set[PointerFlowEdge]:
        assert node in self.out_edges, f"PointerFlowGraph does not have node {node}"
        return self.out_edges[node]

    def get_preds_of(self, node: Pointer) -> Set[Pointer]:
        assert node in self.in_edges, f"PointerFlowGraph does not have node {node}"
        return {p.src for p in self.in_edges[node]}

    def get_succs_of(self, node: Pointer) -> Set[Pointer]:
        assert node in self.out_edges, f"PointerFlowGraph does not have node {node}"
        return {p.tgt for p in self.out_edges[node]}
