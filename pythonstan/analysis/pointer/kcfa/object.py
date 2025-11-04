"""Abstract objects and allocation sites for pointer analysis.

This module defines the representation of heap objects in the k-CFA pointer analysis.
Objects are context-sensitive and identified by their allocation site and context.
"""

from dataclasses import dataclass
from enum import Enum
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .variable import Scope
    from .context import AbstractContext

__all__ = ["AllocKind", "AllocSite", "AbstractObject"]


class AllocKind(Enum):
    """Types of allocations in Python programs."""
    
    OBJECT = "obj"
    LIST = "list"
    TUPLE = "tuple"
    DICT = "dict"
    SET = "set"
    FUNCTION = "func"
    CLASS = "class"
    MODULE = "module"
    BOUND_METHOD = "method"
    BUILTIN = "builtin"
    CELL = "cell"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class AllocSite:
    """Allocation site with source location information.
    
    An allocation site represents a program location where an object is created.
    This forms the static part of an abstract object's identity.
    
    Attributes:
        file: Source file name
        line: Line number
        col: Column offset
        kind: Type of allocation
        name: Optional name for named allocations (functions, classes)
    """
    
    file: str
    line: int
    col: int
    kind: AllocKind
    scope: Optional['Scope']
    name: Optional[str]
    
    def __str__(self) -> str:
        """String representation for debugging and display."""
        loc = f"{self.file}:{self.line}:{self.col}"
        if self.name:
            return f"{loc}:{self.kind.value}:{self.name}"
        return f"{loc}:{self.kind.value}"
    
    @staticmethod
    def from_ir_node(node, kind: AllocKind, scope: Optional['Scope'] = None, name: Optional[str] = None) -> 'AllocSite':
        """Create allocation site from IR node.
        Extract source location from IR node.
        IR nodes typically have file, line, col attributes.
        
        Args:
            node: IR node with source location
            kind: Allocation kind
            name: Optional name
        
        Returns:
            AllocSite extracted from node
        """
        file = getattr(node, 'file', '<unknown>')
        line = getattr(node, 'line', 0)
        col = getattr(node, 'col', 0)
        
        # Some IR nodes use 'lineno' and 'col_offset' (ast-style)
        if line == 0 and hasattr(node, 'lineno'):
            line = node.lineno
        if col == 0 and hasattr(node, 'col_offset'):
            col = node.col_offset
            
        return AllocSite(file=file, line=line, col=col, kind=kind, scope=scope, name=name)


@dataclass(frozen=True)
class AbstractObject:
    """Abstract heap object with context sensitivity.
    
    An abstract object represents a set of concrete runtime objects that share
    the same allocation site and context. The context enables context-sensitive
    analysis by distinguishing objects allocated at the same site in different
    calling contexts.
    
    Attributes:
        alloc_site: Allocation site (static identity)
        context: Analysis context (dynamic identity)
    """
    
    alloc_site: AllocSite
    context: 'AbstractContext'
    
    def __str__(self) -> str:
        """String representation showing site and context."""
        return f"{self.alloc_site}@{self.context}"
    
    @property
    def kind(self) -> AllocKind:
        """Get allocation kind from site."""
        return self.alloc_site.kind
    
    @property
    def is_callable(self) -> bool:
        """Check if object is callable (function, class, bound method)."""
        return self.kind in (
            AllocKind.FUNCTION,
            AllocKind.CLASS,
            AllocKind.BOUND_METHOD,
            AllocKind.BUILTIN
        )

