"""Builtin function and method handling for pointer analysis.

This module provides handlers for Python builtin functions and methods that
model their pointer effects using PFG edges and lazy constraints.

Design:
- Builtin classes/functions/methods are represented as specialized objects
- Operations use PFG edges for direct dataflows
- Lazy constraints are added when new objects might be created later
"""

from typing import List, Optional, Dict, Set, TYPE_CHECKING
import logging

if TYPE_CHECKING:
    from .constraints import Constraint, CallConstraint, LoadConstraint, StoreConstraint
    from .variable import Variable, FieldAccess
    from .context import AbstractContext, Ctx, Scope
    from .config import Config
    from .state import PointerAnalysisState
    from .object import (
        AbstractObject, BuiltinInstanceObject, BuiltinMethodObject,
        BuiltinClassObject, BuiltinFunctionObject
    )

logger = logging.getLogger(__name__)

__all__ = ["BuiltinAPIHandler", "BuiltinSummaryManager"]


class BuiltinAPIHandler:
    """Handler for builtin function and method operations.
    
    Uses PFG edges for direct dataflows and constraints for lazy processing.
    """
    
    # Builtin container types
    CONTAINER_TYPES = {"list", "dict", "tuple", "set", "frozenset"}
    
    # List methods that modify elements
    LIST_MUTATOR_METHODS = {"append", "extend", "insert", "__setitem__"}
    LIST_ACCESSOR_METHODS = {"__getitem__", "__iter__", "pop", "index"}
    
    # Dict methods
    DICT_MUTATOR_METHODS = {"__setitem__", "update", "setdefault"}
    DICT_ACCESSOR_METHODS = {"__getitem__", "get", "pop", "keys", "values", "items", "__iter__"}
    
    # Set methods
    SET_MUTATOR_METHODS = {"add", "update", "discard", "remove"}
    SET_ACCESSOR_METHODS = {"__iter__", "pop"}
    
    # Iterator/functional builtins
    ITERATOR_BUILTINS = {"iter", "next", "enumerate", "zip", "map", "filter", "reversed", "range"}
    
    # Scalar/type builtins
    TYPE_BUILTINS = {"len", "isinstance", "issubclass", "type", "bool", "int", "float", "str", "bytes"}
    
    def __init__(self, state: 'PointerAnalysisState', config: 'Config'):
        """Initialize builtin handler.
        
        Args:
            state: Pointer analysis state
            config: Analysis configuration
        """
        self.state = state
        self.config = config
        self._method_handlers: Dict[str, callable] = self._init_method_handlers()
        self._function_handlers: Dict[str, callable] = self._init_function_handlers()
    
    def _init_method_handlers(self) -> Dict[str, callable]:
        """Initialize method handler dispatch table."""
        return {
            # List methods
            "append": self._handle_list_append,
            "extend": self._handle_list_extend,
            "insert": self._handle_list_insert,
            "pop": self._handle_list_pop,
            "__getitem__": self._handle_container_getitem,
            "__setitem__": self._handle_container_setitem,
            "__iter__": self._handle_container_iter,
            
            # Dict methods
            "get": self._handle_dict_get,
            "update": self._handle_dict_update,
            "setdefault": self._handle_dict_setdefault,
            "keys": self._handle_dict_keys,
            "values": self._handle_dict_values,
            "items": self._handle_dict_items,
            
            # Set methods
            "add": self._handle_set_add,
            "discard": self._handle_set_discard,
            "remove": self._handle_set_remove,
        }
    
    def _init_function_handlers(self) -> Dict[str, callable]:
        """Initialize function handler dispatch table."""
        return {
            # Container constructors
            "list": self._handle_list_constructor,
            "dict": self._handle_dict_constructor,
            "tuple": self._handle_tuple_constructor,
            "set": self._handle_set_constructor,
            
            # Iterator functions
            "iter": self._handle_iter,
            "next": self._handle_next,
            "enumerate": self._handle_enumerate,
            "zip": self._handle_zip,
            "map": self._handle_map,
            "filter": self._handle_filter,
            "reversed": self._handle_reversed,
            "sorted": self._handle_sorted,
            "range": self._handle_range,
            
            # Type/scalar functions
            "len": self._handle_len,
            "isinstance": self._handle_isinstance,
            "type": self._handle_type,
            "bool": self._handle_bool,
            "int": self._handle_int,
            "float": self._handle_float,
            "str": self._handle_str,
        }
    
    def handle_builtin_call(
        self,
        scope: 'Scope',
        context: 'AbstractContext',
        call: 'CallConstraint',
        builtin_obj: 'AbstractObject'
    ) -> List['Constraint']:
        """Handle a call to a builtin function or method.
        
        Returns constraints to be added to the solver for lazy processing.
        
        Args:
            scope: Current scope
            context: Current context
            call: Call constraint
            builtin_obj: Builtin object being called
        
        Returns:
            List of constraints to process
        """
        from .object import (
            BuiltinClassObject, BuiltinFunctionObject,
            BuiltinMethodObject, BuiltinInstanceObject
        )
        
        constraints = []
        
        # Dispatch based on builtin object type
        if isinstance(builtin_obj, BuiltinClassObject):
            # Calling a builtin class as constructor
            handler = self._function_handlers.get(builtin_obj.builtin_name)
            if handler:
                constraints = handler(scope, context, call)
            else:
                # Generic constructor
                constraints = self._handle_generic_constructor(scope, context, call, builtin_obj.builtin_name)
        
        elif isinstance(builtin_obj, BuiltinFunctionObject):
            # Calling a builtin function
            handler = self._function_handlers.get(builtin_obj.function_name)
            if handler:
                constraints = handler(scope, context, call)
            else:
                # Generic builtin function
                constraints = self._handle_generic_builtin(scope, context, call, builtin_obj.function_name)
        
        elif isinstance(builtin_obj, BuiltinMethodObject):
            # Calling a builtin method
            handler = self._method_handlers.get(builtin_obj.method_name)
            if handler:
                constraints = handler(scope, context, call, builtin_obj.receiver)
            else:
                # Generic method
                constraints = self._handle_generic_method(scope, context, call, builtin_obj)
        
        return constraints
    
    # ===== Container Constructors =====
    
    def _handle_list_constructor(
        self,
        scope: 'Scope',
        context: 'AbstractContext',
        call: 'CallConstraint'
    ) -> List['Constraint']:
        """Handle list() constructor call.
        
        Creates a new list instance. If an iterable is provided, adds
        constraints to copy elements from the iterable to the list.
        """
        from .constraints import AllocConstraint, LoadConstraint, StoreConstraint
        from .object import AllocSite, AllocKind
        from .heap_model import elem
        
        constraints = []
        
        if not call.target:
            return constraints
        
        # Create allocation constraint for new list
        alloc_site = AllocSite(stmt=call.call_site, kind=AllocKind.LIST)
        constraints.append(AllocConstraint(target=call.target, alloc_site=alloc_site))
        
        # If iterable argument provided, copy elements
        if len(call.args) > 0:
            iterable_var = call.args[0]
            # Load from iterable.elem() and store to list.elem()
            # This is done via LoadConstraint which will be applied when solver processes it
            constraints.append(LoadConstraint(
                base=iterable_var,
                field=elem(),
                target=call.target
            ))
        
        return constraints
    
    def _handle_dict_constructor(
        self,
        scope: 'Scope',
        context: 'AbstractContext',
        call: 'CallConstraint'
    ) -> List['Constraint']:
        """Handle dict() constructor call."""
        from .constraints import AllocConstraint
        from .object import AllocSite, AllocKind
        
        constraints = []
        
        if not call.target:
            return constraints
        
        alloc_site = AllocSite(stmt=call.call_site, kind=AllocKind.DICT)
        constraints.append(AllocConstraint(target=call.target, alloc_site=alloc_site))
        
        # TODO: Handle dict(**kwargs) and dict(iterable) properly
        
        return constraints
    
    def _handle_tuple_constructor(
        self,
        scope: 'Scope',
        context: 'AbstractContext',
        call: 'CallConstraint'
    ) -> List['Constraint']:
        """Handle tuple() constructor call."""
        from .constraints import AllocConstraint, LoadConstraint
        from .object import AllocSite, AllocKind
        from .heap_model import elem
        
        constraints = []
        
        if not call.target:
            return constraints
        
        alloc_site = AllocSite(stmt=call.call_site, kind=AllocKind.TUPLE)
        constraints.append(AllocConstraint(target=call.target, alloc_site=alloc_site))
        
        # If iterable provided, copy elements
        if len(call.args) > 0:
            iterable_var = call.args[0]
            constraints.append(LoadConstraint(
                base=iterable_var,
                field=elem(),
                target=call.target
            ))
        
        return constraints
    
    def _handle_set_constructor(
        self,
        scope: 'Scope',
        context: 'AbstractContext',
        call: 'CallConstraint'
    ) -> List['Constraint']:
        """Handle set() constructor call."""
        from .constraints import AllocConstraint, LoadConstraint
        from .object import AllocSite, AllocKind
        from .heap_model import elem
        
        constraints = []
        
        if not call.target:
            return constraints
        
        alloc_site = AllocSite(stmt=call.call_site, kind=AllocKind.SET)
        constraints.append(AllocConstraint(target=call.target, alloc_site=alloc_site))
        
        # If iterable provided, copy elements
        if len(call.args) > 0:
            iterable_var = call.args[0]
            constraints.append(LoadConstraint(
                base=iterable_var,
                field=elem(),
                target=call.target
            ))
        
        return constraints
    
    # ===== List Methods =====
    
    def _handle_list_append(
        self,
        scope: 'Scope',
        context: 'AbstractContext',
        call: 'CallConstraint',
        receiver: 'AbstractObject'
    ) -> List['Constraint']:
        """Handle list.append(x).
        
        Stores x to list.elem() via constraint.
        """
        from .constraints import StoreConstraint, CopyConstraint
        from .heap_model import elem
        from .variable import VariableFactory
        
        constraints = []
        
        if len(call.args) > 0:
            # Get the first argument (the item to append)
            item_var = call.args[0]
            
            # Create a temporary variable representing the receiver
            # We need to find the variable that points to receiver
            # For now, create a StoreConstraint that will be processed when
            # the receiver variable is known
            # This is a simplification - in practice we'd track the receiver variable
            
            # Add constraint to store item to receiver.elem()
            # Note: This requires knowing which variable points to receiver
            # For now, we'll handle this in the solver when applying the constraint
            pass
        
        return constraints
    
    def _handle_list_extend(
        self,
        scope: 'Scope',
        context: 'AbstractContext',
        call: 'CallConstraint',
        receiver: 'AbstractObject'
    ) -> List['Constraint']:
        """Handle list.extend(iterable).
        
        Loads from iterable.elem() and stores to list.elem().
        """
        constraints = []
        
        # Similar to append, but loads from iterable
        if len(call.args) > 0:
            iterable_var = call.args[0]
            # Add constraint to copy iterable.elem() to receiver.elem()
            pass
        
        return constraints
    
    def _handle_list_insert(
        self,
        scope: 'Scope',
        context: 'AbstractContext',
        call: 'CallConstraint',
        receiver: 'AbstractObject'
    ) -> List['Constraint']:
        """Handle list.insert(index, x).
        
        Stores x to list.elem().
        """
        constraints = []
        
        if len(call.args) > 1:
            item_var = call.args[1]  # Second arg is the item
            # Add constraint to store item to receiver.elem()
            pass
        
        return constraints
    
    def _handle_list_pop(
        self,
        scope: 'Scope',
        context: 'AbstractContext',
        call: 'CallConstraint',
        receiver: 'AbstractObject'
    ) -> List['Constraint']:
        """Handle list.pop([index]).
        
        Loads from list.elem() to return value.
        """
        from .constraints import LoadConstraint
        from .heap_model import elem
        
        constraints = []
        
        if call.target:
            # Add constraint to load from receiver.elem()
            # This requires creating a variable for the receiver
            pass
        
        return constraints
    
    # ===== Container Methods =====
    
    def _handle_container_getitem(
        self,
        scope: 'Scope',
        context: 'AbstractContext',
        call: 'CallConstraint',
        receiver: 'AbstractObject'
    ) -> List['Constraint']:
        """Handle container.__getitem__(key).
        
        For lists/tuples: loads from elem()
        For dicts: loads from key(k) if constant, else elem()
        """
        from .constraints import LoadSubscrConstraint
        
        constraints = []
        
        if call.target and len(call.args) > 0:
            index_var = call.args[0]
            # Add LoadSubscrConstraint which will be resolved based on index type
            # This will be handled in solver's _apply_load_subscr
            pass
        
        return constraints
    
    def _handle_container_setitem(
        self,
        scope: 'Scope',
        context: 'AbstractContext',
        call: 'CallConstraint',
        receiver: 'AbstractObject'
    ) -> List['Constraint']:
        """Handle container.__setitem__(key, value).
        
        Stores value to appropriate field based on key type.
        """
        from .constraints import StoreSubscrConstraint
        
        constraints = []
        
        if len(call.args) > 1:
            key_var = call.args[0]
            value_var = call.args[1]
            # Add StoreSubscrConstraint
            pass
        
        return constraints
    
    def _handle_container_iter(
        self,
        scope: 'Scope',
        context: 'AbstractContext',
        call: 'CallConstraint',
        receiver: 'AbstractObject'
    ) -> List['Constraint']:
        """Handle container.__iter__().
        
        Creates iterator object that yields container elements.
        """
        from .constraints import AllocConstraint, LoadConstraint
        from .object import AllocSite, AllocKind
        from .heap_model import elem
        
        constraints = []
        
        if call.target:
            # Create iterator object
            alloc_site = AllocSite(stmt=call.call_site, kind=AllocKind.OBJECT)
            constraints.append(AllocConstraint(target=call.target, alloc_site=alloc_site))
            
            # Link iterator to container elements
            # The iterator's elem() field should point to container's elem()
            pass
        
        return constraints
    
    # ===== Dict Methods =====
    
    def _handle_dict_get(
        self,
        scope: 'Scope',
        context: 'AbstractContext',
        call: 'CallConstraint',
        receiver: 'AbstractObject'
    ) -> List['Constraint']:
        """Handle dict.get(key, default=None).
        
        Loads from dict.key(k) if constant, else dict.elem().
        """
        from .constraints import LoadSubscrConstraint
        
        constraints = []
        
        if call.target and len(call.args) > 0:
            key_var = call.args[0]
            # Add LoadSubscrConstraint
            pass
        
        return constraints
    
    def _handle_dict_update(
        self,
        scope: 'Scope',
        context: 'AbstractContext',
        call: 'CallConstraint',
        receiver: 'AbstractObject'
    ) -> List['Constraint']:
        """Handle dict.update(other)."""
        constraints = []
        
        if len(call.args) > 0:
            other_var = call.args[0]
            # Copy other's values to receiver
            pass
        
        return constraints
    
    def _handle_dict_setdefault(
        self,
        scope: 'Scope',
        context: 'AbstractContext',
        call: 'CallConstraint',
        receiver: 'AbstractObject'
    ) -> List['Constraint']:
        """Handle dict.setdefault(key, default)."""
        constraints = []
        
        if len(call.args) > 1:
            key_var = call.args[0]
            default_var = call.args[1]
            # Store default to dict and load from dict to target
            pass
        
        return constraints
    
    def _handle_dict_keys(
        self,
        scope: 'Scope',
        context: 'AbstractContext',
        call: 'CallConstraint',
        receiver: 'AbstractObject'
    ) -> List['Constraint']:
        """Handle dict.keys()."""
        from .constraints import AllocConstraint
        from .object import AllocSite, AllocKind
        
        constraints = []
        
        if call.target:
            # Create dict_keys object
            alloc_site = AllocSite(stmt=call.call_site, kind=AllocKind.OBJECT)
            constraints.append(AllocConstraint(target=call.target, alloc_site=alloc_site))
        
        return constraints
    
    def _handle_dict_values(
        self,
        scope: 'Scope',
        context: 'AbstractContext',
        call: 'CallConstraint',
        receiver: 'AbstractObject'
    ) -> List['Constraint']:
        """Handle dict.values()."""
        from .constraints import AllocConstraint
        from .object import AllocSite, AllocKind
        
        constraints = []
        
        if call.target:
            # Create dict_values object that yields dict values
            alloc_site = AllocSite(stmt=call.call_site, kind=AllocKind.OBJECT)
            constraints.append(AllocConstraint(target=call.target, alloc_site=alloc_site))
            # TODO: Link to dict values via elem()
        
        return constraints
    
    def _handle_dict_items(
        self,
        scope: 'Scope',
        context: 'AbstractContext',
        call: 'CallConstraint',
        receiver: 'AbstractObject'
    ) -> List['Constraint']:
        """Handle dict.items()."""
        from .constraints import AllocConstraint
        from .object import AllocSite, AllocKind
        
        constraints = []
        
        if call.target:
            # Create dict_items object
            alloc_site = AllocSite(stmt=call.call_site, kind=AllocKind.OBJECT)
            constraints.append(AllocConstraint(target=call.target, alloc_site=alloc_site))
            # TODO: Link to dict items (tuples of key-value pairs)
        
        return constraints
    
    # ===== Set Methods =====
    
    def _handle_set_add(
        self,
        scope: 'Scope',
        context: 'AbstractContext',
        call: 'CallConstraint',
        receiver: 'AbstractObject'
    ) -> List['Constraint']:
        """Handle set.add(x).
        
        Stores x to set.elem().
        """
        constraints = []
        
        if len(call.args) > 0:
            item_var = call.args[0]
            # Store item to receiver.elem()
            pass
        
        return constraints
    
    def _handle_set_discard(
        self,
        scope: 'Scope',
        context: 'AbstractContext',
        call: 'CallConstraint',
        receiver: 'AbstractObject'
    ) -> List['Constraint']:
        """Handle set.discard(x)."""
        # No-op for pointer analysis
        return []
    
    def _handle_set_remove(
        self,
        scope: 'Scope',
        context: 'AbstractContext',
        call: 'CallConstraint',
        receiver: 'AbstractObject'
    ) -> List['Constraint']:
        """Handle set.remove(x)."""
        # No-op for pointer analysis
        return []
    
    # ===== Iterator Functions =====
    
    def _handle_iter(
        self,
        scope: 'Scope',
        context: 'AbstractContext',
        call: 'CallConstraint'
    ) -> List['Constraint']:
        """Handle iter(iterable).
        
        Creates iterator linked to iterable elements.
        """
        from .constraints import AllocConstraint, LoadConstraint
        from .object import AllocSite, AllocKind
        from .heap_model import elem
        
        constraints = []
        
        if call.target and len(call.args) > 0:
            iterable_var = call.args[0]
            
            # Create iterator
            alloc_site = AllocSite(stmt=call.call_site, kind=AllocKind.OBJECT)
            constraints.append(AllocConstraint(target=call.target, alloc_site=alloc_site))
            
            # Link to iterable elements
            constraints.append(LoadConstraint(
                base=iterable_var,
                field=elem(),
                target=call.target
            ))
        
        return constraints
    
    def _handle_next(
        self,
        scope: 'Scope',
        context: 'AbstractContext',
        call: 'CallConstraint'
    ) -> List['Constraint']:
        """Handle next(iterator).
        
        Loads from iterator.elem().
        """
        from .constraints import LoadConstraint
        from .heap_model import elem
        
        constraints = []
        
        if call.target and len(call.args) > 0:
            iterator_var = call.args[0]
            
            # Load from iterator.elem()
            constraints.append(LoadConstraint(
                base=iterator_var,
                field=elem(),
                target=call.target
            ))
        
        return constraints
    
    def _handle_enumerate(
        self,
        scope: 'Scope',
        context: 'AbstractContext',
        call: 'CallConstraint'
    ) -> List['Constraint']:
        """Handle enumerate(iterable).
        
        Creates enumerate iterator that yields (index, item) tuples.
        """
        from .constraints import AllocConstraint
        from .object import AllocSite, AllocKind
        
        constraints = []
        
        if call.target:
            # Create enumerate iterator
            alloc_site = AllocSite(stmt=call.call_site, kind=AllocKind.OBJECT)
            constraints.append(AllocConstraint(target=call.target, alloc_site=alloc_site))
            # TODO: Link to iterable elements and create tuples
        
        return constraints
    
    def _handle_zip(
        self,
        scope: 'Scope',
        context: 'AbstractContext',
        call: 'CallConstraint'
    ) -> List['Constraint']:
        """Handle zip(*iterables)."""
        from .constraints import AllocConstraint
        from .object import AllocSite, AllocKind
        
        constraints = []
        
        if call.target:
            # Create zip iterator
            alloc_site = AllocSite(stmt=call.call_site, kind=AllocKind.OBJECT)
            constraints.append(AllocConstraint(target=call.target, alloc_site=alloc_site))
            # TODO: Link to all iterable elements and create tuples
        
        return constraints
    
    def _handle_map(
        self,
        scope: 'Scope',
        context: 'AbstractContext',
        call: 'CallConstraint'
    ) -> List['Constraint']:
        """Handle map(func, *iterables)."""
        from .constraints import AllocConstraint
        from .object import AllocSite, AllocKind
        
        constraints = []
        
        if call.target:
            # Create map iterator
            alloc_site = AllocSite(stmt=call.call_site, kind=AllocKind.OBJECT)
            constraints.append(AllocConstraint(target=call.target, alloc_site=alloc_site))
            # TODO: Model function application on iterable elements
        
        return constraints
    
    def _handle_filter(
        self,
        scope: 'Scope',
        context: 'AbstractContext',
        call: 'CallConstraint'
    ) -> List['Constraint']:
        """Handle filter(func, iterable).
        
        Creates filter iterator linked to iterable elements.
        """
        from .constraints import AllocConstraint, LoadConstraint
        from .object import AllocSite, AllocKind
        from .heap_model import elem
        
        constraints = []
        
        if call.target and len(call.args) > 1:
            iterable_var = call.args[1]
            
            # Create filter iterator
            alloc_site = AllocSite(stmt=call.call_site, kind=AllocKind.OBJECT)
            constraints.append(AllocConstraint(target=call.target, alloc_site=alloc_site))
            
            # Link to iterable elements
            constraints.append(LoadConstraint(
                base=iterable_var,
                field=elem(),
                target=call.target
            ))
        
        return constraints
    
    def _handle_reversed(
        self,
        scope: 'Scope',
        context: 'AbstractContext',
        call: 'CallConstraint'
    ) -> List['Constraint']:
        """Handle reversed(sequence).
        
        Creates reverse iterator linked to sequence elements.
        """
        from .constraints import AllocConstraint, LoadConstraint
        from .object import AllocSite, AllocKind
        from .heap_model import elem
        
        constraints = []
        
        if call.target and len(call.args) > 0:
            sequence_var = call.args[0]
            
            # Create reverse iterator
            alloc_site = AllocSite(stmt=call.call_site, kind=AllocKind.OBJECT)
            constraints.append(AllocConstraint(target=call.target, alloc_site=alloc_site))
            
            # Link to sequence elements
            constraints.append(LoadConstraint(
                base=sequence_var,
                field=elem(),
                target=call.target
            ))
        
        return constraints
    
    def _handle_sorted(
        self,
        scope: 'Scope',
        context: 'AbstractContext',
        call: 'CallConstraint'
    ) -> List['Constraint']:
        """Handle sorted(iterable).
        
        Creates new list with elements from iterable.
        """
        from .constraints import AllocConstraint, LoadConstraint
        from .object import AllocSite, AllocKind
        from .heap_model import elem
        
        constraints = []
        
        if call.target and len(call.args) > 0:
            iterable_var = call.args[0]
            
            # Create list
            alloc_site = AllocSite(stmt=call.call_site, kind=AllocKind.LIST)
            constraints.append(AllocConstraint(target=call.target, alloc_site=alloc_site))
            
            # Copy elements from iterable
            constraints.append(LoadConstraint(
                base=iterable_var,
                field=elem(),
                target=call.target
            ))
        
        return constraints
    
    def _handle_range(
        self,
        scope: 'Scope',
        context: 'AbstractContext',
        call: 'CallConstraint'
    ) -> List['Constraint']:
        """Handle range(...)."""
        from .constraints import AllocConstraint
        from .object import AllocSite, AllocKind
        
        constraints = []
        
        if call.target:
            # Create range object
            alloc_site = AllocSite(stmt=call.call_site, kind=AllocKind.OBJECT)
            constraints.append(AllocConstraint(target=call.target, alloc_site=alloc_site))
        
        return constraints
    
    # ===== Type/Scalar Functions =====
    
    def _handle_len(
        self,
        scope: 'Scope',
        context: 'AbstractContext',
        call: 'CallConstraint'
    ) -> List['Constraint']:
        """Handle len(obj)."""
        from .constraints import AllocConstraint
        from .object import AllocSite, AllocKind
        
        constraints = []
        
        if call.target:
            # Create int object
            alloc_site = AllocSite(stmt=call.call_site, kind=AllocKind.CONSTANT)
            constraints.append(AllocConstraint(target=call.target, alloc_site=alloc_site))
        
        return constraints
    
    def _handle_isinstance(
        self,
        scope: 'Scope',
        context: 'AbstractContext',
        call: 'CallConstraint'
    ) -> List['Constraint']:
        """Handle isinstance(obj, type)."""
        from .constraints import AllocConstraint
        from .object import AllocSite, AllocKind
        
        constraints = []
        
        if call.target:
            # Create bool object
            alloc_site = AllocSite(stmt=call.call_site, kind=AllocKind.CONSTANT)
            constraints.append(AllocConstraint(target=call.target, alloc_site=alloc_site))
        
        return constraints
    
    def _handle_type(
        self,
        scope: 'Scope',
        context: 'AbstractContext',
        call: 'CallConstraint'
    ) -> List['Constraint']:
        """Handle type(obj)."""
        from .constraints import AllocConstraint
        from .object import AllocSite, AllocKind
        
        constraints = []
        
        if call.target:
            # Create type object
            alloc_site = AllocSite(stmt=call.call_site, kind=AllocKind.CLASS)
            constraints.append(AllocConstraint(target=call.target, alloc_site=alloc_site))
        
        return constraints
    
    def _handle_bool(
        self,
        scope: 'Scope',
        context: 'AbstractContext',
        call: 'CallConstraint'
    ) -> List['Constraint']:
        """Handle bool(obj)."""
        from .constraints import AllocConstraint
        from .object import AllocSite, AllocKind
        
        constraints = []
        
        if call.target:
            alloc_site = AllocSite(stmt=call.call_site, kind=AllocKind.CONSTANT)
            constraints.append(AllocConstraint(target=call.target, alloc_site=alloc_site))
        
        return constraints
    
    def _handle_int(
        self,
        scope: 'Scope',
        context: 'AbstractContext',
        call: 'CallConstraint'
    ) -> List['Constraint']:
        """Handle int(obj)."""
        from .constraints import AllocConstraint
        from .object import AllocSite, AllocKind
        
        constraints = []
        
        if call.target:
            alloc_site = AllocSite(stmt=call.call_site, kind=AllocKind.CONSTANT)
            constraints.append(AllocConstraint(target=call.target, alloc_site=alloc_site))
        
        return constraints
    
    def _handle_float(
        self,
        scope: 'Scope',
        context: 'AbstractContext',
        call: 'CallConstraint'
    ) -> List['Constraint']:
        """Handle float(obj)."""
        from .constraints import AllocConstraint
        from .object import AllocSite, AllocKind
        
        constraints = []
        
        if call.target:
            alloc_site = AllocSite(stmt=call.call_site, kind=AllocKind.CONSTANT)
            constraints.append(AllocConstraint(target=call.target, alloc_site=alloc_site))
        
        return constraints
    
    def _handle_str(
        self,
        scope: 'Scope',
        context: 'AbstractContext',
        call: 'CallConstraint'
    ) -> List['Constraint']:
        """Handle str(obj)."""
        from .constraints import AllocConstraint
        from .object import AllocSite, AllocKind
        
        constraints = []
        
        if call.target:
            alloc_site = AllocSite(stmt=call.call_site, kind=AllocKind.CONSTANT)
            constraints.append(AllocConstraint(target=call.target, alloc_site=alloc_site))
        
        return constraints
    
    # ===== Generic Handlers =====
    
    def _handle_generic_constructor(
        self,
        scope: 'Scope',
        context: 'AbstractContext',
        call: 'CallConstraint',
        builtin_name: str
    ) -> List['Constraint']:
        """Handle generic builtin constructor."""
        from .constraints import AllocConstraint
        from .object import AllocSite, AllocKind
        
        constraints = []
        
        if call.target:
            logger.debug(f"Generic constructor: {builtin_name}")
            alloc_site = AllocSite(stmt=call.call_site, kind=AllocKind.OBJECT)
            constraints.append(AllocConstraint(target=call.target, alloc_site=alloc_site))
        
        return constraints
    
    def _handle_generic_builtin(
        self,
        scope: 'Scope',
        context: 'AbstractContext',
        call: 'CallConstraint',
        function_name: str
    ) -> List['Constraint']:
        """Handle generic builtin function."""
        from .constraints import AllocConstraint
        from .object import AllocSite, AllocKind
        
        constraints = []
        
        if call.target:
            logger.debug(f"Generic builtin function: {function_name}")
            alloc_site = AllocSite(stmt=call.call_site, kind=AllocKind.OBJECT)
            constraints.append(AllocConstraint(target=call.target, alloc_site=alloc_site))
        
        return constraints
    
    def _handle_generic_method(
        self,
        scope: 'Scope',
        context: 'AbstractContext',
        call: 'CallConstraint',
        method_obj: 'BuiltinMethodObject'
    ) -> List['Constraint']:
        """Handle generic builtin method."""
        from .constraints import AllocConstraint
        from .object import AllocSite, AllocKind
        
        constraints = []
        
        if call.target:
            logger.debug(f"Generic builtin method: {method_obj.method_name}")
            alloc_site = AllocSite(stmt=call.call_site, kind=AllocKind.OBJECT)
            constraints.append(AllocConstraint(target=call.target, alloc_site=alloc_site))
        
        return constraints


# Maintain backward compatibility with old BuiltinSummaryManager
class BuiltinSummaryManager:
    """Backward-compatible wrapper around BuiltinAPIHandler.
    
    This maintains the old interface while delegating to the new handler.
    """
    
    def __init__(self, config: 'Config'):
        """Initialize builtin summary manager.
        
        Args:
            config: Analysis configuration
        """
        self.config = config
        self._handler: Optional[BuiltinAPIHandler] = None
    
    def set_state(self, state: 'PointerAnalysisState'):
        """Set the analysis state (called by solver)."""
        self._handler = BuiltinAPIHandler(state, self.config)
    
    def get_handler(self) -> Optional[BuiltinAPIHandler]:
        """Get the builtin API handler."""
        return self._handler
    
    def has_summary(self, function_name: str) -> bool:
        """Check if function has a summary."""
        if not self._handler:
            return False
        return (
            function_name in self._handler._function_handlers or
            function_name in BuiltinAPIHandler.CONTAINER_TYPES or
            function_name in BuiltinAPIHandler.ITERATOR_BUILTINS or
            function_name in BuiltinAPIHandler.TYPE_BUILTINS
        )
