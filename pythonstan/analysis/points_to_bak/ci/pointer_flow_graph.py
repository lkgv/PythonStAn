from typing import Dict, Set

from .pointer import Pointer


class PointerFlowEdge:
    src: Pointer
    tgt: Pointer

    def __init__(self, src, tgt):
        self.src = src
        self.tgt = tgt

    def __eq__(self, other):
        return self.src == other.src and self.tgt == other.tgt

    def __str__(self):
        return f"<edge: {self.src} -> {self.tgt}>"


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

    def add_edge(self, src: Pointer, tgt: Pointer):
        edge = PointerFlowEdge(src, tgt)
        if edge.src not in self.nodes:
            self.add_node(edge.src)
        if edge.tgt not in self.nodes:
            self.add_node(edge.tgt)
        self.edges.add(edge)
        self.in_edges[edge.tgt].add(edge)
        self.out_edges[edge.src].add(edge)

    def get_in_edges_of(self, node: Pointer) -> Set[PointerFlowEdge]:
        assert node in self.in_edges, f"PointerFlowGraph does not have node {node}"
        return self.in_edges[node]

    def get_out_edges_of(self, node: Pointer) -> Set[PointerFlowEdge]:
        assert node in self.out_edges, f"PointerFlowGraph does not have node {node}"
        return self.out_edges[node]

    def get_preds_of(self, node: Pointer) -> Set[Pointer]:
        assert node in self.in_edges, f"PointerFlowGraph does not have node {node}"
        return {p for p in self.in_edges[node]}

    def get_succs_of(self, node: Pointer) -> Set[Pointer]:
        assert node in self.out_edges, f"PointerFlowGraph does not have node {node}"
        return {p for p in self.out_edges[node]}