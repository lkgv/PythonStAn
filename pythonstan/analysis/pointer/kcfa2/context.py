"""Context management for k-CFA pointer analysis.

This module implements calling contexts for k-CFA, including call sites,
context strings, context selection, and context management.

See docs/digests/ir-semantics-digest.md for information about the IR operations
that generate call sites and context transitions.
"""

from dataclasses import dataclass
from typing import Tuple, Optional

__all__ = ["CallSite", "Context", "ContextSelector", "ContextManager"]


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


@dataclass(frozen=True)
class Context:
    """A calling context for k-CFA.
    
    A context consists of a sequence of call sites representing the
    call string leading to the current program point.
    
    Attributes:
        call_string: Tuple of call sites in calling order (most recent last)
    """
    call_string: Tuple[CallSite, ...] = ()
    
    def __len__(self) -> int:
        """Get the length of the calling context."""
        return len(self.call_string)
        
    def is_empty(self) -> bool:
        """Check if this is the empty context."""
        return len(self.call_string) == 0
        
    def push(self, call_site: CallSite, k: int) -> "Context":
        """Push a new call site onto the context.
        
        Args:
            call_site: Call site to add
            k: Maximum context length (older calls are dropped)
            
        Returns:
            New context with the call site added
        """
        new_string = self.call_string + (call_site,)
        if len(new_string) > k:
            new_string = new_string[-k:]  # Keep only the k most recent calls
        return Context(new_string)
        
    def pop(self) -> "Context":
        """Pop the most recent call site from the context.
        
        Returns:
            New context with the last call site removed
        """
        if self.is_empty():
            return self
        return Context(self.call_string[:-1])
        
    def __str__(self) -> str:
        if self.is_empty():
            return "[]"
        return "[" + " → ".join(str(cs) for cs in self.call_string) + "]"


class ContextSelector:
    """Selects calling contexts for k-CFA.
    
    The context selector determines how to construct new contexts
    when entering function calls, implementing the k-limiting policy.
    """
    
    def __init__(self, k: int = 2):
        """Initialize context selector.
        
        Args:
            k: Maximum context length for k-CFA
        """
        self.k = k
        
    def push(self, current_ctx: Context, call_site: CallSite) -> Context:
        """Select context for a function call.
        
        Args:
            current_ctx: Current calling context
            call_site: Call site being invoked
            
        Returns:
            New context for the called function
        """
        return current_ctx.push(call_site, self.k)
        
    def __repr__(self) -> str:
        return f"ContextSelector(k={self.k})"


class ContextManager:
    """Manages calling contexts during analysis.
    
    The context manager tracks the current context and provides
    operations for entering and leaving function calls.
    """
    
    def __init__(self, selector: Optional[ContextSelector] = None):
        """Initialize context manager.
        
        Args:
            selector: Context selector (creates default if None)
        """
        self._selector = selector or ContextSelector()
        self._current = Context()
        
    def current(self) -> Context:
        """Get the current calling context.
        
        Returns:
            Current context
        """
        return self._current
        
    def enter_call(self, call_site: CallSite) -> Context:
        """Enter a function call.
        
        Args:
            call_site: Call site being invoked
            
        Returns:
            New context for the called function
        """
        new_ctx = self._selector.push(self._current, call_site)
        self._current = new_ctx
        return new_ctx
        
    def leave_call(self) -> Context:
        """Leave the current function call.
        
        Returns:
            Context after leaving the call
        """
        self._current = self._current.pop()
        return self._current
        
    def truncate(self, k: int) -> None:
        """Truncate current context to maximum length k.
        
        Args:
            k: Maximum context length
        """
        if len(self._current) > k:
            call_string = self._current.call_string[-k:]
            self._current = Context(call_string)
            
    def __repr__(self) -> str:
        return f"ContextManager(current={self._current}, selector={self._selector})"