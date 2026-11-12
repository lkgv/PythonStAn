"""Context selection strategy for different sensitivity policies.

This module implements context selection for various context-sensitive policies.
"""

from __future__ import annotations

from enum import Enum
from typing import override

from .context import (
    AbstractContext,
    CallContextSite,
    CallStringContext,
    ContextSeed,
    HybridContext,
    NativeBaseContext,
    NativeContext,
    ObjectContext,
    ParamContext,
    ParameterContextInput,
    ReceiverContext,
    SummaryContext,
    TypeContext,
    is_native_context,
    make_native_summary,
    native_call_history,
)
from .object import AbstractObject, InstanceObject
from .protocol_context import (
    apply_native_protocol_partition,
    split_native_protocol_context,
    split_protocol_context,
)

__all__ = ["ContextPolicy", "ContextSelector", "parse_policy"]


class ContextPolicy(Enum):
    """Available context sensitivity policies."""

    INSENSITIVE = "0-cfa"
    CALL_1 = "1-cfa"
    CALL_2 = "2-cfa"
    CALL_3 = "3-cfa"
    OBJ_1 = "1-obj"
    OBJ_2 = "2-obj"
    OBJ_3 = "3-obj"
    TYPE_1 = "1-type"
    TYPE_2 = "2-type"
    TYPE_3 = "3-type"
    RECEIVER_1 = "1-rcv"
    RECEIVER_2 = "2-rcv"
    RECEIVER_3 = "3-rcv"
    PARAM_1 = "1-param"
    PARAM_2 = "2-param"
    PARAM_3 = "3-param"
    HYBRID_CALL1_OBJ1 = "1c1o"
    HYBRID_CALL2_OBJ1 = "2c1o"
    HYBRID_CALL1_OBJ2 = "1c2o"


