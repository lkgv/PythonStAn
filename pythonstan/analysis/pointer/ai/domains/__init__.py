"""Pluggable value domains for abstract interpretation.

This package provides modular abstract domains that can be composed
into a reduced product for value abstraction.

Domains:
- base: Domain interface and AbsVal reduced product
- strings: String domain for reflective attribute names
- containers: Container summaries (list/dict/set/tuple)
- bool: Boolean domain for branch refinement
"""

from .base import Domain, DomainRegistry
from .strings import StringDomain, StrLattice
from .containers import ContainerDomain
from .bool import BoolDomain, BoolLattice

__all__ = [
    "Domain",
    "DomainRegistry",
    "StringDomain",
    "StrLattice",
    "ContainerDomain",
    "BoolDomain",
    "BoolLattice",
]

