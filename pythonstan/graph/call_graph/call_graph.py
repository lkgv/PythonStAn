from abc import ABC, abstractmethod
from typing import TypeVar

from .call_edge import CallEdge

__all__ = ["AbstractCallGraph"]

CallSite = TypeVar("CallSite")
Method = TypeVar("Method")


class AbstractCallGraph[CallSite, Method](ABC):
    callsite_to_edges: dict[CallSite, set[CallEdge[CallSite, Method]]]
    callee_to_edges: dict[Method, set[CallEdge[CallSite, Method]]]
    callsite_to_container: dict[CallSite, Method]
    callsites_in: dict[Method, set[CallSite]]
    entry_scopes: set[Method]
    reachable_scopes: set[Method]
    edges: set[CallEdge[CallSite, Method]]

    @abstractmethod
    def __init__(self) -> None:
        self.callsite_to_edges = {}
        self.callee_to_edges = {}
        self.callsite_to_container = {}
        self.callsites_in = {}
        self.entry_scopes = {*()}
        self.reachable_scopes = {*()}
        self.edges = {*()}

    def add_edge(self, edge: CallEdge[CallSite, Method]) -> None:
        if edge.get_callsite() not in self.callsite_to_edges:
            self.callsite_to_edges[edge.get_callsite()] = {*()}
        if edge.get_callee() not in self.callee_to_edges:
            self.callee_to_edges[edge.get_callee()] = {*()}
        if edge.get_callee() not in self.callsites_in:
            self.callsites_in[edge.get_callee()] = {*()}
        self.callsite_to_edges[edge.get_callsite()].add(edge)
        self.callee_to_edges[edge.get_callee()].add(edge)
        self.callsite_to_container[edge.get_callsite()] = edge.get_callee()
        self.callsites_in[edge.get_callee()].add(edge.get_callsite())
        self.reachable_scopes.add(edge.get_callee())
        self.edges.add(edge)

    def get_callers_of(self, callee: Method) -> set[CallSite]:
        return {e.get_callsite() for e in self.callee_to_edges.get(callee, {*()})}

    def get_callees_of(self, callsite: CallSite) -> set[Method]:
        return {e.get_callee() for e in self.callsite_to_edges.get(callsite, {*()})}

    def get_callees_of_scope(self, caller: Method) -> set[Method]:
        ret = {*()}
        for callsite in self.callsites_in.get(caller, {*()}):
            ret.update(self.get_callees_of(callsite))
        return ret

    def get_container_of(self, callsite: CallSite) -> Method | None:
        return self.callsite_to_container.get(callsite, None)

    def get_callsites_in(self, scope: Method) -> set[CallSite]:
        return self.callsites_in.get(scope, {*()})

    def edges_out_of(self, callsite: CallSite) -> set[CallEdge[CallSite, Method]]:
        return self.callsite_to_edges.get(callsite, {*()})

    def edges_in_to(self, callee: Method) -> set[CallEdge[CallSite, Method]]:
        return self.callee_to_edges.get(callee, {*()})

    def get_edges(self) -> set[CallEdge[CallSite, Method]]:
        return self.edges

    def get_number_of_edges(self) -> int:
        return len(self.edges)

    def get_nodes(self) -> set[Method]:
        return self.reachable_scopes

    def has_edge(self, edge: CallEdge[CallSite, Method]) -> bool:
        return edge in self.edges
