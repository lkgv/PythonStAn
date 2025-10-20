"""Context selector for different context sensitivity policies.

This module implements the strategy pattern for context selection, enabling
different context-sensitive policies to be used in the pointer analysis.
"""

from enum import Enum
from typing import Optional
from .context import (
    AbstractContext,
    CallStringContext,
    ObjectContext,
    TypeContext,
    ReceiverContext,
    HybridContext,
    CallSite,
)

__all__ = ["ContextPolicy", "ContextSelector", "parse_policy"]


class ContextPolicy(Enum):
    """Available context sensitivity policies.
    
    Each policy represents a different strategy for distinguishing execution
    contexts in the pointer analysis.
    """
    # Context-insensitive (baseline)
    INSENSITIVE = "0-cfa"
    
    # Call-string sensitivity (k-CFA)
    CALL_1 = "1-cfa"
    CALL_2 = "2-cfa"
    CALL_3 = "3-cfa"
    
    # Object sensitivity (allocation-site based)
    OBJ_1 = "1-obj"
    OBJ_2 = "2-obj"
    OBJ_3 = "3-obj"
    
    # Type sensitivity (class-based)
    TYPE_1 = "1-type"
    TYPE_2 = "2-type"
    TYPE_3 = "3-type"
    
    # Receiver-object sensitivity (Python-specific)
    RECEIVER_1 = "1-rcv"
    RECEIVER_2 = "2-rcv"
    RECEIVER_3 = "3-rcv"
    
    # Hybrid policies (advanced)
    HYBRID_CALL1_OBJ1 = "1c1o"
    HYBRID_CALL2_OBJ1 = "2c1o"
    HYBRID_CALL1_OBJ2 = "1c2o"


