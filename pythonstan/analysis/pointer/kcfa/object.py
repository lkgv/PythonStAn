"""Abstract objects and allocation sites for pointer analysis.

This module defines the representation of heap objects in the k-CFA pointer analysis.
Objects are context-sensitive and identified by their allocation site and context.
"""

from dataclasses import dataclass
from enum import Enum
import ast
from typing import Optional, Tuple, Union, Dict, TYPE_CHECKING

if TYPE_CHECKING:
    from pythonstan.ir.ir_statements import *
    from .context import AbstractContext, Scope, Ctx
    from .variable import Variable

__all__ = ["AllocKind", "AllocSite", "AbstractObject", "FunctionObject", "ConstantObject", 
           "ClassObject", "ModuleObject", "InstanceObject", "MethodObject", "BuiltinObject", "ListObject",
           "TupleObject", "DictObject", "SetObject", "ObjectFactory"]


class AllocKind(Enum):
    """Types of allocations in Python programs."""
    
    OBJECT = "obj"
    LIST = "list"
    TUPLE = "tuple"
    DICT = "dict"
    SET = "set"
    FUNCTION = "func"
    METHOD = "method"
    CLASS = "class"
    INSTANCE = "instance"
    MODULE = "module"
    BOUND_METHOD = "method"
    BUILTIN = "builtin"
    CELL = "cell"
    CONSTANT = "constant"
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

    stmt: 'IRStatement'
    kind: AllocKind
    
    def __str__(self) -> str:
        """String representation for debugging and display."""
        return f"{self.stmt}@{self.kind}"
    
    @property
    def line(self) -> str:        
        return self.stmt.get_ast().getattr('line', 0)
    
    @property
    def col(self) -> str:
        return self.stmt.get_ast().getattr("col_offset", 0)
    
    @staticmethod
    def from_ir_node(stmt: 'IRStatement', kind: AllocKind) -> 'AllocSite':
        """Create allocation site from IR node.
        Extract source location from IR node.
        IR nodes typically have file, line, col attributes.
        
        Args:
            stmt: IR node
            kind: Allocation kind
        
        Returns:
            AllocSite extracted from node
        """

        return AllocSite(stmt, kind)


@dataclass(frozen=True)
class AbstractObject:
    """Abstract heap object with context sensitivity.
    
    An abstract object represents a set of concrete runtime objects that share
    the same allocation site and context. The context enables context-sensitive
    analysis by distinguishing objects allocated at the same site in different
    calling contexts.
    
    Attributes:
        scope: container scope
        context: Analysis context (dynamic identity)
        alloc_site: Allocation site (static identity)
    """
    
    context: 'AbstractContext'
    alloc_site: AllocSite
    
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


@dataclass(frozen=True)
class FunctionObject(AbstractObject):
    """Function object with context sensitivity."""
    
    container_scope: 'Scope'
    ir: 'IRFunc'


@dataclass(frozen=True)
class MethodObject(FunctionObject):
    class_obj: 'ClassObject'
    instance_obj: Optional['InstanceObject']

    def deliver_into(self, inst: 'InstanceObject') -> 'MethodObject':
        return MethodObject(self.context, self.alloc_site, inst.class_obj, inst)
    
    def inherit_into(self, cls_obj: 'ClassObject') -> 'MethodObject':
        return MethodObject(self.context, self.alloc_site, cls_obj, None)
    

# TODO Add some types of objects

@dataclass(frozen=True)
class ClassObject(AbstractObject):

    container_scope: 'Scope'
    ir: 'IRClass'


@dataclass(frozen=True)
class ModuleObject(AbstractObject):
    ir: 'IRModule'


@dataclass(frozen=True)
class InstanceObject(AbstractObject):
    class_obj: 'ClassObject'    
    

@dataclass(frozen=True)
class ConstantObject(AbstractObject):
    value: Union[str, int, float, bool]


@dataclass(frozen=True)
class BuiltinObject(AbstractObject):
    """Builtin object (e.g., built-in functions)."""
    pass


@dataclass(frozen=True)
class ListObject(AbstractObject):
    """List object with mutable elements tracked via elem() field."""
    pass


@dataclass(frozen=True)
class TupleObject(AbstractObject):
    """Tuple object with immutable elements tracked via position(i) fields."""
    pass


@dataclass(frozen=True)
class DictObject(AbstractObject):
    """Dictionary object with values tracked via key(k) and value() fields."""
    pass


@dataclass(frozen=True)
class SetObject(AbstractObject):
    """Set object with elements tracked via elem() field."""
    pass


class ObjectFactory():
    """Factory for creating abstract objects."""
    pass
