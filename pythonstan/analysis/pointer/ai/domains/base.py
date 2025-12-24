"""Base domain interface and registry for pluggable abstractions.

This module defines:
- Domain: Abstract base class for value domains
- DomainRegistry: Registration and coordination of domain plugins
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import (
    TYPE_CHECKING,
    Any,
    Callable,
    Dict,
    FrozenSet,
    Generic,
    List,
    Optional,
    Set,
    Tuple,
    TypeVar,
    Union,
)

if TYPE_CHECKING:
    from ..state import AbsVal, AIState

__all__ = ["Domain", "DomainRegistry", "Fact"]


# =============================================================================
# Fact - Inter-domain communication
# =============================================================================

@dataclass(frozen=True)
class Fact:
    """A fact for inter-domain communication (reduction).
    
    Facts are produced by one domain and can be consumed by others
    to enable sound communication in a reduced product.
    
    Examples:
        - Fact("is_const_str", var="x", value="foo")
        - Fact("is_none", var="x")
        - Fact("in_range", var="x", lo=0, hi=10)
    
    Attributes:
        kind: Type of fact
        var: Variable the fact is about (optional)
        data: Additional data
    """
    kind: str
    var: Optional[str] = None
    data: Any = None

    def __str__(self) -> str:
        if self.var:
            return f"{self.kind}({self.var}, {self.data})"
        return f"{self.kind}({self.data})"


# =============================================================================
# Domain - Abstract base class for value domains
# =============================================================================

D = TypeVar('D')  # Domain element type


class Domain(ABC, Generic[D]):
    """Abstract base class for pluggable value domains.
    
    A domain must implement:
    - Lattice operations (bottom, top, join, meet, leq, widen, narrow)
    - Optional transfer hooks for specific IR statements
    - Optional fact production/consumption for reductions
    
    Type parameter D is the domain's element type.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Domain name for identification."""
        pass

    # -------------------------------------------------------------------------
    # Lattice operations
    # -------------------------------------------------------------------------

    @abstractmethod
    def bottom(self) -> D:
        """Return bottom element (⊥)."""
        pass

    @abstractmethod
    def top(self) -> D:
        """Return top element (⊤)."""
        pass

    @abstractmethod
    def join(self, a: D, b: D) -> D:
        """Join (⊔): least upper bound."""
        pass

    @abstractmethod
    def meet(self, a: D, b: D) -> D:
        """Meet (⊓): greatest lower bound."""
        pass

    @abstractmethod
    def leq(self, a: D, b: D) -> bool:
        """Partial order: a ⊑ b."""
        pass

    def widen(self, old: D, new: D) -> D:
        """Widening (∇): accelerate convergence.
        
        Default implementation just joins.
        """
        return self.join(old, new)

    def narrow(self, old: D, new: D) -> D:
        """Narrowing (△): refine after widening.
        
        Default implementation just meets.
        """
        return self.meet(old, new)

    # -------------------------------------------------------------------------
    # Value abstraction
    # -------------------------------------------------------------------------

    def abstract_const(self, value: Any) -> Optional[D]:
        """Abstract a constant value.
        
        Returns None if this domain doesn't handle this type.
        """
        return None

    # -------------------------------------------------------------------------
    # Fact production/consumption (reduction)
    # -------------------------------------------------------------------------

    def produce_facts(self, var: str, elem: D) -> List[Fact]:
        """Produce facts about a variable's domain element.
        
        Called during reduction to share information with other domains.
        """
        return []

    def consume_fact(self, elem: D, fact: Fact) -> D:
        """Consume a fact to refine this domain's element.
        
        Called during reduction to receive information from other domains.
        """
        return elem

    # -------------------------------------------------------------------------
    # Utilities
    # -------------------------------------------------------------------------

    def is_bottom(self, elem: D) -> bool:
        """Check if element is bottom."""
        return self.leq(elem, self.bottom()) and self.leq(self.bottom(), elem)

    def is_top(self, elem: D) -> bool:
        """Check if element is top."""
        return self.leq(self.top(), elem)


# =============================================================================
# DomainRegistry - Coordination of domain plugins
# =============================================================================

class DomainRegistry:
    """Registry for domain plugins.
    
    Manages a collection of domains and coordinates:
    - Registration of domains
    - Reduction (fact exchange between domains)
    - Widening/narrowing coordination
    """

    def __init__(self):
        """Initialize empty registry."""
        self._domains: Dict[str, Domain] = {}

    def register(self, domain: Domain) -> None:
        """Register a domain plugin."""
        self._domains[domain.name] = domain

    def get(self, name: str) -> Optional[Domain]:
        """Get domain by name."""
        return self._domains.get(name)

    @property
    def domains(self) -> List[Domain]:
        """Get all registered domains."""
        return list(self._domains.values())

    def reduce(self, var: str, components: Dict[str, Any]) -> Dict[str, Any]:
        """Run reduction: exchange facts between domains.
        
        Args:
            var: Variable name
            components: {domain_name: element} mapping
            
        Returns:
            Refined components after fact exchange
        """
        # Collect all facts
        all_facts: List[Fact] = []
        for domain_name, elem in components.items():
            domain = self._domains.get(domain_name)
            if domain:
                facts = domain.produce_facts(var, elem)
                all_facts.extend(facts)

        # If no facts, nothing to reduce
        if not all_facts:
            return components

        # Distribute facts to all domains
        new_components: Dict[str, Any] = {}
        for domain_name, elem in components.items():
            domain = self._domains.get(domain_name)
            if domain:
                refined = elem
                for fact in all_facts:
                    refined = domain.consume_fact(refined, fact)
                new_components[domain_name] = refined
            else:
                new_components[domain_name] = elem

        return new_components


# =============================================================================
# Default registry with standard domains
# =============================================================================

def create_default_registry() -> DomainRegistry:
    """Create a registry with default domains (strings, containers, bool)."""
    from .strings import StringDomain
    from .containers import ContainerDomain
    from .bool import BoolDomain

    registry = DomainRegistry()
    registry.register(StringDomain())
    registry.register(ContainerDomain())
    registry.register(BoolDomain())
    return registry

