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
           "TupleObject", "DictObject", "SetObject", "BuiltinClassObject", "BuiltinInstanceObject",
           "BuiltinMethodObject", "BuiltinFunctionObject", "ObjectFactory"]


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
        assert isinstance(self.stmt, (str, IRStatement)), f"stmt must be an IRStatement or str, but got {type(self.stmt)}"
        assert isinstance(self.kind, AllocKind), f"kind must be an AllocKind, but got {type(self.kind)}"
        # For user-defined functions/methods/classes, stmt should be IRStatement
        # For builtin functions/methods/classes, stmt can be a string identifier
        if self.kind == AllocKind.FUNCTION or self.kind == AllocKind.METHOD or self.kind == AllocKind.CLASS:
            if not isinstance(self.stmt, str) or not self.stmt.startswith("<builtin"):
                # Only enforce IRStatement for non-builtin objects
                if not isinstance(self.stmt, str):
                    assert isinstance(self.stmt, IRStatement), f"stmt must be an IRStatement for non-builtin, but got {type(self.stmt)}"
    
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
        assert isinstance(stmt, (str, IRStatement)), f"stmt must be an IRStatement, but got {type(stmt)}"
        assert isinstance(kind, AllocKind), f"kind must be an AllocKind, but got {type(kind)}"
        if kind == AllocKind.FUNCTION or kind == AllocKind.METHOD or kind == AllocKind.CLASS:
            assert isinstance(stmt, IRStatement), f"stmt must be an IRStatement, but got {type(stmt)}"

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


@dataclass(frozen=True)
class BuiltinClassObject(AbstractObject):
    """Builtin class object (e.g., list, dict, str types).
    
    Represents the builtin type objects themselves, not instances.
    Used for calls like `list()`, `dict()`, etc.
    """
    builtin_name: str  # Name of the builtin (e.g., "list", "dict", "str")
    
    def __str__(self) -> str:
        return f"<builtin_class '{self.builtin_name}' at {self.alloc_site}>"


@dataclass(frozen=True)
class BuiltinInstanceObject(AbstractObject):
    """Builtin instance object (e.g., list instance, dict instance).
    
    Represents instances of builtin types with specific field semantics.
    """
    builtin_type: str  # Type name (e.g., "list", "dict", "str")
    
    def __str__(self) -> str:
        return f"<builtin_instance of '{self.builtin_type}' at {self.alloc_site}>"


@dataclass(frozen=True)
class BuiltinMethodObject(AbstractObject):
    """Builtin method bound to an instance.
    
    Represents methods like list.append, dict.get, etc.
    """
    method_name: str  # Method name (e.g., "append", "get")
    receiver: 'AbstractObject'  # The object this method is bound to
    
    def __str__(self) -> str:
        return f"<builtin_method '{self.method_name}' of {self.receiver}>"


@dataclass(frozen=True)
class BuiltinFunctionObject(AbstractObject):
    """Builtin function object (e.g., len, isinstance, iter).
    
    Represents standalone builtin functions, not methods.
    """
    function_name: str  # Function name (e.g., "len", "iter", "sorted")
    
    def __str__(self) -> str:
        return f"<builtin_function '{self.function_name}'>"


