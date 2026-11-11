"""Detached semantic graph transformations, independent of composition engines.

Values and source AST are immutable. Mutable stores/metadata are copied with one
memo so aliases and cycles in suspended continuations remain aliases and cycles.
Literal target string keys are never interpreted as host object references.
"""
from __future__ import annotations
import ast
from dataclasses import fields,is_dataclass
from enum import Enum
from typing import Mapping
from pythonstan.analysis.domains.values import Value
from pythonstan.analysis.domains.reabstract import resolve_mapping,ReferenceKey

_ADDRESS_METADATA=frozenset({'function','owner','object','objid','class_id','receiver_id','iterator_id','namespace_owner'})


def semantic_payload_equal(left,right,seen=None):
    if isinstance(left,Value) or isinstance(right,Value):return type(left) is type(right) and left==right
    if left is right:return True
    if type(left) is not type(right):return False
    seen=set() if seen is None else seen
    pair=id(left),id(right)
    if pair in seen:return True
    seen.add(pair)
    if isinstance(left,dict):return left.keys()==right.keys() and all(semantic_payload_equal(left[k],right[k],seen) for k in left)
    if isinstance(left,(tuple,list)):return len(left)==len(right) and all(semantic_payload_equal(a,b,seen) for a,b in zip(left,right))
    if isinstance(left,ast.AST):return ast.dump(left,include_attributes=True)==ast.dump(right,include_attributes=True)
    if is_dataclass(left):return all(semantic_payload_equal(getattr(left,f.name),getattr(right,f.name),seen) for f in fields(left) if f.name!='identity_epoch')
    return left==right


def payload_signature(value,seen=None):
    """Hashable continuation identity; never hashes mutable Store directly."""
    seen={} if seen is None else seen
    if isinstance(value,Value):return ('value',value)
    if value is None or isinstance(value,(str,int,bool,bytes,Enum)):return (type(value).__name__,value)
    if isinstance(value,float):
        import struct
        return ('float',struct.pack('!d',value))
    if isinstance(value,ast.AST):return ('ast',ast.dump(value,include_attributes=True))
    if id(value) in seen:return ('cycle',seen[id(value)])
    seen[id(value)]=len(seen)
    if isinstance(value,dict):return ('dict',tuple((payload_signature(k,seen),payload_signature(v,seen)) for k,v in sorted(value.items(),key=lambda pair:repr(pair[0]))))
    if isinstance(value,(tuple,list)):return (type(value).__name__,tuple(payload_signature(x,seen) for x in value))
    if isinstance(value,(set,frozenset)):return (type(value).__name__,tuple(sorted((payload_signature(x,seen) for x in value),key=repr)))
    if is_dataclass(value):return (type(value).__name__,tuple((f.name,payload_signature(getattr(value,f.name),seen)) for f in fields(value) if f.name!='identity_epoch'))
    raise TypeError('Unrepresentable semantic payload: '+type(value).__name__)


