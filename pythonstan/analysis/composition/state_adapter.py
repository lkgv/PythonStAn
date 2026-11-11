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

def detached_payload(value,memo=None):
    """Copy mutable semantic state while sharing immutable values/source IR."""
    memo={} if memo is None else memo
    if value is None or isinstance(value,(str,int,float,bool,Value,ast.AST)):return value
    if id(value) in memo:return memo[id(value)]
    if isinstance(value,Store):
        result=Store();memo[id(value)]=result
        result.cells=dict(value.cells);result.heap={key:detached_payload(obj,memo) for key,obj in value.heap.items()}
        result.initialized=dict(value.initialized);result.writes=set(value.writes);result.events=detached_payload(value.events,memo);result.obligations=value.obligations;result.havoc_namespaces=value.havoc_namespaces;result.havoc_imports=value.havoc_imports
        return result
    if isinstance(value,HeapObject):
        result=value.copy();memo[id(value)]=result;result.metadata=detached_payload(value.metadata,memo);return result
    if isinstance(value,dict):
        result={};memo[id(value)]=result;result.update((key,detached_payload(item,memo)) for key,item in value.items());return result
    if isinstance(value,list):
        result=[];memo[id(value)]=result;result.extend(detached_payload(item,memo) for item in value);return result
    if isinstance(value,tuple):return tuple(detached_payload(item,memo) for item in value)
    if isinstance(value,set):return {detached_payload(item,memo) for item in value}
    if isinstance(value,frozenset):return frozenset(detached_payload(item,memo) for item in value)
    # Frame and semantic IDs are frozen dataclasses, shared as immutable data.
    parameters=getattr(type(value),"__dataclass_params__",None)
    if parameters and parameters.frozen:return value
    raise TypeError("Unsupported mutable boundary metadata: "+type(value).__name__)


def payload_equal(left,right,seen=None):
    if left is right:return True
    if type(left) is not type(right):return False
    seen=set() if seen is None else seen
    pair=(id(left),id(right))
    if pair in seen:return True
    seen.add(pair)
    if isinstance(left,Store):return all(payload_equal(getattr(left,name),getattr(right,name),seen) for name in ("cells","heap","initialized","writes","events","obligations","havoc_namespaces","havoc_imports"))
    if isinstance(left,HeapObject):return all(payload_equal(getattr(left,name),getattr(right,name),seen) for name in ("kind","fields","default","maybe_missing","singleton","class_ids","metadata","havoc"))
    if isinstance(left,dict):return left.keys()==right.keys() and all(payload_equal(left[key],right[key],seen) for key in left)
    if isinstance(left,(list,tuple)):return len(left)==len(right) and all(payload_equal(a,b,seen) for a,b in zip(left,right))
    if isinstance(left,ast.AST):return ast.dump(left,include_attributes=True)==ast.dump(right,include_attributes=True)
    return left==right


class FullStateAdapter:
    def encode(self,flow):
        state=detached_payload(flow.store)
        return BoundaryState(dict(state.cells),{key:BoundaryObject(obj.kind,dict(obj.fields),obj.default,set(obj.maybe_missing),obj.singleton,obj.class_ids,dict(obj.metadata),obj.havoc) for key,obj in state.heap.items()},dict(state.initialized),set(state.writes),tuple(state.events),state.obligations,flow.kind,flow.value,flow.exception,detached_payload(flow.continuation),detached_payload(flow.cleanups),detached_payload(flow.resume_target),state.havoc_namespaces,state.havoc_imports)
    def decode(self,boundary):
        state=Store(dict(boundary.cells),{key:HeapObject(obj.kind,dict(obj.fields),obj.default,set(obj.maybe_missing),obj.singleton,obj.class_ids,detached_payload(obj.metadata),obj.havoc) for key,obj in boundary.heap.items()},dict(boundary.initialized),set(boundary.writes),boundary.events,boundary.obligations,boundary.havoc_namespaces,boundary.havoc_imports)
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
    """Domain coarsening keeps identities and recursively transforms all values.

    This adapter changes scalar abstractions only; it never collapses object
    IDs. Address coarsening requires its own explicit remapping proof.
    """
    result=flow.store.copy();visited={}
    def payload(value):
        if isinstance(value,Value):return reabstract_value(value,operations)
        if isinstance(value,Store):
            if id(value) in visited:return visited[id(value)]
            mapped=value.copy();visited[id(value)]=mapped;transform(mapped);return mapped
        if isinstance(value,dict):return {key:payload(item) for key,item in value.items()}
        if isinstance(value,tuple):return tuple(payload(item) for item in value)
        if isinstance(value,list):return [payload(item) for item in value]
        return value
    def transform(state):
        state.cells={key:reabstract_value(value,operations) for key,value in state.cells.items()}
        for obj in state.heap.values():
            obj.fields={key:reabstract_value(value,operations) for key,value in obj.fields.items()}
            obj.default=reabstract_value(obj.default,operations);obj.metadata=payload(obj.metadata)
    transform(result)
    return replace(flow,store=result,value=reabstract_value(flow.value,operations))
