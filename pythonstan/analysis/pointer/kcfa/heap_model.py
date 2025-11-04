"""Heap model and field abstraction for pointer analysis.

This module defines the abstraction of heap storage including field keys
for attribute access, container elements, and dictionary values.
"""

from dataclasses import dataclass
from enum import Enum
from typing import Optional

__all__ = ["FieldKind", "Field", "attr", "elem", "value", "unknown"]


class FieldKind(Enum):
    """Kinds of heap fields."""
    
    ATTRIBUTE = "attr"
    ELEMENT = "elem"
    VALUE = "value"
    UNKNOWN = "unknown"
    POSITION = "position"


@dataclass(frozen=True)
class Field:
    """Heap field key for object field access.
    
    Fields abstract over different kinds of object field access:
    - ATTRIBUTE: Named attribute access (obj.name)
    - ELEMENT: Container element access (list/set/tuple elements)
    - VALUE: Dictionary value access (dict values, key-insensitive)
    - UNKNOWN: Dynamic/unknown attribute access
    
    Attributes:
        kind: Type of field access
        name: Field name for ATTRIBUTE kind, None otherwise
    """
    
    kind: FieldKind
    name: Optional[str] = None
    index: Optional[int] = None
    
    def __post_init__(self):
        """Validate field constraints."""
        if self.kind == FieldKind.ATTRIBUTE and self.name is None:
            raise ValueError("ATTRIBUTE field must have name")
        if self.kind != FieldKind.ATTRIBUTE and self.name is not None:
            raise ValueError(f"{self.kind.value} field should not have name")
        if self.kind == FieldKind.POSITION and self.index is None:
            raise ValueError("POSITION field must have index")
        if self.kind != FieldKind.POSITION and self.index is not None:
            raise ValueError(f"{self.kind.value} field should not have index")
    
    def __str__(self) -> str:
        """String representation for debugging."""
        if self.kind == FieldKind.ATTRIBUTE:
            return f".{self.name}"
        if self.kind == FieldKind.ATTRIBUTE:
            return f".({self.index})"
        return f".{self.kind.value}"


def position(index: int) -> Field:
    """Create position field key for containers.
    
    Used for list, set, and tuple elements where we abstract over all indices.
    
    Args:
        index: Index of the element
    
    Returns:
        Field for container element access
    """
    return Field(FieldKind.POSITION, str(index))


def attr(name: str) -> Field:
    """Create attribute field key.
    
    Args:
        name: Attribute name
    
    Returns:
        Field for obj.name access
    
    Example:
        >>> attr("foo")
        Field(kind=FieldKind.ATTRIBUTE, name="foo")
    """
    return Field(FieldKind.ATTRIBUTE, name)


def elem() -> Field:
    """Create element field key for containers.
    
    Used for list, set, and tuple elements where we abstract over all indices.
    
    Returns:
        Field for container element access
    
    Example:
        >>> elem()
        Field(kind=FieldKind.ELEMENT, name=None)
    """
    return Field(FieldKind.ELEMENT)


def value() -> Field:
    """Create value field key for dictionaries.
    
    Used for dictionary values where we abstract over all keys.
    
    Returns:
        Field for dictionary value access
    
    Example:
        >>> value()
        Field(kind=FieldKind.VALUE, name=None)
    """
    return Field(FieldKind.VALUE)


def unknown() -> Field:
    """Create unknown field key for dynamic access.
    
    Used when attribute name cannot be determined statically.
    
    Returns:
        Field for unknown attribute access
    
    Example:
        >>> unknown()
        Field(kind=FieldKind.UNKNOWN, name=None)
    """
    return Field(FieldKind.UNKNOWN)
