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
    # Private in-process anchor. Flat fields remain authoritative for engines
    # which replace maps or add BoundaryObjects; this keeps embedded root refs.
    _store_root:Any=field(default=None,repr=False,compare=False)

from pythonstan.analysis.engines.abstract.state_transform import detached_payload,semantic_payload_equal as payload_equal,PayloadTransformer


class _BoundaryPayload(PayloadTransformer):
    def __init__(self,source_type,target_type):
        super().__init__();self.source_type=source_type;self.target_type=target_type
    def visit(self,value):
        if isinstance(value,self.source_type):
            if id(value) in self.memo:return self.memo[id(value)]
            result=object.__new__(self.target_type);self.memo[id(value)]=result
            for name in ('kind','fields','default','maybe_missing','singleton','class_ids','metadata','havoc'):
                setattr(result,name,self.visit(getattr(value,name)))
            return result
        return super().visit(value)


class FullStateAdapter:
    def encode(self,flow):
        copied=_BoundaryPayload(HeapObject,BoundaryObject).visit(flow);state=copied.store
        return BoundaryState(state.cells,state.heap,state.initialized,state.writes,state.events,state.obligations,copied.kind,copied.value,copied.exception,copied.continuation,copied.cleanups,copied.resume_target,state.havoc_namespaces,state.havoc_imports,state.guards,state.identity_epoch,state)
    def decode(self,boundary):
        copied=_BoundaryPayload(BoundaryObject,HeapObject).visit(boundary)
        state=copied._store_root if copied._store_root is not None else Store()
        for name in ('cells','heap','initialized','writes','events','obligations','havoc_namespaces','havoc_imports','guards','identity_epoch'):
            setattr(state,name,getattr(copied,name))
        return Flow(state,copied.completion,copied.value,copied.exception,copied.continuation,copied.cleanups,copied.resume_target)
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
