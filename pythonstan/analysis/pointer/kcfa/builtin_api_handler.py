"""Builtin function and method handling for pointer analysis.

This module provides handlers for Python builtin functions and methods that
model their pointer effects using PFG edges and lazy constraints.

Design:
- Builtin classes/functions/methods are represented as specialized objects
- Operations use PFG edges for direct dataflows
- Lazy constraints are added when new objects might be created later
"""

from .allocation import canonical_constant
from typing import List, Optional, Dict, Set, Tuple, TYPE_CHECKING
import logging

from pythonstan.analysis.pointer.kcfa.constraints import (
    AllocConstraint,
    CallConstraint,
    CopyConstraint,
    AttrReadConstraint,
    AttrWriteConstraint,
    LoadConstraint,
    LoadSubscrConstraint,
    StoreConstraint,
    StoreSubscrConstraint,
    SuperResolveConstraint,
    SpecialMethodLookupConstraint,
    ResidualEffectConstraint,
)
from pythonstan.analysis.pointer.kcfa.heap_model import Field, FieldKind, attr, elem, key, unknown, wildcard_key
from pythonstan.analysis.pointer.kcfa.object import (
    AllocKind,
    AllocSite,
    BuiltinClassObject,
    BuiltinFunctionObject,
    BuiltinInstanceObject,
    BuiltinMethodObject,
    ClassObject,
    ConstantObject,
    InstanceObject,
    ListObject,
    DictObject,
    SetObject,
    TupleObject,
    MappingItemObject,
    GeneratorObject,
    CoroutineObject,
    ObjectFactory,
)
from pythonstan.analysis.pointer.kcfa.pointer_flow_graph import NormalNode
from pythonstan.analysis.pointer.kcfa.points_to_set import PointsToSet
from pythonstan.analysis.pointer.kcfa.variable import Variable, VariableKind
from pythonstan.analysis.pointer.kcfa.native_mapping import NATIVE_MAPPING_KEYS, NATIVE_MAPPING_KEY_FIELDS, NativeMappingProtocolConstraint

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

__all__ = ["BuiltinAPIHandler", "BuiltinSummaryManager", "BUILTIN_FUNCTIONS"]


