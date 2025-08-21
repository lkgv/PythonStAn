"""Worklist management for k-CFA pointer analysis.

This module provides deterministic worklist implementations for managing
constraint propagation and function call processing in the k-CFA analysis.

The worklists support both FIFO and LIFO modes and ensure deterministic
iteration order by using consistent ordering of items.
"""

from collections import deque
from typing import TypeVar, Generic, Set, Deque, Optional
from dataclasses import dataclass

__all__ = ["Worklist", "ConstraintWorklist", "CallWorklist"]

T = TypeVar("T")


class Worklist(Generic[T]):
    """Generic deterministic worklist for k-CFA analysis.
    
    Supports both FIFO and LIFO modes while ensuring deterministic behavior
    by avoiding nondeterministic iteration over sets.
    
    Attributes:
        mode: Processing mode ("FIFO" or "LIFO")
    """
    
    def __init__(self, mode: str = "FIFO"):
        """Initialize worklist.
        
        Args:
            mode: Processing mode - either "FIFO" or "LIFO"
            
        Raises:
            ValueError: If mode is not "FIFO" or "LIFO"
        """
        if mode not in ("FIFO", "LIFO"):
            raise ValueError(f"Invalid mode: {mode}. Must be 'FIFO' or 'LIFO'")
        
        self.mode = mode
        self._queue: Deque[T] = deque()
        self._seen: Set[T] = set()
    
    def push(self, item: T) -> None:
        """Add an item to the worklist.
        
        Items are only added if they haven't been processed before.
        For fairness in FIFO mode, newly discovered items are added to the tail.
        
        Args:
            item: Item to add to the worklist
        """
        if item not in self._seen:
            self._seen.add(item)
            if self.mode == "FIFO":
                self._queue.append(item)  # Add to tail for FIFO fairness
            else:  # LIFO
                self._queue.append(item)  # Add to tail (will pop from same end)
    
    def pop(self) -> T:
        """Remove and return an item from the worklist.
        
        Returns:
            Item from the worklist
            
        Raises:
            IndexError: If worklist is empty
        """
        if self.empty():
            raise IndexError("pop from empty worklist")
            
        if self.mode == "FIFO":
            return self._queue.popleft()  # Remove from head for FIFO
        else:  # LIFO
            return self._queue.pop()      # Remove from tail for LIFO
    
    def empty(self) -> bool:
        """Check if the worklist is empty.
        
        Returns:
            True if worklist is empty, False otherwise
        """
        return len(self._queue) == 0
    
    def size(self) -> int:
        """Get the current size of the worklist.
        
        Returns:
            Number of items in the worklist
        """
        return len(self._queue)
    
    def clear(self) -> None:
        """Clear all items from the worklist."""
        self._queue.clear()
        self._seen.clear()
    
    def __len__(self) -> int:
        """Get the length of the worklist."""
        return len(self._queue)
    
    def __bool__(self) -> bool:
        """Check if worklist is non-empty."""
        return not self.empty()
    
    def __repr__(self) -> str:
        return f"Worklist(mode={self.mode}, size={self.size()})"


@dataclass(frozen=True)
class ConstraintItem:
    """Item representing a pointer constraint for processing.
    
    Attributes:
        constraint_type: Type of constraint (copy, load, store)
        source: Source location/variable
        target: Target location/variable
        context: Calling context where constraint applies
        field: Field key for attribute/element access (optional)
        site_id: Site ID for debugging/tracing (optional)
    """
    constraint_type: str
    source: str
    target: str
    context: str  # String representation of Context
    field: Optional[str] = None
    site_id: Optional[str] = None
    
    def __str__(self) -> str:
        field_str = f".{self.field}" if self.field else ""
        return f"{self.constraint_type}: {self.source}{field_str} → {self.target} @{self.context}"


