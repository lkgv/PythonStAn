"""Tests for context sensitivity policies.

This module tests different context-sensitive policies to ensure they
work correctly and produce different contexts as expected.
"""

import pytest
from pythonstan.analysis.pointer.kcfa2.context import (
    CallSite,
    CallStringContext,
    ObjectContext,
    TypeContext,
    ReceiverContext,
    HybridContext,
)
from pythonstan.analysis.pointer.kcfa2.context_selector import (
    ContextPolicy,
    ContextSelector,
    parse_policy,
)


class TestCallStringContext:
    """Test call-string sensitivity contexts."""
    
    def test_empty_context(self):
        """Test empty context creation."""
        ctx = CallStringContext((), 2)
        assert ctx.is_empty()
        assert len(ctx) == 0
        assert str(ctx) == "[]"
    
    def test_append_call_site(self):
        """Test appending call sites."""
        cs1 = CallSite("file.py:10:5:call", "foo")
        cs2 = CallSite("file.py:20:8:call", "bar")
        
        ctx = CallStringContext((), 2)
        ctx = ctx.append(cs1)
        assert len(ctx) == 1
        assert not ctx.is_empty()
        
        ctx = ctx.append(cs2)
        assert len(ctx) == 2
    
    def test_k_limiting(self):
        """Test that context is limited to k call sites."""
        cs1 = CallSite("file.py:10:5:call", "foo")
        cs2 = CallSite("file.py:20:8:call", "bar")
        cs3 = CallSite("file.py:30:3:call", "baz")
        
        ctx = CallStringContext((), 2)
        ctx = ctx.append(cs1)
        ctx = ctx.append(cs2)
        ctx = ctx.append(cs3)
        
        # Should keep only last 2 call sites
        assert len(ctx) == 2
        assert cs2 in ctx.call_sites
        assert cs3 in ctx.call_sites
        assert cs1 not in ctx.call_sites
    
    def test_context_insensitive(self):
        """Test context-insensitive mode (k=0)."""
        cs1 = CallSite("file.py:10:5:call", "foo")
        cs2 = CallSite("file.py:20:8:call", "bar")
        
        ctx = CallStringContext((), 0)
        ctx = ctx.append(cs1)
        assert ctx.is_empty()  # Should stay empty
        
        ctx = ctx.append(cs2)
        assert ctx.is_empty()
    
    def test_equality_and_hash(self):
        """Test context equality and hashing."""
        cs1 = CallSite("file.py:10:5:call", "foo")
        cs2 = CallSite("file.py:20:8:call", "bar")
        
        ctx1 = CallStringContext((cs1, cs2), 2)
        ctx2 = CallStringContext((cs1, cs2), 2)
        ctx3 = CallStringContext((cs1,), 2)
        
        assert ctx1 == ctx2
        assert hash(ctx1) == hash(ctx2)
        assert ctx1 != ctx3


class TestObjectContext:
    """Test object sensitivity contexts."""
    
    def test_empty_context(self):
        """Test empty object context."""
        ctx = ObjectContext((), 2)
        assert ctx.is_empty()
        assert str(ctx) == "<>"
    
    def test_append_alloc_site(self):
        """Test appending allocation sites."""
        ctx = ObjectContext((), 2)
        ctx = ctx.append("file.py:10:5:Dog")
        assert not ctx.is_empty()
        assert len(ctx.alloc_sites) == 1
        
        ctx = ctx.append("file.py:20:8:Cat")
        assert len(ctx.alloc_sites) == 2
    
    def test_depth_limiting(self):
        """Test depth limiting for object contexts."""
        ctx = ObjectContext((), 2)
        ctx = ctx.append("file.py:10:5:A")
        ctx = ctx.append("file.py:20:8:B")
        ctx = ctx.append("file.py:30:3:C")
        
        # Should keep only last 2 allocation sites
        assert len(ctx.alloc_sites) == 2
        assert "file.py:20:8:B" in ctx.alloc_sites
        assert "file.py:30:3:C" in ctx.alloc_sites
        assert "file.py:10:5:A" not in ctx.alloc_sites


class TestTypeContext:
    """Test type sensitivity contexts."""
    
    def test_empty_context(self):
        """Test empty type context."""
        ctx = TypeContext((), 2)
        assert ctx.is_empty()
        assert str(ctx) == "<:>"
    
    def test_append_type(self):
        """Test appending types."""
        ctx = TypeContext((), 2)
        ctx = ctx.append("Dog")
        assert not ctx.is_empty()
        assert "Dog" in ctx.types
        
        ctx = ctx.append("Animal")
        assert len(ctx.types) == 2


