"""Typed domain protocols and validated heterogeneous plugin registration.

A registry deliberately crosses element types. Each registered provider checks
its explicitly declared finite representation classes before receiving an
opaque component; concrete lattice operations remain fully generic in D.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Protocol, TypedDict, override, runtime_checkable

from pythonstan.analysis.contracts.budgets import AnalysisBudget

__all__ = ["Domain", "DomainRegistry", "Fact"]

type AddrSet = frozenset[object]
type FactPayload = (
    None
    | bool
    | int
    | float
    | str
    | frozenset[int]
    | frozenset[str]
    | tuple[int | None, int | None]
)


@dataclass(frozen=True)
class Fact:
    """Finite scalar/range/set information exchanged by actual domain hooks."""

    kind: str
    var: str | None = None
    data: FactPayload = None

    @override
    def __str__(self) -> str:
        if self.var:
            return f"{self.kind}({self.var}, {self.data})"
        return f"{self.kind}({self.data})"


@runtime_checkable
class TokenCapacityProvider(Protocol):
    """The real optional setting shared by existing higher-order providers."""

    @property
    def max_tokens(self) -> int: ...


class Domain[D](ABC):
    """A concrete typed lattice; registry erasure is validated separately."""

    @property
    @abstractmethod
    def name(self) -> str: ...

    @abstractmethod
    def bottom(self) -> D: ...

    @abstractmethod
    def top(self) -> D: ...

    @abstractmethod
    def join(self, a: D, b: D) -> D: ...

    @abstractmethod
    def meet(self, a: D, b: D) -> D: ...

    @abstractmethod
    def leq(self, a: D, b: D) -> bool: ...

    def widen(self, old: D, new: D) -> D:
        return self.join(old, new)

    def narrow(self, old: D, new: D) -> D:
        return self.meet(old, new)

    def abstract_const(self, value: object) -> D | None:
        return None

    def produce_facts(self, var: str, elem: D) -> list[Fact]:
        return []

    def consume_fact(self, elem: D, fact: Fact) -> D:
        return elem

    def on_reduce_value(
        self,
        var: str,
        addrs: AddrSet,
        elem: D,
        registry: DomainRegistry,
        budget: AnalysisBudget | None = None,
    ) -> D:
        return elem

    def is_bottom(self, elem: D) -> bool:
        return self.leq(elem, self.bottom()) and self.leq(self.bottom(), elem)

    def is_top(self, elem: D) -> bool:
        return self.leq(self.top(), elem)

    def element_types(self) -> tuple[type[D], ...]:
        """Declare all legal runtime representation variants.

        Current built-in providers each use one immutable record. A provider
        with additional union variants overrides this finite declaration; it
        must not assume that the class of bottom alone covers its values.
        """
        return (type(self.bottom()), type(self.top()))

    def decode_element(self, value: object) -> D:
        if not isinstance(value, self.element_types()):
            raise TypeError(f"Invalid element representation for domain {self.name}")
        return value

    def register_with(self, registry: DomainRegistry) -> None:
        """Preserve D while crossing a heterogeneous provider collection."""
        registry.register(self)

    def public_settings(self) -> Mapping[str, int]:
        """Finite settings exposed by the read-only registered view."""
        if isinstance(self, TokenCapacityProvider):
            capacity: object = self.max_tokens
            if not isinstance(capacity, int):
                raise TypeError("Domain token capacity must be an integer")
            return {"max_tokens": capacity}
        return {}


class RegisteredDomain(Protocol):
    """Actual erased plugin boundary; every element argument is validated."""

    @property
    def name(self) -> str: ...
    @property
    def max_consts(self) -> int: ...
    @property
    def max_prefixes(self) -> int: ...
    @property
    def max_suffixes(self) -> int: ...
    @property
    def max_tokens(self) -> int: ...
    def bottom(self) -> object: ...
    def top(self) -> object: ...
    def join(self, a: object, b: object) -> object: ...
    def meet(self, a: object, b: object) -> object: ...
    def leq(self, a: object, b: object) -> bool: ...
    def widen(self, old: object, new: object) -> object: ...
    def narrow(self, old: object, new: object) -> object: ...
    def abstract_const(self, value: object) -> object | None: ...
    def produce_facts(self, var: str, elem: object) -> list[Fact]: ...
    def consume_fact(self, elem: object, fact: Fact) -> object: ...
    def is_bottom(self, elem: object) -> bool: ...
    def is_top(self, elem: object) -> bool: ...
    def on_reduce_value(
        self,
        var: str,
        addrs: AddrSet,
        elem: object,
        registry: DomainRegistry,
        budget: AnalysisBudget | None = None,
    ) -> object: ...


@dataclass(frozen=True)
class _RegisteredDomain[D]:
    _provider: Domain[D]

    @property
    def name(self) -> str:
        return self._provider.name

    def _setting(self, name: str) -> int:
        try:
            return self._provider.public_settings()[name]
        except KeyError as error:
            raise AttributeError(name) from error

    @property
    def max_consts(self) -> int:
        return self._setting("max_consts")

    @property
    def max_prefixes(self) -> int:
        return self._setting("max_prefixes")

    @property
    def max_suffixes(self) -> int:
        return self._setting("max_suffixes")

    @property
    def max_tokens(self) -> int:
        return self._setting("max_tokens")

    def bottom(self) -> D:
        return self._provider.bottom()

    def top(self) -> D:
        return self._provider.top()

    def join(self, a: object, b: object) -> D:
        return self._provider.join(
            self._provider.decode_element(a), self._provider.decode_element(b)
        )

    def meet(self, a: object, b: object) -> D:
        return self._provider.meet(
            self._provider.decode_element(a), self._provider.decode_element(b)
        )

    def leq(self, a: object, b: object) -> bool:
        return self._provider.leq(
            self._provider.decode_element(a), self._provider.decode_element(b)
        )

    def widen(self, old: object, new: object) -> D:
        return self._provider.widen(
            self._provider.decode_element(old), self._provider.decode_element(new)
        )

    def narrow(self, old: object, new: object) -> D:
        return self._provider.narrow(
            self._provider.decode_element(old), self._provider.decode_element(new)
        )

    def abstract_const(self, value: object) -> D | None:
        return self._provider.abstract_const(value)

    def produce_facts(self, var: str, elem: object) -> list[Fact]:
        return self._provider.produce_facts(var, self._provider.decode_element(elem))

    def consume_fact(self, elem: object, fact: Fact) -> D:
        return self._provider.consume_fact(self._provider.decode_element(elem), fact)

    def is_bottom(self, elem: object) -> bool:
        return self._provider.is_bottom(self._provider.decode_element(elem))

    def is_top(self, elem: object) -> bool:
        return self._provider.is_top(self._provider.decode_element(elem))

    def on_reduce_value(
        self,
        var: str,
        addrs: AddrSet,
        elem: object,
        registry: DomainRegistry,
        budget: AnalysisBudget | None = None,
    ) -> D:
        return self._provider.on_reduce_value(
            var, addrs, self._provider.decode_element(elem), registry, budget
        )


class RegistryState(TypedDict):
    domains: dict[str, RegisteredDomain]
    frozen: bool
    version: str | None


class DomainRegistry:
    """Own provider membership and validate every heterogeneous component use."""

    _domains: Mapping[str, RegisteredDomain]
    _frozen: bool
    version: str | None

    def __init__(self) -> None:
        self._domains = {}
        self._frozen = False
        self.version = None

    def register[D](self, domain: Domain[D]) -> None:
        if self._frozen:
            raise TypeError("DomainRegistry is immutable after bundle construction")
        registered: _RegisteredDomain[D] = _RegisteredDomain(domain)
        self._domains = {**self._domains, domain.name: registered}

    def freeze(self, version: str | None = None) -> DomainRegistry:
        if not self._frozen:
            self._domains = MappingProxyType(dict(self._domains))
            self.version = version
            self._frozen = True
        return self

    @override
    def __getstate__(self) -> RegistryState:
        return {"domains": dict(self._domains), "frozen": self._frozen, "version": self.version}

    def __setstate__(self, state: RegistryState) -> None:
        self.version = state["version"]
        self._frozen = state["frozen"]
        self._domains = MappingProxyType(state["domains"]) if self._frozen else state["domains"]

    def get(self, name: str) -> RegisteredDomain | None:
        return self._domains.get(name)

    @property
    def domains(self) -> list[RegisteredDomain]:
        return list(self._domains.values())

    def reduce(self, var: str, components: dict[str, object]) -> dict[str, object]:
        facts: list[Fact] = []
        for name, elem in components.items():
            domain = self._domains.get(name)
            if domain:
                facts.extend(domain.produce_facts(var, elem))
        if not facts:
            return components
        reduced: dict[str, object] = {}
        for name, elem in components.items():
            domain = self._domains.get(name)
            if domain:
                for fact in facts:
                    elem = domain.consume_fact(elem, fact)
            reduced[name] = elem
        return reduced

    def reduce_value(
        self,
        var: str,
        addrs: AddrSet,
        components: Mapping[str, object],
        budget: AnalysisBudget | None = None,
        scalar_kinds: frozenset[str] | None = None,
        has_object_alternatives: bool = False,
    ) -> dict[str, object]:
        reduced = dict(components)
        if scalar_kinds is not None and len(scalar_kinds) == 1 and not has_object_alternatives:
            name = next(iter(scalar_kinds))
            source = self._domains.get(name)
            facts = source.produce_facts(var, components[name]) if source else []
            for target_name, elem in list(reduced.items()):
                target = self._domains.get(target_name)
                if target:
                    for fact in facts:
                        elem = target.consume_fact(elem, fact)
                    reduced[target_name] = elem
        for name, elem in reduced.items():
            domain = self._domains.get(name)
            if domain:
                reduced[name] = domain.on_reduce_value(var, addrs, elem, self, budget)
        return reduced
