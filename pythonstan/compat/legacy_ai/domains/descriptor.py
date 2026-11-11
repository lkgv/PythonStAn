"""Descriptor domain for descriptor-like tokens.

This domain tracks descriptor objects for descriptor protocol modeling.
Extended to support semantic interpretation of common descriptor wrappers:
- property: fget/fset/fdel function references
- classmethod: underlying function + binds to class
- staticmethod: underlying function, no binding
- generic descriptors: __get__/__set__/__delete__ protocol

Key features:
- DescriptorType enum identifies known descriptor kinds
- DescriptorRef includes type and wrapped function info
- DescriptorSemantics service interprets descriptors during attribute access
- Integration with CallableDomain for bound method creation

Design notes:
- Descriptor interpretation happens in semantics layer, not interpreter
- Budget caps limit descriptor token explosion
- Unknown descriptors fall back to protocol-based resolution
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any, Callable, FrozenSet, List, Optional, Set, Tuple, Union

from pythonstan.compat.legacy_ai.domains.base import Domain, DomainRegistry

if TYPE_CHECKING:
    from pythonstan.analysis.pointer.kcfa.object import AbstractObject
    from pythonstan.compat.legacy_ai.value_domain import AddrSet, AbsVal
    from pythonstan.compat.legacy_ai.api import AnalysisBudget
    from pythonstan.compat.legacy_ai.summary import SummaryAccumulator

__all__ = [
    "DescriptorDomain",
    "DescriptorVal",
    "DescriptorKind",
    "DescriptorType",
    "DescriptorRef",
    "PropertyRef",
    "ClassMethodRef",
    "StaticMethodRef",
    "DescriptorSemantics",
    "create_descriptor_val_for_method",
    "is_known_descriptor_method",
]


class DescriptorKind(Enum):
    """Stratification for descriptor values (lattice structure)."""

    BOTTOM = "bottom"
    SET = "set"
    TOP = "top"


class DescriptorType(Enum):
    """Type of descriptor wrapper.
    
    Identifies known descriptor patterns for semantic interpretation.
    """
    GENERIC = "generic"        # Unknown descriptor with __get__/__set__
    PROPERTY = "property"      # property(fget, fset, fdel)
    CLASSMETHOD = "classmethod"  # classmethod(func)
    STATICMETHOD = "staticmethod"  # staticmethod(func)
    SLOT_WRAPPER = "slot_wrapper"  # Built-in slot wrappers
    METHOD_WRAPPER = "method_wrapper"  # Built-in method descriptors


@dataclass(frozen=True)
class DescriptorRef:
    """Token for generic descriptor objects.
    
    Represents descriptors identified only by their protocol methods.
    """
    obj: "AbstractObject"
    has_get: bool = False
    has_set: bool = False
    has_delete: bool = False
    desc_type: DescriptorType = DescriptorType.GENERIC
    get_addrs: FrozenSet["AbstractObject"] = field(default_factory=frozenset)
    set_addrs: FrozenSet["AbstractObject"] = field(default_factory=frozenset)
    delete_addrs: FrozenSet["AbstractObject"] = field(default_factory=frozenset)

    @property
    def is_data_descriptor(self) -> bool:
        """Data descriptors have __set__ or __delete__."""
        return self.has_set or self.has_delete


@dataclass(frozen=True)
class PropertyRef:
    """Token for property descriptor.
    
    Tracks the underlying fget/fset/fdel functions for precise invocation.
    """
    obj: "AbstractObject"
    fget_addrs: FrozenSet["AbstractObject"] = field(default_factory=frozenset)
    fset_addrs: FrozenSet["AbstractObject"] = field(default_factory=frozenset)
    fdel_addrs: FrozenSet["AbstractObject"] = field(default_factory=frozenset)
    
    @property
    def desc_type(self) -> DescriptorType:
        return DescriptorType.PROPERTY
    
    @property
    def has_get(self) -> bool:
        return len(self.fget_addrs) > 0
    
    @property
    def has_set(self) -> bool:
        return len(self.fset_addrs) > 0
    
    @property
    def has_delete(self) -> bool:
        return len(self.fdel_addrs) > 0
    
    @property
    def is_data_descriptor(self) -> bool:
        return self.has_set or self.has_delete


@dataclass(frozen=True)
class ClassMethodRef:
    """Token for classmethod descriptor.
    
    Tracks the underlying function that will be bound to the class.
    """
    obj: "AbstractObject"
    func_addrs: FrozenSet["AbstractObject"] = field(default_factory=frozenset)
    
    @property
    def desc_type(self) -> DescriptorType:
        return DescriptorType.CLASSMETHOD
    
    @property
    def has_get(self) -> bool:
        return True  # classmethod always has __get__
    
    @property
    def has_set(self) -> bool:
        return False
    
    @property
    def has_delete(self) -> bool:
        return False
    
    @property
    def is_data_descriptor(self) -> bool:
        return False


@dataclass(frozen=True)
class StaticMethodRef:
    """Token for staticmethod descriptor.
    
    Tracks the underlying function that has no binding.
    """
    obj: "AbstractObject"
    func_addrs: FrozenSet["AbstractObject"] = field(default_factory=frozenset)
    
    @property
    def desc_type(self) -> DescriptorType:
        return DescriptorType.STATICMETHOD
    
    @property
    def has_get(self) -> bool:
        return True  # staticmethod always has __get__
    
    @property
    def has_set(self) -> bool:
        return False
    
    @property
    def has_delete(self) -> bool:
        return False
    
    @property
    def is_data_descriptor(self) -> bool:
        return False


# Union of all descriptor token types
DescriptorToken = Union[DescriptorRef, PropertyRef, ClassMethodRef, StaticMethodRef]


@dataclass(frozen=True)
class DescriptorVal:
    """Abstract descriptor value.
    
    Tracks descriptor tokens with type information for semantic interpretation.
    """

    kind: DescriptorKind = DescriptorKind.BOTTOM
    tokens: FrozenSet[DescriptorToken] = field(default_factory=frozenset)

    @classmethod
    def bottom(cls) -> "DescriptorVal":
        return cls(DescriptorKind.BOTTOM, frozenset())

    @classmethod
    def top(cls) -> "DescriptorVal":
        return cls(DescriptorKind.TOP, frozenset())

    @classmethod
    def from_tokens(cls, tokens: FrozenSet[DescriptorToken]) -> "DescriptorVal":
        if len(tokens) == 0:
            return cls.bottom()
        return cls(DescriptorKind.SET, tokens)
    
    @classmethod
    def from_property(
        cls,
        obj: "AbstractObject",
        fget_addrs: FrozenSet["AbstractObject"],
        fset_addrs: FrozenSet["AbstractObject"] = frozenset(),
        fdel_addrs: FrozenSet["AbstractObject"] = frozenset(),
    ) -> "DescriptorVal":
        """Create descriptor value for property."""
        ref = PropertyRef(obj, fget_addrs, fset_addrs, fdel_addrs)
        return cls.from_tokens(frozenset({ref}))
    
    @classmethod
    def from_classmethod(
        cls,
        obj: "AbstractObject",
        func_addrs: FrozenSet["AbstractObject"],
    ) -> "DescriptorVal":
        """Create descriptor value for classmethod."""
        ref = ClassMethodRef(obj, func_addrs)
        return cls.from_tokens(frozenset({ref}))
    
    @classmethod
    def from_staticmethod(
        cls,
        obj: "AbstractObject",
        func_addrs: FrozenSet["AbstractObject"],
    ) -> "DescriptorVal":
        """Create descriptor value for staticmethod."""
        ref = StaticMethodRef(obj, func_addrs)
        return cls.from_tokens(frozenset({ref}))

    def is_bottom(self) -> bool:
        return self.kind == DescriptorKind.BOTTOM

    def is_top(self) -> bool:
        return self.kind == DescriptorKind.TOP
    
    def has_data_descriptor(self) -> bool:
        """Check if any token is a data descriptor."""
        if self.is_top():
            return True  # Conservative: might be data descriptor
        return any(tok.is_data_descriptor for tok in self.tokens)
    
    def get_properties(self) -> List[PropertyRef]:
        """Get all property tokens."""
        return [t for t in self.tokens if isinstance(t, PropertyRef)]
    
    def get_classmethods(self) -> List[ClassMethodRef]:
        """Get all classmethod tokens."""
        return [t for t in self.tokens if isinstance(t, ClassMethodRef)]
    
    def get_staticmethods(self) -> List[StaticMethodRef]:
        """Get all staticmethod tokens."""
        return [t for t in self.tokens if isinstance(t, StaticMethodRef)]
    
    def get_generic_descriptors(self) -> List[DescriptorRef]:
        """Get all generic descriptor tokens."""
        return [t for t in self.tokens if isinstance(t, DescriptorRef)]

    def join(self, other: "DescriptorVal") -> "DescriptorVal":
        if self.is_bottom():
            return other
        if other.is_bottom():
            return self
        if self.is_top() or other.is_top():
            return DescriptorVal.top()
        return DescriptorVal.from_tokens(self.tokens | other.tokens)

    def meet(self, other: "DescriptorVal") -> "DescriptorVal":
        if self.is_top():
            return other
        if other.is_top():
            return self
        if self.is_bottom() or other.is_bottom():
            return DescriptorVal.bottom()
        return DescriptorVal.from_tokens(self.tokens & other.tokens)

    def leq(self, other: "DescriptorVal") -> bool:
        if self.is_bottom():
            return True
        if other.is_top():
            return True
        if self.is_top():
            return other.is_top()
        if other.kind != DescriptorKind.SET:
            return False
        return self.tokens <= other.tokens

    def __str__(self) -> str:
        if self.is_bottom():
            return "⊥_desc"
        if self.is_top():
            return "⊤_desc"
        types = set(t.desc_type.value for t in self.tokens)
        return f"Desc({len(self.tokens)} descs: {types})"


class DescriptorDomain(Domain[DescriptorVal]):
    """Domain for descriptor tokens with bounded set widening."""

    def __init__(self, max_tokens: int = 20):
        self.max_tokens = max_tokens

    @property
    def name(self) -> str:
        return "descriptor"

    def bottom(self) -> DescriptorVal:
        return DescriptorVal.bottom()

    def top(self) -> DescriptorVal:
        return DescriptorVal.top()

    def join(self, a: DescriptorVal, b: DescriptorVal) -> DescriptorVal:
        return a.join(b)

    def meet(self, a: DescriptorVal, b: DescriptorVal) -> DescriptorVal:
        return a.meet(b)

    def leq(self, a: DescriptorVal, b: DescriptorVal) -> bool:
        return a.leq(b)

    def widen(self, old: DescriptorVal, new: DescriptorVal) -> DescriptorVal:
        joined = old.join(new)
        if (
            joined.kind == DescriptorKind.SET
            and len(joined.tokens) > self.max_tokens
        ):
            return DescriptorVal.top()
        return joined

    def narrow(self, old: DescriptorVal, new: DescriptorVal) -> DescriptorVal:
        return old.meet(new)

    # Note: on_reduce_value is not overridden here since descriptor tokens
    # are typically created during attribute resolution, not from address sets.


# =============================================================================
# DescriptorSemantics - Service for descriptor interpretation
# =============================================================================

# Type alias for the callee invocation callback
InvokeCalleeFunc = Callable[
    [
        "AbstractObject",  # callee_obj
        List[Tuple[str, "AbsVal"]],  # arg_vals
        "SummaryAccumulator",  # summary
        Optional["AbsVal"],  # receiver_val
    ],
    "AbsVal",  # return value
]


class DescriptorSemantics:
    """Retired private descriptor callback interpreter; tokens remain supported."""
    def __init__(self,*args,**kwargs):
        from pythonstan.compat.shared_ai import BoundaryUnavailable
        raise BoundaryUnavailable("Private descriptor callback interpreter is retired; use shared source semantics")


def identify_descriptor_type(
    obj: "AbstractObject",
    state: Optional[Any] = None,
) -> Optional[DescriptorType]:
    """Identify the type of descriptor from an object.
    
    Checks the object's kind/class to identify known descriptor patterns.
    
    Args:
        obj: The abstract object to identify
        state: Optional AI state for class lookup
        
    Returns:
        DescriptorType or None if not a known descriptor
    """
    try:
        # Check object kind
        kind = getattr(obj, "kind", None)
        if kind is None:
            return None
        
        # Convert to string for comparison
        kind_val = kind.value if hasattr(kind, "value") else str(kind)
        
        # Check for builtin descriptor kinds (would need PTA/IR info)
        # For now, rely on explicit creation via from_property/etc.
        
        return None
    except Exception:
        return None


def create_descriptor_ref(
    obj: "AbstractObject",
    has_get: bool = False,
    has_set: bool = False,
    has_delete: bool = False,
) -> DescriptorToken:
    """Create appropriate descriptor ref based on protocol methods.
    
    Args:
        obj: The descriptor object
        has_get: Whether object has __get__
        has_set: Whether object has __set__
        has_delete: Whether object has __delete__
        
    Returns:
        DescriptorToken (DescriptorRef for generic descriptors)
    """
    return DescriptorRef(
        obj=obj,
        has_get=has_get,
        has_set=has_set,
        has_delete=has_delete,
        desc_type=DescriptorType.GENERIC,
    )


def create_descriptor_val_for_method(
    obj: "AbstractObject",
    state: Optional[Any] = None,
) -> Optional[DescriptorVal]:
    """Create DescriptorVal for method objects based on decorator flags.
    
    Checks if the object is a MethodObject (or FunctionObject) with decorator
    flags indicating it's a property, classmethod, or staticmethod, and returns
    the appropriate DescriptorVal with typed tokens.
    
    This enables semantic interpretation of descriptors during attribute lookup
    without relying on protocol-based `__get__` invocation.
    
    Args:
        obj: The abstract object (typically MethodObject or FunctionObject)
        state: Optional AI state (unused currently, for future extensions)
        
    Returns:
        DescriptorVal with appropriate token type, or None if not a known descriptor
    """
    try:
        # Check if object has IR with decorator info
        ir = getattr(obj, "ir", None)
        if ir is None:
            return None
        
        # Check for @property (is_getter flag)
        if getattr(ir, "is_getter", False):
            # Property descriptor - obj is the fget function
            return DescriptorVal.from_property(
                obj=obj,
                fget_addrs=frozenset({obj}),
                fset_addrs=frozenset(),
                fdel_addrs=frozenset(),
            )
        
        # Check for @classmethod
        if getattr(ir, "is_class_method", False):
            return DescriptorVal.from_classmethod(
                obj=obj,
                func_addrs=frozenset({obj}),
            )
        
        # Check for @staticmethod
        if getattr(ir, "is_static_method", False):
            return DescriptorVal.from_staticmethod(
                obj=obj,
                func_addrs=frozenset({obj}),
            )
        
        # Not a known descriptor type
        return None
        
    except Exception:
        return None


def is_known_descriptor_method(obj: "AbstractObject") -> bool:
    """Check if object is a known descriptor method type.
    
    Quick check without creating the full DescriptorVal.
    
    Args:
        obj: The abstract object to check
        
    Returns:
        True if object is a property/classmethod/staticmethod
    """
    try:
        ir = getattr(obj, "ir", None)
        if ir is None:
            return False
        return (
            getattr(ir, "is_getter", False) or
            getattr(ir, "is_class_method", False) or
            getattr(ir, "is_static_method", False)
        )
    except Exception:
        return False