class TestReceiverContext:
    """Test receiver-object sensitivity contexts."""
    
    def test_empty_context(self):
        """Test empty receiver context."""
        ctx = ReceiverContext((), 2)
        assert ctx.is_empty()
        assert str(ctx) == "<rcv:>"
    
    def test_append_receiver(self):
        """Test appending receiver allocation sites."""
        ctx = ReceiverContext((), 2)
        ctx = ctx.append("file.py:10:5:Dog")
        assert not ctx.is_empty()
        assert "file.py:10:5:Dog" in ctx.receivers


class TestHybridContext:
    """Test hybrid context sensitivity."""
    
    def test_empty_context(self):
        """Test empty hybrid context."""
        ctx = HybridContext((), (), 1, 1)
        assert ctx.is_empty()
    
    def test_append_call_and_object(self):
        """Test appending both call sites and allocation sites."""
        cs1 = CallSite("file.py:10:5:call", "foo")
        
        ctx = HybridContext((), (), 1, 1)
        ctx = ctx.append_call(cs1)
        assert not ctx.is_empty()
        assert len(ctx.call_sites) == 1
        
        ctx = ctx.append_object("file.py:20:8:Dog")
        assert len(ctx.alloc_sites) == 1
    
    def test_separate_limiting(self):
        """Test that call and object dimensions are limited separately."""
        cs1 = CallSite("file.py:10:5:call", "foo")
        cs2 = CallSite("file.py:20:8:call", "bar")
        
        ctx = HybridContext((), (), 1, 2)
        ctx = ctx.append_call(cs1)
        ctx = ctx.append_call(cs2)
        
        # Should keep only 1 call site
        assert len(ctx.call_sites) == 1
        assert cs2 in ctx.call_sites
        
        # But can have 2 object sites
        ctx = ctx.append_object("file.py:30:3:A")
        ctx = ctx.append_object("file.py:40:5:B")
        assert len(ctx.alloc_sites) == 2


class TestContextSelector:
    """Test context selector for different policies."""
    
    def test_parse_policy(self):
        """Test policy string parsing."""
        assert parse_policy("0-cfa") == ContextPolicy.INSENSITIVE
        assert parse_policy("1-cfa") == ContextPolicy.CALL_1
        assert parse_policy("2-cfa") == ContextPolicy.CALL_2
        assert parse_policy("1-obj") == ContextPolicy.OBJ_1
        assert parse_policy("1-type") == ContextPolicy.TYPE_1
        assert parse_policy("1-rcv") == ContextPolicy.RECEIVER_1
        assert parse_policy("1c1o") == ContextPolicy.HYBRID_CALL1_OBJ1
        
        with pytest.raises(ValueError):
            parse_policy("invalid-policy")
    
    def test_empty_context_for_policies(self):
        """Test that each policy creates appropriate empty context."""
        # Call-string policies
        selector = ContextSelector(ContextPolicy.CALL_2)
        ctx = selector.empty_context()
        assert isinstance(ctx, CallStringContext)
        assert ctx.k == 2
        
        # Object sensitivity
        selector = ContextSelector(ContextPolicy.OBJ_1)
        ctx = selector.empty_context()
        assert isinstance(ctx, ObjectContext)
        assert ctx.depth == 1
        
        # Type sensitivity
        selector = ContextSelector(ContextPolicy.TYPE_2)
        ctx = selector.empty_context()
        assert isinstance(ctx, TypeContext)
        assert ctx.depth == 2
        
        # Receiver sensitivity
        selector = ContextSelector(ContextPolicy.RECEIVER_1)
        ctx = selector.empty_context()
        assert isinstance(ctx, ReceiverContext)
        assert ctx.depth == 1
        
        # Hybrid
        selector = ContextSelector(ContextPolicy.HYBRID_CALL1_OBJ1)
        ctx = selector.empty_context()
        assert isinstance(ctx, HybridContext)
        assert ctx.call_k == 1
        assert ctx.obj_depth == 1
    
    def test_select_call_context_cfa(self):
        """Test call context selection for k-CFA."""
        selector = ContextSelector(ContextPolicy.CALL_2)
        ctx = selector.empty_context()
        
        cs1 = CallSite("file.py:10:5:call", "foo")
        cs2 = CallSite("file.py:20:8:call", "bar")
        
        ctx = selector.select_call_context(
            caller_ctx=ctx,
            call_site=cs1,
            callee="foo"
        )
        assert len(ctx.call_sites) == 1
        
        ctx = selector.select_call_context(
            caller_ctx=ctx,
            call_site=cs2,
            callee="bar"
        )
        assert len(ctx.call_sites) == 2
    
    def test_select_call_context_object(self):
        """Test call context selection for object sensitivity."""
        selector = ContextSelector(ContextPolicy.OBJ_1)
        ctx = selector.empty_context()
        
        cs1 = CallSite("file.py:10:5:call", "method")
        
        # Method call with receiver
        ctx = selector.select_call_context(
            caller_ctx=ctx,
            call_site=cs1,
            callee="method",
            receiver_alloc="file.py:5:3:Dog"
        )
        assert isinstance(ctx, ObjectContext)
        assert "file.py:5:3:Dog" in ctx.alloc_sites
    
    def test_select_call_context_type(self):
        """Test call context selection for type sensitivity."""
        selector = ContextSelector(ContextPolicy.TYPE_1)
        ctx = selector.empty_context()
        
        cs1 = CallSite("file.py:10:5:call", "method")
        
        # Method call with receiver type
        ctx = selector.select_call_context(
            caller_ctx=ctx,
            call_site=cs1,
            callee="method",
            receiver_type="Dog"
        )
        assert isinstance(ctx, TypeContext)
        assert "Dog" in ctx.types
    
    def test_select_call_context_receiver(self):
        """Test call context selection for receiver sensitivity."""
        selector = ContextSelector(ContextPolicy.RECEIVER_1)
        ctx = selector.empty_context()
        
        cs1 = CallSite("file.py:10:5:call", "method")
        cs2 = CallSite("file.py:20:8:call", "function")
        
        # Method call - should change context
        ctx = selector.select_call_context(
            caller_ctx=ctx,
            call_site=cs1,
            callee="method",
            receiver_alloc="file.py:5:3:Dog"
        )
        assert not ctx.is_empty()
        
        # Regular function call - should NOT change context
        ctx2 = selector.select_call_context(
            caller_ctx=ctx,
            call_site=cs2,
            callee="function",
            receiver_alloc=None
        )
        assert ctx2 == ctx  # Same context
    
    def test_select_call_context_insensitive(self):
        """Test call context selection for context-insensitive."""
        selector = ContextSelector(ContextPolicy.INSENSITIVE)
        ctx = selector.empty_context()
        
        cs1 = CallSite("file.py:10:5:call", "foo")
        
        ctx = selector.select_call_context(
            caller_ctx=ctx,
            call_site=cs1,
            callee="foo"
        )
        assert ctx.is_empty()  # Should stay empty
    
    def test_backward_compatibility(self):
        """Test backward compatibility with k parameter."""
        # No policy specified, use k
        selector = ContextSelector(policy=None, k=2)
        assert selector.policy == ContextPolicy.CALL_2
        
        selector = ContextSelector(policy=None, k=1)
        assert selector.policy == ContextPolicy.CALL_1
        
        selector = ContextSelector(policy=None, k=0)
        assert selector.policy == ContextPolicy.INSENSITIVE


