"""Immutable run-local domain policy and actual registered provider assembly."""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from hashlib import sha256
from typing import Protocol, runtime_checkable

from pythonstan.analysis.contracts.json_data import JsonObject

from .base import DomainRegistry
from .ints import IntDomain
from .strings import StringDomain


class DomainBudget(Protocol):
    @property
    def max_const_strings(self) -> int: ...
    @property
    def max_addr_set_size(self) -> int: ...
    @property
    def max_ho_tokens(self) -> int: ...


@runtime_checkable
class IntegerCapacityBudget(Protocol):
    @property
    def int_domain_max_consts(self) -> int: ...


class DomainProvider(Protocol):
    """Genuine generic registration port, used by every extra provider."""

    def register_with(self, registry: DomainRegistry) -> None: ...


@dataclass(frozen=True)
class DomainPolicy:
    max_const_strings: int = 10
    max_int_constants: int = 10
    max_addr_set_size: int = 50
    max_ho_tokens: int = 20

    def __post_init__(self) -> None:
        capacities = (
            self.max_const_strings,
            self.max_int_constants,
            self.max_addr_set_size,
            self.max_ho_tokens,
        )
        if any(type(value) is not int or value < 1 for value in capacities):
            raise ValueError("Domain capacities must be positive integers")

    @classmethod
    def from_budget(cls, budget: DomainBudget | None = None) -> DomainPolicy:
        if budget is None:
            return cls()
        integers = budget.int_domain_max_consts if isinstance(budget, IntegerCapacityBudget) else 10
        return cls(
            max_const_strings=budget.max_const_strings,
            max_int_constants=integers,
            max_addr_set_size=budget.max_addr_set_size,
            max_ho_tokens=budget.max_ho_tokens,
        )


@dataclass(frozen=True)
class DomainBundle:
    policy: DomainPolicy
    version: str
    registry: DomainRegistry

    @classmethod
    def build(
        cls,
        policy: DomainPolicy | None = None,
        additional_domains: Iterable[DomainProvider] = (),
    ) -> DomainBundle:
        policy = policy or DomainPolicy()
        registry = DomainRegistry()
        registry.register(StringDomain(max_consts=policy.max_const_strings))
        registry.register(IntDomain(max_consts=policy.max_int_constants))
        for domain in additional_domains:
            domain.register_with(registry)
        payload: JsonObject = {
            "policy": {
                "max_const_strings": policy.max_const_strings,
                "max_int_constants": policy.max_int_constants,
                "max_addr_set_size": policy.max_addr_set_size,
                "max_ho_tokens": policy.max_ho_tokens,
            },
            "domains": [domain.name for domain in registry.domains],
        }
        version = sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
        registry.freeze(version=version)
        return cls(policy, version, registry)
