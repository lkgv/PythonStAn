"""Immutable, run-local domain policy and registry assembly.

Concrete engines inject additional domain providers; this module never imports
an engine. Domain capacities differ from transfer enumeration budgets.
"""
from dataclasses import dataclass
from hashlib import sha256
import json
from typing import Iterable

from .base import DomainRegistry
from .ints import IntDomain
from .strings import StringDomain


@dataclass(frozen=True)
class DomainPolicy:
    max_const_strings: int = 10
    max_int_constants: int = 10
    max_addr_set_size: int = 50
    max_ho_tokens: int = 20

    def __post_init__(self):
        if any(type(v) is not int or v < 1 for v in self.__dict__.values()):
            raise ValueError('Domain capacities must be positive integers')

    @classmethod
    def from_budget(cls, budget=None):
        return cls(max_const_strings=getattr(budget, 'max_const_strings', 10),
                   max_int_constants=getattr(budget, 'int_domain_max_consts', 10),
                   max_addr_set_size=getattr(budget, 'max_addr_set_size', 50),
                   max_ho_tokens=getattr(budget, 'max_ho_tokens', 20))


@dataclass(frozen=True)
class DomainBundle:
    policy: DomainPolicy
    version: str
    registry: DomainRegistry

    @classmethod
    def build(cls, policy=None, additional_domains: Iterable = ()):
        policy = policy or DomainPolicy()
        registry = DomainRegistry()
        registry.register(StringDomain(max_consts=policy.max_const_strings))
        registry.register(IntDomain(max_consts=policy.max_int_constants))
        for domain in additional_domains:
            registry.register(domain)
        payload = {'policy': policy.__dict__, 'domains': [d.name for d in registry.domains]}
        version = sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
        registry.freeze(version=version)
        return cls(policy, version, registry)