class TestContextIntegration:
    """Integration tests for different context policies."""
    
    def test_different_policies_produce_different_contexts(self):
        """Test that different policies produce different contexts for same calls."""
        cs1 = CallSite("file.py:10:5:call", "foo")
        cs2 = CallSite("file.py:20:8:call", "bar")
        
        # 1-CFA vs 2-CFA
        sel1 = ContextSelector(ContextPolicy.CALL_1)
        ctx1 = sel1.empty_context()
        ctx1 = sel1.select_call_context(ctx1, cs1, "foo")
        ctx1 = sel1.select_call_context(ctx1, cs2, "bar")
        
        sel2 = ContextSelector(ContextPolicy.CALL_2)
        ctx2 = sel2.empty_context()
        ctx2 = sel2.select_call_context(ctx2, cs1, "foo")
        ctx2 = sel2.select_call_context(ctx2, cs2, "bar")
        
        # 1-CFA should have only 1 call site, 2-CFA should have 2
        assert len(ctx1.call_sites) == 1
        assert len(ctx2.call_sites) == 2
    
    def test_object_vs_call_sensitivity(self):
        """Test that object sensitivity differs from call sensitivity."""
        cs1 = CallSite("file.py:10:5:call", "method")
        
        # Call-string sensitivity
        sel_call = ContextSelector(ContextPolicy.CALL_1)
        ctx_call = sel_call.empty_context()
        ctx_call = sel_call.select_call_context(ctx_call, cs1, "method")
        
        # Object sensitivity
        sel_obj = ContextSelector(ContextPolicy.OBJ_1)
        ctx_obj = sel_obj.empty_context()
        ctx_obj = sel_obj.select_call_context(
            ctx_obj, cs1, "method",
            receiver_alloc="file.py:5:3:Dog"
        )
        
        # Different types of contexts
        assert type(ctx_call) != type(ctx_obj)
        assert isinstance(ctx_call, CallStringContext)
        assert isinstance(ctx_obj, ObjectContext)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