class ContextSelector:
    """Selects context based on policy.
    
    The context selector implements the strategy for choosing contexts
    when entering function calls and allocating objects.
    
    Attributes:
        policy: The context sensitivity policy to use
    """
    
    def __init__(self, policy: Optional[ContextPolicy] = None, k: int = 2):
        """Initialize context selector.
        
        Args:
            policy: Context sensitivity policy (None for default based on k)
            k: Call-string depth (for backward compatibility, used if policy is None)
        """
        if policy is None:
            # Backward compatibility: map k to policy
            if k == 0:
                policy = ContextPolicy.INSENSITIVE
            elif k == 1:
                policy = ContextPolicy.CALL_1
            elif k == 2:
                policy = ContextPolicy.CALL_2
            elif k == 3:
                policy = ContextPolicy.CALL_3
            else:
                policy = ContextPolicy.CALL_2  # Default
        
        self.policy = policy
        self.k = k  # Store for backward compatibility
        self._empty_context = self._create_empty_context()
    
    def _create_empty_context(self) -> AbstractContext:
        """Create empty context for the policy.
        
        Returns:
            Empty context appropriate for the selected policy
        """
        if self.policy == ContextPolicy.INSENSITIVE:
            return CallStringContext((), 0)  # k=0 means no context
        elif self.policy == ContextPolicy.CALL_1:
            return CallStringContext((), 1)
        elif self.policy == ContextPolicy.CALL_2:
            return CallStringContext((), 2)
        elif self.policy == ContextPolicy.CALL_3:
            return CallStringContext((), 3)
        elif self.policy == ContextPolicy.OBJ_1:
            return ObjectContext((), 1)
        elif self.policy == ContextPolicy.OBJ_2:
            return ObjectContext((), 2)
        elif self.policy == ContextPolicy.OBJ_3:
            return ObjectContext((), 3)
        elif self.policy == ContextPolicy.TYPE_1:
            return TypeContext((), 1)
        elif self.policy == ContextPolicy.TYPE_2:
            return TypeContext((), 2)
        elif self.policy == ContextPolicy.TYPE_3:
            return TypeContext((), 3)
        elif self.policy == ContextPolicy.RECEIVER_1:
            return ReceiverContext((), 1)
        elif self.policy == ContextPolicy.RECEIVER_2:
            return ReceiverContext((), 2)
        elif self.policy == ContextPolicy.RECEIVER_3:
            return ReceiverContext((), 3)
        elif self.policy == ContextPolicy.HYBRID_CALL1_OBJ1:
            return HybridContext((), (), 1, 1)
        elif self.policy == ContextPolicy.HYBRID_CALL2_OBJ1:
            return HybridContext((), (), 2, 1)
        elif self.policy == ContextPolicy.HYBRID_CALL1_OBJ2:
            return HybridContext((), (), 1, 2)
        else:
            raise ValueError(f"Unknown policy: {self.policy}")
    
    def empty_context(self) -> AbstractContext:
        """Get empty context for this policy.
        
        Returns:
            Empty context
        """
        return self._empty_context
    
    def select_call_context(
        self,
        caller_ctx: AbstractContext,
        call_site: CallSite,
        callee: str,
        receiver_alloc: Optional[str] = None,
        receiver_type: Optional[str] = None
    ) -> AbstractContext:
        """Select context for a function call.
        
        This is the main entry point for context selection when entering a
        function call. The strategy depends on the selected policy.
        
        Args:
            caller_ctx: Current calling context
            call_site: Call site being invoked
            callee: Name of the called function
            receiver_alloc: Allocation site of receiver object (for method calls)
            receiver_type: Type of receiver object (for method calls)
            
        Returns:
            New context for the called function
        """
        if self.policy == ContextPolicy.INSENSITIVE:
            # Context-insensitive: no change
            return caller_ctx
        
        elif self.policy in (ContextPolicy.CALL_1, ContextPolicy.CALL_2, ContextPolicy.CALL_3):
            # Call-string sensitivity: append call site
            if isinstance(caller_ctx, CallStringContext):
                return caller_ctx.append(call_site)
            else:
                # Should not happen, but handle gracefully
                return self._empty_context.append(call_site)
        
        elif self.policy in (ContextPolicy.OBJ_1, ContextPolicy.OBJ_2, ContextPolicy.OBJ_3):
            # Object sensitivity: use receiver allocation site if available
            if not isinstance(caller_ctx, ObjectContext):
                caller_ctx = ObjectContext((), self._get_depth())
            
            if receiver_alloc:
                return caller_ctx.append(receiver_alloc)
            else:
                # For non-method calls, use call site as proxy
                # This allows object-sensitive analysis to still distinguish contexts
                proxy_alloc = f"call:{call_site.site_id}"
                return caller_ctx.append(proxy_alloc)
        
        elif self.policy in (ContextPolicy.TYPE_1, ContextPolicy.TYPE_2, ContextPolicy.TYPE_3):
            # Type sensitivity: use receiver type if available
            if not isinstance(caller_ctx, TypeContext):
                caller_ctx = TypeContext((), self._get_depth())
            
            if receiver_type:
                return caller_ctx.append(receiver_type)
            else:
                # For non-method calls, use callee name as proxy
                # This provides some context sensitivity even for functions
                return caller_ctx.append(callee)
        
        elif self.policy in (ContextPolicy.RECEIVER_1, ContextPolicy.RECEIVER_2, ContextPolicy.RECEIVER_3):
            # Receiver sensitivity: only change context for method calls
            if not isinstance(caller_ctx, ReceiverContext):
                caller_ctx = ReceiverContext((), self._get_depth())
            
            if receiver_alloc:
                return caller_ctx.append(receiver_alloc)
            else:
                # Non-method calls stay in same context
                return caller_ctx
        
        elif self.policy in (ContextPolicy.HYBRID_CALL1_OBJ1, 
                            ContextPolicy.HYBRID_CALL2_OBJ1,
                            ContextPolicy.HYBRID_CALL1_OBJ2):
            # Hybrid: update both dimensions
            if not isinstance(caller_ctx, HybridContext):
                caller_ctx = HybridContext((), (), self._get_call_k(), self._get_obj_depth())
            
            # Always append call site
            ctx = caller_ctx.append_call(call_site)
            
            # Append receiver allocation if available
            if receiver_alloc:
                ctx = ctx.append_object(receiver_alloc)
            
            return ctx
        
        else:
            raise ValueError(f"Unknown policy: {self.policy}")
    
    def select_alloc_context(
        self,
        current_ctx: AbstractContext,
        alloc_site: str,
        alloc_type: Optional[str] = None
    ) -> AbstractContext:
        """Select context for object allocation.
        
        For most policies, allocations inherit current context.
        For object-sensitive policies, this is where context changes.
        
        Args:
            current_ctx: Current context
            alloc_site: Allocation site identifier
            alloc_type: Type being allocated (optional)
            
        Returns:
            Context for the allocated object
        """
        # For object-sensitive policies, append allocation site
        if self.policy in (ContextPolicy.OBJ_1, ContextPolicy.OBJ_2, ContextPolicy.OBJ_3):
            if isinstance(current_ctx, ObjectContext):
                return current_ctx.append(alloc_site)
            else:
                # Convert to object context
                ctx = ObjectContext((), self._get_depth())
                return ctx.append(alloc_site)
        
        # For hybrid policies, update object dimension
        elif self.policy in (ContextPolicy.HYBRID_CALL1_OBJ1,
                            ContextPolicy.HYBRID_CALL2_OBJ1,
                            ContextPolicy.HYBRID_CALL1_OBJ2):
            if isinstance(current_ctx, HybridContext):
                return current_ctx.append_object(alloc_site)
            else:
                # Convert to hybrid context
                ctx = HybridContext((), (), self._get_call_k(), self._get_obj_depth())
                return ctx.append_object(alloc_site)
        
        # For other policies, allocation doesn't change context
        else:
            return current_ctx
    
    def _get_depth(self) -> int:
        """Get depth parameter for current policy.
        
        Returns:
            Depth for object/type/receiver sensitivity
        """
        if self.policy in (ContextPolicy.OBJ_1, ContextPolicy.TYPE_1, ContextPolicy.RECEIVER_1):
            return 1
        elif self.policy in (ContextPolicy.OBJ_2, ContextPolicy.TYPE_2, ContextPolicy.RECEIVER_2):
            return 2
        elif self.policy in (ContextPolicy.OBJ_3, ContextPolicy.TYPE_3, ContextPolicy.RECEIVER_3):
            return 3
        elif self.policy == ContextPolicy.HYBRID_CALL1_OBJ2:
            return 2
        else:
            return 1
    
    def _get_call_k(self) -> int:
        """Get call-string depth for hybrid policies.
        
        Returns:
            Call-string depth
        """
        if self.policy in (ContextPolicy.HYBRID_CALL2_OBJ1,):
            return 2
        else:
            return 1
    
    def _get_obj_depth(self) -> int:
        """Get object depth for hybrid policies.
        
        Returns:
            Object sensitivity depth
        """
        if self.policy == ContextPolicy.HYBRID_CALL1_OBJ2:
            return 2
        else:
            return 1
    
    def push(self, current_ctx: AbstractContext, call_site: CallSite) -> AbstractContext:
        """Legacy push method for backward compatibility.
        
        Args:
            current_ctx: Current context
            call_site: Call site to push
            
        Returns:
            New context with call site added
        """
        return self.select_call_context(
            caller_ctx=current_ctx,
            call_site=call_site,
            callee=call_site.fn,
            receiver_alloc=None,
            receiver_type=None
        )
    
    def __repr__(self) -> str:
        return f"ContextSelector(policy={self.policy.value})"


# Utility function for parsing policy strings
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
        "1c1o": ContextPolicy.HYBRID_CALL1_OBJ1,
        "2c1o": ContextPolicy.HYBRID_CALL2_OBJ1,
        "1c2o": ContextPolicy.HYBRID_CALL1_OBJ2,
    }
    
    if policy_str not in policy_map:
        raise ValueError(
            f"Unknown policy: {policy_str}. "
            f"Available policies: {', '.join(policy_map.keys())}"
        )
    
    return policy_map[policy_str]

