"""Call dispatch semantics for abstract interpretation.

This module encapsulates Python call resolution and dispatch:
- Extracting callable targets from abstract values
- Method lookup for class __call__ invocation
- Class-call fallback when scope IR unavailable
- Unknown call effect recording

The interpreter delegates to CallSemantics to keep call dispatch logic
in the domains layer rather than the interpreter.
"""

from dataclasses import dataclass, field
from typing import (
    TYPE_CHECKING,
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
    from pythonstan.analysis.pointer.kcfa.context import AbstractContext, Scope
    from pythonstan.ir.ir_statements import IRFunc
    from ..state import AIState
    from ..value_domain import AbsVal
    from ..summary import SummaryAccumulator
    from ..api import PtaQuery

__all__ = ["CallSemantics", "CallDispatchResult"]


# Type aliases for injected callbacks
InvokeCalleeFunc = Callable[
    [
        "AbstractObject",  # callee_obj
        List[Tuple[str, "AbsVal"]],  # arg_vals
        "SummaryAccumulator",  # summary
        Optional["AbsVal"],  # receiver_val
        Optional[str],  # callsite_id
        Optional["AbstractContext"],  # call_context
        Optional[int],  # argc
    ],
    "AbsVal",  # return value
]

GetCalleeScopeIRFunc = Callable[
    ["AbstractObject"],  # callee_obj
    Optional["IRFunc"],  # return IR scope or None
]

SelectCallContextFunc = Callable[
    [
        str,  # callsite_id
        int,  # argc
        Optional["AbstractObject"],  # receiver_obj
        Optional[Tuple["AbstractObject", ...]],  # params
    ],
    Optional["AbstractContext"],
]

BuildArgValsFunc = Callable[
    [
        "IRFunc",  # scope_ir
        List["AbsVal"],  # pos_args
        Dict[str, "AbsVal"],  # kw_args
        Optional["AbsVal"],  # receiver_val
    ],
    List[Tuple[str, "AbsVal"]],
]

GetReceiverForMethodFunc = Callable[
    [
        "AbstractObject",  # callee_obj
        "IRFunc",  # scope_ir
        Optional["AbsVal"],  # explicit_receiver
    ],
    Optional["AbsVal"],
]

CollectParamAddrsFunc = Callable[
    [
        List["AbsVal"],  # arg_vals
        Dict[str, "AbsVal"],  # kw_vals
    ],
    Tuple["AbstractObject", ...],
]


@dataclass
class CallDispatchResult:
    """Result of call dispatch.

    Attributes:
        result_val: Joined return value from all resolved callees
        resolved_any: Whether any callee was resolved
        callee_addrs: Set of resolved callee objects
        unresolved_addrs: Set of unresolved callee objects
        heap_updates: AI heap updates (for generator object allocation)
    """

    result_val: "AbsVal"
    resolved_any: bool
    callee_addrs: FrozenSet["AbstractObject"]
    unresolved_addrs: FrozenSet["AbstractObject"]
    heap_updates: Tuple = field(default_factory=tuple)


class CallSemantics:
    """Encapsulates Python call dispatch semantics.

    This class owns the logic for:
    - Iterating callable targets from an abstract value
    - Class-call fallback (invoking __call__ on class objects)
    - Recording unknown call effects
    - Coordinating callee invocation via injected callbacks

    The interpreter injects its methods so we can dispatch calls
    without circular imports.
    """

    def __init__(
        self,
        pta_query: Optional["PtaQuery"],
        caller_scope: Optional["Scope"],
        call_context: Optional["AbstractContext"],
        invoke_callee: InvokeCalleeFunc,
        get_callee_scope_ir: GetCalleeScopeIRFunc,
        select_call_context: SelectCallContextFunc,
        build_arg_vals_for_scope: BuildArgValsFunc,
        get_receiver_for_method: GetReceiverForMethodFunc,
        collect_param_addrs: CollectParamAddrsFunc,
    ):
        """Initialize call semantics.

        Args:
            pta_query: PTA query interface for method lookups
            caller_scope: Scope that initiated the call
            call_context: Context at the call site
            invoke_callee: Callback to invoke callees (avoids interpreter import)
            get_callee_scope_ir: Callback to get IR scope for a callee
            select_call_context: Callback to select call context
            build_arg_vals_for_scope: Callback to build arg bindings
            get_receiver_for_method: Callback to get receiver for methods
            collect_param_addrs: Callback to collect param addresses for context
        """
        self.pta_query = pta_query
        self.caller_scope = caller_scope
        self.call_context = call_context
        self.invoke_callee = invoke_callee
        self.get_callee_scope_ir = get_callee_scope_ir
        self.select_call_context = select_call_context
        self.build_arg_vals_for_scope = build_arg_vals_for_scope
        self.get_receiver_for_method = get_receiver_for_method
        self.collect_param_addrs = collect_param_addrs

    def dispatch_call(
        self,
        callee_val: "AbsVal",
        pos_arg_vals: List["AbsVal"],
        kw_arg_vals: Dict[str, "AbsVal"],
        callsite_id: str,
        state: "AIState",
        summary: "SummaryAccumulator",
    ) -> CallDispatchResult:
        """Dispatch a call to callable targets.

        Implements the generic call path:
        1. Try builtin method handling first (for dict.get, dict.update, etc.)
        2. Extract callable targets from callee_val
        3. For each target, try to get scope IR
        4. If generator function: allocate generator object without executing body
        5. If scope IR available and not generator: invoke callee with proper arg binding
        6. If scope IR unavailable, try class-call fallback
        7. Track resolved/unresolved targets

        Args:
            callee_val: Abstract value representing the callee
            pos_arg_vals: List of positional argument values
            kw_arg_vals: Dict of keyword argument values
            callsite_id: Unique identifier for this call site
            state: Current abstract state
            summary: Summary accumulator

        Returns:
            CallDispatchResult with result value, resolution status, and heap_updates
        """
        from .binding import to_callable_targets
        from ..value_domain import AbsVal

        result_val = AbsVal.bottom()
        resolved_any = False
        unresolved_addrs: Set["AbstractObject"] = set()
        callee_addrs: Set["AbstractObject"] = set()
        heap_updates: List[Tuple] = []

        # Try to handle builtin methods first (dict + generators)
        builtin_result, builtin_handled, builtin_updates, handled_addrs = self._handle_builtin_methods(
            callee_val, pos_arg_vals, kw_arg_vals, state, summary
        )
        if builtin_handled:
            result_val = result_val.join(builtin_result)
            resolved_any = True
            heap_updates.extend(builtin_updates)

        for target in to_callable_targets(callee_val):
            callee_obj = target.callee_obj
            explicit_receiver = target.receiver
            if callee_obj in handled_addrs:
                continue
            scope_ir = self.get_callee_scope_ir(callee_obj)

            if scope_ir is None:
                # Try class-call fallback
                class_result, class_callees = self._invoke_class_call(
                    callee_obj,
                    pos_arg_vals,
                    kw_arg_vals,
                    summary,
                    callsite_id,
                    state,
                )
                if class_result is not None:
                    resolved_any = True
                    result_val = result_val.join(class_result)
                    callee_addrs.update(class_callees)
                else:
                    unresolved_addrs.add(callee_obj)
                continue

            # Check if this is a generator function
            if self._is_generator_function(scope_ir):
                # Generator function: allocate generator object, don't execute body
                callee_addrs.add(callee_obj)
                resolved_any = True

                receiver_val = self.get_receiver_for_method(
                    callee_obj, scope_ir, explicit_receiver
                )
                arg_vals = self.build_arg_vals_for_scope(
                    scope_ir, pos_arg_vals, kw_arg_vals, receiver_val
                )
                self_name = self._get_self_param_name(scope_ir)
                if receiver_val is not None and self_name is not None:
                    arg_vals = [(self_name, receiver_val)] + arg_vals

                try:
                    from .iterators import GeneratorSemantics
                    gen_sem = GeneratorSemantics(
                        pta_query=self.pta_query,
                        budget=None,
                        caller_scope=self.caller_scope,
                        call_context=self.call_context,
                    )

                    func_val = AbsVal.from_addrs(frozenset({callee_obj}))
                    new_state, gen_val, ai_addr, ai_obj = gen_sem.alloc_generator_obj(
                        callsite_id=callsite_id,
                        call_context=self.call_context,
                        func_val=func_val,
                        arg_bindings=arg_vals,
                        state=state,
                    )

                    heap_updates.append((ai_addr, ai_obj))
                    result_val = result_val.join(gen_val)
                except Exception:
                    result_val = result_val.join(AbsVal.top())

                continue

            callee_addrs.add(callee_obj)
            resolved_any = True

            receiver_val = self.get_receiver_for_method(
                callee_obj, scope_ir, explicit_receiver
            )
            receiver_obj = None
            if receiver_val is not None and receiver_val.addrs:
                receiver_obj = next(iter(receiver_val.addrs))

            arg_vals = self.build_arg_vals_for_scope(
                scope_ir, pos_arg_vals, kw_arg_vals, receiver_val
            )
            params = self.collect_param_addrs(
                [v for _, v in arg_vals], kw_arg_vals
            )

            callee_ctx = self.select_call_context(
                callsite_id,
                len(pos_arg_vals),
                receiver_obj,
                params,
            ) or self.call_context

            ret_val = self.invoke_callee(
                callee_obj,
                arg_vals,
                summary,
                receiver_val,
                callsite_id,
                callee_ctx,
                len(pos_arg_vals),
            )
            result_val = result_val.join(ret_val)

        return CallDispatchResult(
            result_val=result_val,
            resolved_any=resolved_any,
            callee_addrs=frozenset(callee_addrs),
            unresolved_addrs=frozenset(unresolved_addrs),
            heap_updates=tuple(heap_updates),
        )

    def _is_generator_function(self, scope_ir: "IRFunc") -> bool:
        """Check if a scope IR represents a generator function."""
        from .iterators import is_generator_function
        return is_generator_function(scope_ir)

    def _invoke_class_call(
        self,
        cls_obj: "AbstractObject",
        pos_arg_vals: List["AbsVal"],
        kw_arg_vals: Dict[str, "AbsVal"],
        summary: "SummaryAccumulator",
        callsite_id: str,
        state: "AIState",
    ) -> Tuple[Optional["AbsVal"], Set["AbstractObject"]]:
        """Attempt to invoke __call__ on a class object.

        Args:
            cls_obj: The class object to invoke
            pos_arg_vals: Positional argument values
            kw_arg_vals: Keyword argument values
            summary: Summary accumulator
            callsite_id: Call site identifier
            state: Current abstract state

        Returns:
            Tuple of (result_val, callee_addrs) or (None, set()) if not a class
        """
        from ..value_domain import AbsVal

        if not self._is_class_object(cls_obj):
            return None, set()

        call_addrs = self._lookup_method(cls_obj, "__call__", state)
        if not call_addrs:
            return None, set()

        receiver_val = AbsVal.from_addrs(frozenset({cls_obj}))
        result_val = AbsVal.bottom()
        callee_addrs: Set["AbstractObject"] = set()

        for callee_obj in call_addrs:
            scope_ir = self.get_callee_scope_ir(callee_obj)
            if scope_ir is None:
                continue

            callee_addrs.add(callee_obj)
            arg_vals = self.build_arg_vals_for_scope(
                scope_ir, pos_arg_vals, kw_arg_vals, receiver_val
            )
            params = self.collect_param_addrs(
                [v for _, v in arg_vals], kw_arg_vals
            )

            callee_ctx = self.select_call_context(
                callsite_id,
                len(pos_arg_vals),
                cls_obj,
                params,
            ) or self.call_context

            ret_val = self.invoke_callee(
                callee_obj,
                arg_vals,
                summary,
                receiver_val,
                callsite_id,
                callee_ctx,
                len(pos_arg_vals),
            )
            result_val = result_val.join(ret_val)

        if not callee_addrs:
            return None, set()
        return result_val, callee_addrs

    def _lookup_method(
        self,
        obj: "AbstractObject",
        method_name: str,
        state: Optional["AIState"] = None,
    ) -> FrozenSet["AbstractObject"]:
        """Look up a method on an object via PTA/state.

        Args:
            obj: Object to look up on
            method_name: Method name (e.g., "__call__")
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

    def _is_class_object(self, obj: "AbstractObject") -> bool:
        """Check if an object represents a class."""
        try:
            if hasattr(obj, "kind"):
                kind = getattr(obj, "kind")
                if hasattr(kind, "value") and kind.value == "class":
                    return True
                kind_str = str(kind).lower()
                if kind_str == "class" or "class" in kind_str:
                    return True
            class_name = type(obj).__name__.lower()
            return "class" in class_name
        except Exception:
            return False

    def _handle_builtin_dict_methods(
        self,
        callee_val: "AbsVal",
        pos_arg_vals: List["AbsVal"],
        kw_arg_vals: Dict[str, "AbsVal"],
        state: "AIState",
        summary: "SummaryAccumulator",
    ) -> Tuple["AbsVal", bool, FrozenSet["AbstractObject"]]:
        """Handle builtin dict method calls (get, setdefault, update, keys, values).
        
        Checks if callee_val contains BuiltinMethodObject for dict methods and
        dispatches to DictSemantics.
        
        Args:
            callee_val: Abstract value representing the callee
            pos_arg_vals: Positional argument values
            kw_arg_vals: Keyword argument values (for default=)
            state: Current abstract state
            summary: Summary accumulator
            
        Returns:
            Tuple of (result_val, was_handled)
        """
        from pythonstan.analysis.pointer.kcfa.object import BuiltinMethodObject, AllocKind
        from ..value_domain import AbsVal
        
        try:
            from .mapslots import DictSemantics
        except ImportError:
            return AbsVal.bottom(), False
        
        result_val = AbsVal.bottom()
        handled_any = False
        handled_addrs: Set["AbstractObject"] = set()
        
        for obj in callee_val.addrs:
            if not isinstance(obj, BuiltinMethodObject):
                continue
            
            method_name = obj.method_name
            receiver = obj.receiver
            
            # Check if receiver is a dict
            receiver_kind = getattr(receiver, "kind", None)
            is_dict = (
                receiver_kind == AllocKind.DICT or
                (hasattr(receiver_kind, "value") and receiver_kind.value == "dict")
            )
            
            if not is_dict:
                continue
            
            # Get receiver as AbsVal
            receiver_val = AbsVal.from_addrs(frozenset({receiver}))
            
            # Create DictSemantics
            dict_sem = DictSemantics(
                caller_scope=self.caller_scope,
                call_context=self.call_context,
                budget=None,  # Will use defaults
            )
            
            # Handle specific methods
            if method_name == "get":
                # dict.get(key, default=None)
                if len(pos_arg_vals) >= 1:
                    key_val = pos_arg_vals[0]
                    default_val = pos_arg_vals[1] if len(pos_arg_vals) > 1 else kw_arg_vals.get("default", AbsVal.none_val())
                    result = dict_sem.dict_get(receiver_val, key_val, default_val, state, summary)
                    result_val = result_val.join(result)
                    handled_any = True
                    handled_addrs.add(obj)
            
            elif method_name == "setdefault":
                # dict.setdefault(key, default=None)
                if len(pos_arg_vals) >= 1:
                    key_val = pos_arg_vals[0]
                    default_val = pos_arg_vals[1] if len(pos_arg_vals) > 1 else kw_arg_vals.get("default", AbsVal.none_val())
                    # NOTE: This mutates state, but we can't return the new state from here
                    # For soundness, we still update via store_item
                    _, result = dict_sem.dict_setdefault(receiver_val, key_val, default_val, state, summary)
                    result_val = result_val.join(result)
                    handled_any = True
                    handled_addrs.add(obj)
            
            elif method_name == "update":
                # dict.update(other)
                if len(pos_arg_vals) >= 1:
                    other_val = pos_arg_vals[0]
                    # Update is a mutating operation
                    dict_sem.dict_update(receiver_val, other_val, state, summary)
                    result_val = result_val.join(AbsVal.none_val())  # update returns None
                    handled_any = True
                    handled_addrs.add(obj)
            
            elif method_name == "keys":
                # dict.keys()
                keys_val = dict_sem.dict_keys(receiver_val, state)
                result_val = result_val.join(keys_val)
                handled_any = True
                handled_addrs.add(obj)
            
            elif method_name == "values":
                # dict.values()
                values_val = dict_sem.dict_values(receiver_val, state)
                result_val = result_val.join(values_val)
                handled_any = True
                handled_addrs.add(obj)
            
            elif method_name == "items":
                # dict.items() - returns tuples of (key, value)
                keys_val = dict_sem.dict_keys(receiver_val, state)
                values_val = dict_sem.dict_values(receiver_val, state)
                # Create a tuple-like result (simplified)
                result_val = result_val.join(keys_val).join(values_val)
                handled_any = True
                handled_addrs.add(obj)
            
            elif method_name == "pop":
                # dict.pop(key, default=None)
                if len(pos_arg_vals) >= 1:
                    key_val = pos_arg_vals[0]
                    default_val = pos_arg_vals[1] if len(pos_arg_vals) > 1 else kw_arg_vals.get("default", AbsVal.top())
                    # Similar to get but also removes the key
                    result = dict_sem.dict_get(receiver_val, key_val, default_val, state, summary)
                    result_val = result_val.join(result)
                    handled_any = True
                    handled_addrs.add(obj)
            
            elif method_name in ("clear", "popitem"):
                # These are mutating operations that we handle conservatively
                result_val = result_val.join(AbsVal.none_val())
                handled_any = True
                handled_addrs.add(obj)
        
        return result_val, handled_any, frozenset(handled_addrs)

    def _handle_builtin_methods(
        self,
        callee_val: "AbsVal",
        pos_arg_vals: List["AbsVal"],
        kw_arg_vals: Dict[str, "AbsVal"],
        state: "AIState",
        summary: "SummaryAccumulator",
    ) -> Tuple["AbsVal", bool, List[Tuple], FrozenSet["AbstractObject"]]:
        """Handle builtin method calls (dict + generator methods)."""
        from ..value_domain import AbsVal

        result_val = AbsVal.bottom()
        handled_any = False
        heap_updates: List[Tuple] = []
        handled_addrs: Set["AbstractObject"] = set()

        dict_result, dict_handled, dict_addrs = self._handle_builtin_dict_methods(
            callee_val, pos_arg_vals, kw_arg_vals, state, summary
        )
        if dict_handled:
            result_val = result_val.join(dict_result)
            handled_any = True
            handled_addrs.update(dict_addrs)

        gen_result, gen_handled, gen_updates, gen_addrs = self._handle_builtin_generator_methods(
            callee_val, pos_arg_vals, kw_arg_vals, state, summary
        )
        if gen_handled:
            result_val = result_val.join(gen_result)
            handled_any = True
            heap_updates.extend(gen_updates)
            handled_addrs.update(gen_addrs)

        return result_val, handled_any, heap_updates, frozenset(handled_addrs)

    def _handle_builtin_generator_methods(
        self,
        callee_val: "AbsVal",
        pos_arg_vals: List["AbsVal"],
        kw_arg_vals: Dict[str, "AbsVal"],
        state: "AIState",
        summary: "SummaryAccumulator",
    ) -> Tuple["AbsVal", bool, List[Tuple], FrozenSet["AbstractObject"]]:
        """Handle generator method calls: send/throw/close."""
        from pythonstan.analysis.pointer.kcfa.object import BuiltinMethodObject
        from ..value_domain import AbsVal
        from .iterators import GeneratorSemantics

        result_val = AbsVal.bottom()
        handled_any = False
        heap_updates: List[Tuple] = []
        handled_addrs: Set["AbstractObject"] = set()

        for obj in callee_val.addrs:
            if not isinstance(obj, BuiltinMethodObject):
                continue

            method_name = obj.method_name
            if method_name not in ("send", "throw", "close"):
                continue

            receiver = obj.receiver
            if not self._is_generator_receiver(receiver, state):
                continue

            gen_val = AbsVal.from_addrs(frozenset({receiver}))
            gen_sem = GeneratorSemantics(
                pta_query=self.pta_query,
                budget=state.budget,
                caller_scope=self.caller_scope,
                call_context=self.call_context,
            )

            if method_name == "send":
                send_val = pos_arg_vals[0] if pos_arg_vals else AbsVal.none_val()
                _state, method_result, updates = gen_sem.eval_send(
                    gen_val, send_val, state, summary
                )
            elif method_name == "throw":
                _state, method_result, updates = gen_sem.eval_throw(
                    gen_val, state, summary
                )
            else:
                _state, method_result, updates = gen_sem.eval_close(
                    gen_val, state, summary
                )

            result_val = result_val.join(method_result)
            handled_any = True
            heap_updates.extend(updates)
            handled_addrs.add(obj)

        return result_val, handled_any, heap_updates, frozenset(handled_addrs)

    def _is_generator_receiver(
        self,
        receiver: "AbstractObject",
        state: "AIState",
    ) -> bool:
        from ..heap import AIAddr, ObjKind
        from .iterators import GEN_FUNC_KEY

        ai_addr = AIAddr.from_kcfa_obj(receiver)
        ai_obj = state.get_obj(ai_addr)
        if ai_obj is None:
            return False
        if ai_obj.kind != ObjKind.ITERATOR:
            return False
        slot = ai_obj.slots.get_str(GEN_FUNC_KEY)
        return slot.pres.may_be_present() and not slot.val.is_bottom()

    def _get_self_param_name(self, scope_ir: "IRFunc") -> Optional[str]:
        """Get the 'self' or 'cls' parameter name from a function scope."""
        if hasattr(scope_ir, "args") and scope_ir.args is not None:
            args = scope_ir.args
            if hasattr(args, "args") and len(args.args) > 0:
                return args.args[0].arg
        return None
