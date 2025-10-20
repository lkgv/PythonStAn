"""Heap model utilities for k-CFA pointer analysis.

This module provides functions and classes for computing abstract object keys
and field addressing in the 2-object sensitive k-CFA analysis.

The heap model handles:
- Object allocation with context sensitivity  
- 2-object sensitivity for receiver objects
- Field key creation for different access patterns
- Allocation site ID generation
"""

from typing import Optional, Tuple, List
from .model import AbstractObject, FieldKey
from .context import AbstractContext

__all__ = [
    "make_object",
    "attr_key", 
    "elem_key",
    "value_key", 
    "unknown_attr_key",
    "compute_recv_context_fingerprint"
]


def make_object(
    alloc_id: str, 
    alloc_ctx: AbstractContext,
    recv_obj_ctx: Optional[Tuple[AbstractObject, ...]] = None,
    depth: int = 2
) -> AbstractObject:
    """Create an abstract object with 2-object sensitivity.
    
    For 2-object sensitivity, the abstract object's identity depends on:
    1. The allocation site (alloc_id)
    2. The allocation context (alloc_ctx) 
    3. The allocation contexts of receiver objects up to depth
    
    Args:
        alloc_id: Unique identifier for the allocation site
        alloc_ctx: Context where this object was allocated
        recv_obj_ctx: Sequence of receiver objects and their contexts
        depth: Maximum depth for receiver context tracking
        
    Returns:
        Abstract object with appropriate context fingerprint
        
    Notes:
        The recv_ctx_fingerprint captures the essence of 2-object sensitivity:
        when allocating an object o through a method call on receiver r,
        the abstract object for o depends on the abstract object for r.
    """
    recv_fingerprint = None
    if recv_obj_ctx:
        # Compute fingerprint from receiver contexts up to depth
        recv_fingerprint = compute_recv_context_fingerprint(recv_obj_ctx, depth)
        
    return AbstractObject(
        alloc_id=alloc_id,
        alloc_ctx=alloc_ctx, 
        recv_ctx_fingerprint=recv_fingerprint
    )


def compute_recv_context_fingerprint(
    recv_objects: Tuple[AbstractObject, ...],
    depth: int
) -> tuple:
    """Compute receiver context fingerprint for 2-object sensitivity.
    
    The fingerprint captures the allocation information of receiver objects
    up to a specified depth, enabling 2-object sensitivity.
    
    Args:
        recv_objects: Sequence of receiver objects in call chain
        depth: Maximum depth to consider
        
    Returns:
        Tuple representing the receiver context fingerprint
        
    Notes:
        The fingerprint includes:
        - Allocation IDs of receiver objects  
        - Allocation contexts (truncated for finiteness)
        - Limited to specified depth to ensure termination
    """
    if not recv_objects or depth <= 0:
        return ()
        
    # Take only the most recent receivers up to depth
    relevant_receivers = recv_objects[-depth:]
    
    # Create fingerprint from allocation info
    fingerprint_parts = []
    for recv_obj in relevant_receivers:
        # Include allocation site and context info
        part = (recv_obj.alloc_id, str(recv_obj.alloc_ctx))
        fingerprint_parts.append(part)
        
    return tuple(fingerprint_parts)


def attr_key(name: str) -> FieldKey:
    """Create a field key for named attribute access.
    
    Args:
        name: Attribute name
        
    Returns:
        Field key for obj.name access
        
    Example:
        >>> attr_key("foo")
        FieldKey(kind="attr", name="foo")
    """
    return FieldKey(kind="attr", name=name)


def elem_key() -> FieldKey:
    """Create a field key for container element access.
    
    Used for list, set, and tuple elements where we abstract over
    all indices/elements using a single "elem" field.
    
    Returns:
        Field key for container element access
        
    Example:
        >>> elem_key()
        FieldKey(kind="elem", name=None)
    """
    return FieldKey(kind="elem", name=None)


def value_key() -> FieldKey:
    """Create a field key for dictionary value access.
    
    Used for dictionary values where we abstract over all keys
    using a single "value" field.
    
    Returns:
        Field key for dictionary value access
        
    Example:
        >>> value_key()  
        FieldKey(kind="value", name=None)
    """
    return FieldKey(kind="value", name=None)


def unknown_attr_key() -> FieldKey:
    """Create a field key for unknown/dynamic attribute access.
    
    Used when the attribute name cannot be determined statically,
    such as in getattr/setattr calls with dynamic names.
    
    Returns:
        Field key for unknown attribute access
        
    Example:
        >>> unknown_attr_key()
        FieldKey(kind="unknown", name=None)
    """
    return FieldKey(kind="unknown", name=None)


# Allocation site ID generation utilities

def format_alloc_id(file_path: str, lineno: int, col: int, kind: str) -> str:
    """Format an allocation site ID.
    
    Args:
        file_path: Source file path
        lineno: Line number
        col: Column number  
        kind: Allocation kind (obj, list, tuple, dict, set, func, class, etc.)
        
    Returns:
        Formatted allocation site ID
        
    Example:
        >>> format_alloc_id("example.py", 42, 10, "obj")
        "example.py:42:10:obj"
    """
    return f"{file_path}:{lineno}:{col}:{kind}"


def format_call_id(file_path: str, lineno: int, col: int) -> str:
    """Format a call site ID.
    
    Args:
        file_path: Source file path
        lineno: Line number
        col: Column number
        
    Returns:
        Formatted call site ID
        
    Example:
        >>> format_call_id("example.py", 42, 10)
        "example.py:42:10:call"
    """
    return f"{file_path}:{lineno}:{col}:call"


def format_fallback_id(file_stem: str, op: str, uid_hash: int) -> str:
    """Format a fallback allocation ID when position info is unavailable.
    
    Args:
        file_stem: Base filename without extension
        op: Operation type
        uid_hash: Hash of unique identifier
        
    Returns:
        Formatted fallback ID
        
    Example:
        >>> format_fallback_id("example", "alloc", 0x12345678)
        "example:alloc:12345678"
    """
    return f"{file_stem}:{op}:{uid_hash:x}"


# Equality and hash invariants documentation

"""
Equality and Hash Invariants:

1. AbstractObject equality/hash:
   - Based on (alloc_id, alloc_ctx, recv_ctx_fingerprint)
   - Two objects are equal iff all three components are equal
   - Hash must be consistent with equality

2. FieldKey equality/hash:
   - Based on (kind, name) 
   - Two field keys are equal iff both components are equal
   - Hash must be consistent with equality

3. Context equality/hash:
   - Based on call_string tuple
   - Two contexts are equal iff their call strings are equal
   - Hash must be consistent with equality

4. CallSite equality/hash:
   - Based on (site_id, fn, bb, idx)
   - Two call sites are equal iff all components are equal
   - Hash must be consistent with equality

These invariants ensure that objects can be used safely as dictionary keys
and in sets, which is critical for the analysis correctness.
"""