"""Abstract objects and allocation sites for pointer analysis.

This module defines the representation of heap objects in the k-CFA pointer analysis.
Objects are context-sensitive and identified by their allocation site and context.
"""

from dataclasses import dataclass
from enum import Enum
import ast
from typing import Optional, Tuple, Union, Dict, TYPE_CHECKING, Set

if TYPE_CHECKING:
    from pythonstan.ir.ir_statements import *
    from .context import AbstractContext, Scope, Ctx
    from .variable import Variable

__all__ = ["AllocKind", "AllocSite", "AbstractObject", "FunctionObject", "ConstantObject", 
           "ClassObject", "ModuleObject", "InstanceObject", "MethodObject", "BuiltinObject", "ListObject",
           "TupleObject", "DictObject", "SetObject", "ObjectFactory", "BuiltinStmt", "BuiltinFunctionObject",
           "BuiltinMethodObject"]


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
    
    def __post_init__(self):
        from pythonstan.ir.ir_statements import IRStatement
        assert isinstance(self.stmt, (str, IRStatement, BuiltinStmt)), f"stmt must be an IRStatement or BuiltinStmt, but got {type(self.stmt)}"
        assert isinstance(self.kind, AllocKind), f"kind must be an AllocKind, but got {type(self.kind)}"
        if self.kind == AllocKind.FUNCTION or self.kind == AllocKind.METHOD or self.kind == AllocKind.CLASS:
            assert isinstance(self.stmt, (IRStatement, BuiltinStmt)), f"stmt must be an IRStatement or BuiltinStmt, but got {type(self.stmt)}"
    
    def __str__(self) -> str:
        from pythonstan.ir.ir_statements import IRStatement

        """String representation for debugging and display."""
        if self.kind == AllocKind.FUNCTION or self.kind == AllocKind.METHOD or self.kind == AllocKind.CLASS:
            assert isinstance(self.stmt, IRStatement), f"stmt must be an IRStatement, but got {type(self.stmt)}"
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
        
        from pythonstan.ir.ir_statements import IRStatement
        assert isinstance(stmt, (str, IRStatement, BuiltinStmt)), f"stmt must be an IRStatement or BuiltinStmt, but got {type(stmt)}"
        assert isinstance(kind, AllocKind), f"kind must be an AllocKind, but got {type(kind)}"
        if kind == AllocKind.FUNCTION or kind == AllocKind.METHOD or kind == AllocKind.CLASS:
            assert isinstance(stmt, (IRStatement, BuiltinStmt)), f"stmt must be an IRStatement or BuiltinStmt, but got {type(stmt)}"

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
        return MethodObject(self.context, self.alloc_site, self.container_scope, self.ir, self.class_obj, inst)
    
    def inherit_into(self, cls_obj: 'ClassObject') -> 'MethodObject':
        return MethodObject(self.context, self.alloc_site, self.container_scope, self.ir, cls_obj, None)
    

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


class BuiltinStmt:
    """Shim to represent builtin symbols as IRStatement-like objects.
    
    This allows builtin functions/types/methods to have AllocSites compatible
    with the existing IRStatement-based architecture.
    """
    
    def __init__(self, name: str, kind: str = "builtin"):
        """Initialize builtin statement shim.
        
        Args:
            name: Builtin name (e.g., 'list', 'len', 'append')
            kind: Kind of builtin ('type', 'function', 'method')
        """
        self.name = name
        self.kind = kind
        self._qualname = f"<builtin>.{name}"
    
    def get_name(self) -> str:
        """Get builtin name."""
        return self.name
    
    def get_qualname(self) -> str:
        """Get qualified name."""
        return self._qualname
    
    def get_ast(self):
        """Return None as builtins have no AST."""
        return None
    
    def get_stores(self) -> Set[str]:
        """Builtins have no stores."""
        return set()
    
    def get_loads(self) -> Set[str]:
        """Builtins have no loads."""
        return set()
    
    def get_dels(self) -> Set[str]:
        """Builtins have no dels."""
        return set()
    
    def __str__(self) -> str:
        return self._qualname
    
    def __repr__(self) -> str:
        return f"BuiltinStmt({self.name!r}, {self.kind!r})"
    
    def __hash__(self) -> int:
        return hash((self.name, self.kind))
    
    def __eq__(self, other) -> bool:
        if isinstance(other, BuiltinStmt):
            return self.name == other.name and self.kind == other.kind
        return False


@dataclass(frozen=True)
class BuiltinFunctionObject(AbstractObject):
    """Builtin function object (e.g., len, range, isinstance)."""
    name: str
    
    
@dataclass(frozen=True)
class BuiltinMethodObject(AbstractObject):
    """Builtin method object (e.g., list.append, dict.items)."""
    name: str
    owner_type: str  # 'list', 'dict', 'str', etc.


class ObjectFactory:
    """Factory for creating abstract objects."""
    
    def __init__(self):
        """Initialize object factory."""
        self._builtin_stmts: Dict[str, BuiltinStmt] = {}
    
    def get_or_create_builtin_stmt(self, name: str, kind: str = "builtin") -> BuiltinStmt:
        """Get or create a cached builtin statement shim.
        
        Args:
            name: Builtin name
            kind: Kind of builtin
        
        Returns:
            Cached or new BuiltinStmt
        """
        key = f"{name}:{kind}"
        if key not in self._builtin_stmts:
            self._builtin_stmts[key] = BuiltinStmt(name, kind)
        return self._builtin_stmts[key]
    
    def create_builtin_function(
        self,
        name: str,
        context: 'AbstractContext'
    ) -> BuiltinFunctionObject:
        """Create a builtin function object.
        
        Args:
            name: Function name (e.g., 'len', 'range')
            context: Analysis context
        
        Returns:
            BuiltinFunctionObject
        """
        stmt = self.get_or_create_builtin_stmt(name, "function")
        alloc_site = AllocSite(stmt, AllocKind.BUILTIN)
        return BuiltinFunctionObject(context, alloc_site, name)
    
    def create_builtin_method(
        self,
        name: str,
        owner_type: str,
        context: 'AbstractContext'
    ) -> BuiltinMethodObject:
        """Create a builtin method object.
        
        Args:
            name: Method name (e.g., 'append', 'items')
            owner_type: Type owning this method (e.g., 'list', 'dict')
            context: Analysis context
        
        Returns:
            BuiltinMethodObject
        """
        full_name = f"{owner_type}.{name}"
        stmt = self.get_or_create_builtin_stmt(full_name, "method")
        alloc_site = AllocSite(stmt, AllocKind.BUILTIN)
        return BuiltinMethodObject(context, alloc_site, name, owner_type)
