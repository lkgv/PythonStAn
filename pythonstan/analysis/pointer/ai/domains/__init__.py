"""Pluggable value domains for abstract interpretation.

This package provides modular abstract domains that can be composed
into a reduced product for value abstraction.

Domains:
- base: Domain interface and registry
- strings: String domain for reflective attribute names
- bool: Boolean domain for branch refinement
- none: None-ness domain for null checks
- callable: Callable tokens (functions, methods, bound methods)
- type_domain: Type/class tokens
- descriptor: Descriptor tokens with semantic wrappers

Semantics services:
- attributes: Python attribute resolution protocol (MRO, descriptors, hooks)
- builtins: Builtin attribute function semantics (getattr/setattr/hasattr)
- binding: Callable target extraction and descriptor binding
- calls: Call dispatch semantics (method lookup, class __call__, receiver binding)
- subscripts: Subscript access semantics (load/store with bucket management)
- conditions: Condition refinement for value partitioning

Enhanced modeling:
- mapslots: Unified namespace/mapping abstraction with presence tracking
- classinfo: Enhanced class/type modeling with namespace, bases, metaclass
- kwargs: Keyword argument shape modeling for call precision
- tuples: Tuple shape service for *args and sequence modeling
- scopes: Scope/closure semantics for first-class functions (globals/locals/nonlocal/cell)
"""

from .base import Domain, DomainRegistry
from .strings import StringDomain, StrLattice
from .bool import BoolDomain, BoolLattice
from .none import NoneDomain, NoneLattice
from .callable import CallableDomain, CallableVal, FunctionRef, BoundMethodRef
from .type_domain import TypeDomain, TypeVal, ClassRef
from .descriptor import (
    DescriptorDomain, DescriptorVal, DescriptorRef, DescriptorType,
    PropertyRef, ClassMethodRef, StaticMethodRef, DescriptorSemantics,
)
from .attributes import AttributeSemantics
from .builtins import BuiltinAttributeSemantics
from .binding import to_callable_targets, bind_descriptor
from .calls import CallSemantics, CallDispatchResult
from .subscripts import SubscriptSemantics, SubscriptLoadResult
from .conditions import ConditionRefiner

# Enhanced modeling imports - unified SlotMap API
from .mapslots import (
    SlotMap, Slot, Presence, KeySig, LoadResult,
    SlotMapSemantics, DictSemantics, KeyDecomposer,
    make_str_keysig, make_int_keysig, keysig_kind, keysig_value,
)
from .classinfo import ClassInfo, ClassInfoCache, get_class_info, create_dynamic_class_info
from .kwargs import KwShape, KwShapeDisj, ArgKwShape, materialize_kwargs_shape, expand_kwargs
from .tuples import TupleExact, TupleSummary, TupleDisj, materialize_tuple_disj, expand_starred_arg
from .scopes import ScopeSemantics, CellStore, ScopeBinding, NameKind

__all__ = [
    # Base
    "Domain",
    "DomainRegistry",
    # String domain
    "StringDomain",
    "StrLattice",
    # Bool domain
    "BoolDomain",
    "BoolLattice",
    # None domain
    "NoneDomain",
    "NoneLattice",
    # Callable domain
    "CallableDomain",
    "CallableVal",
    "FunctionRef",
    "BoundMethodRef",
    # Type domain
    "TypeDomain",
    "TypeVal",
    "ClassRef",
    # Descriptor domain (extended)
    "DescriptorDomain",
    "DescriptorVal",
    "DescriptorRef",
    "DescriptorType",
    "PropertyRef",
    "ClassMethodRef",
    "StaticMethodRef",
    "DescriptorSemantics",
    # Semantics services
    "AttributeSemantics",
    "BuiltinAttributeSemantics",
    # Binding operations
    "to_callable_targets",
    "bind_descriptor",
    # Call semantics
    "CallSemantics",
    "CallDispatchResult",
    # Subscript semantics
    "SubscriptSemantics",
    "SubscriptLoadResult",
    # Condition refinement
    "ConditionRefiner",
    "IteratorBuiltinsSemantics",
    "GeneratorSemantics",
    "is_generator_function",
    # SlotMap (unified namespace/dict abstraction)
    "SlotMap",
    "Slot",
    "Presence",
    "KeySig",
    "LoadResult",
    "SlotMapSemantics",
    "DictSemantics",
    "KeyDecomposer",
    "make_str_keysig",
    "make_int_keysig",
    "keysig_kind",
    "keysig_value",
    # ClassInfo (enhanced type modeling)
    "ClassInfo",
    "ClassInfoCache",
    "get_class_info",
    "create_dynamic_class_info",
    # KwShape (kwargs modeling)
    "KwShape",
    "KwShapeDisj",
    "ArgKwShape",
    "materialize_kwargs_shape",
    "expand_kwargs",
    # Tuple shape
    "TupleExact",
    "TupleSummary",
    "TupleDisj",
    "materialize_tuple_disj",
    "expand_starred_arg",
    # Scope/closure semantics
    "ScopeSemantics",
    "CellStore",
    "ScopeBinding",
    "NameKind",
]