class PayloadTransformer:
    def __init__(self,mapping=None,ops=None,scalar=None,epoch=None,memo=None,charge=None):
        self.mapping={} if mapping is None else mapping;self.ops=ops;self.scalar=scalar;self.epoch=epoch
        self.memo={} if memo is None else memo;self.work=0;self.charge=charge

    def address(self,identifier):return resolve_mapping(identifier,self.mapping)

    def key(self,key):
        if isinstance(key,Value):return self.visit(key)
        if isinstance(key,ReferenceKey):return ReferenceKey(self.address(key.identifier))
        if isinstance(key,tuple):return tuple(self.key(x) for x in key)
        # Plain strings, enum labels and numeric dictionary keys are literals.
        return key

    def metadata(self,data):
        if id(data) in self.memo:return self.memo[id(data)]
        result={};self.memo[id(data)]=result
        for key,value in data.items():
            if key in _ADDRESS_METADATA and isinstance(value,str):mapped=self.address(value)
            elif key in {'mro','bases','class_ids'} and isinstance(value,(list,tuple,set,frozenset)):
                mapped=type(value)(self.address(item) if isinstance(item,str) else self.visit(item) for item in value)
            elif key=='closure' and isinstance(value,(list,tuple)):
                mapped=type(value)((name,self.address(cell)) for name,cell in value)
            else:mapped=self.visit(value)
            result[self.key(key)]=mapped
        return result

    def visit(self,value):
        from .state import Store,HeapObject,Frame,FunctionInfo
        self.work+=1
        if self.charge is not None:self.charge(1)
        if isinstance(value,ReferenceKey):return ReferenceKey(self.address(value.identifier))
        if isinstance(value,Value):
            result=self.ops.remap(value,self.mapping) if self.ops is not None and self.mapping else value
            return self.scalar(result) if self.scalar is not None else result
        if value is None or isinstance(value,(str,int,float,bool,bytes,Enum,ast.AST)):return value
        if id(value) in self.memo:return self.memo[id(value)]
        if isinstance(value,Store):
            result=object.__new__(Store);self.memo[id(value)]=result
            result.cells={self.address(k):self.visit(v) for k,v in value.cells.items()}
            result.heap={}
            for old,obj in value.heap.items():
                new=self.address(old);mapped=self.visit(obj)
                if new!=old:mapped.singleton=False
                if new in result.heap:
                    if self.ops is None:raise ValueError('Address collision requires domain operations')
                    mapped=result.heap[new].join(mapped,self.ops);mapped.singleton=False
                result.heap[new]=mapped
            result.initialized=dict(value.initialized)
            result.writes={(kind,self.address(owner) if kind in {'heap','heap_default','heap_meta','cell'} else owner,self.key(key)) for kind,owner,key in value.writes}
            result.events=self.visit(value.events);result.obligations=value.obligations
            result.havoc_namespaces=value.havoc_namespaces;result.havoc_imports=value.havoc_imports
            result.guards={} if self.mapping else self.visit(getattr(value,'guards',{}))
            result.identity_epoch=getattr(value,'identity_epoch',0) if self.epoch is None else self.epoch
            return result
        if isinstance(value,HeapObject):
            result=object.__new__(HeapObject);self.memo[id(value)]=result
            result.kind=value.kind;result.fields={self.key(k):self.visit(v) for k,v in value.fields.items()}
            result.default=self.visit(value.default);result.maybe_missing={self.key(k) for k in value.maybe_missing}
            result.singleton=value.singleton;result.class_ids=frozenset(self.address(k) for k in value.class_ids)
            result.metadata=self.metadata(value.metadata);result.havoc=value.havoc
            return result
        if isinstance(value,Frame):
            result=object.__new__(Frame);self.memo[id(value)]=result
            for f in fields(value):
                item=getattr(value,f.name)
                if f.name=='closure':item=tuple((name,self.address(cell)) for name,cell in item)
                else:item=self.visit(item)
                object.__setattr__(result,f.name,item)
            return result
        if isinstance(value,FunctionInfo):
            result=object.__new__(FunctionInfo);self.memo[id(value)]=result
            for f in fields(value):
                item=getattr(value,f.name)
                if f.name=='identifier':item=self.address(item)
                elif f.name=='closure':item=tuple((name,self.address(cell)) for name,cell in item)
                else:item=self.visit(item)
                setattr(result,f.name,item)
            return result
        if isinstance(value,dict):
            result={};self.memo[id(value)]=result
            for key,item in value.items():result[self.key(key)]=self.visit(item)
            return result
        if isinstance(value,list):
            result=[];self.memo[id(value)]=result;result.extend(self.visit(item) for item in value);return result
        if isinstance(value,tuple):
            items=[self.visit(item) for item in value]
            # A tuple reached again through a mutable cycle may already have
            # been installed by the recursive visit, as in copy.deepcopy.
            if id(value) in self.memo:return self.memo[id(value)]
            result=tuple(items);self.memo[id(value)]=result;return result
        if isinstance(value,set):
            result=set();self.memo[id(value)]=result;result.update(self.visit(item) for item in value);return result
        if isinstance(value,frozenset):
            result=frozenset(self.visit(item) for item in value);self.memo[id(value)]=result;return result
        if is_dataclass(value):
            result=object.__new__(type(value));self.memo[id(value)]=result
            for f in fields(value):object.__setattr__(result,f.name,self.visit(getattr(value,f.name)))
            return result
        raise TypeError('Unrepresentable semantic payload: '+type(value).__name__)


def detached_payload(value,memo=None):return PayloadTransformer(memo=memo).visit(value)
def remap_payload(value,mapping,ops,*,epoch=None,memo=None):return PayloadTransformer(mapping,ops,epoch=epoch,memo=memo).visit(value)
def remap_store(store,mapping,ops,*,epoch=None,memo=None):return remap_payload(store,mapping,ops,epoch=epoch,memo=memo)
def equivalent(left,right):return semantic_payload_equal(left,right)
