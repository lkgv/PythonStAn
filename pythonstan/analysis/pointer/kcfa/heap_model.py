"""Heap model and field abstraction for pointer analysis.

This module defines the abstraction of heap storage including field keys
for attribute access, container elements, and dictionary values.
"""

from dataclasses import dataclass
from enum import Enum
from typing import Optional, TYPE_CHECKING, Set

from yaml import NodeEvent

if TYPE_CHECKING:
    from .object import AbstractObject
    from .variable import Variable
    from .context import AbstractContext, Ctx

__all__ = ["FieldKind", "Field", "attr", "elem", "value", "unknown"]


class FieldKind(Enum):
    """Kinds of heap fields."""
    
    ATTRIBUTE = "attr"
    ELEMENT = "elem"
    VALUE = "value"
    UNKNOWN = "unknown"
    POSITION = "position"
    KEY = "key"


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
        if self.kind == FieldKind.KEY and self.name is None:
            raise ValueError("KEY field must have name")
        if self.kind == FieldKind.POSITION and self.index is None:
            raise ValueError("POSITION field must have index")
        if self.kind in [FieldKind.ELEMENT, FieldKind.VALUE, FieldKind.UNKNOWN]:
            if self.name is not None or self.index is not None:
                raise ValueError(f"{self.kind.value} field should not have name or index")
    
    def __str__(self) -> str:
        """String representation for debugging."""
        if self.kind == FieldKind.ATTRIBUTE:
            return f".{self.name}"
        if self.kind == FieldKind.POSITION:
            return f"[{self.index}]"
        if self.kind == FieldKind.KEY:
            return f"['{self.name}']"
        return f".{self.kind.value}"


def position(index: int) -> Field:
    """Create position field key for containers.
    
    Used for tuple elements with specific integer indices for precise tracking.
    
    Args:
        index: Index of the element (must be an integer)
    
    Returns:
        Field for specific position access (e.g., tuple[0], tuple[1])
    """
    assert isinstance(index, int), "index must be an integer"
    return Field(FieldKind.POSITION, None, index)


def key(key_name: str) -> Field:
    """Create key field for specific dict key access.
    
    Used for dict["key"] where key is statically known constant.
    
    Args:
        key_name: Dictionary key as string
    
    Returns:
        Field for specific key access
    """
    if not isinstance(key_name, str):
        key_name = str(key_name)
    return Field(FieldKind.KEY, key_name, None)


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
    return Field(FieldKind.ATTRIBUTE, name, None)


def elem() -> Field:
    """Create element field key for containers.
    
    Used for list and set elements where we abstract over all indices.
    Also used as a fallback for tuples with unknown/dynamic indices.
    
    Returns:
        Field for generic container element access
    
    Example:
        >>> elem()
        Field(kind=FieldKind.ELEMENT, name=None, index=None)
    """
    return Field(FieldKind.ELEMENT, None, None)


def value() -> Field:
    """Create value field key for dictionaries.
    
    Used for dictionary values where we abstract over all keys.
    
    Returns:
        Field for dictionary value access
    
    Example:
        >>> value()
        Field(kind=FieldKind.VALUE, name=None)
    """
    return Field(FieldKind.VALUE, None, None)


def unknown() -> Field:
    """Create unknown field key for dynamic access.
    
    Used when attribute name cannot be determined statically.
    
    Returns:
        Field for unknown attribute access
    
    Example:
        >>> unknown()
        Field(kind=FieldKind.UNKNOWN, name=None)
    """
    return Field(FieldKind.UNKNOWN, None, None)