class ObjectFactory:
    """Factory for creating abstract objects with proper context sensitivity.
    
    Provides convenient methods for creating various kinds of abstract objects
    including builtin objects that require special handling.
    """
    
    def __init__(self):
        """Initialize object factory."""
        pass
    
    @staticmethod
    def create_builtin_class(builtin_name: str, context: 'AbstractContext') -> BuiltinClassObject:
        """Create a builtin class object.
        
        Args:
            builtin_name: Name of builtin type (e.g., "list", "dict")
            context: Analysis context
        
        Returns:
            BuiltinClassObject for the specified builtin
        """
        alloc_site = AllocSite(
            stmt=f"<builtin_class:{builtin_name}>",
            kind=AllocKind.CLASS
        )
        return BuiltinClassObject(
            context=context,
            alloc_site=alloc_site,
            builtin_name=builtin_name
        )
    
    @staticmethod
    def create_builtin_instance(
        builtin_type: str,
        context: 'AbstractContext',
        stmt: Union[str, 'IRStatement']
    ) -> BuiltinInstanceObject:
        """Create a builtin instance object.
        
        Args:
            builtin_type: Type of builtin (e.g., "list", "dict")
            context: Analysis context
            stmt: IR statement or synthetic identifier for allocation site
        
        Returns:
            BuiltinInstanceObject for the specified type
        """
        # Map builtin type name to AllocKind
        kind_map = {
            "list": AllocKind.LIST,
            "dict": AllocKind.DICT,
            "tuple": AllocKind.TUPLE,
            "set": AllocKind.SET,
        }
        kind = kind_map.get(builtin_type, AllocKind.OBJECT)
        
        alloc_site = AllocSite(stmt=stmt, kind=kind)
        return BuiltinInstanceObject(
            context=context,
            alloc_site=alloc_site,
            builtin_type=builtin_type
        )
    
    @staticmethod
    def create_builtin_method(
        method_name: str,
        receiver: 'AbstractObject',
        context: 'AbstractContext'
    ) -> BuiltinMethodObject:
        """Create a builtin method object bound to a receiver.
        
        Args:
            method_name: Name of method (e.g., "append", "get")
            receiver: Object this method is bound to
            context: Analysis context
        
        Returns:
            BuiltinMethodObject bound to the receiver
        """
        alloc_site = AllocSite(
            stmt=f"<builtin_method:{method_name}>",
            kind=AllocKind.METHOD
        )
        return BuiltinMethodObject(
            context=context,
            alloc_site=alloc_site,
            method_name=method_name,
            receiver=receiver
        )
    
    @staticmethod
    def create_builtin_function(
        function_name: str,
        context: 'AbstractContext'
    ) -> BuiltinFunctionObject:
        """Create a builtin function object.
        
        Args:
            function_name: Name of function (e.g., "len", "iter", "sorted")
            context: Analysis context
        
        Returns:
            BuiltinFunctionObject for the specified function
        """
        alloc_site = AllocSite(
            stmt=f"<builtin_function:{function_name}>",
            kind=AllocKind.BUILTIN
        )
        return BuiltinFunctionObject(
            context=context,
            alloc_site=alloc_site,
            function_name=function_name
        )
    
    @staticmethod
    def create_list(context: 'AbstractContext', stmt: Union[str, 'IRStatement']) -> 'ListObject':
        """Create a list object.
        
        Args:
            context: Analysis context
            stmt: IR statement for allocation site
        
        Returns:
            ListObject
        """
        alloc_site = AllocSite(stmt=stmt, kind=AllocKind.LIST)
        return ListObject(context=context, alloc_site=alloc_site)
    
    @staticmethod
    def create_dict(context: 'AbstractContext', stmt: Union[str, 'IRStatement']) -> 'DictObject':
        """Create a dict object.
        
        Args:
            context: Analysis context
            stmt: IR statement for allocation site
        
        Returns:
            DictObject
        """
        alloc_site = AllocSite(stmt=stmt, kind=AllocKind.DICT)
        return DictObject(context=context, alloc_site=alloc_site)
    
    @staticmethod
    def create_tuple(context: 'AbstractContext', stmt: Union[str, 'IRStatement']) -> 'TupleObject':
        """Create a tuple object.
        
        Args:
            context: Analysis context
            stmt: IR statement for allocation site
        
        Returns:
            TupleObject
        """
        alloc_site = AllocSite(stmt=stmt, kind=AllocKind.TUPLE)
        return TupleObject(context=context, alloc_site=alloc_site)
    
    @staticmethod
    def create_set(context: 'AbstractContext', stmt: Union[str, 'IRStatement']) -> 'SetObject':
        """Create a set object.
        
        Args:
            context: Analysis context
            stmt: IR statement for allocation site
        
        Returns:
            SetObject
        """
        alloc_site = AllocSite(stmt=stmt, kind=AllocKind.SET)
        return SetObject(context=context, alloc_site=alloc_site)
