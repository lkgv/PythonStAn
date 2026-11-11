"""Compatibility registry factory; pure protocols live in analysis.domains."""
from typing import *
from pythonstan.analysis.domains.base import Domain, Fact, DomainRegistry

# =============================================================================
# Default registry with standard domains
# =============================================================================

def create_default_registry(budget=None) -> DomainRegistry:
    """Construct one sealed run-local compatibility bundle registry."""
    from pythonstan.analysis.domains.bundle import DomainBundle, DomainPolicy
    from pythonstan.analysis.domains.bool import BoolDomain
    from pythonstan.analysis.domains.none import NoneDomain
    from pythonstan.compat.legacy_ai.domains.callable import CallableDomain
    from pythonstan.compat.legacy_ai.domains.type_domain import TypeDomain
    from pythonstan.compat.legacy_ai.domains.descriptor import DescriptorDomain
    policy = DomainPolicy.from_budget(budget)
    providers = (BoolDomain(), NoneDomain(),
                 CallableDomain(max_tokens=policy.max_ho_tokens),
                 TypeDomain(max_tokens=policy.max_ho_tokens),
                 DescriptorDomain(max_tokens=policy.max_ho_tokens))
    return DomainBundle.build(policy, providers).registry