class ConstraintWorklist(Worklist[ConstraintItem]):
    """Specialized worklist for pointer constraints.
    
    Manages constraint items with deterministic processing order.
    """
    
    def __init__(self, mode: str = "FIFO"):
        """Initialize constraint worklist.
        
        Args:
            mode: Processing mode - either "FIFO" or "LIFO"
        """
        super().__init__(mode)
    
    def add_copy_constraint(self, source: str, target: str, context: str, site_id: Optional[str] = None) -> None:
        """Add a copy constraint: target = source.
        
        Args:
            source: Source variable
            target: Target variable
            context: Calling context
            site_id: Site ID for debugging
        """
        constraint = ConstraintItem(
            constraint_type="copy",
            source=source,
            target=target,
            context=context,
            site_id=site_id
        )
        self.push(constraint)
    
    def add_load_constraint(self, source: str, field: str, target: str, context: str, site_id: Optional[str] = None) -> None:
        """Add a load constraint: target = source.field.
        
        Args:
            source: Source object
            field: Field name/key
            target: Target variable
            context: Calling context
            site_id: Site ID for debugging
        """
        constraint = ConstraintItem(
            constraint_type="load",
            source=source,
            target=target,
            context=context,
            field=field,
            site_id=site_id
        )
        self.push(constraint)
    
    def add_store_constraint(self, target: str, field: str, source: str, context: str, site_id: Optional[str] = None) -> None:
        """Add a store constraint: target.field = source.
        
        Args:
            target: Target object
            field: Field name/key
            source: Source variable
            context: Calling context
            site_id: Site ID for debugging
        """
        constraint = ConstraintItem(
            constraint_type="store",
            source=source,
            target=target,
            context=context,
            field=field,
            site_id=site_id
        )
        self.push(constraint)


@dataclass(frozen=True)
class CallItem:
    """Item representing a function call for processing.
    
    Attributes:
        call_type: Type of call (direct, indirect, method)
        call_id: Unique call site identifier
        caller_ctx: Context of the caller
        callee: Callee function/variable
        receiver: Receiver object for method calls (optional)
        args: Argument variables
        target: Target variable for return value (optional)
    """
    call_type: str
    call_id: str
    caller_ctx: str  # Context representation as string for hashing
    callee: str
    receiver: Optional[str] = None
    args: tuple = ()  # Tuple for hashability
    target: Optional[str] = None
    
    def __str__(self) -> str:
        if self.call_type == "method":
            return f"method call: {self.receiver}.{self.callee}({', '.join(self.args)})"
        else:
            return f"{self.call_type} call: {self.callee}({', '.join(self.args)})"


class CallWorklist(Worklist[CallItem]):
    """Specialized worklist for function call processing.
    
    Manages function calls with deterministic processing order.
    """
    
    def __init__(self, mode: str = "FIFO"):
        """Initialize call worklist.
        
        Args:
            mode: Processing mode - either "FIFO" or "LIFO"
        """
        super().__init__(mode)
    
    def add_call(
        self, 
        call_type: str,
        call_id: str,
        caller_ctx: str,
        callee: str,
        args: tuple = (),
        receiver: Optional[str] = None,
        target: Optional[str] = None
    ) -> None:
        """Add a function call for processing.
        
        Args:
            call_type: Type of call (direct, indirect, method)
            call_id: Unique call site identifier
            caller_ctx: Context of the caller
            callee: Callee function/variable
            args: Argument variables
            receiver: Receiver object for method calls
            target: Target variable for return value
        """
        call_item = CallItem(
            call_type=call_type,
            call_id=call_id,
            caller_ctx=caller_ctx,
            callee=callee,
            receiver=receiver,
            args=args,
            target=target
        )
        self.push(call_item)
    
    def add_direct_call(
        self,
        call_id: str,
        caller_ctx: str,
        callee: str,
        args: tuple = (),
        target: Optional[str] = None
    ) -> None:
        """Add a direct function call.
        
        Args:
            call_id: Unique call site identifier
            caller_ctx: Context of the caller
            callee: Callee function name
            args: Argument variables
            target: Target variable for return value
        """
        self.add_call("direct", call_id, caller_ctx, callee, args, target=target)
    
    def add_method_call(
        self,
        call_id: str,
        caller_ctx: str,
        receiver: str,
        method: str,
        args: tuple = (),
        target: Optional[str] = None
    ) -> None:
        """Add a method call.
        
        Args:
            call_id: Unique call site identifier
            caller_ctx: Context of the caller
            receiver: Receiver object
            method: Method name
            args: Argument variables
            target: Target variable for return value
        """
        self.add_call("method", call_id, caller_ctx, method, args, receiver, target)
    
    def add_indirect_call(
        self,
        call_id: str,
        caller_ctx: str,
        callee_var: str,
        args: tuple = (),
        target: Optional[str] = None
    ) -> None:
        """Add an indirect call through a variable.
        
        Args:
            call_id: Unique call site identifier
            caller_ctx: Context of the caller
            callee_var: Variable holding the callee
            args: Argument variables
            target: Target variable for return value
        """
        self.add_call("indirect", call_id, caller_ctx, callee_var, args, target=target)
