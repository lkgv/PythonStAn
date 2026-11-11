"""Finite may relations carried by individual abstract values.

Tokens relate one dictionary key alternative to one source-bound handler. They
are data, never certificates. Cap overflow coarsens to the Cartesian product,
retaining all old pairs; it cannot turn missing observations into exclusions.
"""
from __future__ import annotations
from dataclasses import dataclass
from pythonstan.analysis.contracts import stable_digest

@dataclass(frozen=True)
class PartitionToken:
    point:str
    key_type:str
    key_repr:str
    handler:str
    partitioned:bool=True
    @classmethod
    def make(cls,point,key,handler,partitioned=True):return cls(point,type(key).__name__,repr(key),handler,partitioned)
    @property
    def identity(self):return stable_digest((self.point,self.key_type,self.key_repr,self.handler,self.partitioned))
    def export(self):return {"token":self.identity,"point":self.point,"key_type":self.key_type,"key":self.key_repr,"handler":self.handler,"partitioned":self.partitioned}

@dataclass(frozen=True)
class KeyValueRelation:
    tokens:frozenset[PartitionToken]=frozenset()
    def join(self,other):return KeyValueRelation(self.tokens|other.tokens)
    def coarsen(self,cap):
        if len(self.tokens)<=cap:return self
        points={token.point for token in self.tokens};result=set()
        for point in points:
            group=[token for token in self.tokens if token.point==point]
            for key in {(token.key_type,token.key_repr) for token in group}:
                for handler in {token.handler for token in group}:result.add(PartitionToken(point,*key,handler,False))
        return KeyValueRelation(frozenset(result))
    def export(self):return [token.export() for token in sorted(self.tokens,key=lambda x:x.identity)]
