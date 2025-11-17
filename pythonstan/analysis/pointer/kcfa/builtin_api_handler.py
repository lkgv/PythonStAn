"""Builtin library for pointer analysis.

This module provides a comprehensive builtin modeling system that creates
builtin objects and handles their operations through the pointer flow graph.
"""

from typing import Dict, List, Optional, Callable, TYPE_CHECKING
from dataclasses import dataclass
from enum import Enum

if TYPE_CHECKING:
    from .object import AbstractObject, BuiltinFunctionObject, BuiltinMethodObject, ObjectFactory
    from .context import AbstractContext, Scope, Ctx
    from .variable import Variable
    from .heap_model import Field
    from .state import PointerAnalysisState
    from .config import Config

__all__ = ["BuiltinLibrary", "BuiltinOperationKind"]


class BuiltinOperationKind(Enum):
    """Kinds of builtin operations."""
    CONSTRUCTOR = "constructor"
    METHOD = "method"
    FUNCTION = "function"
    BINARY_OP = "binary_op"
    UNARY_OP = "unary_op"


@dataclass
class BuiltinOperation:
    """Describes a builtin operation's behavior."""
    name: str
    kind: BuiltinOperationKind
    handler: Optional[Callable] = None


class BuiltinLibrary:
    """Central registry for Python builtin types, functions, and methods.
    
    This class maintains:
    - Builtin type prototypes (list, dict, str, etc.)
    - Builtin functions (len, range, isinstance, etc.)
    - Method registrations for each builtin type
    - Operation handlers for pointer flow
    """
    
    def __init__(self, config: 'Config', object_factory: 'ObjectFactory'):
        """Initialize builtin library.
        
        Args:
            config: Analysis configuration
            object_factory: Factory for creating builtin objects
        """
        self.config = config
        self.object_factory = object_factory
        
        # Registry of builtin functions: name -> BuiltinFunctionObject
        self._functions: Dict[str, 'BuiltinFunctionObject'] = {}
        
        # Registry of builtin types/constructors: name -> constructor info
        self._types: Dict[str, BuiltinOperation] = {}
        
        # Registry of type methods: (type_name, method_name) -> BuiltinMethodObject
        self._methods: Dict[tuple, 'BuiltinMethodObject'] = {}
        
        # Registry of operation handlers
        self._handlers: Dict[str, Callable] = {}
        
        self._initialize_builtins()
    
    def _initialize_builtins(self):
        """Initialize all builtin registrations."""
        self._register_container_types()
        self._register_builtin_functions()
        self._register_container_methods()
        self._register_binary_operators()
    
    def _register_container_types(self):
        """Register container type constructors."""
        containers = ['list', 'dict', 'tuple', 'set', 'frozenset']
        for container in containers:
            self._types[container] = BuiltinOperation(
                name=container,
                kind=BuiltinOperationKind.CONSTRUCTOR
            )
    
    def _register_builtin_functions(self):
        """Register common builtin functions."""
        # Type/introspection functions
        functions = [
            'len', 'type', 'isinstance', 'issubclass', 'hasattr', 'getattr', 
            'setattr', 'delattr', 'callable', 'id', 'hash',
            # Conversion functions
            'int', 'float', 'str', 'bool', 'bytes', 'bytearray',
            # Iterator functions
            'iter', 'next', 'enumerate', 'zip', 'map', 'filter', 'reversed',
            # Aggregate functions
            'sum', 'min', 'max', 'all', 'any', 'sorted',
            # I/O functions
            'print', 'input', 'open',
            # Other common builtins
            'range', 'abs', 'round', 'pow', 'divmod',
            'chr', 'ord', 'hex', 'oct', 'bin',
            'repr', 'ascii', 'format',
            'vars', 'dir', 'globals', 'locals',
            'super', 'property', 'staticmethod', 'classmethod',
        ]
        
        for func_name in functions:
            self._types[func_name] = BuiltinOperation(
                name=func_name,
                kind=BuiltinOperationKind.FUNCTION
            )
    
    def _register_container_methods(self):
        """Register methods for container types."""
        # List methods
        list_methods = [
            'append', 'extend', 'insert', 'remove', 'pop', 'clear',
            'index', 'count', 'sort', 'reverse', 'copy'
        ]
        
        # Dict methods
        dict_methods = [
            'get', 'keys', 'values', 'items', 'pop', 'popitem', 
            'clear', 'update', 'setdefault', 'copy', 'fromkeys'
        ]
        
        # Set methods
        set_methods = [
            'add', 'remove', 'discard', 'pop', 'clear',
            'union', 'intersection', 'difference', 'symmetric_difference',
            'update', 'intersection_update', 'difference_update',
            'issubset', 'issuperset', 'isdisjoint', 'copy'
        ]
        
        # String methods (common subset)
        str_methods = [
            'upper', 'lower', 'capitalize', 'title', 'strip', 'lstrip', 'rstrip',
            'split', 'join', 'replace', 'find', 'index', 'count',
            'startswith', 'endswith', 'isdigit', 'isalpha', 'isalnum',
            'format', 'encode'
        ]
        
        # Register all methods
        for method in list_methods:
            key = ('list', method)
            self._methods[key] = method
        
        for method in dict_methods:
            key = ('dict', method)
            self._methods[key] = method
        
        for method in set_methods:
            key = ('set', method)
            self._methods[key] = method
        
        for method in str_methods:
            key = ('str', method)
            self._methods[key] = method
    
    def _register_binary_operators(self):
        """Register binary operators (+, -, *, etc.)."""
        operators = [
            '__add__', '__sub__', '__mul__', '__truediv__', '__floordiv__',
            '__mod__', '__pow__', '__and__', '__or__', '__xor__',
            '__lshift__', '__rshift__', '__eq__', '__ne__', '__lt__',
            '__le__', '__gt__', '__ge__', '__contains__', '__getitem__',
            '__setitem__', '__delitem__'
        ]
        
        for op in operators:
            self._types[op] = BuiltinOperation(
                name=op,
                kind=BuiltinOperationKind.BINARY_OP
            )
    
    def get_builtin_function(self, name: str, context: 'AbstractContext') -> Optional['BuiltinFunctionObject']:
        """Get or create a builtin function object.
        
        Args:
            name: Function name
            context: Analysis context
        
        Returns:
            BuiltinFunctionObject if registered, None otherwise
        """
        if name not in self._types and name not in self._functions:
            return None
        
        if name not in self._functions:
            self._functions[name] = self.object_factory.create_builtin_function(name, context)
        
        return self._functions[name]
    
    def get_builtin_method(
        self,
        owner_type: str,
        method_name: str,
        context: 'AbstractContext'
    ) -> Optional['BuiltinMethodObject']:
        """Get or create a builtin method object.
        
        Args:
            owner_type: Type owning the method (e.g., 'list', 'dict')
            method_name: Method name
            context: Analysis context
        
        Returns:
            BuiltinMethodObject if registered, None otherwise
        """
        key = (owner_type, method_name)
        if key not in self._methods:
            return None
        
        if not isinstance(self._methods[key], str):
            return self._methods[key]
        
        # Create and cache the method object
        method_obj = self.object_factory.create_builtin_method(
            method_name, owner_type, context
        )
        self._methods[key] = method_obj
        return method_obj
    
    def has_builtin(self, name: str) -> bool:
        """Check if a name is a registered builtin.
        
        Args:
            name: Name to check
        
        Returns:
            True if registered as builtin
        """
        return name in self._types or name in self._functions
    
    def is_container_type(self, type_name: str) -> bool:
        """Check if a type is a container type.
        
        Args:
            type_name: Type name
        
        Returns:
            True if container type
        """
        return type_name in ['list', 'dict', 'tuple', 'set', 'frozenset']
    
    def handle_builtin_call(
        self,
        state: 'PointerAnalysisState',
        scope: 'Scope',
        context: 'AbstractContext',
        builtin_obj: 'AbstractObject',
        args: List['Ctx[Variable]'],
        kwargs: Dict[str, 'Ctx[Variable]'],
        target: Optional['Ctx[Variable]']
    ) -> bool:
        """Handle a builtin function/method call.
        
        This dispatches to specific handlers based on the builtin being called.
        
        Args:
            state: Pointer analysis state
            scope: Current scope
            context: Current context
            builtin_obj: Builtin object being called
            args: Argument variables
            kwargs: Keyword argument variables
            target: Target variable for return value
        
        Returns:
            True if handled successfully
        """
        from .object import BuiltinFunctionObject, BuiltinMethodObject, BuiltinStmt
        
        # Determine the builtin name
        if isinstance(builtin_obj, BuiltinFunctionObject):
            name = builtin_obj.name
            return self._handle_builtin_function(state, scope, context, name, args, kwargs, target)
        elif isinstance(builtin_obj, BuiltinMethodObject):
            return self._handle_builtin_method(
                state, scope, context, builtin_obj.owner_type, 
                builtin_obj.name, args, kwargs, target
            )
        elif isinstance(builtin_obj.alloc_site.stmt, BuiltinStmt):
            name = builtin_obj.alloc_site.stmt.name
            return self._handle_builtin_function(state, scope, context, name, args, kwargs, target)
        
        return False
    
    def _handle_builtin_function(
        self,
        state: 'PointerAnalysisState',
        scope: 'Scope',
        context: 'AbstractContext',
        name: str,
        args: List['Ctx[Variable]'],
        kwargs: Dict[str, 'Ctx[Variable]'],
        target: Optional['Ctx[Variable]']
    ) -> bool:
        """Handle builtin function call."""
        # Container constructors
        if name in ['list', 'dict', 'tuple', 'set', 'frozenset']:
            return self._handle_container_constructor(
                state, scope, context, name, args, target
            )
        
        # len() returns a constant
        if name == 'len':
            return self._handle_len(state, scope, context, args, target)
        
        # iter() creates iterator
        if name == 'iter':
            return self._handle_iter(state, scope, context, args, target)
        
        # For other builtins, create a generic result object
        if target:
            from .object import AllocSite, AllocKind, ConstantObject
            alloc_site = AllocSite(
                self.object_factory.get_or_create_builtin_stmt(f"{name}_result", "result"),
                AllocKind.OBJECT
            )
            result_obj = ConstantObject(context, alloc_site, f"<{name}_result>")
            from .points_to_set import PointsToSet
            from .pointer_flow_graph import NormalNode
            state._worklist.add((scope, NormalNode(target), PointsToSet.singleton(result_obj)))
            return True
        
        return False
    
    def _handle_container_constructor(
        self,
        state: 'PointerAnalysisState',
        scope: 'Scope',
        context: 'AbstractContext',
        container_type: str,
        args: List['Ctx[Variable]'],
        target: Optional['Ctx[Variable]']
    ) -> bool:
        """Handle container constructor call."""
        if not target:
            return False
        
        from .object import AllocSite, AllocKind, ListObject, DictObject, TupleObject, SetObject
        from .points_to_set import PointsToSet
        from .pointer_flow_graph import NormalNode
        from .heap_model import elem
        
        # Create appropriate container object
        stmt = self.object_factory.get_or_create_builtin_stmt(f"{container_type}_constructor", "constructor")
        alloc_site = AllocSite(stmt, getattr(AllocKind, container_type.upper()))
        
        if container_type == 'list':
            container_obj = ListObject(context, alloc_site)
        elif container_type == 'dict':
            container_obj = DictObject(context, alloc_site)
        elif container_type == 'tuple':
            container_obj = TupleObject(context, alloc_site)
        elif container_type == 'set':
            container_obj = SetObject(context, alloc_site)
        else:
            return False
        
        # Add container to target
        state._worklist.add((scope, NormalNode(target), PointsToSet.singleton(container_obj)))
        
        # If constructor has arguments, flow them to container elements
        if args and len(args) > 0:
            # Get element field of container
            field_access = state.get_field(scope, context, container_obj, elem())
            # Flow first argument (iterable) elements to container
            state._add_var_points_flow(args[0], field_access)
        
        return True
    
    def _handle_len(
        self,
        state: 'PointerAnalysisState',
        scope: 'Scope',
        context: 'AbstractContext',
        args: List['Ctx[Variable]'],
        target: Optional['Ctx[Variable]']
    ) -> bool:
        """Handle len() builtin."""
        if not target or len(args) == 0:
            return False
        
        from .object import AllocSite, AllocKind, ConstantObject
        from .points_to_set import PointsToSet
        from .pointer_flow_graph import NormalNode
        
        # len() returns an integer constant
        stmt = self.object_factory.get_or_create_builtin_stmt("len_result", "result")
        alloc_site = AllocSite(stmt, AllocKind.CONSTANT)
        result_obj = ConstantObject(context, alloc_site, "<int>")
        
        state._worklist.add((scope, NormalNode(target), PointsToSet.singleton(result_obj)))
        return True
    
    def _handle_iter(
        self,
        state: 'PointerAnalysisState',
        scope: 'Scope',
        context: 'AbstractContext',
        args: List['Ctx[Variable]'],
        target: Optional['Ctx[Variable]']
    ) -> bool:
        """Handle iter() builtin."""
        if not target or len(args) == 0:
            return False
        
        from .object import AllocSite, AllocKind, AbstractObject
        from .points_to_set import PointsToSet
        from .pointer_flow_graph import NormalNode
        from .heap_model import elem
        
        # Create iterator object
        stmt = self.object_factory.get_or_create_builtin_stmt("iter_result", "result")
        alloc_site = AllocSite(stmt, AllocKind.OBJECT)
        iter_obj = AbstractObject(context, alloc_site)
        
        state._worklist.add((scope, NormalNode(target), PointsToSet.singleton(iter_obj)))
        
        # Link iterator elements to source container elements
        iter_field = state.get_field(scope, context, iter_obj, elem())
        
        # Flow elements from source to iterator
        src_pts = state.get_points_to(args[0])
        for src_obj in src_pts:
            src_field = state.get_field(scope, context, src_obj, elem())
            state._add_var_points_flow(src_field, iter_field)
        
        return True
    
    def _handle_builtin_method(
        self,
        state: 'PointerAnalysisState',
        scope: 'Scope',
        context: 'AbstractContext',
        owner_type: str,
        method_name: str,
        args: List['Ctx[Variable]'],
        kwargs: Dict[str, 'Ctx[Variable]'],
        target: Optional['Ctx[Variable]']
    ) -> bool:
        """Handle builtin method call."""
        
        # List methods
        if owner_type == 'list':
            if method_name == 'append' and len(args) >= 2:
                return self._handle_list_append(state, scope, context, args[0], args[1])
            elif method_name == 'pop' and target and len(args) >= 1:
                return self._handle_list_pop(state, scope, context, args[0], target)
            elif method_name == 'extend' and len(args) >= 2:
                return self._handle_list_extend(state, scope, context, args[0], args[1])
        
        # Dict methods
        elif owner_type == 'dict':
            if method_name == 'items' and target and len(args) >= 1:
                return self._handle_dict_items(state, scope, context, args[0], target)
            elif method_name == 'keys' and target and len(args) >= 1:
                return self._handle_dict_keys(state, scope, context, args[0], target)
            elif method_name == 'values' and target and len(args) >= 1:
                return self._handle_dict_values(state, scope, context, args[0], target)
            elif method_name == 'get' and len(args) >= 2 and target:
                return self._handle_dict_get(state, scope, context, args[0], args[1], target)
        
        # Set methods
        elif owner_type == 'set':
            if method_name == 'add' and len(args) >= 2:
                return self._handle_set_add(state, scope, context, args[0], args[1])
            elif method_name == 'pop' and target and len(args) >= 1:
                return self._handle_set_pop(state, scope, context, args[0], target)
        
        return False
    
    def _handle_list_append(
        self,
        state: 'PointerAnalysisState',
        scope: 'Scope',
        context: 'AbstractContext',
        list_var: 'Ctx[Variable]',
        item_var: 'Ctx[Variable]'
    ) -> bool:
        """Handle list.append(item)."""
        from .heap_model import elem
        
        # Get list objects
        list_pts = state.get_points_to(list_var)
        for list_obj in list_pts:
            # Get element field
            elem_field = state.get_field(scope, context, list_obj, elem())
            # Flow item to elements
            state._add_var_points_flow(item_var, elem_field)
        
        return True
    
    def _handle_list_pop(
        self,
        state: 'PointerAnalysisState',
        scope: 'Scope',
        context: 'AbstractContext',
        list_var: 'Ctx[Variable]',
        target: 'Ctx[Variable]'
    ) -> bool:
        """Handle target = list.pop()."""
        from .heap_model import elem
        
        # Get list objects
        list_pts = state.get_points_to(list_var)
        for list_obj in list_pts:
            # Get element field
            elem_field = state.get_field(scope, context, list_obj, elem())
            # Flow elements to target
            state._add_var_points_flow(elem_field, target)
        
        return True
    
    def _handle_list_extend(
        self,
        state: 'PointerAnalysisState',
        scope: 'Scope',
        context: 'AbstractContext',
        list_var: 'Ctx[Variable]',
        iterable_var: 'Ctx[Variable]'
    ) -> bool:
        """Handle list.extend(iterable)."""
        from .heap_model import elem
        
        # Get list objects
        list_pts = state.get_points_to(list_var)
        iterable_pts = state.get_points_to(iterable_var)
        
        for list_obj in list_pts:
            list_elem = state.get_field(scope, context, list_obj, elem())
            
            for iter_obj in iterable_pts:
                # Flow iterable elements to list elements
                iter_elem = state.get_field(scope, context, iter_obj, elem())
                state._add_var_points_flow(iter_elem, list_elem)
        
        return True
    
    def _handle_dict_items(
        self,
        state: 'PointerAnalysisState',
        scope: 'Scope',
        context: 'AbstractContext',
        dict_var: 'Ctx[Variable]',
        target: 'Ctx[Variable]'
    ) -> bool:
        """Handle target = dict.items()."""
        from .object import AllocSite, AllocKind, ListObject
        from .points_to_set import PointsToSet
        from .pointer_flow_graph import NormalNode
        from .heap_model import elem
        
        # Create list of tuples
        stmt = self.object_factory.get_or_create_builtin_stmt("dict_items_result", "result")
        alloc_site = AllocSite(stmt, AllocKind.LIST)
        items_list = ListObject(context, alloc_site)
        
        state._worklist.add((scope, NormalNode(target), PointsToSet.singleton(items_list)))
        
        # Flow dict values to list elements (simplified - should create tuples)
        dict_pts = state.get_points_to(dict_var)
        list_elem = state.get_field(scope, context, items_list, elem())
        
        for dict_obj in dict_pts:
            dict_elem = state.get_field(scope, context, dict_obj, elem())
            state._add_var_points_flow(dict_elem, list_elem)
        
        return True
    
    def _handle_dict_keys(
        self,
        state: 'PointerAnalysisState',
        scope: 'Scope',
        context: 'AbstractContext',
        dict_var: 'Ctx[Variable]',
        target: 'Ctx[Variable]'
    ) -> bool:
        """Handle target = dict.keys()."""
        from .object import AllocSite, AllocKind, ListObject
        from .points_to_set import PointsToSet
        from .pointer_flow_graph import NormalNode
        
        # Create list-like object for keys
        stmt = self.object_factory.get_or_create_builtin_stmt("dict_keys_result", "result")
        alloc_site = AllocSite(stmt, AllocKind.LIST)
        keys_obj = ListObject(context, alloc_site)
        
        state._worklist.add((scope, NormalNode(target), PointsToSet.singleton(keys_obj)))
        return True
    
    def _handle_dict_values(
        self,
        state: 'PointerAnalysisState',
        scope: 'Scope',
        context: 'AbstractContext',
        dict_var: 'Ctx[Variable]',
        target: 'Ctx[Variable]'
    ) -> bool:
        """Handle target = dict.values()."""
        from .object import AllocSite, AllocKind, ListObject
        from .points_to_set import PointsToSet
        from .pointer_flow_graph import NormalNode
        from .heap_model import elem
        
        # Create list-like object for values
        stmt = self.object_factory.get_or_create_builtin_stmt("dict_values_result", "result")
        alloc_site = AllocSite(stmt, AllocKind.LIST)
        values_obj = ListObject(context, alloc_site)
        
        state._worklist.add((scope, NormalNode(target), PointsToSet.singleton(values_obj)))
        
        # Flow dict values to result
        dict_pts = state.get_points_to(dict_var)
        values_elem = state.get_field(scope, context, values_obj, elem())
        
        for dict_obj in dict_pts:
            dict_elem = state.get_field(scope, context, dict_obj, elem())
            state._add_var_points_flow(dict_elem, values_elem)
        
        return True
    
    def _handle_dict_get(
        self,
        state: 'PointerAnalysisState',
        scope: 'Scope',
        context: 'AbstractContext',
        dict_var: 'Ctx[Variable]',
        key_var: 'Ctx[Variable]',
        target: 'Ctx[Variable]'
    ) -> bool:
        """Handle target = dict.get(key)."""
        from .heap_model import elem
        
        # Flow dict values to target
        dict_pts = state.get_points_to(dict_var)
        for dict_obj in dict_pts:
            dict_elem = state.get_field(scope, context, dict_obj, elem())
            state._add_var_points_flow(dict_elem, target)
        
        return True
    
    def _handle_set_add(
        self,
        state: 'PointerAnalysisState',
        scope: 'Scope',
        context: 'AbstractContext',
        set_var: 'Ctx[Variable]',
        item_var: 'Ctx[Variable]'
    ) -> bool:
        """Handle set.add(item)."""
        from .heap_model import elem
        
        # Get set objects
        set_pts = state.get_points_to(set_var)
        for set_obj in set_pts:
            # Get element field
            elem_field = state.get_field(scope, context, set_obj, elem())
            # Flow item to elements
            state._add_var_points_flow(item_var, elem_field)
        
        return True
    
    def _handle_set_pop(
        self,
        state: 'PointerAnalysisState',
        scope: 'Scope',
        context: 'AbstractContext',
        set_var: 'Ctx[Variable]',
        target: 'Ctx[Variable]'
    ) -> bool:
        """Handle target = set.pop()."""
        from .heap_model import elem
        
        # Get set objects
        set_pts = state.get_points_to(set_var)
        for set_obj in set_pts:
            # Get element field
            elem_field = state.get_field(scope, context, set_obj, elem())
            # Flow elements to target
            state._add_var_points_flow(elem_field, target)
        
        return True