class ContextSelector:
    """Selects contexts based on policy."""

    def __init__(
        self,
        policy: ContextPolicy = ContextPolicy.CALL_2,
        heap_context_policy: ContextPolicy | str | None = None,
        heap_context_depth: int | None = None,
    ) -> None:
        """Initialize context selector.

        Args:
            policy: Context sensitivity policy
        """
        self.policy = policy
        self._empty_context = self._create_empty_context()
        if heap_context_depth is not None and (
            type(heap_context_depth) is not int or not 0 <= heap_context_depth <= 3
        ):
            raise ValueError("heap_context_depth must be in 0..3 or None")
        self.heap_context_depth = heap_context_depth
        heap_policy = (
            parse_policy(heap_context_policy)
            if isinstance(heap_context_policy, str)
            else heap_context_policy
        )
        if heap_policy is not None and not isinstance(heap_policy, ContextPolicy):
            raise ValueError("heap_context_policy must be a registered context policy")
        self._heap_selector = ContextSelector(heap_policy) if heap_policy is not None else None

    def _create_empty_context(self) -> NativeBaseContext:
        """Create empty context for policy."""
        if self.policy == ContextPolicy.INSENSITIVE:
            return CallStringContext((), 0)
        if self.policy == ContextPolicy.CALL_1:
            return CallStringContext((), 1)
        if self.policy == ContextPolicy.CALL_2:
            return CallStringContext((), 2)
        if self.policy == ContextPolicy.CALL_3:
            return CallStringContext((), 3)
        if self.policy == ContextPolicy.OBJ_1:
            return ObjectContext((), 1)
        if self.policy == ContextPolicy.OBJ_2:
            return ObjectContext((), 2)
        if self.policy == ContextPolicy.OBJ_3:
            return ObjectContext((), 3)
        if self.policy == ContextPolicy.TYPE_1:
            return TypeContext((), 1)
        if self.policy == ContextPolicy.TYPE_2:
            return TypeContext((), 2)
        if self.policy == ContextPolicy.TYPE_3:
            return TypeContext((), 3)
        if self.policy == ContextPolicy.RECEIVER_1:
            return ReceiverContext((), 1)
        if self.policy == ContextPolicy.RECEIVER_2:
            return ReceiverContext((), 2)
        if self.policy == ContextPolicy.RECEIVER_3:
            return ReceiverContext((), 3)
        if self.policy == ContextPolicy.PARAM_1:
            return ParamContext((), 1)
        if self.policy == ContextPolicy.PARAM_2:
            return ParamContext((), 2)
        if self.policy == ContextPolicy.PARAM_3:
            return ParamContext((), 3)
        if self.policy == ContextPolicy.HYBRID_CALL1_OBJ1:
            return HybridContext((), (), 1, 1)
        if self.policy == ContextPolicy.HYBRID_CALL2_OBJ1:
            return HybridContext((), (), 2, 1)
        if self.policy == ContextPolicy.HYBRID_CALL1_OBJ2:
            return HybridContext((), (), 1, 2)
        raise ValueError(f"Unknown policy: {self.policy}")

    def empty_context(self) -> NativeContext:
        """Get empty context."""
        return self._empty_context

    def select_call_context[InputT](
        self,
        call_site: CallContextSite,
        caller_ctx: AbstractContext[InputT] | None,
        callee_obj: AbstractObject | None = None,
        params: ParameterContextInput | None = None,
    ) -> NativeContext:
        """Select context for function call.

        Args:
            caller_ctx: Current calling context
            call_site: Call site being invoked
            callee: Name of the called function
            receiver_alloc: Allocation site of receiver (for method calls)
            receiver_type: Type of receiver (for method calls)

        Returns:
            New context for the called function
        """
        base, partition = split_protocol_context(caller_ctx)
        if partition is not None:
            summarized = isinstance(base, SummaryContext)
            while isinstance(base, SummaryContext):
                base = base.inner
            selected = self.select_call_context(call_site, base, callee_obj, params)
            if summarized:
                selected = make_native_summary(selected)
            return apply_native_protocol_partition(selected, partition)
        if self.policy == ContextPolicy.INSENSITIVE:
            return self.empty_context()

        if self.policy in (ContextPolicy.CALL_1, ContextPolicy.CALL_2, ContextPolicy.CALL_3):
            if isinstance(caller_ctx, CallStringContext):
                return caller_ctx.append(call_site)
            empty = self._empty_context
            if not isinstance(empty, CallStringContext):
                raise ValueError("Call policy requires its matching empty context")
            return empty.append(call_site)

        if self.policy in (ContextPolicy.OBJ_1, ContextPolicy.OBJ_2, ContextPolicy.OBJ_3):
            object_context = (
                caller_ctx
                if isinstance(caller_ctx, ObjectContext)
                else ObjectContext((), self._get_depth())
            )
            if callee_obj:
                return object_context.append(callee_obj)
            return object_context.append(call_site)

        if self.policy in (ContextPolicy.TYPE_1, ContextPolicy.TYPE_2, ContextPolicy.TYPE_3):
            type_context = (
                caller_ctx
                if isinstance(caller_ctx, TypeContext)
                else TypeContext((), self._get_depth())
            )
            if callee_obj:
                if isinstance(callee_obj, InstanceObject):
                    return type_context.append(callee_obj.class_obj)
                return type_context.append(callee_obj)
            return type_context.append(call_site)

        if self.policy in (
            ContextPolicy.RECEIVER_1,
            ContextPolicy.RECEIVER_2,
            ContextPolicy.RECEIVER_3,
        ):
            receiver_context = (
                caller_ctx
                if isinstance(caller_ctx, ReceiverContext)
                else ReceiverContext((), self._get_depth())
            )
            if callee_obj:
                return receiver_context.append(callee_obj.alloc_site)
            return receiver_context.append(call_site)

        if self.policy in (ContextPolicy.PARAM_1, ContextPolicy.PARAM_2, ContextPolicy.PARAM_3):
            parameter_context = (
                caller_ctx
                if isinstance(caller_ctx, ParamContext)
                else ParamContext((), self._get_depth())
            )
            if params is not None:
                return parameter_context.append(params)
            # return parameter_context.append(call_site)
            return parameter_context

        if self.policy in (
            ContextPolicy.HYBRID_CALL1_OBJ1,
            ContextPolicy.HYBRID_CALL2_OBJ1,
            ContextPolicy.HYBRID_CALL1_OBJ2,
        ):
            hybrid_context = (
                caller_ctx
                if isinstance(caller_ctx, HybridContext)
                else HybridContext((), (), self._get_call_k(), self._get_obj_depth())
            )
            if callee_obj:
                return hybrid_context.append(call_site, callee_obj)
            return hybrid_context.append(call_site, None)
        raise ValueError(f"Unknown policy: {self.policy}")

    def select_alloc_context[InputT](
        self,
        current_ctx: AbstractContext[InputT] | None,
        alloc_site: ContextSeed,
        alloc_type: ContextSeed | None = None,
    ) -> NativeContext:
        """Select heap identity independently from method context.

        A different family projects only history represented by the method
        context; it does not invent forgotten calls. Depth is an independent
        upper bound on the available flat history. Protocol/summary wrappers
        remain explicit and flat.
        """
        base, partition = split_protocol_context(current_ctx)
        if partition is not None:
            return apply_native_protocol_partition(
                self.select_alloc_context(base, alloc_site, alloc_type), partition
            )
        if isinstance(base, SummaryContext):
            inner = base.inner
            return make_native_summary(self.select_alloc_context(inner, alloc_site, alloc_type))
        selector = self._heap_selector or self
        empty = selector.empty_context()
        projected: NativeContext
        if isinstance(base, type(empty)) and is_native_context(base):
            projected = base
        else:
            # Switching to call strings retains only actual call sites present
            # in the finite method context. Other unavailable history is forgotten.
            if isinstance(empty, CallStringContext):
                calls = native_call_history(base)
                projected = CallStringContext(calls[-empty.k :] if empty.k else (), empty.k)
            else:
                projected = empty
        selected = selector._select_base_alloc_context(projected, alloc_site, alloc_type)
        depth = self.heap_context_depth
        if depth is None:
            return selected
        if isinstance(selected, CallStringContext):
            return CallStringContext(selected.call_sites[-depth:] if depth else (), depth)
        if isinstance(selected, HybridContext):
            return HybridContext(
                selected.call_sites[-depth:] if depth else (),
                selected.alloc_sites[-depth:] if depth else (),
                depth,
                depth,
            )
        if isinstance(selected, ObjectContext):
            return ObjectContext(selected.alloc_sites[-depth:] if depth else (), depth)
        if isinstance(selected, TypeContext):
            return TypeContext(selected.types[-depth:] if depth else (), depth)
        if isinstance(selected, ReceiverContext):
            return ReceiverContext(selected.receivers[-depth:] if depth else (), depth)
        if isinstance(selected, ParamContext):
            return ParamContext(selected.params[-depth:] if depth else (), depth)
        raise ValueError("Unsupported heap context representation")

    def _select_base_alloc_context(
        self,
        current_ctx: NativeContext,
        alloc_site: ContextSeed,
        alloc_type: ContextSeed | None = None,
    ) -> NativeContext:
        """Select context for object allocation.

        Args:
            current_ctx: Current context
            alloc_site: Allocation site identifier
            alloc_type: Type being allocated (optional)

        Returns:
            Context for the allocated object
        """
        base, partition = split_native_protocol_context(current_ctx)
        if partition is not None:
            summarized = isinstance(base, SummaryContext)
            while isinstance(base, SummaryContext):
                inner = base.inner
                if not is_native_context(inner):
                    raise ValueError("Underlying context is outside the Native profile")
                base = inner
            selected = self._select_base_alloc_context(base, alloc_site, alloc_type)
            if summarized:
                selected = make_native_summary(selected)
            return apply_native_protocol_partition(selected, partition)
        if self.policy == ContextPolicy.INSENSITIVE:
            return self.empty_context()

        if self.policy in (ContextPolicy.OBJ_1, ContextPolicy.OBJ_2, ContextPolicy.OBJ_3):
            if isinstance(current_ctx, ObjectContext):
                return current_ctx.append(alloc_site)
            object_context = ObjectContext((), self._get_depth())
            return object_context.append(alloc_site)

        if self.policy in (ContextPolicy.TYPE_1, ContextPolicy.TYPE_2, ContextPolicy.TYPE_3):
            # For TYPE-sensitive policies, use alloc_type if provided, otherwise alloc_site
            type_elem = alloc_type if alloc_type is not None else alloc_site
            if isinstance(current_ctx, TypeContext):
                return current_ctx.append(type_elem)
            type_context = TypeContext((), self._get_depth())
            return type_context.append(type_elem)

        if self.policy in (
            ContextPolicy.HYBRID_CALL1_OBJ1,
            ContextPolicy.HYBRID_CALL2_OBJ1,
            ContextPolicy.HYBRID_CALL1_OBJ2,
        ):
            if isinstance(current_ctx, HybridContext):
                return current_ctx.append_object(alloc_site)
            hybrid_context = HybridContext((), (), self._get_call_k(), self._get_obj_depth())
            return hybrid_context.append_object(alloc_site)

        return current_ctx

    def _get_depth(self) -> int:
        """Get depth parameter for current policy."""
        if self.policy in (
            ContextPolicy.OBJ_1,
            ContextPolicy.TYPE_1,
            ContextPolicy.PARAM_1,
            ContextPolicy.HYBRID_CALL1_OBJ1,
            ContextPolicy.RECEIVER_1,
        ):
            return 1
        if self.policy in (
            ContextPolicy.OBJ_2,
            ContextPolicy.TYPE_2,
            ContextPolicy.PARAM_2,
            ContextPolicy.RECEIVER_2,
        ):
            return 2
        if self.policy in (
            ContextPolicy.OBJ_3,
            ContextPolicy.TYPE_3,
            ContextPolicy.PARAM_3,
            ContextPolicy.RECEIVER_3,
        ):
            return 3
        if self.policy in (ContextPolicy.HYBRID_CALL1_OBJ2, ContextPolicy.HYBRID_CALL2_OBJ1):
            return 2
        return 1

    def _get_call_k(self) -> int:
        """Get call-string depth for hybrid policies."""
        if self.policy in (ContextPolicy.HYBRID_CALL2_OBJ1,):
            return 2
        return 1

    def _get_obj_depth(self) -> int:
        """Get object depth for hybrid policies."""
        if self.policy == ContextPolicy.HYBRID_CALL1_OBJ2:
            return 2
        return 1

    @override
    def __repr__(self) -> str:
        return f"ContextSelector(policy={self.policy.value})"