class BuiltinAPIHandler:
    """Handler for builtin function and method operations.
    
    Uses PFG edges for direct dataflows and constraints for lazy processing.
    """
    
    # Builtin container types
    CONTAINER_TYPES = {
        "list",
        "dict",
        "tuple",
        "set",
        "frozenset",
        "range",
        "str",
        "bytes",
        "bytearray",
        "memoryview",
        "dict_keys",
        "dict_values",
        "dict_items",
    }

    # Iterator-like builtin instance types
    ITERATOR_TYPES = {
        "coroutine_iterator",
        "iterator",
        "generator",
        "enumerate",
        "zip",
        "map",
        "filter",
        "reversed",
    }

    # Dict view types
    DICT_VIEW_TYPES = {"dict_keys", "dict_values", "dict_items"}
    
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
    ITERATOR_BUILTINS = {
        "iter",
        "next",
        "aiter",
        "anext",
        "enumerate",
        "zip",
        "map",
        "filter",
        "reversed",
        "range",
        "sorted",
        "sum",
        "min",
        "max",
        "all",
        "any",
    }
    
    # Scalar/type builtins
    TYPE_BUILTINS = {
        "len",
        "isinstance",
        "issubclass",
        "type",
        "bool",
        "int",
        "float",
        "str",
        "bytes",
        "bytearray",
        "memoryview",
        "slice",
        "complex",
        "abs",
        "round",
        "divmod",
        "pow",
        "repr",
        "ascii",
        "format",
        "bin",
        "hex",
        "oct",
        "chr",
        "ord",
        "hash",
        "id",
    }
    
    def __init__(self, state: 'PointerAnalysisState', config: 'Config'):
        """Initialize builtin handler.
        
        Args:
            state: Pointer analysis state
            config: Analysis configuration
        """
        self.state = state
        self.config = config
        self._builtin_type_objects = {}
        self._method_handlers: Dict[str, callable] = self._init_method_handlers()
        self._function_handlers: Dict[str, callable] = self._init_function_handlers()

    def _make_temp_var(self, prefix: str, call: 'CallConstraint') -> 'Variable':
        """Create a temporary variable unique to the call site."""
        return Variable(
            # Lowering can assign the same display line/column to distinct
            # nested calls. The full site includes the statement and scope.
            name=f"${prefix}@{call.call_site.site_id}",
            kind=VariableKind.TEMPORARY,
        )

    def _add_points_to_target(
        self,
        scope: 'Scope',
        context: 'AbstractContext',
        target: 'Variable',
        obj: 'AbstractObject',
    ) -> None:
        """Add a single object to a target variable points-to set."""
        ctx_target = self.state.get_variable(scope, context, target)
        self.state._worklist.add((scope, NormalNode(ctx_target), PointsToSet.singleton(obj)))

    def _get_or_create_builtin_instance(
        self,
        scope: 'Scope',
        context: 'AbstractContext',
        alloc_site: AllocSite,
        builtin_type: str,
    ) -> BuiltinInstanceObject:
        """Create or reuse a builtin instance object for an allocation site."""
        existing = self.state._heap.get_obj(scope, context, alloc_site)
        if isinstance(existing, BuiltinInstanceObject):
            return existing
        obj = BuiltinInstanceObject(
            context=self.state.select_allocation_context(context, alloc_site),
            alloc_site=alloc_site,
            builtin_type=builtin_type,
        )
        self.state._heap.set_obj(scope, context, alloc_site, obj)
        self.state.obj_scope[obj] = scope
        return obj

    def _allocate_builtin_instance_to_target(
        self,
        scope: 'Scope',
        context: 'AbstractContext',
        call: 'CallConstraint',
        builtin_type: str,
        alloc_kind: AllocKind = AllocKind.OBJECT,
        alloc_suffix: Optional[str] = None,
    ) -> Optional[BuiltinInstanceObject]:
        """Allocate a builtin instance and add it to the call target."""
        if not call.target:
            return None
        suffix = alloc_suffix or builtin_type
        alloc_site = AllocSite(
            # The statement identity, rather than its display location, is the
            # finite allocation-site key. Repeated calls at this exact site
            # still share the selected abstract context as before.
            stmt=("builtin_instance", suffix, call.call_site),
            kind=alloc_kind,
        )
        obj = self._get_or_create_builtin_instance(scope, context, alloc_site, builtin_type)
        self._add_points_to_target(scope, context, call.target, obj)
        return obj

    @staticmethod
    def _alloc_primitive(call: 'CallConstraint', kind: AllocKind) -> List['Constraint']:
        """Allocate a primitive object to the call target."""
        if not call.target:
            return []
        alloc_site = AllocSite(stmt=call.stmt, kind=kind)
        return [AllocConstraint(target=call.target, alloc_site=alloc_site)]

    @staticmethod
    def _infer_container_type(receiver: 'AbstractObject') -> Optional[str]:
        """Infer builtin container type name from receiver object."""
        if isinstance(receiver, BuiltinInstanceObject):
            return receiver.builtin_type
        if isinstance(receiver, ListObject):
            return "list"
        if isinstance(receiver, DictObject):
            return "dict"
        if isinstance(receiver, TupleObject):
            return "tuple"
        if isinstance(receiver, SetObject):
            return "set"
        if isinstance(receiver, ConstantObject):
            if isinstance(receiver.value, str):
                return "str"
            if isinstance(receiver.value, bytes):
                return "bytes"
            if isinstance(receiver.value, bytearray):
                return "bytearray"
        return None

    def _resolve_attr_names(
        self,
        scope: 'Scope',
        context: 'AbstractContext',
        name_var: 'Variable',
    ) -> Tuple[Set[str], bool]:
        """Resolve constant attribute names from name_var points-to set."""
        ctx_name = self.state.get_variable(scope, context, name_var)
        name_pts = self.state.get_points_to(ctx_name)
        const_names: Set[str] = set()
        has_non_const = False
        for obj in name_pts:
            if isinstance(obj, ConstantObject) and isinstance(obj.value, str):
                const_names.add(obj.value)
            else:
                has_non_const = True
        if len(name_pts) == 0:
            has_non_const = True
        return const_names, has_non_const

    def _collect_known_attr_names(
        self,
        scope: 'Scope',
        context: 'AbstractContext',
        base_var: 'Variable',
    ) -> Set[str]:
        """Collect known attribute names for objects in base_var points-to set."""
        ctx_base = self.state.get_variable(scope, context, base_var)
        base_pts = self.state.get_points_to(ctx_base)
        names: Set[str] = set()
        if not base_pts:
            return names
        for obj in base_pts:
            for field in self.state.known_fields_for_object(obj):
                if field.kind == FieldKind.ATTRIBUTE and field.name:
                    names.add(field.name)
        return names
    
    def _init_method_handlers(self) -> Dict[str, callable]:
        """Initialize method handler dispatch table."""
        return {
            # List methods
            "append": self._handle_list_append,
            "extend": self._handle_list_extend,
            "insert": self._handle_list_insert,
            "pop": self._handle_container_pop,
            
            # Dict methods
            "get": self._handle_dict_get,
            "setdefault": self._handle_dict_setdefault,
            "update": self._handle_container_update,
            "keys": self._handle_dict_keys,
            "values": self._handle_dict_values,
            "items": self._handle_dict_items,
            "popitem": self._handle_dict_popitem,
            "copy": self._handle_container_copy,
            
            # Set methods
            "add": self._handle_set_add,
            "discard": self._handle_set_discard,
            "remove": self._handle_set_remove,
            "update": self._handle_container_update,

            # Protocol methods
            "__getitem__": self._handle_container_getitem,
            "__setitem__": self._handle_container_setitem,
            "__iter__": self._handle_iter_method,
            "__next__": self._handle_iterator_next,
            "__len__": self._handle_len_method,
            "__contains__": self._handle_contains_method,
            "__bool__": self._handle_bool_method,

            # Descriptor protocol
            "__get__": self._handle_descriptor_get,
            "__set__": self._handle_descriptor_set,
            "__delete__": self._handle_descriptor_delete,

            # Context manager protocol
            "__enter__": self._handle_context_enter,
            "__exit__": self._handle_context_exit,

            # Generator/coroutine methods
            "send": self._handle_generator_send,
            "throw": self._handle_generator_throw,
            "close": self._handle_generator_close,
            "__await__": self._handle_coroutine_await,
        }
    
    def _init_function_handlers(self) -> Dict[str, callable]:
        """Initialize function handler dispatch table."""
        return {
            # Container constructors
            "list": self._handle_list_constructor,
            "dict": self._handle_dict_constructor,
            "tuple": self._handle_tuple_constructor,
            "set": self._handle_set_constructor,
            "frozenset": self._handle_frozenset_constructor,
            
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
            "sum": self._handle_sum,
            "min": self._handle_min,
            "max": self._handle_max,
            "all": self._handle_all,
            "any": self._handle_any,
            
            # Type/scalar functions
            "len": self._handle_len,
            "isinstance": self._handle_isinstance,
            "issubclass": self._handle_issubclass,
            "type": self._handle_type,
            "bool": self._handle_bool,
            "int": self._handle_int,
            "float": self._handle_float,
            "str": self._handle_str,
            "bytes": self._handle_bytes,
            "bytearray": self._handle_bytearray,
            "memoryview": self._handle_memoryview,
            "slice": self._handle_slice,
            "callable": self._handle_callable,
            "abs": self._handle_abs,
            "round": self._handle_round,
            "divmod": self._handle_divmod,
            "pow": self._handle_pow,
            "complex": self._handle_complex,
            "repr": self._handle_repr,
            "ascii": self._handle_ascii,
            "format": self._handle_format,
            "bin": self._handle_bin,
            "hex": self._handle_hex,
            "oct": self._handle_oct,
            "chr": self._handle_chr,
            "ord": self._handle_ord,
            "hash": self._handle_hash,
            "id": self._handle_id,
            
            # Introspection/dynamic attribute functions
            "getattr": self._handle_getattr,
            "setattr": self._handle_setattr,
            "hasattr": self._handle_hasattr,
            "delattr": self._handle_delattr,
            "vars": self._handle_vars,
            "dir": self._handle_dir,
            "globals": self._handle_globals,
            "locals": self._handle_locals,
            
            # Object-oriented functions
            "super": self._handle_super,

            # Descriptor constructors
            "property": self._handle_property,
            "classmethod": self._handle_classmethod,
            "staticmethod": self._handle_staticmethod,

            # Miscellaneous
            "object": self._handle_object,
            "open": self._handle_open,
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
        constraints = []
        
        # Dispatch based on builtin object type
        if isinstance(builtin_obj, BuiltinClassObject):
            from pythonstan.models.builtins import EXCEPTION_TYPES
            if builtin_obj.builtin_name in EXCEPTION_TYPES:
                from .exception_models import construct_exception
                return construct_exception(self, scope, context, call, builtin_obj.builtin_name)
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
                # Pass both the receiver object and the method object (which has receiver_var)
                constraints = handler(scope, context, call, builtin_obj)
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
        constraints = []
        
        if not call.target:
            return constraints
        
        # Create allocation constraint for new list
        alloc_site = AllocSite(stmt=call.stmt, kind=AllocKind.LIST)
        constraints.append(AllocConstraint(target=call.target, alloc_site=alloc_site))
        
        # If iterable argument provided, copy elements
        if len(call.args) > 0:
            iterable_var = call.args[0]
            elem_var = self._make_temp_var("list_ctor_elem", call)
            constraints.append(LoadConstraint(
                base=iterable_var,
                field=elem(),
                target=elem_var,
            ))
            constraints.append(StoreConstraint(
                base=call.target,
                field=elem(),
                source=elem_var,
            ))
        
        return constraints
    
    def _handle_dict_constructor(
        self,
        scope: 'Scope',
        context: 'AbstractContext',
        call: 'CallConstraint'
    ) -> List['Constraint']:
        """Handle dict() constructor call."""
        constraints = []
        
        if not call.target:
            return constraints
        
        alloc_site = AllocSite(stmt=call.stmt, kind=AllocKind.DICT)
        constraints.append(AllocConstraint(target=call.target, alloc_site=alloc_site))
        
        constraints.extend(self._mapping_copy_constraints(scope, context, call, call.target,
                                                          call.args[:1], call.kwargs))
        return constraints

    def _mapping_copy_constraints(self, scope, context, call, target, mappings, kwargs=()):
        """One-way per-key flows; allocation and aliasing are handled separately."""
        constraints = []
        mappings = list(mappings)
        for name, variable in kwargs:
            if name == "**":
                mappings.append(variable)
            else:
                constraints.append(StoreConstraint(target, key(name), variable))
                # Keyword-created entries have exact string keys too.  Keep
                # their identities available when a later custom key compares
                # against them; never mix key metadata into mapping values.
                key_variable = self._make_temp_var(f"mapping_keyword_key_{name}", call)
                site = AllocSite(("mapping_keyword_key", call.call_site, name), AllocKind.CONSTANT)
                key_object = self.state._heap.get_obj(scope, context, site)
                if key_object is None:
                    key_object = canonical_constant(self.state, context, site, name)
                    self.state._heap.set_obj(scope, context, site, key_object)
                    self.state.obj_scope[key_object] = scope
                self._add_points_to_target(scope, context, key_variable, key_object)
                constraints.append(StoreConstraint(target, NATIVE_MAPPING_KEYS, key_variable))
        for mapping_index, source in enumerate(mappings):
            for field in NATIVE_MAPPING_KEY_FIELDS:
                private_keys = self._make_temp_var(f"mapping_copy_native_keys_{mapping_index}_{field.name}", call)
                constraints.append(LoadConstraint(source, field, private_keys))
                constraints.append(StoreConstraint(target, field, private_keys))
            objects = self.state.get_points_to(self.state.get_variable(scope, context, source))
            if not objects or any(not isinstance(obj, DictObject) for obj in objects):
                constraints.append(ResidualEffectConstraint(
                    str(call.call_site),
                    "Mapping-copy key provenance from opaque/custom mappings or iterable pairs is incomplete; native key callbacks remain open",
                    ("native_mapping_key_provenance", "mapping_protocol", "may_invoke_callbacks", "may_mutate_heap", "may_raise"),
                ))
            fields = {wildcard_key()}
            fields.update(field for obj in objects
                          for field in self.state.known_fields_for_object(obj)
                          if field.kind == FieldKind.KEY)
            if not self.config.index_sensitive or any(not isinstance(obj, DictObject) for obj in objects):
                fields.add(elem())
            for field in fields:
                temporary = self._make_temp_var(f"mapping_copy_{mapping_index}_{field!r}", call)
                constraints.append(LoadConstraint(source, field, temporary))
                constraints.append(StoreConstraint(target,
                    wildcard_key() if field.kind == FieldKind.ELEMENT else field, temporary))
        return constraints

    def _handle_container_copy(self, scope, context, call, method_obj):
        if not call.target or not method_obj.receiver_var:
            return []
        if isinstance(method_obj.receiver, DictObject):
            return [AllocConstraint(call.target, AllocSite(call.stmt, AllocKind.DICT)),
                    *self._mapping_copy_constraints(scope, context, call, call.target,
                                                   (method_obj.receiver_var,))]
        kind = AllocKind.SET if isinstance(method_obj.receiver, SetObject) else AllocKind.LIST
        temporary = self._make_temp_var("container_copy_element", call)
        return [AllocConstraint(call.target, AllocSite(call.stmt, kind)),
                LoadConstraint(method_obj.receiver_var, elem(), temporary),
                StoreConstraint(call.target, elem(), temporary)]
    
    def _handle_tuple_constructor(
        self,
        scope: 'Scope',
        context: 'AbstractContext',
        call: 'CallConstraint'
    ) -> List['Constraint']:
        """Handle tuple() constructor call."""
        constraints = []
        
        if not call.target:
            return constraints
        
        alloc_site = AllocSite(stmt=call.stmt, kind=AllocKind.TUPLE)
        constraints.append(AllocConstraint(target=call.target, alloc_site=alloc_site))
        
        # If iterable provided, copy elements
        if len(call.args) > 0:
            iterable_var = call.args[0]
            elem_var = self._make_temp_var("tuple_ctor_elem", call)
            constraints.append(LoadConstraint(
                base=iterable_var,
                field=elem(),
                target=elem_var,
            ))
            constraints.append(StoreConstraint(
                base=call.target,
                field=elem(),
                source=elem_var,
            ))
        
        return constraints
    
    def _handle_set_constructor(
        self,
        scope: 'Scope',
        context: 'AbstractContext',
        call: 'CallConstraint'
    ) -> List['Constraint']:
        """Handle set() constructor call."""
        constraints = []
        
        if not call.target:
            return constraints
        
        alloc_site = AllocSite(stmt=call.stmt, kind=AllocKind.SET)
        constraints.append(AllocConstraint(target=call.target, alloc_site=alloc_site))
        
        # If iterable provided, copy elements
        if len(call.args) > 0:
            iterable_var = call.args[0]
            elem_var = self._make_temp_var("set_ctor_elem", call)
            constraints.append(LoadConstraint(
                base=iterable_var,
                field=elem(),
                target=elem_var,
            ))
            constraints.append(StoreConstraint(
                base=call.target,
                field=elem(),
                source=elem_var,
            ))
        
        return constraints

    def _handle_frozenset_constructor(
        self,
        scope: 'Scope',
        context: 'AbstractContext',
        call: 'CallConstraint'
    ) -> List['Constraint']:
        """Handle frozenset() constructor call."""
        constraints: List['Constraint'] = []
        if not call.target:
            return constraints

        self._allocate_builtin_instance_to_target(
            scope,
            context,
            call,
            builtin_type="frozenset",
            alloc_kind=AllocKind.SET,
            alloc_suffix="frozenset",
        )

        if len(call.args) > 0:
            iterable_var = call.args[0]
            elem_var = self._make_temp_var("frozenset_ctor_elem", call)
            constraints.append(LoadConstraint(
                base=iterable_var,
                field=elem(),
                target=elem_var,
            ))
            constraints.append(StoreConstraint(
                base=call.target,
                field=elem(),
                source=elem_var,
            ))
        return constraints
    
    # ===== List Methods =====
    
    def _handle_list_append(
        self,
        scope: 'Scope',
        context: 'AbstractContext',
        call: 'CallConstraint',
        method_obj: 'BuiltinMethodObject'
    ) -> List['Constraint']:
        """Handle list.append(x).
        
        Stores x to list.elem() by directly setting up PFG edge and also adding
        a StoreConstraint as a fallback for cases where the receiver's points-to
        set is populated after this call.
        """
        constraints = []
        
        if len(call.args) > 0 and method_obj.receiver_var:
            # Get the first argument (the item to append)
            item_var = call.args[0]
            
            # Get contextualized variables
            base_ctx_var = self.state.get_variable(scope, context, method_obj.receiver_var)
            item_ctx_var = self.state.get_variable(scope, context, item_var)
            
            # Directly set up the PFG edge from item to receiver.elem()
            # This handles cases where the receiver already has objects
            base_pts = self.state.get_points_to(base_ctx_var)
            for base_obj in base_pts:
                field_access = self.state.get_field(scope, context, base_obj, elem())
                self.state._add_var_points_flow(item_ctx_var, field_access)
            
            # ALSO add a StoreConstraint as fallback for lazy resolution
            # This ensures the store is captured even if the list object is created later
            constraints.append(StoreConstraint(
                base=method_obj.receiver_var,
                field=elem(),
                source=item_var
            ))
        
        return constraints
    
    def _handle_list_extend(
        self,
        scope: 'Scope',
        context: 'AbstractContext',
        call: 'CallConstraint',
        method_obj: 'BuiltinMethodObject'
    ) -> List['Constraint']:
        """Handle list.extend(iterable).
        
        Loads from iterable.elem() and stores to list.elem().
        """
        constraints = []
        
        # Similar to append, but loads from iterable
        if len(call.args) > 0 and method_obj.receiver_var:
            iterable_var = call.args[0]
            elem_var = self._make_temp_var("list_extend_elem", call)
            constraints.append(LoadConstraint(
                base=iterable_var,
                field=elem(),
                target=elem_var,
            ))
            constraints.append(StoreConstraint(
                base=method_obj.receiver_var,
                field=elem(),
                source=elem_var,
            ))
        
        return constraints
    
    def _handle_list_insert(
        self,
        scope: 'Scope',
        context: 'AbstractContext',
        call: 'CallConstraint',
        method_obj: 'BuiltinMethodObject'
    ) -> List['Constraint']:
        """Handle list.insert(index, x).
        
        Stores x to list.elem().
        """
        constraints = []
        
        if len(call.args) > 1 and method_obj.receiver_var:
            item_var = call.args[1]  # Second arg is the item
            # Add constraint to store item to receiver.elem()
            constraints.append(StoreConstraint(
                base=method_obj.receiver_var,
                field=elem(),
                source=item_var,
            ))
        
        return constraints
    
    def _handle_list_pop(
        self,
        scope: 'Scope',
        context: 'AbstractContext',
        call: 'CallConstraint',
        method_obj: 'BuiltinMethodObject'
    ) -> List['Constraint']:
        """Handle list.pop([index]).
        
        Loads from list.elem() to return value.
        """
        constraints = []
        
        if call.target and method_obj.receiver_var:
            if len(call.args) > 0:
                index_var = call.args[0]
                constraints.append(LoadSubscrConstraint(
                    target=call.target,
                    base=method_obj.receiver_var,
                    index=index_var,
                ))
            else:
                constraints.append(LoadConstraint(
                    base=method_obj.receiver_var,
                    field=elem(),
                    target=call.target,
                ))
        
        return constraints
    
    # ===== Container Methods =====
    
    def _handle_container_getitem(
        self,
        scope: 'Scope',
        context: 'AbstractContext',
        call: 'CallConstraint',
        method_obj: 'BuiltinMethodObject'
    ) -> List['Constraint']:
        """Handle container.__getitem__(key).
        
        For lists/tuples: loads from elem()
        For dicts: loads from key(k) if constant, else elem()
        """
        constraints = self._native_dict_key_constraints(call, method_obj, False, "getitem")
        
        if call.target and len(call.args) > 0 and method_obj.receiver_var:
            index_var = call.args[0]
            # Add LoadSubscrConstraint which will be resolved based on index type
            # This will be handled in solver's _apply_load_subscr
            constraints.append(LoadSubscrConstraint(
                target=call.target,
                base=method_obj.receiver_var,
                index=index_var,
            ))
        
        return constraints
    
    def _handle_container_setitem(
        self,
        scope: 'Scope',
        context: 'AbstractContext',
        call: 'CallConstraint',
        method_obj: 'BuiltinMethodObject'
    ) -> List['Constraint']:
        """Handle container.__setitem__(key, value).
        
        Stores value to appropriate field based on key type.
        """
        constraints = self._native_dict_key_constraints(call, method_obj, True, "setitem")
        
        if len(call.args) > 1 and method_obj.receiver_var:
            key_var = call.args[0]
            value_var = call.args[1]
            # Add StoreSubscrConstraint
            constraints.append(StoreSubscrConstraint(
                base=method_obj.receiver_var,
                index=key_var,
                source=value_var,
            ))
        
        return constraints
    
    def _handle_container_iter(
        self,
        scope: 'Scope',
        context: 'AbstractContext',
        call: 'CallConstraint',
        method_obj: 'BuiltinMethodObject'
    ) -> List['Constraint']:
        """Handle container.__iter__().
        
        Creates iterator object that yields container elements.
        """
        constraints = []
        
        if call.target and method_obj.receiver_var:
            self._allocate_builtin_instance_to_target(
                scope,
                context,
                call,
                builtin_type="iterator",
                alloc_suffix="container_iter",
            )
            
            # Link iterator to container elements
            # The iterator's elem() field should point to container's elem()
            container_type = self._infer_container_type(method_obj.receiver)
            if container_type == "dict":
                unknown_var = self._make_temp_var("dict_iter_key", call)
                constraints.append(AllocConstraint(
                    target=unknown_var,
                    alloc_site=AllocSite(stmt=call.stmt, kind=AllocKind.UNKNOWN),
                ))
                constraints.append(StoreConstraint(
                    base=call.target,
                    field=elem(),
                    source=unknown_var,
                ))
            else:
                elem_var = self._make_temp_var("iter_elem", call)
                constraints.append(LoadConstraint(
                    base=method_obj.receiver_var,
                    field=elem(),
                    target=elem_var,
                ))
                constraints.append(StoreConstraint(
                    base=call.target,
                    field=elem(),
                    source=elem_var,
                ))
        
        return constraints

    def _handle_container_pop(
        self,
        scope: 'Scope',
        context: 'AbstractContext',
        call: 'CallConstraint',
        method_obj: 'BuiltinMethodObject'
    ) -> List['Constraint']:
        """Handle container.pop(...) for list/dict/set."""
        constraints = []
        if not call.target or not method_obj.receiver_var:
            return constraints
        
        container_type = self._infer_container_type(method_obj.receiver)
        if container_type == "dict":
            if len(call.args) > 0:
                key_var = call.args[0]
                constraints.append(LoadSubscrConstraint(
                    target=call.target,
                    base=method_obj.receiver_var,
                    index=key_var,
                ))
                if len(call.args) > 1:
                    default_var = call.args[1]
                    constraints.append(CopyConstraint(
                        source=default_var,
                        target=call.target,
                    ))
        elif container_type in {"list", "tuple", "set"}:
            if len(call.args) > 0 and container_type == "list":
                index_var = call.args[0]
                constraints.append(LoadSubscrConstraint(
                    target=call.target,
                    base=method_obj.receiver_var,
                    index=index_var,
                ))
            else:
                constraints.append(LoadConstraint(
                    base=method_obj.receiver_var,
                    field=elem(),
                    target=call.target,
                ))
        return constraints

    def _handle_container_update(
        self,
        scope: 'Scope',
        context: 'AbstractContext',
        call: 'CallConstraint',
        method_obj: 'BuiltinMethodObject'
    ) -> List['Constraint']:
        """Handle update() for dict/set by receiver type."""
        container_type = self._infer_container_type(method_obj.receiver)
        if container_type == "dict":
            return self._handle_dict_update(scope, context, call, method_obj)
        if container_type == "set":
            return self._handle_set_update(scope, context, call, method_obj)
        return []

    # ===== Protocol Methods =====

    def _handle_iter_method(
        self,
        scope: 'Scope',
        context: 'AbstractContext',
        call: 'CallConstraint',
        method_obj: 'BuiltinMethodObject'
    ) -> List['Constraint']:
        """Handle __iter__ on builtin instances and iterators."""
        if not call.target or not method_obj.receiver_var:
            return []

        receiver = method_obj.receiver
        receiver_type = self._infer_container_type(receiver)
        if isinstance(receiver, GeneratorObject):
            receiver_type = "generator"
        if isinstance(receiver, CoroutineObject):
            receiver_type = "coroutine"

        if receiver_type in self.ITERATOR_TYPES:
            return [CopyConstraint(source=method_obj.receiver_var, target=call.target)]

        if receiver_type == "dict":
            return self._handle_container_iter(scope, context, call, method_obj)

        # Default: create iterator and load from receiver.elem()
        constraints: List['Constraint'] = []
        self._allocate_builtin_instance_to_target(
            scope,
            context,
            call,
            builtin_type="iterator",
            alloc_suffix="iter",
        )
        elem_var = self._make_temp_var("iter_elem", call)
        if isinstance(receiver, ConstantObject):
            elem_kind = AllocKind.STRING if isinstance(receiver.value, str) else AllocKind.INTEGER
            constraints.append(AllocConstraint(
                target=elem_var,
                alloc_site=AllocSite(stmt=call.stmt, kind=elem_kind),
            ))
        else:
            constraints.append(LoadConstraint(
                base=method_obj.receiver_var,
                field=elem(),
                target=elem_var,
            ))
        constraints.append(StoreConstraint(
            base=call.target,
            field=elem(),
            source=elem_var,
        ))
        return constraints

    def _handle_iterator_next(
        self,
        scope: 'Scope',
        context: 'AbstractContext',
        call: 'CallConstraint',
        method_obj: 'BuiltinMethodObject'
    ) -> List['Constraint']:
        """Handle __next__ for iterators and generators."""
        if not call.target or not method_obj.receiver_var:
            return []
        return [LoadConstraint(
            base=method_obj.receiver_var,
            field=elem(),
            target=call.target,
        )]

    def _handle_len_method(
        self,
        scope: 'Scope',
        context: 'AbstractContext',
        call: 'CallConstraint',
        method_obj: 'BuiltinMethodObject'
    ) -> List['Constraint']:
        """Handle __len__() returning an int."""
        return self._alloc_primitive(call, AllocKind.INTEGER)

    def _handle_contains_method(
        self,
        scope: 'Scope',
        context: 'AbstractContext',
        call: 'CallConstraint',
        method_obj: 'BuiltinMethodObject'
    ) -> List['Constraint']:
        """Handle __contains__(item) returning a bool."""
        return self._alloc_primitive(call, AllocKind.BOOLEAN)

    def _handle_bool_method(
        self,
        scope: 'Scope',
        context: 'AbstractContext',
        call: 'CallConstraint',
        method_obj: 'BuiltinMethodObject'
    ) -> List['Constraint']:
        """Handle __bool__() returning a bool."""
        return self._alloc_primitive(call, AllocKind.BOOLEAN)

    def _handle_context_enter(
        self,
        scope: 'Scope',
        context: 'AbstractContext',
        call: 'CallConstraint',
        method_obj: 'BuiltinMethodObject'
    ) -> List['Constraint']:
        """Handle __enter__ returning the receiver."""
        if call.target and method_obj.receiver_var:
            return [CopyConstraint(source=method_obj.receiver_var, target=call.target)]
        return []

    def _handle_context_exit(
        self,
        scope: 'Scope',
        context: 'AbstractContext',
        call: 'CallConstraint',
        method_obj: 'BuiltinMethodObject'
    ) -> List['Constraint']:
        """Handle __exit__ returning a bool."""
        return self._alloc_primitive(call, AllocKind.BOOLEAN)

    def _handle_descriptor_get(
        self,
        scope: 'Scope',
        context: 'AbstractContext',
        call: 'CallConstraint',
        method_obj: 'BuiltinMethodObject'
    ) -> List['Constraint']:
        """Handle __get__ for builtin descriptors."""
        receiver_type = self._infer_container_type(method_obj.receiver)
        if isinstance(method_obj.receiver, BuiltinInstanceObject):
            receiver_type = method_obj.receiver.builtin_type

        if receiver_type == "property" and call.target and method_obj.receiver_var:
            fget_var = self._make_temp_var("property_fget", call)
            constraints = [
                LoadConstraint(
                    base=method_obj.receiver_var,
                    field=attr("fget"),
                    target=fget_var,
                )
            ]
            args: Tuple['Variable', ...] = ()
            if len(call.args) > 0:
                args = (call.args[0],)
            constraints.append(CallConstraint(
                callee=fget_var,
                args=args,
                kwargs=frozenset(),
                target=call.target,
                call_site=call.call_site,
            ))
            return constraints

        if receiver_type in {"classmethod", "staticmethod"} and call.target and method_obj.receiver_var:
            from .processor.attribute_semantics import ClassMethodBindConstraint
            func_var = self._make_temp_var("descriptor_func", call)
            binding = (ClassMethodBindConstraint(func_var, call.args, call.target, call.call_site)
                       if receiver_type == "classmethod"
                       else CopyConstraint(source=func_var, target=call.target))
            return [
                LoadConstraint(
                    base=method_obj.receiver_var,
                    field=attr("func"),
                    target=func_var,
                ),
                binding,
            ]

        return []

    def _handle_descriptor_set(
        self,
        scope: 'Scope',
        context: 'AbstractContext',
        call: 'CallConstraint',
        method_obj: 'BuiltinMethodObject'
    ) -> List['Constraint']:
        """Handle __set__ for builtin descriptors."""
        receiver_type = self._infer_container_type(method_obj.receiver)
        if isinstance(method_obj.receiver, BuiltinInstanceObject):
            receiver_type = method_obj.receiver.builtin_type

        if receiver_type == "property" and method_obj.receiver_var and len(call.args) >= 2:
            fset_var = self._make_temp_var("property_fset", call)
            value_var = call.args[1]
            constraints = [
                LoadConstraint(
                    base=method_obj.receiver_var,
                    field=attr("fset"),
                    target=fset_var,
                ),
                CallConstraint(
                    callee=fset_var,
                    args=(call.args[0], value_var),
                    kwargs=frozenset(),
                    target=None,
                    call_site=call.call_site,
                ),
            ]
            return constraints

        return []

    def _handle_descriptor_delete(
        self,
        scope: 'Scope',
        context: 'AbstractContext',
        call: 'CallConstraint',
        method_obj: 'BuiltinMethodObject'
    ) -> List['Constraint']:
        """Handle __delete__ for builtin descriptors."""
        receiver_type = self._infer_container_type(method_obj.receiver)
        if isinstance(method_obj.receiver, BuiltinInstanceObject):
            receiver_type = method_obj.receiver.builtin_type

        if receiver_type == "property" and method_obj.receiver_var and len(call.args) >= 1:
            fdel_var = self._make_temp_var("property_fdel", call)
            return [
                LoadConstraint(
                    base=method_obj.receiver_var,
                    field=attr("fdel"),
                    target=fdel_var,
                ),
                CallConstraint(
                    callee=fdel_var,
                    args=(call.args[0],),
                    kwargs=frozenset(),
                    target=None,
                    call_site=call.call_site,
                ),
            ]

        return []

    def _handle_generator_send(
        self,
        scope: 'Scope',
        context: 'AbstractContext',
        call: 'CallConstraint',
        method_obj: 'BuiltinMethodObject'
    ) -> List['Constraint']:
        """Handle generator.send(value)."""
        constraints: List['Constraint'] = []
        if method_obj.receiver_var and len(call.args) > 0:
            constraints.append(StoreConstraint(
                base=method_obj.receiver_var,
                field=attr("$sent"),
                source=call.args[0],
            ))
        if call.target and method_obj.receiver_var:
            constraints.append(LoadConstraint(
                base=method_obj.receiver_var,
                field=elem(),
                target=call.target,
            ))
        return constraints

    def _handle_generator_throw(
        self,
        scope: 'Scope',
        context: 'AbstractContext',
        call: 'CallConstraint',
        method_obj: 'BuiltinMethodObject'
    ) -> List['Constraint']:
        """Handle generator.throw(...)."""
        return self._alloc_primitive(call, AllocKind.UNKNOWN)

    def _handle_generator_close(
        self,
        scope: 'Scope',
        context: 'AbstractContext',
        call: 'CallConstraint',
        method_obj: 'BuiltinMethodObject'
    ) -> List['Constraint']:
        """Handle generator.close()."""
        return self._alloc_primitive(call, AllocKind.UNKNOWN)

    def _handle_coroutine_await(
        self,
        scope: 'Scope',
        context: 'AbstractContext',
        call: 'CallConstraint',
        method_obj: 'BuiltinMethodObject'
    ) -> List['Constraint']:
        """Handle coroutine.__await__ returning an iterator."""
        if not call.target or not method_obj.receiver_var:
            return []
        constraints: List['Constraint'] = []
        self._allocate_builtin_instance_to_target(
            scope,
            context,
            call,
            builtin_type="coroutine_iterator",
            alloc_suffix="await_iter",
        )
        elem_var = self._make_temp_var("await_elem", call)
        constraints.append(LoadConstraint(
            base=method_obj.receiver_var,
            field=Field(FieldKind.CLASS_METADATA, name="continuation.suspension"),
            target=elem_var,
        ))
        constraints.append(StoreConstraint(
            base=call.target,
            field=elem(),
            source=elem_var,
        ))
        # Iterator.send()/next() normally returns suspension tokens. A native
        # coroutine's final value is a separate StopIteration/await completion.
        result_var = self._make_temp_var("await_completion", call)
        constraints.extend((LoadConstraint(method_obj.receiver_var, attr("$await_result"), result_var),
                            StoreConstraint(call.target, attr("$await_result"), result_var)))
        return constraints
    
    # ===== Dict Methods =====

    def _native_dict_key_constraints(self, call, method_obj, store_key, operation):
        if (self._infer_container_type(method_obj.receiver) == "dict"
                and method_obj.receiver_var is not None and call.args):
            return [NativeMappingProtocolConstraint(method_obj.receiver_var, call.args[0],
                                                     call.call_site, store_key, operation)]
        return []

    def _dict_default_variable(self, scope, context, call):
        """Return the explicit default or a stable implicit ``None`` value.

        The synthetic variable is populated independently of the call target:
        ``mapping.setdefault(key)`` must still write its default when its return
        value is discarded. Its allocation identity is finite across builtin
        dependency replays at the same call site and abstract context.
        """
        if len(call.args) > 1:
            return call.args[1]
        default_var = self._make_temp_var("dict_implicit_default", call)
        alloc_site = AllocSite(("dict_implicit_default", call.call_site), AllocKind.CONSTANT)
        obj = self.state._heap.get_obj(scope, context, alloc_site)
        if obj is None:
            obj = canonical_constant(self.state, context, alloc_site, None)
            self.state._heap.set_obj(scope, context, alloc_site, obj)
            self.state.obj_scope[obj] = scope
        self._add_points_to_target(scope, context, default_var, obj)
        return default_var

    @staticmethod
    def _dict_default_argument_status(call, method_name):
        """Separate definitely invalid calls from unresolved unpacking shapes.

        These native methods accept one or two positional-only arguments. A
        starred operand is not itself proof of its eventual argument count,
        and an unknown ``**`` mapping may be empty. Retain their existing may
        transfers and an explicit obligation rather than reject them by the
        number of syntactic operands.
        """
        explicit_keywords = any(name != "**" for name, _ in call.kwargs)
        expanded_keywords = any(name == "**" for name, _ in call.kwargs)
        get_args = getattr(call.stmt, "get_args", None)
        expanded_positionals = bool(get_args and any(starred for _, starred in get_args()))
        invalid = explicit_keywords or (
            not expanded_keywords and not expanded_positionals and len(call.args) not in (1, 2)
        )
        if invalid:
            return True, [ResidualEffectConstraint(
                str(call.call_site),
                f"dict.{method_name} requires one or two positional-only arguments",
                ("may_raise", "exception_routing"),
            )]
        if expanded_keywords or expanded_positionals:
            return False, [ResidualEffectConstraint(
                str(call.call_site),
                f"dict.{method_name} argument expansion shape remains unresolved",
                ("argument_expansion", "may_raise"),
            )]
        return False, []
    
    def _handle_dict_get(
        self,
        scope: 'Scope',
        context: 'AbstractContext',
        call: 'CallConstraint',
        method_obj: 'BuiltinMethodObject'
    ) -> List['Constraint']:
        """Handle dict.get(key, default=None).
        
        Loads from dict.key(k) if constant, else dict.elem().
        """
        invalid, constraints = self._dict_default_argument_status(call, "get")
        if invalid:
            return constraints
        constraints.extend(self._native_dict_key_constraints(call, method_obj, False, "get"))
        
        if call.target and len(call.args) > 0 and method_obj.receiver_var:
            key_var = call.args[0]
            # Add LoadSubscrConstraint
            constraints.append(LoadSubscrConstraint(
                target=call.target,
                base=method_obj.receiver_var,
                index=key_var,
            ))
            # Membership is a may fact. Preserve the stored values as well as
            # either default, including when keys or values arrive later.
            default_var = self._dict_default_variable(scope, context, call)
            constraints.append(CopyConstraint(source=default_var, target=call.target))
        
        return constraints
    
    def _handle_dict_update(
        self,
        scope: 'Scope',
        context: 'AbstractContext',
        call: 'CallConstraint',
        method_obj: 'BuiltinMethodObject'
    ) -> List['Constraint']:
        """Copy known keys separately and retain residual-key writes."""
        if not method_obj.receiver_var:
            return []
        return self._mapping_copy_constraints(scope, context, call, method_obj.receiver_var,
                                              call.args[:1], call.kwargs)
    
    def _handle_dict_setdefault(
        self,
        scope: 'Scope',
        context: 'AbstractContext',
        call: 'CallConstraint',
        method_obj: 'BuiltinMethodObject'
    ) -> List['Constraint']:
        """Weakly join dict.setdefault(key, default=None) values and effects."""
        invalid, constraints = self._dict_default_argument_status(call, "setdefault")
        if invalid:
            return constraints
        constraints.extend(self._native_dict_key_constraints(call, method_obj, True, "setdefault"))
        
        if len(call.args) > 0 and method_obj.receiver_var:
            key_var = call.args[0]
            default_var = self._dict_default_variable(scope, context, call)
            constraints.append(StoreSubscrConstraint(
                base=method_obj.receiver_var,
                index=key_var,
                source=default_var,
            ))
            if call.target:
                constraints.append(CopyConstraint(source=default_var, target=call.target))
            if call.target:
                constraints.append(LoadSubscrConstraint(
                    target=call.target,
                    base=method_obj.receiver_var,
                    index=key_var,
                ))
        
        return constraints
    
    def _handle_dict_keys(
        self,
        scope: 'Scope',
        context: 'AbstractContext',
        call: 'CallConstraint',
        method_obj: 'BuiltinMethodObject'
    ) -> List['Constraint']:
        """Handle dict.keys()."""
        constraints: List['Constraint'] = []
        if call.target:
            self._allocate_builtin_instance_to_target(
                scope,
                context,
                call,
                builtin_type="dict_keys",
                alloc_kind=AllocKind.OBJECT,
                alloc_suffix="dict_keys",
            )
            if method_obj.receiver_var:
                keys_var = self._make_temp_var("dict_keys_provenance", call)
                constraints.extend((
                    LoadConstraint(method_obj.receiver_var, NATIVE_MAPPING_KEYS, keys_var),
                    StoreConstraint(call.target, elem(), keys_var),
                    ResidualEffectConstraint(str(call.call_site),
                        "Native dictionary keys view retains known identities; complete provenance, presence, order and mutation routing remain unproved",
                        ("native_mapping_key_provenance", "unknown_yield", "key_presence", "iteration_order", "iteration_mutation", "may_invoke_callbacks", "may_raise")),
                ))
            from .native_mapping import exact_native_mapping
            if exact_native_mapping(method_obj.receiver):
                # An invented Unknown key erases native key/value roles and
                # can feed unrelated dictionary values into named arguments.
                # Opaque provenance remains an explicit obligation above.
                return constraints
            unknown_var = self._make_temp_var("dict_keys_elem", call)
            constraints.append(AllocConstraint(
                target=unknown_var,
                alloc_site=AllocSite(stmt=call.stmt, kind=AllocKind.UNKNOWN),
            ))
            constraints.append(StoreConstraint(
                base=call.target,
                field=elem(),
                source=unknown_var,
            ))
        return constraints
    
    def _handle_dict_values(
        self,
        scope: 'Scope',
        context: 'AbstractContext',
        call: 'CallConstraint',
        method_obj: 'BuiltinMethodObject'
    ) -> List['Constraint']:
        """Handle dict.values()."""
        constraints: List['Constraint'] = []
        if call.target and method_obj.receiver_var:
            self._allocate_builtin_instance_to_target(
                scope,
                context,
                call,
                builtin_type="dict_values",
                alloc_kind=AllocKind.OBJECT,
                alloc_suffix="dict_values",
            )
            elem_var = self._make_temp_var("dict_values_elem", call)
            constraints.append(LoadConstraint(
                base=method_obj.receiver_var,
                field=elem(),
                target=elem_var,
            ))
            constraints.append(StoreConstraint(
                base=call.target,
                field=elem(),
                source=elem_var,
            ))
        return constraints
    
    def _handle_dict_items(self, scope, context, call, method_obj):
        """Retain one two-slot item per finite key, plus an unknown-key item.

        Replayed on receiver-field growth. Each tuple keeps the source key and
        its own value field; later loop joins may still forget this relation.
        """
        if not call.target or not method_obj.receiver_var:
            return []
        self._allocate_builtin_instance_to_target(
            scope, context, call, "dict_items", alloc_suffix="dict_items")
        receiver = method_obj.receiver
        fields = [field for field in self.state.known_fields_for_object(receiver)
                  if field.kind == FieldKind.KEY]
        fields.append(wildcard_key())
        if not self.config.index_sensitive:
            fields.append(elem())
        constraints = []
        for field in fields:
            values = self.state.get_field(scope, context, receiver, field)
            # An empty read-created slot is not evidence that this key exists.
            if self.state.get_points_to(values).is_empty():
                continue
            site = AllocSite(("dict_item", call.call_site, receiver, field), AllocKind.TUPLE)
            pair = self.state._heap.get_obj(scope, context, site)
            if pair is None:
                pair = MappingItemObject(self.state.select_allocation_context(context, site, identity="alias"), site)
                self.state._heap.set_obj(scope, context, site, pair)
                self.state.obj_scope[pair] = scope
            pair_var = self._make_temp_var(f"item_{id(receiver)}_{field!r}", call)
            self._add_points_to_target(scope, context, pair_var, pair)
            constraints.append(StoreConstraint(call.target, elem(), pair_var))
            pair_value = self.state.get_field(scope, context, pair, key(1))
            aggregate = self.state.get_field(scope, context, pair, elem())
            self.state._add_var_points_flow(values, pair_value)
            self.state._add_var_points_flow(values, aggregate)
            key_site = AllocSite(("dict_item_key", call.call_site, receiver, field),
                                 AllocKind.CONSTANT if field.kind == FieldKind.KEY and field.key_type is None
                                 else AllocKind.UNKNOWN)
            if key_site.kind == AllocKind.CONSTANT:
                key_object = canonical_constant(self.state, context, key_site, field.name)
            else:
                from .object import UnknownValue
                key_object = UnknownValue(self.state.select_allocation_context(context, key_site), key_site)
            key_slot = self.state.get_field(scope, context, pair, key(0))
            self.state._worklist.add((scope, NormalNode(key_slot), PointsToSet.singleton(key_object)))
            self.state._worklist.add((scope, NormalNode(aggregate), PointsToSet.singleton(key_object)))
        # Allocation happened directly, but retaining an empty element load is
        # also a persistent declared transfer (not an unhandled builtin call).
        if not constraints:
            dummy = self._make_temp_var("items_empty_observation", call)
            constraints.append(LoadConstraint(call.target, elem(), dummy))
        return constraints

    def _handle_dict_popitem(
        self,
        scope: 'Scope',
        context: 'AbstractContext',
        call: 'CallConstraint',
        method_obj: 'BuiltinMethodObject'
    ) -> List['Constraint']:
        """Handle dict.popitem() -> (key, value)."""
        constraints: List['Constraint'] = []
        if not call.target or not method_obj.receiver_var:
            return constraints

        tuple_var = call.target
        constraints.append(AllocConstraint(
            target=tuple_var,
            alloc_site=AllocSite(stmt=call.stmt, kind=AllocKind.TUPLE),
        ))
        value_var = self._make_temp_var("dict_popitem_value", call)
        constraints.append(LoadConstraint(
            base=method_obj.receiver_var,
            field=elem(),
            target=value_var,
        ))
        constraints.append(StoreConstraint(
            base=tuple_var,
            field=key(1),
            source=value_var,
        ))
        constraints.append(StoreConstraint(
            base=tuple_var,
            field=elem(),
            source=value_var,
        ))

        key_var = self._make_temp_var("dict_popitem_key", call)
        constraints.append(AllocConstraint(
            target=key_var,
            alloc_site=AllocSite(stmt=call.stmt, kind=AllocKind.UNKNOWN),
        ))
        constraints.append(StoreConstraint(
            base=tuple_var,
            field=key(0),
            source=key_var,
        ))
        constraints.append(StoreConstraint(
            base=tuple_var,
            field=elem(),
            source=key_var,
        ))
        return constraints
    
    # ===== Set Methods =====
    
    def _handle_set_add(
        self,
        scope: 'Scope',
        context: 'AbstractContext',
        call: 'CallConstraint',
        method_obj: 'BuiltinMethodObject'
    ) -> List['Constraint']:
        """Handle set.add(x).
        
        Stores x to set.elem().
        """
        constraints = []
        
        if len(call.args) > 0 and method_obj.receiver_var:
            item_var = call.args[0]
            # Store item to receiver.elem()
            constraints.append(StoreConstraint(
                base=method_obj.receiver_var,
                field=elem(),
                source=item_var,
            ))
        
        return constraints

    def _handle_set_update(
        self,
        scope: 'Scope',
        context: 'AbstractContext',
        call: 'CallConstraint',
        method_obj: 'BuiltinMethodObject'
    ) -> List['Constraint']:
        """Handle set.update(iterable)."""
        constraints = []
        
        if len(call.args) > 0 and method_obj.receiver_var:
            iterable_var = call.args[0]
            elem_var = self._make_temp_var("set_update_elem", call)
            constraints.append(LoadConstraint(
                base=iterable_var,
                field=elem(),
                target=elem_var,
            ))
            constraints.append(StoreConstraint(
                base=method_obj.receiver_var,
                field=elem(),
                source=elem_var,
            ))
        
        return constraints
    
    def _handle_set_discard(
        self,
        scope: 'Scope',
        context: 'AbstractContext',
        call: 'CallConstraint',
        method_obj: 'BuiltinMethodObject'
    ) -> List['Constraint']:
        """Handle set.discard(x)."""
        # No-op for pointer analysis
        return []
    
    def _handle_set_remove(
        self,
        scope: 'Scope',
        context: 'AbstractContext',
        call: 'CallConstraint',
        method_obj: 'BuiltinMethodObject'
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
        
        Creates an iterator whose elem() flows from iterable.elem().
        """
        constraints: List['Constraint'] = []

        if len(call.args) == 0:
            return constraints

        if len(call.args) == 1:
            contextual = self.state.get_variable(scope, context, call.args[0])
            for receiver in self.state.get_points_to(contextual):
                if not isinstance(receiver, InstanceObject):
                    continue
                receiver_var = self._make_temp_var(f"iter_receiver_{id(receiver)}", call)
                method_var = self._make_temp_var(f"iter_method_{id(receiver)}", call)
                self._add_points_to_target(scope, context, receiver_var, receiver)
                constraints.extend([
                    SpecialMethodLookupConstraint(receiver_var, "__iter__", method_var),
                    CallConstraint(method_var, (receiver_var,), frozenset(), call.target, call.call_site),
                    ResidualEffectConstraint(str(call.call_site),
                        "Instance iteration may-flows leave sequence fallback, descriptor binding and iterator validity unresolved",
                        ("getitem_fallback", "special_method_descriptor", "iterator_validity", "may_raise")),
                ])
        if not call.target:
            # Discarding an iterator does not discard __iter__ side effects.
            # A container-only call is still handled; its identity edge is a
            # no-op, while builtin argument dependencies remain subscribed.
            return constraints or [CopyConstraint(call.args[0], call.args[0])]

        self._allocate_builtin_instance_to_target(
            scope,
            context,
            call,
            builtin_type="iterator",
            alloc_suffix="iter",
        )

        if len(call.args) == 1:
            iterable_var = call.args[0]
            elem_var = self._make_temp_var("iter_elem", call)
            constraints.append(LoadConstraint(
                base=iterable_var,
                field=elem(),
                target=elem_var,
            ))
            constraints.append(StoreConstraint(
                base=call.target,
                field=elem(),
                source=elem_var,
            ))
        else:
            # iter(callable, sentinel) -> call callable() and yield results
            callable_var = call.args[0]
            result_var = self._make_temp_var("iter_call_result", call)
            constraints.append(CallConstraint(
                callee=callable_var,
                args=(),
                kwargs=frozenset(),
                target=result_var,
                call_site=call.call_site,
            ))
            constraints.append(StoreConstraint(
                base=call.target,
                field=elem(),
                source=result_var,
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
        constraints: List['Constraint'] = []

        if call.args:
            contextual = self.state.get_variable(scope, context, call.args[0])
            for receiver in self.state.get_points_to(contextual):
                if not isinstance(receiver, InstanceObject):
                    continue
                receiver_var = self._make_temp_var(f"next_receiver_{id(receiver)}", call)
                method_var = self._make_temp_var(f"next_method_{id(receiver)}", call)
                self._add_points_to_target(scope, context, receiver_var, receiver)
                constraints.extend([
                    SpecialMethodLookupConstraint(receiver_var, "__next__", method_var),
                    CallConstraint(method_var, (receiver_var,), frozenset(), call.target, call.call_site),
                    ResidualEffectConstraint(str(call.call_site),
                        "Instance advancement may-flows leave descriptor binding, exhaustion and state transitions unresolved",
                        ("special_method_descriptor", "iterator_exhaustion", "iterator_state", "may_raise")),
                ])
            if not call.target:
                return constraints or [CopyConstraint(call.args[0], call.args[0])]

        if call.target and len(call.args) > 0:
            iterator_var = call.args[0]
            constraints.append(LoadConstraint(
                base=iterator_var,
                field=elem(),
                target=call.target,
            ))
            if len(call.args) > 1:
                default_var = call.args[1]
                constraints.append(CopyConstraint(
                    source=default_var,
                    target=call.target,
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
        constraints: List['Constraint'] = []

        if call.target and len(call.args) > 0:
            self._allocate_builtin_instance_to_target(
                scope,
                context,
                call,
                builtin_type="enumerate",
                alloc_suffix="enumerate",
            )
            tuple_var = self._make_temp_var("enumerate_tuple", call)
            constraints.append(AllocConstraint(
                target=tuple_var,
                alloc_site=AllocSite(stmt=call.stmt, kind=AllocKind.TUPLE),
            ))
            constraints.append(StoreConstraint(
                base=call.target,
                field=elem(),
                source=tuple_var,
            ))

            item_var = self._make_temp_var("enumerate_item", call)
            constraints.append(LoadConstraint(
                base=call.args[0],
                field=elem(),
                target=item_var,
            ))
            constraints.append(StoreConstraint(
                base=tuple_var,
                field=key(1),
                source=item_var,
            ))
            constraints.append(StoreConstraint(
                base=tuple_var,
                field=elem(),
                source=item_var,
            ))

            index_var = self._make_temp_var("enumerate_index", call)
            constraints.append(AllocConstraint(
                target=index_var,
                alloc_site=AllocSite(stmt=call.stmt, kind=AllocKind.INTEGER),
            ))
            constraints.append(StoreConstraint(
                base=tuple_var,
                field=key(0),
                source=index_var,
            ))
            constraints.append(StoreConstraint(
                base=tuple_var,
                field=elem(),
                source=index_var,
            ))
        
        return constraints
    
    def _handle_zip(
        self,
        scope: 'Scope',
        context: 'AbstractContext',
        call: 'CallConstraint'
    ) -> List['Constraint']:
        """Handle zip(*iterables)."""
        constraints: List['Constraint'] = []

        if call.target:
            self._allocate_builtin_instance_to_target(
                scope,
                context,
                call,
                builtin_type="zip",
                alloc_suffix="zip",
            )
            tuple_var = self._make_temp_var("zip_tuple", call)
            constraints.append(AllocConstraint(
                target=tuple_var,
                alloc_site=AllocSite(stmt=call.stmt, kind=AllocKind.TUPLE),
            ))
            constraints.append(StoreConstraint(
                base=call.target,
                field=elem(),
                source=tuple_var,
            ))
            for idx, iterable_var in enumerate(call.args):
                elem_var = self._make_temp_var(f"zip_elem_{idx}", call)
                constraints.append(LoadConstraint(
                    base=iterable_var,
                    field=elem(),
                    target=elem_var,
                ))
                constraints.append(StoreConstraint(
                    base=tuple_var,
                    field=key(idx),
                    source=elem_var,
                ))
                constraints.append(StoreConstraint(
                    base=tuple_var,
                    field=elem(),
                    source=elem_var,
                ))
        
        return constraints
    
    def _handle_map(
        self,
        scope: 'Scope',
        context: 'AbstractContext',
        call: 'CallConstraint'
    ) -> List['Constraint']:
        """Handle map(func, *iterables)."""
        constraints: List['Constraint'] = []

        if call.target and len(call.args) > 1:
            self._allocate_builtin_instance_to_target(
                scope,
                context,
                call,
                builtin_type="map",
                alloc_suffix="map",
            )
            func_var = call.args[0]
            elem_vars = []
            for idx, iterable_var in enumerate(call.args[1:]):
                elem_var = self._make_temp_var(f"map_elem_{idx}", call)
                constraints.append(LoadConstraint(
                    base=iterable_var,
                    field=elem(),
                    target=elem_var,
                ))
                elem_vars.append(elem_var)
            result_var = self._make_temp_var("map_result", call)
            constraints.append(CallConstraint(
                callee=func_var,
                args=tuple(elem_vars),
                kwargs=frozenset(call.kwargs),
                target=result_var,
                call_site=call.call_site,
            ))
            constraints.append(StoreConstraint(
                base=call.target,
                field=elem(),
                source=result_var,
            ))
        
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
        constraints: List['Constraint'] = []

        if call.target and len(call.args) > 1:
            func_var = call.args[0]
            iterable_var = call.args[1]

            self._allocate_builtin_instance_to_target(
                scope,
                context,
                call,
                builtin_type="filter",
                alloc_suffix="filter",
            )
            
            # Link to iterable elements
            elem_var = self._make_temp_var("filter_elem", call)
            constraints.append(LoadConstraint(
                base=iterable_var,
                field=elem(),
                target=elem_var,
            ))
            pred_var = self._make_temp_var("filter_pred", call)
            constraints.append(CallConstraint(
                callee=func_var,
                args=(elem_var,),
                kwargs=frozenset(),
                target=pred_var,
                call_site=call.call_site,
            ))
            constraints.append(StoreConstraint(
                base=call.target,
                field=elem(),
                source=elem_var,
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
        constraints: List['Constraint'] = []

        if call.target and len(call.args) > 0:
            sequence_var = call.args[0]

            self._allocate_builtin_instance_to_target(
                scope,
                context,
                call,
                builtin_type="reversed",
                alloc_suffix="reversed",
            )
            
            # Link to sequence elements
            elem_var = self._make_temp_var("reversed_elem", call)
            constraints.append(LoadConstraint(
                base=sequence_var,
                field=elem(),
                target=elem_var,
            ))
            constraints.append(StoreConstraint(
                base=call.target,
                field=elem(),
                source=elem_var,
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
        constraints = []
        
        if call.target and len(call.args) > 0:
            alloc_site = AllocSite(stmt=call.stmt, kind=AllocKind.LIST)
            constraints.append(AllocConstraint(target=call.target, alloc_site=alloc_site))
            elem_var = self._make_temp_var("sorted_elem", call)
            constraints.append(LoadConstraint(
                base=call.args[0],
                field=elem(),
                target=elem_var,
            ))
            constraints.append(StoreConstraint(
                base=call.target,
                field=elem(),
                source=elem_var,
            ))

            key_var = None
            for name, var in call.kwargs:
                if name == "key":
                    key_var = var
                    break
            if key_var is not None:
                key_result = self._make_temp_var("sorted_key_result", call)
                constraints.append(CallConstraint(
                    callee=key_var,
                    args=(elem_var,),
                    kwargs=frozenset(),
                    target=key_result,
                    call_site=call.call_site,
                ))
        
        return constraints
    
    def _handle_range(
        self,
        scope: 'Scope',
        context: 'AbstractContext',
        call: 'CallConstraint'
    ) -> List['Constraint']:
        """Handle range(...)."""
        constraints: List['Constraint'] = []

        if call.target:
            self._allocate_builtin_instance_to_target(
                scope,
                context,
                call,
                builtin_type="range",
                alloc_kind=AllocKind.OBJECT,
                alloc_suffix="range",
            )
            elem_var = self._make_temp_var("range_elem", call)
            constraints.append(AllocConstraint(
                target=elem_var,
                alloc_site=AllocSite(stmt=call.stmt, kind=AllocKind.INTEGER),
            ))
            constraints.append(StoreConstraint(
                base=call.target,
                field=elem(),
                source=elem_var,
            ))

        return constraints

    def _handle_sum(
        self,
        scope: 'Scope',
        context: 'AbstractContext',
        call: 'CallConstraint'
    ) -> List['Constraint']:
        """Handle sum(iterable, start=0)."""
        constraints: List['Constraint'] = []
        if not call.target or len(call.args) == 0:
            return constraints
        elem_var = self._make_temp_var("sum_elem", call)
        constraints.append(LoadConstraint(
            base=call.args[0],
            field=elem(),
            target=elem_var,
        ))
        constraints.append(CopyConstraint(
            source=elem_var,
            target=call.target,
        ))
        if len(call.args) > 1:
            constraints.append(CopyConstraint(
                source=call.args[1],
                target=call.target,
            ))
        for name, var in call.kwargs:
            if name == "start":
                constraints.append(CopyConstraint(
                    source=var,
                    target=call.target,
                ))
        return constraints

    def _handle_min(
        self,
        scope: 'Scope',
        context: 'AbstractContext',
        call: 'CallConstraint'
    ) -> List['Constraint']:
        """Handle min(iterable, *args, default=...)."""
        constraints: List['Constraint'] = []
        if not call.target:
            return constraints
        if len(call.args) == 0:
            return constraints
        if len(call.args) == 1:
            elem_var = self._make_temp_var("min_elem", call)
            constraints.append(LoadConstraint(
                base=call.args[0],
                field=elem(),
                target=elem_var,
            ))
            constraints.append(CopyConstraint(
                source=elem_var,
                target=call.target,
            ))
        else:
            for arg in call.args:
                constraints.append(CopyConstraint(
                    source=arg,
                    target=call.target,
                ))
        for name, var in call.kwargs:
            if name == "default":
                constraints.append(CopyConstraint(
                    source=var,
                    target=call.target,
                ))
        return constraints

    def _handle_max(
        self,
        scope: 'Scope',
        context: 'AbstractContext',
        call: 'CallConstraint'
    ) -> List['Constraint']:
        """Handle max(iterable, *args, default=...)."""
        return self._handle_min(scope, context, call)

    def _handle_all(
        self,
        scope: 'Scope',
        context: 'AbstractContext',
        call: 'CallConstraint'
    ) -> List['Constraint']:
        """Handle all(iterable)."""
        return self._alloc_primitive(call, AllocKind.BOOLEAN)

    def _handle_any(
        self,
        scope: 'Scope',
        context: 'AbstractContext',
        call: 'CallConstraint'
    ) -> List['Constraint']:
        """Handle any(iterable)."""
        return self._alloc_primitive(call, AllocKind.BOOLEAN)
    
    # ===== Type/Scalar Functions =====
    
    def _handle_len(
        self,
        scope: 'Scope',
        context: 'AbstractContext',
        call: 'CallConstraint'
    ) -> List['Constraint']:
        """Handle len(obj)."""
        constraints = []
        
        if call.target:
            # Create int object
            alloc_site = AllocSite(stmt=call.stmt, kind=AllocKind.INTEGER)
            constraints.append(AllocConstraint(target=call.target, alloc_site=alloc_site))
        
        return constraints
    
    def _handle_isinstance(
        self,
        scope: 'Scope',
        context: 'AbstractContext',
        call: 'CallConstraint'
    ) -> List['Constraint']:
        """Handle isinstance(obj, type)."""
        constraints = []
        
        if call.target:
            # Create bool object
            alloc_site = AllocSite(stmt=call.stmt, kind=AllocKind.BOOLEAN)
            constraints.append(AllocConstraint(target=call.target, alloc_site=alloc_site))
        
        return constraints

    def _handle_issubclass(
        self,
        scope: 'Scope',
        context: 'AbstractContext',
        call: 'CallConstraint'
    ) -> List['Constraint']:
        """Handle issubclass(cls, classinfo)."""
        constraints = []
        
        if call.target:
            alloc_site = AllocSite(stmt=call.stmt, kind=AllocKind.BOOLEAN)
            constraints.append(AllocConstraint(target=call.target, alloc_site=alloc_site))
        
        return constraints
    
    def _handle_type(
        self,
        scope: 'Scope',
        context: 'AbstractContext',
        call: 'CallConstraint'
    ) -> List['Constraint']:
        """Handle type(obj)."""
        constraints = []
        
        if call.target and len(call.args) > 0:
            arg_var = call.args[0]
            arg_ctx = self.state.get_variable(scope, context, arg_var)
            target_ctx = self.state.get_variable(scope, context, call.target)
            arg_pts = self.state.get_points_to(arg_ctx)
            added = False
            for obj in arg_pts:
                if isinstance(obj, InstanceObject):
                    self.state._worklist.add((scope, NormalNode(target_ctx), PointsToSet.singleton(obj.class_obj)))
                    added = True
                elif isinstance(obj, ClassObject):
                    self.state._worklist.add((scope, NormalNode(target_ctx), PointsToSet.singleton(obj)))
                    added = True
                elif isinstance(obj, BuiltinInstanceObject):
                    builtin_cls = self._builtin_type_objects.get(obj.builtin_type)
                    if builtin_cls is None:
                        builtin_cls = ObjectFactory.create_builtin_class(obj.builtin_type, context)
                        canonical = self.state.select_allocation_context(
                            context, builtin_cls.alloc_site, identity="canonical")
                        builtin_cls = BuiltinClassObject(canonical, builtin_cls.alloc_site,
                                                         builtin_cls.builtin_name)
                        self._builtin_type_objects[obj.builtin_type] = builtin_cls
                    self.state._worklist.add((scope, NormalNode(target_ctx), PointsToSet.singleton(builtin_cls)))
                    added = True
                elif isinstance(obj, BuiltinClassObject):
                    self.state._worklist.add((scope, NormalNode(target_ctx), PointsToSet.singleton(obj)))
                    added = True
            if not added:
                alloc_site = AllocSite(stmt=call.stmt, kind=AllocKind.OBJECT)
                constraints.append(AllocConstraint(target=call.target, alloc_site=alloc_site))
        
        return constraints
    
    def _handle_bool(
        self,
        scope: 'Scope',
        context: 'AbstractContext',
        call: 'CallConstraint'
    ) -> List['Constraint']:
        """Handle bool(obj)."""
        constraints = []
        
        if call.target:
            alloc_site = AllocSite(stmt=call.stmt, kind=AllocKind.BOOLEAN)
            constraints.append(AllocConstraint(target=call.target, alloc_site=alloc_site))
        
        return constraints
    
    def _handle_int(
        self,
        scope: 'Scope',
        context: 'AbstractContext',
        call: 'CallConstraint'
    ) -> List['Constraint']:
        """Handle int(obj)."""
        constraints = []
        
        if call.target:
            alloc_site = AllocSite(stmt=call.stmt, kind=AllocKind.INTEGER)
            constraints.append(AllocConstraint(target=call.target, alloc_site=alloc_site))
        
        return constraints
    
    def _handle_float(
        self,
        scope: 'Scope',
        context: 'AbstractContext',
        call: 'CallConstraint'
    ) -> List['Constraint']:
        """Handle float(obj)."""
        constraints = []
        
        if call.target:
            alloc_site = AllocSite(stmt=call.stmt, kind=AllocKind.FLOAT)
            constraints.append(AllocConstraint(target=call.target, alloc_site=alloc_site))
        
        return constraints
    
    def _handle_str(
        self,
        scope: 'Scope',
        context: 'AbstractContext',
        call: 'CallConstraint'
    ) -> List['Constraint']:
        """Handle str(obj)."""
        constraints: List['Constraint'] = []
        if not call.target:
            return constraints

        self._allocate_builtin_instance_to_target(
            scope,
            context,
            call,
            builtin_type="str",
            alloc_kind=AllocKind.STRING,
            alloc_suffix="str",
        )

        elem_var = self._make_temp_var("str_elem", call)
        constraints.append(AllocConstraint(
            target=elem_var,
            alloc_site=AllocSite(stmt=call.stmt, kind=AllocKind.STRING),
        ))
        constraints.append(StoreConstraint(
            base=call.target,
            field=elem(),
            source=elem_var,
        ))
        return constraints

    def _handle_bytes(
        self,
        scope: 'Scope',
        context: 'AbstractContext',
        call: 'CallConstraint'
    ) -> List['Constraint']:
        """Handle bytes(obj)."""
        constraints: List['Constraint'] = []
        if not call.target:
            return constraints

        self._allocate_builtin_instance_to_target(
            scope,
            context,
            call,
            builtin_type="bytes",
            alloc_kind=AllocKind.OBJECT,
            alloc_suffix="bytes",
        )

        elem_var = self._make_temp_var("bytes_elem", call)
        constraints.append(AllocConstraint(
            target=elem_var,
            alloc_site=AllocSite(stmt=call.stmt, kind=AllocKind.INTEGER),
        ))
        if len(call.args) > 0:
            constraints.append(LoadConstraint(
                base=call.args[0],
                field=elem(),
                target=elem_var,
            ))
        constraints.append(StoreConstraint(
            base=call.target,
            field=elem(),
            source=elem_var,
        ))
        return constraints

    def _handle_bytearray(
        self,
        scope: 'Scope',
        context: 'AbstractContext',
        call: 'CallConstraint'
    ) -> List['Constraint']:
        """Handle bytearray(obj)."""
        constraints: List['Constraint'] = []
        if not call.target:
            return constraints

        self._allocate_builtin_instance_to_target(
            scope,
            context,
            call,
            builtin_type="bytearray",
            alloc_kind=AllocKind.OBJECT,
            alloc_suffix="bytearray",
        )

        elem_var = self._make_temp_var("bytearray_elem", call)
        constraints.append(AllocConstraint(
            target=elem_var,
            alloc_site=AllocSite(stmt=call.stmt, kind=AllocKind.INTEGER),
        ))
        if len(call.args) > 0:
            constraints.append(LoadConstraint(
                base=call.args[0],
                field=elem(),
                target=elem_var,
            ))
        constraints.append(StoreConstraint(
            base=call.target,
            field=elem(),
            source=elem_var,
        ))
        return constraints

    def _handle_memoryview(
        self,
        scope: 'Scope',
        context: 'AbstractContext',
        call: 'CallConstraint'
    ) -> List['Constraint']:
        """Handle memoryview(obj)."""
        constraints: List['Constraint'] = []
        if not call.target:
            return constraints

        self._allocate_builtin_instance_to_target(
            scope,
            context,
            call,
            builtin_type="memoryview",
            alloc_kind=AllocKind.OBJECT,
            alloc_suffix="memoryview",
        )

        elem_var = self._make_temp_var("memoryview_elem", call)
        if len(call.args) > 0:
            constraints.append(LoadConstraint(
                base=call.args[0],
                field=elem(),
                target=elem_var,
            ))
        else:
            constraints.append(AllocConstraint(
                target=elem_var,
                alloc_site=AllocSite(stmt=call.stmt, kind=AllocKind.INTEGER),
            ))
        constraints.append(StoreConstraint(
            base=call.target,
            field=elem(),
            source=elem_var,
        ))
        return constraints

    def _handle_slice(
        self,
        scope: 'Scope',
        context: 'AbstractContext',
        call: 'CallConstraint'
    ) -> List['Constraint']:
        """Handle slice(start, stop, step)."""
        constraints: List['Constraint'] = []
        if not call.target:
            return constraints

        self._allocate_builtin_instance_to_target(
            scope,
            context,
            call,
            builtin_type="slice",
            alloc_kind=AllocKind.OBJECT,
            alloc_suffix="slice",
        )
        return constraints

    def _handle_abs(
        self,
        scope: 'Scope',
        context: 'AbstractContext',
        call: 'CallConstraint'
    ) -> List['Constraint']:
        """Handle abs(x)."""
        return self._alloc_primitive(call, AllocKind.INTEGER)

    def _handle_round(
        self,
        scope: 'Scope',
        context: 'AbstractContext',
        call: 'CallConstraint'
    ) -> List['Constraint']:
        """Handle round(x, ndigits=None)."""
        return self._alloc_primitive(call, AllocKind.INTEGER)

    def _handle_divmod(
        self,
        scope: 'Scope',
        context: 'AbstractContext',
        call: 'CallConstraint'
    ) -> List['Constraint']:
        """Handle divmod(a, b) -> (q, r)."""
        constraints: List['Constraint'] = []
        if not call.target:
            return constraints
        tuple_var = call.target
        constraints.append(AllocConstraint(
            target=tuple_var,
            alloc_site=AllocSite(stmt=call.stmt, kind=AllocKind.TUPLE),
        ))
        q_var = self._make_temp_var("divmod_q", call)
        r_var = self._make_temp_var("divmod_r", call)
        constraints.append(AllocConstraint(
            target=q_var,
            alloc_site=AllocSite(stmt=call.stmt, kind=AllocKind.INTEGER),
        ))
        constraints.append(AllocConstraint(
            target=r_var,
            alloc_site=AllocSite(stmt=call.stmt, kind=AllocKind.INTEGER),
        ))
        constraints.append(StoreConstraint(
            base=tuple_var,
            field=key(0),
            source=q_var,
        ))
        constraints.append(StoreConstraint(
            base=tuple_var,
            field=key(1),
            source=r_var,
        ))
        constraints.append(StoreConstraint(
            base=tuple_var,
            field=elem(),
            source=q_var,
        ))
        constraints.append(StoreConstraint(
            base=tuple_var,
            field=elem(),
            source=r_var,
        ))
        return constraints

    def _handle_pow(
        self,
        scope: 'Scope',
        context: 'AbstractContext',
        call: 'CallConstraint'
    ) -> List['Constraint']:
        """Handle pow(x, y, mod=None)."""
        return self._alloc_primitive(call, AllocKind.INTEGER)

    def _handle_complex(
        self,
        scope: 'Scope',
        context: 'AbstractContext',
        call: 'CallConstraint'
    ) -> List['Constraint']:
        """Handle complex(real, imag)."""
        return self._alloc_primitive(call, AllocKind.OBJECT)

    def _handle_repr(
        self,
        scope: 'Scope',
        context: 'AbstractContext',
        call: 'CallConstraint'
    ) -> List['Constraint']:
        """Handle repr(obj)."""
        return self._alloc_primitive(call, AllocKind.STRING)

    def _handle_ascii(
        self,
        scope: 'Scope',
        context: 'AbstractContext',
        call: 'CallConstraint'
    ) -> List['Constraint']:
        """Handle ascii(obj)."""
        return self._alloc_primitive(call, AllocKind.STRING)

    def _handle_format(
        self,
        scope: 'Scope',
        context: 'AbstractContext',
        call: 'CallConstraint'
    ) -> List['Constraint']:
        """Handle format(value, format_spec)."""
        return self._alloc_primitive(call, AllocKind.STRING)

    def _handle_bin(
        self,
        scope: 'Scope',
        context: 'AbstractContext',
        call: 'CallConstraint'
    ) -> List['Constraint']:
        """Handle bin(x)."""
        return self._alloc_primitive(call, AllocKind.STRING)

    def _handle_hex(
        self,
        scope: 'Scope',
        context: 'AbstractContext',
        call: 'CallConstraint'
    ) -> List['Constraint']:
        """Handle hex(x)."""
        return self._alloc_primitive(call, AllocKind.STRING)

    def _handle_oct(
        self,
        scope: 'Scope',
        context: 'AbstractContext',
        call: 'CallConstraint'
    ) -> List['Constraint']:
        """Handle oct(x)."""
        return self._alloc_primitive(call, AllocKind.STRING)

    def _handle_chr(
        self,
        scope: 'Scope',
        context: 'AbstractContext',
        call: 'CallConstraint'
    ) -> List['Constraint']:
        """Handle chr(i)."""
        return self._alloc_primitive(call, AllocKind.STRING)

    def _handle_ord(
        self,
        scope: 'Scope',
        context: 'AbstractContext',
        call: 'CallConstraint'
    ) -> List['Constraint']:
        """Handle ord(c)."""
        return self._alloc_primitive(call, AllocKind.INTEGER)

    def _handle_hash(
        self,
        scope: 'Scope',
        context: 'AbstractContext',
        call: 'CallConstraint'
    ) -> List['Constraint']:
        """Handle hash(obj)."""
        return self._alloc_primitive(call, AllocKind.INTEGER)

    def _handle_id(
        self,
        scope: 'Scope',
        context: 'AbstractContext',
        call: 'CallConstraint'
    ) -> List['Constraint']:
        """Handle id(obj)."""
        return self._alloc_primitive(call, AllocKind.INTEGER)

    def _handle_callable(
        self,
        scope: 'Scope',
        context: 'AbstractContext',
        call: 'CallConstraint'
    ) -> List['Constraint']:
        """Handle callable(obj)."""
        constraints = []
        
        if call.target:
            alloc_site = AllocSite(stmt=call.stmt, kind=AllocKind.BOOLEAN)
            constraints.append(AllocConstraint(target=call.target, alloc_site=alloc_site))
        
        return constraints

    def _handle_getattr(
        self,
        scope: 'Scope',
        context: 'AbstractContext',
        call: 'CallConstraint'
    ) -> List['Constraint']:
        """Handle getattr(obj, name, default=None)."""
        constraints = []
        
        if not call.target or len(call.args) < 2:
            return constraints
        
        base_var = call.args[0]
        name_var = call.args[1]
        const_names, has_non_const = self._resolve_attr_names(scope, context, name_var)
        
        for name in const_names:
            constraints.append(AttrReadConstraint(
                base=base_var,
                attr=name,
                target=call.target,
                call_site=call.call_site,
            ))
        
        if has_non_const or not const_names:
            constraints.append(AttrReadConstraint(
                base=base_var,
                attr=unknown(),
                target=call.target,
                call_site=call.call_site,
            ))
        
        if len(call.args) > 2:
            default_var = call.args[2]
            constraints.append(CopyConstraint(
                source=default_var,
                target=call.target,
            ))
        
        return constraints

    def _handle_setattr(
        self,
        scope: 'Scope',
        context: 'AbstractContext',
        call: 'CallConstraint'
    ) -> List['Constraint']:
        """Handle setattr(obj, name, value)."""
        constraints = []
        
        if len(call.args) < 3:
            return constraints
        
        base_var = call.args[0]
        name_var = call.args[1]
        value_var = call.args[2]
        const_names, has_non_const = self._resolve_attr_names(scope, context, name_var)
        
        for name in const_names:
            constraints.append(AttrWriteConstraint(
                base=base_var,
                attr=name,
                source=value_var,
                call_site=call.call_site,
            ))
        
        if has_non_const or not const_names:
            constraints.append(AttrWriteConstraint(
                base=base_var,
                attr=unknown(),
                source=value_var,
                call_site=call.call_site,
            ))
        
        return constraints

    def _handle_hasattr(
        self,
        scope: 'Scope',
        context: 'AbstractContext',
        call: 'CallConstraint'
    ) -> List['Constraint']:
        """Handle hasattr(obj, name)."""
        constraints = []
        
        if call.target:
            alloc_site = AllocSite(stmt=call.stmt, kind=AllocKind.BOOLEAN)
            constraints.append(AllocConstraint(target=call.target, alloc_site=alloc_site))

        if len(call.args) < 2:
            return constraints

        base_var = call.args[0]
        name_var = call.args[1]
        const_names, has_non_const = self._resolve_attr_names(scope, context, name_var)
        temp_var = self._make_temp_var("hasattr_value", call)

        for name in const_names:
            constraints.append(AttrReadConstraint(
                base=base_var,
                attr=name,
                target=temp_var,
                call_site=call.call_site,
            ))

        if has_non_const or not const_names:
            constraints.append(AttrReadConstraint(
                base=base_var,
                attr=unknown(),
                target=temp_var,
                call_site=call.call_site,
            ))
        
        return constraints

    def _handle_delattr(
        self,
        scope: 'Scope',
        context: 'AbstractContext',
        call: 'CallConstraint'
    ) -> List['Constraint']:
        """Handle delattr(obj, name)."""
        constraints = []
        
        if len(call.args) < 2:
            return constraints
        
        base_var = call.args[0]
        name_var = call.args[1]
        const_names, has_non_const = self._resolve_attr_names(scope, context, name_var)
        
        value_var = self._make_temp_var("delattr_value", call)
        constraints.append(AllocConstraint(
            target=value_var,
            alloc_site=AllocSite(stmt=call.stmt, kind=AllocKind.UNKNOWN),
        ))
        
        for name in const_names:
            constraints.append(AttrWriteConstraint(
                base=base_var,
                attr=name,
                source=value_var,
                call_site=call.call_site,
            ))
        
        if has_non_const or not const_names:
            constraints.append(AttrWriteConstraint(
                base=base_var,
                attr=unknown(),
                source=value_var,
                call_site=call.call_site,
            ))
        
        return constraints

    def _handle_vars(
        self,
        scope: 'Scope',
        context: 'AbstractContext',
        call: 'CallConstraint'
    ) -> List['Constraint']:
        """Handle vars(obj)."""
        constraints = []
        
        if not call.target:
            return constraints
        if len(call.args) < 1:
            unknown_var = self._make_temp_var("vars_unknown", call)
            constraints.append(AllocConstraint(
                target=call.target,
                alloc_site=AllocSite(stmt=call.stmt, kind=AllocKind.DICT),
            ))
            constraints.append(AllocConstraint(
                target=unknown_var,
                alloc_site=AllocSite(stmt=call.stmt, kind=AllocKind.UNKNOWN),
            ))
            constraints.append(StoreConstraint(
                base=call.target,
                field=elem(),
                source=unknown_var,
            ))
            return constraints
        
        # vars(instance) returns the live own namespace, not a copied mapping
        # of inherited/getattr-resolved values.
        return [AttrReadConstraint(call.args[0], "__dict__", call.target, call.call_site)]

    def _handle_dir(
        self,
        scope: 'Scope',
        context: 'AbstractContext',
        call: 'CallConstraint'
    ) -> List['Constraint']:
        """Handle dir(obj) returning list of names."""
        constraints: List['Constraint'] = []
        if not call.target:
            return constraints

        constraints.append(AllocConstraint(
            target=call.target,
            alloc_site=AllocSite(stmt=call.stmt, kind=AllocKind.LIST),
        ))
        elem_var = self._make_temp_var("dir_name", call)
        constraints.append(AllocConstraint(
            target=elem_var,
            alloc_site=AllocSite(stmt=call.stmt, kind=AllocKind.STRING),
        ))
        constraints.append(StoreConstraint(
            base=call.target,
            field=elem(),
            source=elem_var,
        ))
        return constraints

    def _handle_globals(
        self,
        scope: 'Scope',
        context: 'AbstractContext',
        call: 'CallConstraint'
    ) -> List['Constraint']:
        """Handle globals() returning dict-like environment."""
        constraints: List['Constraint'] = []
        if not call.target:
            return constraints
        constraints.append(AllocConstraint(
            target=call.target,
            alloc_site=AllocSite(stmt=call.stmt, kind=AllocKind.DICT),
        ))
        unknown_var = self._make_temp_var("globals_unknown", call)
        constraints.append(AllocConstraint(
            target=unknown_var,
            alloc_site=AllocSite(stmt=call.stmt, kind=AllocKind.UNKNOWN),
        ))
        constraints.append(StoreConstraint(
            base=call.target,
            field=elem(),
            source=unknown_var,
        ))
        return constraints

    def _handle_locals(
        self,
        scope: 'Scope',
        context: 'AbstractContext',
        call: 'CallConstraint'
    ) -> List['Constraint']:
        """Handle locals() returning dict-like environment."""
        return self._handle_globals(scope, context, call)
    
    # ===== Object-Oriented Functions =====
    
    def _handle_super(
        self,
        scope: 'Scope',
        context: 'AbstractContext',
        call: 'CallConstraint'
    ) -> List['Constraint']:
        """Handle super() builtin for MRO-based method resolution.
        
        Creates a SuperObject that enables parent class field access:
        
        Supported forms:
        - super() - no args, uses __class__ cell var and first param
        - super(Class, instance) - explicit class and instance
        
        The SuperObject is created via AllocConstraint. A SuperResolveConstraint
        then populates it with current_class and instance_obj. When fields are
        accessed on the SuperObject, state.get_field() uses InheritanceConstraint
        to resolve through parent classes via PFG edges.
        
        Design:
        1. AllocConstraint creates SuperObject allocation
        2. SuperResolveConstraint resolves class/instance from args
        3. Field access on SuperObject triggers InheritanceConstraint
        4. Parent class fields flow to super field via PFG edges
        5. Methods are bound to instance_obj when called
        """
        constraints = []
        
        if not call.target:
            return constraints
        
        # Create SuperObject allocation
        alloc_site = AllocSite(stmt=call.stmt, kind=AllocKind.OBJECT)
        constraints.append(AllocConstraint(target=call.target, alloc_site=alloc_site))
        
        # Add SuperResolveConstraint to populate current_class and instance_obj
        # This constraint applies when the target's points-to set contains SuperObject
        if len(call.args) >= 2:
            # super(Class, instance) - explicit form
            class_var = call.args[0]
            instance_var = call.args[1]
            constraints.append(SuperResolveConstraint(
                target=call.target,
                class_var=class_var,
                instance_var=instance_var,
                implicit=False
            ))
        else:
            # super() - implicit form, needs to look up __class__ and first param
            # The constraint examines the enclosing scope when applied
            constraints.append(SuperResolveConstraint(
                target=call.target,
                class_var=None,
                instance_var=None,
                implicit=True
            ))
        
        return constraints

    # ===== Descriptor Constructors =====

    def _handle_property(
        self,
        scope: 'Scope',
        context: 'AbstractContext',
        call: 'CallConstraint'
    ) -> List['Constraint']:
        """Handle property(fget, fset=None, fdel=None)."""
        constraints: List['Constraint'] = []
        if not call.target:
            return constraints

        self._allocate_builtin_instance_to_target(
            scope,
            context,
            call,
            builtin_type="property",
            alloc_kind=AllocKind.OBJECT,
            alloc_suffix="property",
        )

        if len(call.args) > 0:
            constraints.append(StoreConstraint(
                base=call.target,
                field=attr("fget"),
                source=call.args[0],
            ))
        if len(call.args) > 1:
            constraints.append(StoreConstraint(
                base=call.target,
                field=attr("fset"),
                source=call.args[1],
            ))
        if len(call.args) > 2:
            constraints.append(StoreConstraint(
                base=call.target,
                field=attr("fdel"),
                source=call.args[2],
            ))

        for name, var in call.kwargs:
            if name == "fget":
                constraints.append(StoreConstraint(
                    base=call.target,
                    field=attr("fget"),
                    source=var,
                ))
            if name == "fset":
                constraints.append(StoreConstraint(
                    base=call.target,
                    field=attr("fset"),
                    source=var,
                ))
            if name == "fdel":
                constraints.append(StoreConstraint(
                    base=call.target,
                    field=attr("fdel"),
                    source=var,
                ))
        return constraints

    def _handle_classmethod(
        self,
        scope: 'Scope',
        context: 'AbstractContext',
        call: 'CallConstraint'
    ) -> List['Constraint']:
        """Handle classmethod(func)."""
        constraints: List['Constraint'] = []
        if not call.target:
            return constraints

        self._allocate_builtin_instance_to_target(
            scope,
            context,
            call,
            builtin_type="classmethod",
            alloc_kind=AllocKind.OBJECT,
            alloc_suffix="classmethod",
        )
        if len(call.args) > 0:
            constraints.append(StoreConstraint(
                base=call.target,
                field=attr("func"),
                source=call.args[0],
            ))
        return constraints

    def _handle_staticmethod(
        self,
        scope: 'Scope',
        context: 'AbstractContext',
        call: 'CallConstraint'
    ) -> List['Constraint']:
        """Handle staticmethod(func)."""
        constraints: List['Constraint'] = []
        if not call.target:
            return constraints

        self._allocate_builtin_instance_to_target(
            scope,
            context,
            call,
            builtin_type="staticmethod",
            alloc_kind=AllocKind.OBJECT,
            alloc_suffix="staticmethod",
        )
        if len(call.args) > 0:
            constraints.append(StoreConstraint(
                base=call.target,
                field=attr("func"),
                source=call.args[0],
            ))
        return constraints

    # ===== Miscellaneous Builtins =====

    def _handle_object(
        self,
        scope: 'Scope',
        context: 'AbstractContext',
        call: 'CallConstraint'
    ) -> List['Constraint']:
        """Handle object() constructor."""
        if not call.target:
            return []
        alloc_site = AllocSite(stmt=call.stmt, kind=AllocKind.OBJECT)
        return [AllocConstraint(target=call.target, alloc_site=alloc_site)]

    def _handle_open(
        self,
        scope: 'Scope',
        context: 'AbstractContext',
        call: 'CallConstraint'
    ) -> List['Constraint']:
        """Handle open(...) returning a file-like object."""
        constraints: List['Constraint'] = []
        if not call.target:
            return constraints

        self._allocate_builtin_instance_to_target(
            scope,
            context,
            call,
            builtin_type="file",
            alloc_kind=AllocKind.OBJECT,
            alloc_suffix="file",
        )
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
        constraints = []
        
        if call.target:
            logger.debug(f"Generic constructor: {builtin_name}")
            alloc_site = AllocSite(stmt=call.stmt, kind=AllocKind.OBJECT)
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
        constraints = []
        
        if call.target:
            logger.debug(f"Generic builtin function: {function_name}")
            alloc_site = AllocSite(stmt=call.stmt, kind=AllocKind.OBJECT)
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
        constraints = []
        
        if call.target:
            logger.debug(f"Generic builtin method: {method_obj.method_name}")
            alloc_site = AllocSite(stmt=call.stmt, kind=AllocKind.OBJECT)
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


BUILTIN_FUNCTIONS = BuiltinAPIHandler.CONTAINER_TYPES | BuiltinAPIHandler.ITERATOR_BUILTINS | BuiltinAPIHandler.TYPE_BUILTINS
