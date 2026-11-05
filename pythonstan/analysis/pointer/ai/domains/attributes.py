"""Attribute resolution semantics for AI abstract interpretation.

This module encapsulates Python attribute resolution protocol:
- Instance attribute lookup (instance dict vs class dict via MRO)
- Descriptor classification and precedence (data vs non-data)
- Dunder hooks (__getattribute__, __getattr__, __setattr__)
- Module __getattr__ (PEP 562)
- ClassInfo-aware class attribute lookup with metaclass support
- DescriptorSemantics integration for property/classmethod/staticmethod

The interpreter delegates to AttributeSemantics to keep value semantics
in the domains layer rather than the interpreter.

Enhanced features:
- ClassInfo integration: Uses ClassInfo for class namespace and MRO
- Metaclass-aware lookup: Class objects as instances of their metaclass
- Descriptor semantics: Proper handling of property/classmethod/staticmethod
- Presence tracking: Knows when attributes may or may not exist
"""

from typing import (
    TYPE_CHECKING,
    Any,
    Callable,
    Dict,
    FrozenSet,
    List,
    Optional,
    Set,
    Tuple,
)

if TYPE_CHECKING:
    from pythonstan.analysis.pointer.kcfa.object import AbstractObject
    from pythonstan.analysis.pointer.kcfa.heap_model import Field
    from pythonstan.analysis.pointer.kcfa.context import AbstractContext, Scope
    from ..state import AIState
    from ..value_domain import AbsVal
    from ..summary import SummaryAccumulator
    from ..api import PtaQuery, AnalysisBudget
    from .classinfo import ClassInfo, ClassInfoCache
    from .descriptor import DescriptorSemantics, DescriptorVal

__all__ = ["AttributeSemantics"]


# Type alias for the callee invocation callback
InvokeCalleeFunc = Callable[
    [
        "AbstractObject",  # callee_obj
        List[Tuple[str, "AbsVal"]],  # arg_vals
        "SummaryAccumulator",  # summary
        Optional["AbsVal"],  # receiver_val
    ],
    "AbsVal",  # return value
]


