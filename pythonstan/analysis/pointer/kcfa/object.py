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

__all__ = ["AllocKind", "AllocSite", "AbstractObject", "FunctionObject"]


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
    
    file: str
    line: int
    col: int
    kind: AllocKind
    scope: Optional['Scope']
    name: Optional[str]
    stmt: Optional['IRStatement']
    
    def __str__(self) -> str:
        """String representation for debugging and display."""
        loc = f"{self.file}:{self.line}:{self.col}"
        if self.name:
            return f"{loc}:{self.kind.value}:{self.name}"
        return f"{loc}:{self.kind.value}"
    
    @staticmethod
    def get_location(stmt: ast.AST) -> Tuple[int, int]:
        line = getattr(stmt, 'line', 0)
        col = getattr(stmt, 'col_offset', 0)
        return line, col
    
    @staticmethod
    def from_ir_node(stmt: Optional['IRStatement'], kind: AllocKind, scope: Optional['Scope'] = None, name: Optional[str] = None) -> 'AllocSite':
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
        if scope is not None:
            mod_name = scope.name
        else:
            mod_name = "<unknown>"
        if stmt is not None:
            ast_repr = stmt.get_ast()
            line = getattr(ast_repr, 'line', 0)
            col = getattr(ast_repr, 'col_offset', 0)
        else:
            line = 0
            col = 0
            
        return AllocSite(file=mod_name, line=line, col=col, kind=kind, scope=scope, name=name, stmt=stmt)


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


@dataclass(frozen=True)
class FunctionObject(AbstractObject):
    """Function object with context sensitivity."""
    
    container_scope: 'Scope'
    ir: 'IRFunc'
    cell_vars: 'Dict[Ctx[Variable]]'
    global_vars: 'Dict[Ctx[Variable]]'
    nonlocal_vars: 'Dict[Ctx[Variable]]'

    def __hash__(self):
        return hash(self.container_scope) * 17 + hash(self.ir)


@dataclass(frozen=True)
class MethodObject(FunctionObject):
    pass



# TODO Add some types of objects

class ClassObject(AbstractObject):
    ir: 'IRClass'


class ModuleObject(AbstractObject):
    ir: 'IRModule'
    
    
class ConstObject(AbstractObject):
    value: Union[str, int, float, bool]

class BuiltinObject(AbstractObject):
    ...

class CellObject(AbstractObject):
    ...
    
class InstanceObject(AbstractObject):
    ...

class ListObject(AbstractObject):
    ...

class TupleObject(AbstractObject):
    ...

class DictObject(AbstractObject):
    ...

class SetObject(AbstractObject):
    ...
    
class MethodObject(AbstractObject):
    ...


class ObjectFactory():
    ...
