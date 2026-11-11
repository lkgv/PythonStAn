"""Run-local identity substitutions shared by every domain in one session.

Preparing a substitution is pure.  Publication replaces the complete alias map
and increments one epoch, so an interrupted preparation cannot expose half of
an address quotient to the solver.
"""
from __future__ import annotations
from dataclasses import dataclass


@dataclass(frozen=True)
class ReferenceKey:
    """Opaque host reference key, distinct from every target tuple/string."""
    identifier: str



def resolve_mapping(identifier, mapping):
    seen=set();current=identifier
    while current in mapping and mapping[current]!=current:
        if current in seen:raise ValueError("Cyclic address substitution")
        seen.add(current);current=mapping[current]
    return current


class IdentityArena:
    def __init__(self):
        self.aliases={};self.epoch=0;self.summaries=set()

    def resolve(self, identifier):
        return resolve_mapping(identifier,self.aliases)

    def prepare(self, mapping):
        if any(type(key) is not str or type(value) is not str for key,value in mapping.items()):
            raise ValueError("Address substitutions require opaque string identities")
        combined=dict(self.aliases)
        for old,new in mapping.items():
            old=self.resolve(old);new=self.resolve(new)
            if old!=new:combined[old]=new
        # Resolve *all* existing aliases after adding the new quotient.
        resolved={}
        for old in combined:
            path=[];seen=set();current=old
            while current in combined and combined[current]!=current and current not in resolved:
                if current in seen:raise ValueError("Cyclic address substitution")
                seen.add(current);path.append(current);current=combined[current]
            target=resolved.get(current,current)
            for alias in path:resolved[alias]=target
        return {old:new for old,new in resolved.items() if old!=new}

    def publish(self, prepared, *, summaries=None):
        if prepared==self.aliases:return self.epoch
        # Callers performing a host transaction pass preallocated summaries;
        # the convenience path also allocates before changing any live field.
        if summaries is None:summaries=self.summaries|set(prepared.values())
        next_epoch=self.epoch+1
        self.aliases=prepared;self.summaries=summaries;self.epoch=next_epoch
        return next_epoch

    def is_summary(self, identifier):
        return self.resolve(identifier) in self.summaries
