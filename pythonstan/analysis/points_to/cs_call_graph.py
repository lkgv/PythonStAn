from typing import Dict, Set, Optional, TypeVar, Generic, Tuple

from .cs_manager import CSManager
from .context import Context
from .stmts import PtInvoke
from pythonstan.ir import IRScope
from pythonstan.utils.common import multimap_add
from pythonstan.graph.call_graph import AbstractCallGraph, CallEdge





class CSCallGraph(AbstractCallGraph[CSCallSite, CSScope]):
    cs_manager: CSManager

    def __init__(self, cs_manager: CSManager):
        super().__init__()
        self.cs_manager = cs_manager

    def add_entry_scope(self, scope: CSScope):
        self.entry_scopes.add(scope)

    def add_reachable_scope(self, scope: CSScope) -> bool:
        ret = scope not in self.reachable_scopes
        self.reachable_scopes.add(scope)
        return ret

    def add_edge(self, edge: CallEdge[CSCallSite, CSScope]):
        if edge not in self.edges:
            self.edges.add(edge)
            callsite = edge.get_callsite()
            callee = edge.get_callee()
            multimap_add(self.callsite_to_edges, callsite, edge)
            multimap_add(self.callee_to_edges, callee, edge)
            self.callsite_to_container[callsite] = callsite.get_container()
            multimap_add(self.callsites_in, callsite.get_container(), callsite)
            return True
        else:
            return False

    def get_edges(self) -> Set[CallEdge[CSCallSite, CSScope]]:
        return self.edges