def parse_policy(policy_str: str) -> ContextPolicy:
    """Parse policy string to enum.

    Args:
        policy_str: Policy string (e.g., "2-cfa", "1-obj")

    Returns:
        ContextPolicy enum value

    Raises:
        ValueError: If policy string is not recognized
    """
    policy_map = {
        "0-cfa": ContextPolicy.INSENSITIVE,
        "1-cfa": ContextPolicy.CALL_1,
        "2-cfa": ContextPolicy.CALL_2,
        "3-cfa": ContextPolicy.CALL_3,
        "1-obj": ContextPolicy.OBJ_1,
        "2-obj": ContextPolicy.OBJ_2,
        "3-obj": ContextPolicy.OBJ_3,
        "1-type": ContextPolicy.TYPE_1,
        "2-type": ContextPolicy.TYPE_2,
        "3-type": ContextPolicy.TYPE_3,
        "1-rcv": ContextPolicy.RECEIVER_1,
        "2-rcv": ContextPolicy.RECEIVER_2,
        "3-rcv": ContextPolicy.RECEIVER_3,
        "1-param": ContextPolicy.PARAM_1,
        "2-param": ContextPolicy.PARAM_2,
        "3-param": ContextPolicy.PARAM_3,
        "1c1o": ContextPolicy.HYBRID_CALL1_OBJ1,
        "2c1o": ContextPolicy.HYBRID_CALL2_OBJ1,
        "1c2o": ContextPolicy.HYBRID_CALL1_OBJ2,
    }

    if policy_str not in policy_map:
        raise ValueError(
            f"Unknown policy: {policy_str}. Available policies: {', '.join(policy_map.keys())}"
        )

    return policy_map[policy_str]
