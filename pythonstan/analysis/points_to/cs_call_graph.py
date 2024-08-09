from typing import Dict, Set, Optional, TypeVar, Generic, Tuple

from .cs_manager import CSManager
from .elements import *
from pythonstan.utils.common import multimap_add
from pythonstan.graph.call_graph import AbstractCallGraph, CallEdge

__all__ = ['CSCallGraph', 'CSCallEdge']


CSCallEdge = CallEdge[PtInvoke, Obj]


class CSCallGraph(AbstractCallGraph[PtInvoke, Obj]):
    cs_manager: CSManager
    edge_to_frame: Dict[CSCallEdge, PtFrame]

    def __init__(self, cs_manager: CSManager):
        super().__init__()
        self.cs_manager = cs_manager
        self.edge_to_frame = {}

    def add_entry_scope(self, scope: Obj):
        self.entry_scopes.add(scope)

    def add_reachable_scope(self, scope: Obj) -> bool:
        ret = scope not in self.reachable_scopes
        self.reachable_scopes.add(scope)
        return ret

    def add_edge(self, edge: CSCallEdge) -> bool:
        if edge not in self.edges:
            self.edges.add(edge)
            callsite = edge.get_callsite()
            callee = edge.get_callee()
            multimap_add(self.callsite_to_edges, callsite, edge)
            multimap_add(self.callee_to_edges, callee, edge)
            container_obj = callsite.get_frame().get_code_obj()
            self.callsite_to_container[callsite] = container_obj
            multimap_add(self.callsites_in, container_obj, callsite)
            return True
        else:
            return False

    def get_edges(self) -> Set[CSCallEdge]:
        return self.edges

    def set_edge_to_frame(self, edge: CSCallEdge, frame: PtFrame):
        self.edge_to_frame[edge] = frame

    def get_edge_to_frame(self, edge: CSCallEdge) -> Optional[PtFrame]:
        return self.edge_to_frame.get(edge, None)
