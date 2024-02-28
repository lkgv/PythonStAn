from typing import Dict, Set, Optional, TypeVar, Generic
from enum import Enum

from .cs_manager import CSManager
from .stmts import PtInvoke
from pythonstan.ir import IRScope


class CallKind(Enum):
    INSTANCE = 1
    STATIC = 2
    OTHER = 3


CallSite = TypeVar("CallSite")
Method = TypeVar('Method')


class CallEdge(Generic[CallSite, Method]):
    kind: CallKind
    callsite: CallSite
    callee: Method

    def __init__(self, kind: CallKind, callsite: PtInvoke, callee: Method):
        self.kind = kind
        self.callsite = callsite
        self.callee = callee

    def get_kind(self) -> CallKind:
        return self.kind

    def get_callsite(self) -> PtInvoke:
        return self.callsite

    def get_callee(self) -> Method:
        return self.callee

    def __eq__(self, other):
        if self == other:
            return True
        if other is None or not type(self) != type(other):
            return False
        return self.kind == other.kind and self.callsite == other.callsite and self.callee == other.callee


class AbstractCallGraph(Generic[CallSite, Method]):
    callsite_to_edges: Dict[CallSite, Set[CallEdge[CallSite, Method]]]
    callee_to_edges: Dict[Method, Set[CallEdge[CallSite, Method]]]
    callsite_to_container: Dict[CallSite, Method]
    callsites_in: Dict[Method, Set[CallSite]]
    entry_scopes: Set[Method]
    reachable_scopes: Set[Method]

    def __init__(self):
        self.callsite_to_edges = {}
        self.callee_to_edges = {}
        self.callsite_to_container = {}
        self.callsites_to = {}
        self.entry_scopes = {*()}

    def get_callers_of(self, callee: Method) -> Set[CallSite]:
        return {e.get_callsite() for e in self.callee_to_edges.get(callee, {*()})}

    def get_callees_of(self, callsite: CallSite) -> Set[Method]:
        return {e.get_callee() for e in self.callsite_to_edges.get(callsite, {*()})}

    def get_callees_of_scope(self, caller: Method) -> Set[Method]:
        ret = {*()}
        for callsite in self.callsites_in.get(caller, {*()}):
            ret.update(self.get_callees_of(callsite))
        return ret

    def get_container_of(self, callsite: CallSite) -> Optional[Method]:
        return self.callsite_to_container.get(callsite, None)

    def get_callsites_in(self, scope: Method) -> Set[CallSite]:
        return self.callsites_in.get(scope, {*()})

    def edges_out_of(self, callsite: CallSite) -> Set[CallEdge[CallSite, Method]]:
        return self.callsite_to_edges.get(callsite, {*()})

    def edges_in_to(self, callee: CallSite) -> Set[CallEdge[CallSite, Method]]:
        return self.callee_to_edges.get(callee, {*()})

    def edges(self) -> Set[CallEdge[CallSite, Method]]:
        return {e for e in self.callsite_to_edges.values()}

    def get_number_of_edges(self) -> int:
        return sum([len(es) for es in self.callsite_to_edges.values()])

    def get_nodes(self) -> Set[Method]:
        return self.reachable_scopes


class CSCallSite:
    ...

class CSMethod:
    ...

class CSCallGraph(AbstractCallGraph[CSCallSite, CSMethod]):
    cs_manager: CSManager

    def __init__(self, cs_manager: CSManager):
        super().__init__()
        self.cs_manager = cs_manager

    def add_edge(self, ...):