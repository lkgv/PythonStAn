"""Explicit full-store boundaries between abstract domains and inclusion regions."""
from __future__ import annotations
from dataclasses import dataclass,field,replace
from typing import Any
import ast
from pythonstan.analysis.domains.values import Value,ValueOperations
from pythonstan.analysis.domains.ints import IntLattice
from pythonstan.analysis.engines.abstract.state import Store,HeapObject,Flow

@dataclass
class BoundaryObject:
    kind:str
    fields:dict[Any,Value]
    default:Value
    maybe_missing:set
    singleton:bool
    class_ids:frozenset[str]
    metadata:dict[str,Any]
    havoc:bool

@dataclass
class BoundaryState:
    cells:dict[str,Value]
    heap:dict[str,BoundaryObject]
    initialized:dict[str,str]
    writes:set
    events:tuple
    obligations:frozenset
    completion:str="normal"
    value:Value=field(default_factory=Value.bottom)
    exception:str|None=None
    continuation:tuple=()
    cleanups:tuple=()
    resume_target:Any=None
    havoc_namespaces:frozenset[str]=frozenset()
    havoc_imports:bool=False
    guards:dict=field(default_factory=dict)
    identity_epoch:int=0

from pythonstan.analysis.engines.abstract.state_transform import detached_payload,semantic_payload_equal as payload_equal,PayloadTransformer


class FullStateAdapter:
    def encode(self,flow):
        state=detached_payload(flow.store)
        return BoundaryState(dict(state.cells),{key:BoundaryObject(obj.kind,dict(obj.fields),obj.default,set(obj.maybe_missing),obj.singleton,obj.class_ids,dict(obj.metadata),obj.havoc) for key,obj in state.heap.items()},dict(state.initialized),set(state.writes),tuple(state.events),state.obligations,flow.kind,flow.value,flow.exception,detached_payload(flow.continuation),detached_payload(flow.cleanups),detached_payload(flow.resume_target),state.havoc_namespaces,state.havoc_imports,dict(state.guards),state.identity_epoch)
    def decode(self,boundary):
        state=Store(dict(boundary.cells),{key:HeapObject(obj.kind,dict(obj.fields),obj.default,set(obj.maybe_missing),obj.singleton,obj.class_ids,detached_payload(obj.metadata),obj.havoc) for key,obj in boundary.heap.items()},dict(boundary.initialized),set(boundary.writes),boundary.events,boundary.obligations,boundary.havoc_namespaces,boundary.havoc_imports,dict(boundary.guards),boundary.identity_epoch)
        return Flow(state,boundary.completion,boundary.value,boundary.exception,detached_payload(boundary.continuation),detached_payload(boundary.cleanups),detached_payload(boundary.resume_target))
    def check_roundtrip(self,flow,ops):
        decoded=self.decode(self.encode(flow))
        return (flow.kind==decoded.kind and flow.exception==decoded.exception and payload_equal(flow.store,decoded.store)
                and payload_equal(flow.continuation,decoded.continuation) and payload_equal(flow.cleanups,decoded.cleanups)
                and payload_equal(flow.resume_target,decoded.resume_target) and flow.value==decoded.value)


def reabstract_value(value,operations):
    integer=value.ints
    if integer.consts and len(integer.consts)>operations.policy.max_const_ints:
        integer=IntLattice.from_interval(min(integer.consts),max(integer.consts),integer.parity)
    string=operations.string.widen(value.strings,value.strings)
    return replace(value,ints=integer,strings=string)


def reabstract_state(flow,operations):
    """Reabstract all scalar payloads, preserving the shared identity arena."""
    transform=PayloadTransformer(operations.arena.aliases,operations,
        scalar=lambda value:reabstract_value(value,operations),epoch=operations.arena.epoch)
    return transform.visit(flow)
