"""Compatibility registry factory; pure protocols live in analysis.domains."""
from typing import *
from pythonstan.analysis.domains.base import Domain, Fact, DomainRegistry

# =============================================================================
# Default registry with standard domains
# =============================================================================

def create_default_registry(budget=None) -> DomainRegistry:
    """Construct one sealed run-local compatibility bundle registry."""
    from pythonstan.analysis.domains.bundle import DomainBundle, DomainPolicy
    from .bool import BoolDomain
    from .none import NoneDomain
    from .callable import CallableDomain
    from .type_domain import TypeDomain
    from .descriptor import DescriptorDomain
    policy = DomainPolicy.from_budget(budget)
    providers = (BoolDomain(), NoneDomain(),
                 CallableDomain(max_tokens=policy.max_ho_tokens),
                 TypeDomain(max_tokens=policy.max_ho_tokens),
                 DescriptorDomain(max_tokens=policy.max_ho_tokens))
    return DomainBundle.build(policy, providers).registry