class AttributeSemantics:
    """Encapsulates Python attribute resolution protocol.
    
    This class owns the logic for:
    - MRO-aware attribute lookup
    - Descriptor invocation (__get__, __set__, __delete__)
    - Hook invocation (__getattribute__, __getattr__, __setattr__)
    - Module __getattr__ (PEP 562)
    - ClassInfo-aware class namespace lookup
    - Metaclass-aware class attribute resolution
    
    The interpreter injects its invoke_callee method so we can call
    descriptors/hooks without circular imports.
    
    Enhanced features:
    - ClassInfo integration for class namespaces, bases, metaclass
    - DescriptorSemantics for property/classmethod/staticmethod interpretation
    - Presence tracking for missing attribute refinement
    """

    def __init__(
        self,
        pta_query: Optional["PtaQuery"],
        caller_scope: Optional["Scope"],
        call_context: Optional["AbstractContext"],
        invoke_callee: InvokeCalleeFunc,
        budget: Optional["AnalysisBudget"] = None,
        class_info_cache: Optional["ClassInfoCache"] = None,
    ):
        """Initialize attribute semantics.
        
        Args:
            pta_query: PTA query interface for MRO and field lookups
            caller_scope: Scope that called this function
            call_context: Context at the call site
            invoke_callee: Callback to invoke callees (avoids interpreter import)
            budget: Analysis budget for caps
            class_info_cache: Cache for ClassInfo (shared across lookups)
        """
        self.pta_query = pta_query
        self.caller_scope = caller_scope
        self.call_context = call_context
        self.invoke_callee = invoke_callee
        self.budget = budget
        
        # Initialize ClassInfo cache
        if class_info_cache is not None:
            self.class_info_cache = class_info_cache
        else:
            from .classinfo import ClassInfoCache
            self.class_info_cache = ClassInfoCache(pta_query, budget)
        
        # Initialize DescriptorSemantics
        from .descriptor import DescriptorSemantics
        self.descriptor_semantics = DescriptorSemantics(invoke_callee)

    def read_attr(
        self,
        receiver_addrs: FrozenSet["AbstractObject"],
        attr_name: str,
        state: "AIState",
        summary: "SummaryAccumulator",
    ) -> "AbsVal":
        """Read attribute with full Python semantics.
        
        Implements the attribute resolution protocol:
        1. Check for __getattribute__ override
        2. Check for data descriptors (__get__ + __set__/__delete__)
        3. Instance/object __dict__ lookup
        4. Check for non-data descriptors (__get__ only)
        5. Fallback to __getattr__ if attribute not found
        6. Module __getattr__ (PEP 562) for module objects
        
        Enhanced with:
        - ClassInfo for class namespace lookup and MRO traversal
        - DescriptorSemantics for property/classmethod/staticmethod interpretation
        
        Args:
            receiver_addrs: Set of receiver objects
            attr_name: Attribute name to look up
            state: Current abstract state
            summary: Summary accumulator
            
        Returns:
            AbsVal representing the attribute value
        """
        from pythonstan.analysis.pointer.kcfa.heap_model import attr, unknown
        from pythonstan.analysis.pointer.kcfa.object import InstanceObject
        from ..value_domain import AbsVal
        from .binding import bind_descriptor

        result_val = AbsVal.bottom()
        saw_havoc = False

        for obj in receiver_addrs:
            if attr_name in ("send", "throw", "close"):
                gen_method_val = self._get_generator_builtin_method(
                    obj, attr_name, state
                )
                if gen_method_val is not None:
                    result_val = result_val.join(gen_method_val)
                    obj_found_attr = True
                    continue

            field_key = attr(attr_name)
            receiver_val = AbsVal.from_addrs(frozenset({obj}))
            obj_found_attr = False

            # Step 1: Check for __getattribute__ override
            getattribute_addrs = self._lookup_method(obj, "__getattribute__", state)
            if getattribute_addrs:
                for ga_obj in getattribute_addrs:
                    ga_result = self.invoke_callee(
                        ga_obj,
                        [("name", AbsVal.from_str(attr_name))],
                        summary,
                        receiver_val,
                    )
                    result_val = result_val.join(ga_result)
                obj_found_attr = True

            if isinstance(obj, InstanceObject):
                # Use enhanced ClassInfo-based lookup
                class_val, class_havoc, owner_cls = self.read_class_attr_with_classinfo(
                    obj.class_obj,
                    attr_name,
                    state,
                    summary,
                    receiver_val,
                )
                if class_havoc:
                    saw_havoc = True
                if not class_val.is_bottom():
                    obj_found_attr = True

                # Classify descriptors using DescriptorSemantics
                desc_val = self._classify_as_descriptor(class_val, state)
                owner_cls_val = AbsVal.from_addrs(frozenset({owner_cls})) if owner_cls else None
                
                # Data descriptor values (take precedence over instance dict)
                data_desc_result = AbsVal.bottom()
                # Non-data descriptor values (checked after instance dict)
                non_data_desc_result = AbsVal.bottom()
                # Plain class values (not descriptors)
                plain_class_val = AbsVal.bottom()
                
                if desc_val.has_data_descriptor():
                    # Data descriptors have highest precedence
                    data_desc_result = self.descriptor_semantics.interpret_get(
                        desc_val, receiver_val, owner_cls_val, summary
                    )
                    obj_found_attr = True
                elif not desc_val.is_bottom():
                    # Non-data descriptors (checked after instance dict)
                    non_data_desc_result = self.descriptor_semantics.interpret_get(
                        desc_val, receiver_val, owner_cls_val, summary
                    )
                    obj_found_attr = True
                
                # Collect plain class values (addresses not identified as descriptors)
                desc_addrs = set()
                for tok in desc_val.tokens if not desc_val.is_bottom() else []:
                    desc_addrs.add(getattr(tok, 'obj', None))
                
                for field_obj in class_val.addrs:
                    if field_obj not in desc_addrs:
                        # Not a typed descriptor - check for __get__ protocol
                        get_method_addrs = self._lookup_method(field_obj, "__get__", state)
                        if get_method_addrs:
                            # Has __get__ but wasn't classified - use protocol
                            has_set = bool(self._lookup_method(field_obj, "__set__", state))
                            has_delete = bool(self._lookup_method(field_obj, "__delete__", state))
                            for gm_obj in get_method_addrs:
                                proto_result = self.invoke_callee(
                                    gm_obj,
                                    [("obj", receiver_val), ("type", AbsVal.top())],
                                    summary,
                                    AbsVal.from_addrs(frozenset({field_obj})),
                                )
                                if has_set or has_delete:
                                    data_desc_result = data_desc_result.join(proto_result)
                                else:
                                    non_data_desc_result = non_data_desc_result.join(proto_result)
                            obj_found_attr = True
                        else:
                            # Plain class value (no descriptor)
                            plain_class_val = plain_class_val.join(
                                AbsVal.from_addrs(frozenset({field_obj}))
                            )
                            obj_found_attr = True

                # Include non-address components from class_val
                if not class_val.is_bottom():
                    plain_class_val = plain_class_val.join(
                        class_val.with_addrs(frozenset())
                    )

                # Instance dict lookup
                inst_val, inst_havoc = state.read_field_value(
                    obj, field_key, self.caller_scope, self.call_context
                )
                if inst_havoc:
                    saw_havoc = True
                if not inst_val.is_bottom():
                    obj_found_attr = True

                # Combine results according to Python precedence:
                # 1. Data descriptors (highest)
                # 2. Instance dict
                # 3. Non-data descriptors
                # 4. Plain class values
                result_val = result_val.join(data_desc_result)
                result_val = result_val.join(inst_val)
                result_val = result_val.join(non_data_desc_result)
                result_val = result_val.join(plain_class_val)

                # Method binding for plain callables
                if owner_cls is not None and not class_val.is_bottom():
                    result_val = result_val.join(
                        bind_descriptor(owner_cls, class_val, receiver_val)
                    )

                summary.add_read(frozenset({obj}), field_key)
            else:
                # Non-instance object (class, module, other)
                # Delegate to specialized handlers
                obj_result, obj_havoc, found = self._read_attr_non_instance(
                    obj, attr_name, field_key, receiver_val, state, summary
                )
                if obj_havoc:
                    saw_havoc = True
                if found:
                    obj_found_attr = True
                result_val = result_val.join(obj_result)

            # Also try unknown field for dynamic attributes
            unknown_val, unknown_havoc = state.read_field_value(
                obj, unknown(), self.caller_scope, self.call_context
            )
            if unknown_havoc:
                saw_havoc = True
            if not unknown_val.is_bottom():
                result_val = result_val.join(unknown_val)
                obj_found_attr = True

            # Step 5: Check for __getattr__ fallback if attribute might be missing
            if not obj_found_attr:
                getattr_addrs = self._lookup_method(obj, "__getattr__", state)
                if getattr_addrs:
                    for ga_obj in getattr_addrs:
                        ga_result = self.invoke_callee(
                            ga_obj,
                            [("name", AbsVal.from_str(attr_name))],
                            summary,
                            receiver_val,
                        )
                        result_val = result_val.join(ga_result)
                    obj_found_attr = True

            # Step 6: Module __getattr__ (PEP 562)
            if self._is_module_object(obj) and not obj_found_attr:
                module_getattr_addrs = self._lookup_method(obj, "__getattr__", state)
                if module_getattr_addrs:
                    for mg_obj in module_getattr_addrs:
                        mg_result = self.invoke_callee(
                            mg_obj,
                            [("name", AbsVal.from_str(attr_name))],
                            summary,
                            None,
                        )
                        result_val = result_val.join(mg_result)
                    obj_found_attr = True

        if result_val.is_bottom():
            summary.add_may_raise("AttributeError")
        if saw_havoc or result_val.is_bottom():
            from ..value_domain import AbsVal

            result_val = result_val.join(AbsVal.top())
        return result_val

    def _get_generator_builtin_method(
        self,
        obj: "AbstractObject",
        attr_name: str,
        state: "AIState",
    ) -> Optional["AbsVal"]:
        from pythonstan.analysis.pointer.kcfa.object import ObjectFactory
        from ..heap import AIAddr, ObjKind
        from ..value_domain import AbsVal

        ai_addr = AIAddr.from_kcfa_obj(obj)
        ai_obj = state.get_obj(ai_addr)
        if ai_obj is None or ai_obj.kind != ObjKind.ITERATOR:
            return None

        slot = ai_obj.slots.get_str("__gen_func__")
        if not slot.pres.may_be_present() or slot.val.is_bottom():
            return None

        method_obj = ObjectFactory.create_builtin_method(attr_name, obj, obj.context)
        return AbsVal.from_addrs(frozenset({method_obj}))

    def _read_attr_non_instance(
        self,
        obj: "AbstractObject",
        attr_name: str,
        field_key: "Field",
        receiver_val: "AbsVal",
        state: "AIState",
        summary: "SummaryAccumulator",
    ) -> Tuple["AbsVal", bool, bool]:
        """Read attribute from non-instance object (class, module, other).
        
        Uses ClassInfo and DescriptorSemantics for class objects.
        
        Args:
            obj: The receiver object (not an InstanceObject)
            attr_name: Attribute name
            field_key: Pre-computed field key
            receiver_val: AbsVal for the receiver
            state: AI state
            summary: Summary accumulator
            
        Returns:
            Tuple of (result_val, saw_havoc, found_attr)
        """
        from ..value_domain import AbsVal
        from .binding import bind_descriptor
        
        result_val = AbsVal.bottom()
        saw_havoc = False
        found_attr = False
        
        if self._is_class_object(obj):
            # Class object - use ClassInfo-aware lookup (includes metaclass)
            class_val, class_havoc, owner_cls = self.read_class_attr_with_classinfo(
                obj,
                attr_name,
                state,
                summary,
                receiver_val,
            )
            if class_havoc:
                saw_havoc = True
            if not class_val.is_bottom():
                found_attr = True
                
                # Classify descriptors
                desc_val = self._classify_as_descriptor(class_val, state)
                owner_cls_val = AbsVal.from_addrs(frozenset({owner_cls})) if owner_cls else None
                
                if not desc_val.is_bottom():
                    # Interpret through DescriptorSemantics
                    desc_result = self.descriptor_semantics.interpret_get(
                        desc_val, receiver_val, owner_cls_val, summary
                    )
                    result_val = result_val.join(desc_result)
                
                # Also include non-descriptor values
                desc_addrs = set()
                for tok in desc_val.tokens if not desc_val.is_bottom() else []:
                    desc_addrs.add(getattr(tok, 'obj', None))
                
                for field_obj in class_val.addrs:
                    if field_obj not in desc_addrs:
                        # Check for __get__ protocol
                        get_method_addrs = self._lookup_method(field_obj, "__get__", state)
                        if get_method_addrs:
                            for gm_obj in get_method_addrs:
                                proto_result = self.invoke_callee(
                                    gm_obj,
                                    [("obj", receiver_val), ("type", AbsVal.top())],
                                    summary,
                                    AbsVal.from_addrs(frozenset({field_obj})),
                                )
                                result_val = result_val.join(proto_result)
                        else:
                            result_val = result_val.join(
                                AbsVal.from_addrs(frozenset({field_obj}))
                            )
                
                # Include non-address components
                result_val = result_val.join(class_val.with_addrs(frozenset()))
                
            summary.add_read(frozenset({obj}), field_key)
        else:
            # Other objects (module, etc.) - direct lookup with descriptor checks
            field_val, field_havoc = state.read_field_value(
                obj, field_key, self.caller_scope, self.call_context
            )
            if field_havoc:
                saw_havoc = True

            if not field_val.is_bottom():
                found_attr = True

            # Classify and interpret descriptors
            desc_val = self._classify_as_descriptor(field_val, state)
            
            if not desc_val.is_bottom():
                desc_result = self.descriptor_semantics.interpret_get(
                    desc_val, receiver_val, None, summary
                )
                result_val = result_val.join(desc_result)
            
            # Check for __get__ protocol on non-classified addresses
            desc_addrs = set()
            for tok in desc_val.tokens if not desc_val.is_bottom() else []:
                desc_addrs.add(getattr(tok, 'obj', None))
            
            for field_obj in field_val.addrs:
                if field_obj not in desc_addrs:
                    get_method_addrs = self._lookup_method(field_obj, "__get__", state)
                    if get_method_addrs:
                        for gm_obj in get_method_addrs:
                            proto_result = self.invoke_callee(
                                gm_obj,
                                [("obj", receiver_val), ("type", AbsVal.top())],
                                summary,
                                AbsVal.from_addrs(frozenset({field_obj})),
                            )
                            result_val = result_val.join(proto_result)
                        found_attr = True
                    else:
                        result_val = result_val.join(
                            AbsVal.from_addrs(frozenset({field_obj}))
                        )
                        found_attr = True

            # Include non-address components and binding effects
            result_val = result_val.join(
                bind_descriptor(obj, field_val, receiver_val)
            )
            summary.add_read(frozenset({obj}), field_key)
        
        return result_val, saw_havoc, found_attr

    def write_attr(
        self,
        receiver_addrs: FrozenSet["AbstractObject"],
        attr_name: str,
        value_val: "AbsVal",
        state: "AIState",
        summary: "SummaryAccumulator",
    ) -> "AIState":
        """Write attribute with full Python semantics.
        
        Implements the attribute assignment protocol:
        1. Check for __setattr__ override
        2. Check for data descriptors (__set__) via DescriptorSemantics
        3. Direct write to instance __dict__
        
        Enhanced with:
        - ClassInfo for class attribute lookup
        - DescriptorSemantics for property/data descriptor handling
        
        Args:
            receiver_addrs: Set of receiver objects
            attr_name: Attribute name to write
            value_val: Value to write
            state: Current abstract state
            summary: Summary accumulator
            
        Returns:
            Updated AIState
        """
        from pythonstan.analysis.pointer.kcfa.heap_model import attr
        from pythonstan.analysis.pointer.kcfa.object import InstanceObject
        from ..value_domain import AbsVal

        field_key = attr(attr_name)
        new_state = state

        for obj in receiver_addrs:
            receiver_val = AbsVal.from_addrs(frozenset({obj}))
            data_desc_handled = False
            
            # Step 1: Check for __setattr__ override
            setattr_addrs = self._lookup_method(obj, "__setattr__", state)
            if setattr_addrs:
                for sa_obj in setattr_addrs:
                    self.invoke_callee(
                        sa_obj,
                        [
                            ("name", AbsVal.from_str(attr_name)),
                            ("value", value_val),
                        ],
                        summary,
                        receiver_val,
                    )

            # Step 2: Check for data descriptors
            if isinstance(obj, InstanceObject):
                # Use ClassInfo to find class attribute
                class_val, _, owner_cls = self.read_class_attr_with_classinfo(
                    obj.class_obj,
                    attr_name,
                    state,
                    summary,
                    receiver_val,
                )
                
                if not class_val.is_bottom():
                    # Classify descriptors
                    desc_val = self._classify_as_descriptor(class_val, state)
                    
                    if desc_val.has_data_descriptor():
                        # Use DescriptorSemantics for data descriptor
                        data_desc_handled = self.descriptor_semantics.interpret_set(
                            desc_val, receiver_val, value_val, summary
                        )
                    
                    # Also check for protocol-based descriptors not in desc_val
                    desc_addrs = set()
                    for tok in desc_val.tokens if not desc_val.is_bottom() else []:
                        desc_addrs.add(getattr(tok, 'obj', None))
                    
                    for field_obj in class_val.addrs:
                        if field_obj not in desc_addrs:
                            set_method_addrs = self._lookup_method(field_obj, "__set__", state)
                            if set_method_addrs:
                                for sm_obj in set_method_addrs:
                                    self.invoke_callee(
                                        sm_obj,
                                        [("obj", receiver_val), ("value", value_val)],
                                        summary,
                                        AbsVal.from_addrs(frozenset({field_obj})),
                                    )
                                data_desc_handled = True
            else:
                # Non-instance object - check for descriptors in field value
                field_val, _ = state.read_field_value(
                    obj, field_key, self.caller_scope, self.call_context
                )
                
                if not field_val.is_bottom():
                    desc_val = self._classify_as_descriptor(field_val, state)
                    
                    if desc_val.has_data_descriptor():
                        data_desc_handled = self.descriptor_semantics.interpret_set(
                            desc_val, receiver_val, value_val, summary
                        )
                    
                    # Protocol-based descriptors
                    desc_addrs = set()
                    for tok in desc_val.tokens if not desc_val.is_bottom() else []:
                        desc_addrs.add(getattr(tok, 'obj', None))
                    
                    for field_obj in field_val.addrs:
                        if field_obj not in desc_addrs:
                            set_method_addrs = self._lookup_method(field_obj, "__set__", state)
                            if set_method_addrs:
                                for sm_obj in set_method_addrs:
                                    self.invoke_callee(
                                        sm_obj,
                                        [("obj", receiver_val), ("value", value_val)],
                                        summary,
                                        AbsVal.from_addrs(frozenset({field_obj})),
                                    )
                                data_desc_handled = True

            # Step 3: Direct write to instance __dict__
            # Note: In Python, data descriptors completely intercept the write,
            # but we conservatively also write to instance dict for soundness
            # (the descriptor might not fully handle all paths)
            if value_val.addrs:
                summary.add_write(
                    frozenset({obj}),
                    field_key,
                    frozenset(value_val.addrs),
                )
            new_state = new_state.write_field_value(obj, field_key, value_val)

        return new_state

    # -------------------------------------------------------------------------
    # ClassInfo-enhanced lookup methods
    # -------------------------------------------------------------------------

    def read_class_attr_with_classinfo(
        self,
        cls_obj: "AbstractObject",
        attr_name: str,
        state: "AIState",
        summary: "SummaryAccumulator",
        receiver_val: Optional["AbsVal"] = None,
    ) -> Tuple["AbsVal", bool, Optional["AbstractObject"]]:
        """Read class attribute using ClassInfo if available.
        
        Enhanced lookup that uses ClassInfo for:
        - Class namespace lookup with presence tracking
        - MRO traversal using cached MRO
        - Metaclass-aware lookup (class as instance of metaclass)
        
        Args:
            cls_obj: Class object to look up in
            attr_name: Attribute name
            state: Current AI state
            summary: Summary accumulator
            receiver_val: Optional receiver for descriptor binding
            
        Returns:
            Tuple of (value, saw_havoc, owner_class)
        """
        from ..value_domain import AbsVal
        from pythonstan.analysis.pointer.kcfa.heap_model import attr
        from pythonstan.analysis.pointer.kcfa.object import AllocKind
        
        # Get ClassInfo
        class_info = self.class_info_cache.get_info(cls_obj, state)
        
        # First check class namespace via ClassInfo
        if class_info.has_namespace():
            ns_val = class_info.get_namespace_value(attr_name)
            if ns_val is not None and not ns_val.is_bottom():
                # Found in class namespace
                return ns_val, False, cls_obj
        
        # Use MRO from ClassInfo or fallback to PTA
        mro = self.class_info_cache.get_mro(cls_obj, state)
        
        # Search MRO
        field_key = attr(attr_name)
        saw_havoc = False
        
        for mro_cls in mro:
            # Check ClassInfo namespace for each MRO class
            mro_info = self.class_info_cache.get_info(mro_cls, state)
            if mro_info.has_namespace():
                mro_ns_val = mro_info.get_namespace_value(attr_name)
                if mro_ns_val is not None and not mro_ns_val.is_bottom():
                    return mro_ns_val, saw_havoc, mro_cls
            
            # Fallback to state/PTA lookup
            field_val, field_havoc = state.read_field_value(
                mro_cls, field_key, self.caller_scope, self.call_context
            )
            if field_havoc:
                saw_havoc = True
            summary.add_read(frozenset({mro_cls}), field_key)
            if not field_val.is_bottom():
                return field_val, saw_havoc, mro_cls
        
        # Check metaclass if this is a class object
        if class_info.has_metaclass() and self._is_class_object(cls_obj):
            # Class objects are instances of their metaclass
            # Look up attribute in metaclass MRO
            for meta_obj in class_info.metaclass_addrs:
                meta_result = self._read_class_attr_in_mro(
                    meta_obj, field_key, state, summary
                )
                meta_val, meta_havoc, meta_owner = meta_result
                if meta_havoc:
                    saw_havoc = True
                if not meta_val.is_bottom():
                    # Found in metaclass - need descriptor binding
                    # The class itself is the receiver
                    cls_val = AbsVal.from_addrs(frozenset({cls_obj}))
                    bound_val = self._bind_metaclass_descriptor(
                        meta_val, cls_val, meta_owner, summary
                    )
                    return bound_val, saw_havoc, meta_owner
        
        return AbsVal.bottom(), saw_havoc, None

    def _bind_metaclass_descriptor(
        self,
        desc_val: "AbsVal",
        cls_val: "AbsVal",
        owner: Optional["AbstractObject"],
        summary: "SummaryAccumulator",
    ) -> "AbsVal":
        """Bind descriptor found in metaclass to class receiver.
        
        When accessing attribute on class, and it's found in metaclass,
        the descriptor receives the class as instance.
        
        Args:
            desc_val: Descriptor value from metaclass
            cls_val: Class value (receiver for descriptor)
            owner: Metaclass where descriptor was found
            summary: Summary accumulator
            
        Returns:
            Bound value
        """
        from ..value_domain import AbsVal
        from .descriptor import DescriptorVal, PropertyRef, ClassMethodRef, StaticMethodRef
        
        result = AbsVal.bottom()
        
        # Try to interpret as known descriptor types
        desc_domain_val = desc_val.get_component("descriptor")
        if isinstance(desc_domain_val, DescriptorVal) and not desc_domain_val.is_top():
            # Use DescriptorSemantics for known descriptors
            owner_val = AbsVal.from_addrs(frozenset({owner})) if owner else None
            result = self.descriptor_semantics.interpret_get(
                desc_domain_val, cls_val, owner_val, summary
            )
        
        # Also check for __get__ protocol
        for field_obj in desc_val.addrs:
            get_method_addrs = self._lookup_method(field_obj, "__get__", None)
            if get_method_addrs:
                for gm_obj in get_method_addrs:
                    desc_result = self.invoke_callee(
                        gm_obj,
                        [
                            ("obj", cls_val),
                            ("type", AbsVal.top()),
                        ],
                        summary,
                        AbsVal.from_addrs(frozenset({field_obj})),
                    )
                    result = result.join(desc_result)
            else:
                # Non-descriptor - return as-is
                result = result.join(AbsVal.from_addrs(frozenset({field_obj})))
        
        # Include non-address components
        result = result.join(desc_val.with_addrs(frozenset()))
        
        return result

    # -------------------------------------------------------------------------
    # Helper methods
    # -------------------------------------------------------------------------

    def get_class_mro(
        self,
        cls_obj: "AbstractObject",
    ) -> List["AbstractObject"]:
        """Get the class MRO for attribute resolution.
        
        Uses ClassInfo cache if available.
        """
        # Try ClassInfo first
        return self.class_info_cache.get_mro(cls_obj, None)

    def _read_class_attr_in_mro(
        self,
        cls_obj: "AbstractObject",
        field_key: "Field",
        state: "AIState",
        summary: "SummaryAccumulator",
    ) -> Tuple["AbsVal", bool, Optional["AbstractObject"]]:
        """Read the first class attribute found in MRO order."""
        from ..value_domain import AbsVal

        saw_havoc = False
        for mro_cls in self.get_class_mro(cls_obj):
            field_val, field_havoc = state.read_field_value(
                mro_cls, field_key, self.caller_scope, self.call_context
            )
            if field_havoc:
                saw_havoc = True
            summary.add_read(frozenset({mro_cls}), field_key)
            if not field_val.is_bottom():
                return field_val, saw_havoc, mro_cls
        return AbsVal.bottom(), saw_havoc, None

    def _lookup_method(
        self,
        obj: "AbstractObject",
        method_name: str,
        state: Optional["AIState"] = None,
    ) -> FrozenSet["AbstractObject"]:
        """Look up a method on an object via PTA/state.
        
        Args:
            obj: Object to look up on
            method_name: Method name (e.g., "__get__", "__getattr__")
            state: Optional state for overlay reads
            
        Returns:
            Set of callable objects for the method, or empty set
        """
        from pythonstan.analysis.pointer.kcfa.heap_model import attr

        try:
            field_key = attr(method_name)
            if state is not None:
                method_val, _ = state.read_field_value(
                    obj, field_key, self.caller_scope, self.call_context
                )
                return frozenset(method_val.addrs)
            if self.pta_query is not None:
                method_pts = self.pta_query.field_points_to(
                    obj, field_key, self.caller_scope, self.call_context
                )
                return frozenset(method_pts)
            return frozenset()
        except Exception:
            return frozenset()

    def _is_module_object(self, obj: "AbstractObject") -> bool:
        """Check if an object represents a module."""
        try:
            if hasattr(obj, "kind"):
                from pythonstan.analysis.pointer.kcfa.object import AllocKind

                return getattr(obj, "kind", None) == AllocKind.MODULE
            if hasattr(obj, "is_module"):
                return obj.is_module
            class_name = type(obj).__name__.lower()
            return "module" in class_name
        except Exception:
            return False

    def _is_class_object(self, obj: "AbstractObject") -> bool:
        """Check if an object represents a class."""
        try:
            if hasattr(obj, "kind"):
                from pythonstan.analysis.pointer.kcfa.object import AllocKind

                return getattr(obj, "kind", None) == AllocKind.CLASS
            class_name = type(obj).__name__.lower()
            return "class" in class_name
        except Exception:
            return False

    def _classify_as_descriptor(
        self,
        attr_val: "AbsVal",
        state: "AIState",
    ) -> "DescriptorVal":
        """Classify attribute value addresses as descriptor tokens if applicable.
        
        Examines each address in attr_val to determine if it represents a known
        descriptor type (property, classmethod, staticmethod) based on decorator
        flags in the IR. Also checks for generic descriptors with __get__ protocol.
        
        Args:
            attr_val: Abstract value containing potential descriptor addresses
            state: AI state for method lookups
            
        Returns:
            DescriptorVal with typed tokens, or bottom if no descriptors found
        """
        from .descriptor import (
            DescriptorVal,
            DescriptorRef,
            create_descriptor_val_for_method,
        )
        
        tokens = set()
        
        for obj in attr_val.addrs:
            tok = self._make_descriptor_token(obj, state)
            if tok is not None:
                tokens.add(tok)
        
        if not tokens:
            return DescriptorVal.bottom()
        return DescriptorVal.from_tokens(frozenset(tokens))

    def _make_descriptor_token(
        self,
        obj: "AbstractObject",
        state: "AIState",
    ):
        """Create a descriptor token for an object if it's a descriptor.
        
        Checks:
        1. IR decorator flags (is_getter, is_class_method, is_static_method)
        2. Protocol methods (__get__, __set__, __delete__)
        
        Args:
            obj: Object to check
            state: AI state for method lookups
            
        Returns:
            DescriptorToken or None if not a descriptor
        """
        from .descriptor import (
            DescriptorRef,
            PropertyRef,
            ClassMethodRef,
            StaticMethodRef,
            create_descriptor_val_for_method,
        )
        
        # First try known descriptor types from IR flags
        desc_val = create_descriptor_val_for_method(obj, state)
        if desc_val is not None and not desc_val.is_bottom():
            # Return the first token from the created DescriptorVal
            if desc_val.tokens:
                return next(iter(desc_val.tokens))
        
        # Fall back to protocol-based detection
        has_get = bool(self._lookup_method(obj, "__get__", state))
        if not has_get:
            return None
        
        has_set = bool(self._lookup_method(obj, "__set__", state))
        has_delete = bool(self._lookup_method(obj, "__delete__", state))
        
        return DescriptorRef(
            obj=obj,
            has_get=has_get,
            has_set=has_set,
            has_delete=has_delete,
        )
