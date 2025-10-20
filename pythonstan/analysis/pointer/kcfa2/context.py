"""Context management for pointer analysis with multiple context sensitivity policies.

This module implements abstract context representations for different context-sensitive
policies including call-string sensitivity, object sensitivity, type sensitivity,
receiver sensitivity, and hybrid policies.

The context abstraction enables comparison of different context-sensitive strategies
for Python pointer analysis.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Tuple, Optional, Any

__all__ = [
    "CallSite",
    "AbstractContext",
    "CallStringContext",
    "ObjectContext",
    "TypeContext",
    "ReceiverContext",
    "HybridContext",
]


@dataclass(frozen=True)
class CallSite:
    """A call site in the program.
    
    Call sites uniquely identify function invocation points and are used
    to build calling contexts in k-CFA.
    
    Attributes:
        site_id: Unique identifier for this call site (file:line:col:call format)
        fn: Name of the function containing this call site
        bb: Basic block identifier containing this call (optional)
        idx: Index within the basic block (for ordering)
    """
    site_id: str
    fn: str
    bb: Optional[str] = None
    idx: int = 0
    
    def __str__(self) -> str:
        bb_suffix = f":{self.bb}" if self.bb else ""
        return f"{self.site_id}{bb_suffix}#{self.idx}"


class AbstractContext(ABC):
    """Base class for all context implementations.
    
    This abstract base class defines the interface that all context-sensitive
    policies must implement.
    """
    
    @abstractmethod
    def to_string(self) -> str:
        """String representation for hashing/comparison."""
        pass
    
    @abstractmethod
    def is_empty(self) -> bool:
        """Check if context is empty."""
        pass
    
    @abstractmethod
    def __hash__(self) -> int:
        """Hash for use in dictionaries/sets."""
        pass
    
    @abstractmethod
    def __eq__(self, other: Any) -> bool:
        """Equality comparison."""
        pass
    
    def __str__(self) -> str:
        return self.to_string()
    
    def __repr__(self) -> str:
        return f"{self.__class__.__name__}({self.to_string()})"


@dataclass(frozen=True)
class CallStringContext(AbstractContext):
    """k-CFA: Call-string sensitivity.
    
    Context consists of a sequence of call sites representing the
    call string leading to the current program point.
    
    Attributes:
        call_sites: Tuple of call sites in calling order (most recent last)
        k: Maximum context length
    """
    call_sites: Tuple[CallSite, ...] = ()
    k: int = 2
    
    def to_string(self) -> str:
        if not self.call_sites:
            return "[]"
        return "[" + " → ".join(str(cs) for cs in self.call_sites) + "]"
    
    def is_empty(self) -> bool:
        return len(self.call_sites) == 0
    
    def append(self, call_site: CallSite) -> 'CallStringContext':
        """Create new context by appending call site."""
        if self.k == 0:
            # Context-insensitive: ignore call sites
            return self
        new_sites = (self.call_sites + (call_site,))[-self.k:]
        return CallStringContext(new_sites, self.k)
    
    def __len__(self) -> int:
        return len(self.call_sites)
    
    def __hash__(self) -> int:
        return hash((self.call_sites, self.k))
    
    def __eq__(self, other: Any) -> bool:
        if not isinstance(other, CallStringContext):
            return False
        return self.call_sites == other.call_sites and self.k == other.k
    
    # Legacy compatibility methods
    @property
    def call_string(self) -> Tuple[CallSite, ...]:
        """Legacy property for backward compatibility."""
        return self.call_sites
    
    def push(self, call_site: CallSite, k: Optional[int] = None) -> 'CallStringContext':
        """Legacy push method for backward compatibility."""
        if k is not None:
            # Create new context with different k
            new_sites = (self.call_sites + (call_site,))[-k:]
            return CallStringContext(new_sites, k)
        return self.append(call_site)
    
    def pop(self) -> 'CallStringContext':
        """Pop the most recent call site from the context."""
        if self.is_empty():
            return self
        return CallStringContext(self.call_sites[:-1], self.k)


@dataclass(frozen=True)
class ObjectContext(AbstractContext):
    """Object sensitivity: allocation site chain.
    
    Context consists of allocation sites in the receiver object chain,
    enabling object-sensitive analysis.
    
    Attributes:
        alloc_sites: Tuple of allocation site IDs
        depth: Maximum depth for object context
    """
    alloc_sites: Tuple[str, ...] = ()
    depth: int = 2
    
    def to_string(self) -> str:
        if not self.alloc_sites:
            return "<>"
        # Shorten allocation site IDs for readability
        shortened = [s.split(':')[-1] if ':' in s else s for s in self.alloc_sites]
        return "<" + ",".join(shortened) + ">"
    
    def is_empty(self) -> bool:
        return len(self.alloc_sites) == 0
    
    def append(self, alloc_site: str) -> 'ObjectContext':
        """Create new context by appending allocation site."""
        if self.depth == 0:
            return self
        new_sites = (self.alloc_sites + (alloc_site,))[-self.depth:]
        return ObjectContext(new_sites, self.depth)
    
    def __hash__(self) -> int:
        return hash((self.alloc_sites, self.depth))
    
    def __eq__(self, other: Any) -> bool:
        if not isinstance(other, ObjectContext):
            return False
        return self.alloc_sites == other.alloc_sites and self.depth == other.depth


@dataclass(frozen=True)
class TypeContext(AbstractContext):
    """Type sensitivity: receiver type chain.
    
    Context consists of types in the receiver object chain, using type
    information to distinguish different execution contexts.
    
    Attributes:
        types: Tuple of type names
        depth: Maximum depth for type context
    """
    types: Tuple[str, ...] = ()
    depth: int = 2
    
    def to_string(self) -> str:
        if not self.types:
            return "<:>"
        return "<" + ":".join(self.types) + ">"
    
    def is_empty(self) -> bool:
        return len(self.types) == 0
    
    def append(self, type_name: str) -> 'TypeContext':
        """Create new context by appending type."""
        if self.depth == 0:
            return self
        new_types = (self.types + (type_name,))[-self.depth:]
        return TypeContext(new_types, self.depth)
    
    def __hash__(self) -> int:
        return hash((self.types, self.depth))
    
    def __eq__(self, other: Any) -> bool:
        if not isinstance(other, TypeContext):
            return False
        return self.types == other.types and self.depth == other.depth


@dataclass(frozen=True)
class ReceiverContext(AbstractContext):
    """Receiver-object sensitivity: self/receiver allocation sites.
    
    Context consists of allocation sites of receiver objects (self parameter)
    in method calls, which is particularly relevant for Python's object-oriented
    nature.
    
    Attributes:
        receivers: Tuple of receiver allocation site IDs
        depth: Maximum depth for receiver context
    """
    receivers: Tuple[str, ...] = ()
    depth: int = 2
    
    def to_string(self) -> str:
        if not self.receivers:
            return "<rcv:>"
        # Shorten receiver IDs for readability
        shortened = [r.split(':')[-1] if ':' in r else r for r in self.receivers]
        return "<rcv:" + ",".join(shortened) + ">"
    
    def is_empty(self) -> bool:
        return len(self.receivers) == 0
    
    def append(self, receiver_site: str) -> 'ReceiverContext':
        """Create new context by appending receiver allocation site."""
        if self.depth == 0:
            return self
        new_receivers = (self.receivers + (receiver_site,))[-self.depth:]
        return ReceiverContext(new_receivers, self.depth)
    
    def __hash__(self) -> int:
        return hash((self.receivers, self.depth))
    
    def __eq__(self, other: Any) -> bool:
        if not isinstance(other, ReceiverContext):
            return False
        return self.receivers == other.receivers and self.depth == other.depth


@dataclass(frozen=True)
class HybridContext(AbstractContext):
    """Hybrid: Combine call-string + object sensitivity.
    
    Context consists of both call sites and allocation sites, combining
    the benefits of call-string and object sensitivity.
    
    Attributes:
        call_sites: Tuple of call sites
        alloc_sites: Tuple of allocation site IDs
        call_k: Maximum call-string length
        obj_depth: Maximum object context depth
    """
    call_sites: Tuple[CallSite, ...] = ()
    alloc_sites: Tuple[str, ...] = ()
    call_k: int = 1
    obj_depth: int = 1
    
    def to_string(self) -> str:
        call_part = "[" + ",".join(str(cs) for cs in self.call_sites) + "]" if self.call_sites else "[]"
        # Shorten allocation sites
        shortened = [s.split(':')[-1] if ':' in s else s for s in self.alloc_sites]
        obj_part = "<" + ",".join(shortened) + ">" if self.alloc_sites else "<>"
        return call_part + obj_part
    
    def is_empty(self) -> bool:
        return len(self.call_sites) == 0 and len(self.alloc_sites) == 0
    
    def append_call(self, call_site: CallSite) -> 'HybridContext':
        """Create new context by appending call site."""
        if self.call_k == 0:
            return self
        new_calls = (self.call_sites + (call_site,))[-self.call_k:]
        return HybridContext(new_calls, self.alloc_sites, self.call_k, self.obj_depth)
    
    def append_object(self, alloc_site: str) -> 'HybridContext':
        """Create new context by appending allocation site."""
        if self.obj_depth == 0:
            return self
        new_allocs = (self.alloc_sites + (alloc_site,))[-self.obj_depth:]
        return HybridContext(self.call_sites, new_allocs, self.call_k, self.obj_depth)
    
    def __hash__(self) -> int:
        return hash((self.call_sites, self.alloc_sites, self.call_k, self.obj_depth))
    
    def __eq__(self, other: Any) -> bool:
        if not isinstance(other, HybridContext):
            return False
        return (self.call_sites == other.call_sites and 
                self.alloc_sites == other.alloc_sites and
                self.call_k == other.call_k and 
                self.obj_depth == other.obj_depth)


# Legacy compatibility aliases
Context = CallStringContext
ContextManager = None  # Deprecated - use ContextSelector instead
