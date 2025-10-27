"""Tests for context selector and policy selection."""

import pytest
from pythonstan.analysis.pointer.kcfa import (
    ContextPolicy,
    ContextSelector,
    CallSite,
    CallStringContext,
    ObjectContext,
    TypeContext,
    ReceiverContext,
    HybridContext,
)
from pythonstan.analysis.pointer.kcfa.context_selector import parse_policy


class TestContextPolicy:
    """Tests for ContextPolicy enum."""
    
    def test_all_policies_defined(self):
        """Test that all 15 policies are defined."""
        assert ContextPolicy.INSENSITIVE
        assert ContextPolicy.CALL_1
        assert ContextPolicy.CALL_2
        assert ContextPolicy.CALL_3
        assert ContextPolicy.OBJ_1
        assert ContextPolicy.OBJ_2
        assert ContextPolicy.OBJ_3
        assert ContextPolicy.TYPE_1
        assert ContextPolicy.TYPE_2
        assert ContextPolicy.TYPE_3
        assert ContextPolicy.RECEIVER_1
        assert ContextPolicy.RECEIVER_2
        assert ContextPolicy.RECEIVER_3
        assert ContextPolicy.HYBRID_CALL1_OBJ1
        assert ContextPolicy.HYBRID_CALL2_OBJ1
        assert ContextPolicy.HYBRID_CALL1_OBJ2
    
    def test_policy_values(self):
        """Test policy enum string values."""
        assert ContextPolicy.INSENSITIVE.value == "0-cfa"
        assert ContextPolicy.CALL_2.value == "2-cfa"
        assert ContextPolicy.OBJ_1.value == "1-obj"
        assert ContextPolicy.TYPE_2.value == "2-type"
        assert ContextPolicy.RECEIVER_1.value == "1-rcv"
        assert ContextPolicy.HYBRID_CALL1_OBJ1.value == "1c1o"


class TestParsePolicy:
    """Tests for parse_policy function."""
    
    def test_parse_cfa_policies(self):
        """Test parsing call-string policies."""
        assert parse_policy("0-cfa") == ContextPolicy.INSENSITIVE
        assert parse_policy("1-cfa") == ContextPolicy.CALL_1
        assert parse_policy("2-cfa") == ContextPolicy.CALL_2
        assert parse_policy("3-cfa") == ContextPolicy.CALL_3
    
    def test_parse_obj_policies(self):
        """Test parsing object-sensitive policies."""
        assert parse_policy("1-obj") == ContextPolicy.OBJ_1
        assert parse_policy("2-obj") == ContextPolicy.OBJ_2
        assert parse_policy("3-obj") == ContextPolicy.OBJ_3
    
    def test_parse_type_policies(self):
        """Test parsing type-sensitive policies."""
        assert parse_policy("1-type") == ContextPolicy.TYPE_1
        assert parse_policy("2-type") == ContextPolicy.TYPE_2
        assert parse_policy("3-type") == ContextPolicy.TYPE_3
    
    def test_parse_receiver_policies(self):
        """Test parsing receiver-sensitive policies."""
        assert parse_policy("1-rcv") == ContextPolicy.RECEIVER_1
        assert parse_policy("2-rcv") == ContextPolicy.RECEIVER_2
        assert parse_policy("3-rcv") == ContextPolicy.RECEIVER_3
    
    def test_parse_hybrid_policies(self):
        """Test parsing hybrid policies."""
        assert parse_policy("1c1o") == ContextPolicy.HYBRID_CALL1_OBJ1
        assert parse_policy("2c1o") == ContextPolicy.HYBRID_CALL2_OBJ1
        assert parse_policy("1c2o") == ContextPolicy.HYBRID_CALL1_OBJ2
    
    def test_parse_unknown_policy_raises(self):
        """Test that unknown policy raises ValueError."""
        with pytest.raises(ValueError, match="Unknown policy"):
            parse_policy("unknown")
        
        with pytest.raises(ValueError, match="Unknown policy"):
            parse_policy("4-cfa")


class TestContextSelectorInitialization:
    """Tests for ContextSelector initialization."""
    
    def test_default_initialization(self):
        """Test default initialization (2-CFA)."""
        selector = ContextSelector()
        assert selector.policy == ContextPolicy.CALL_2
    
    def test_initialization_with_policy(self):
        """Test initialization with specific policy."""
        selector = ContextSelector(ContextPolicy.OBJ_1)
        assert selector.policy == ContextPolicy.OBJ_1
    
    def test_repr(self):
        """Test string representation."""
        selector = ContextSelector(ContextPolicy.CALL_2)
        assert "2-cfa" in repr(selector)


class TestEmptyContextCreation:
    """Tests for empty context creation across policies."""
    
    def test_insensitive_empty_context(self):
        """Test 0-CFA creates empty call-string context with k=0."""
        selector = ContextSelector(ContextPolicy.INSENSITIVE)
        ctx = selector.empty_context()
        
        assert isinstance(ctx, CallStringContext)
        assert ctx.k == 0
        assert ctx.is_empty()
    
    def test_call_sensitive_empty_contexts(self):
        """Test k-CFA policies create appropriate empty contexts."""
        selector1 = ContextSelector(ContextPolicy.CALL_1)
        ctx1 = selector1.empty_context()
        assert isinstance(ctx1, CallStringContext)
        assert ctx1.k == 1
        
        selector2 = ContextSelector(ContextPolicy.CALL_2)
        ctx2 = selector2.empty_context()
        assert ctx2.k == 2
        
        selector3 = ContextSelector(ContextPolicy.CALL_3)
        ctx3 = selector3.empty_context()
        assert ctx3.k == 3
    
    def test_object_sensitive_empty_contexts(self):
        """Test object-sensitive policies create appropriate empty contexts."""
        selector1 = ContextSelector(ContextPolicy.OBJ_1)
        ctx1 = selector1.empty_context()
        assert isinstance(ctx1, ObjectContext)
        assert ctx1.depth == 1
        
        selector2 = ContextSelector(ContextPolicy.OBJ_2)
        ctx2 = selector2.empty_context()
        assert ctx2.depth == 2
    
    def test_type_sensitive_empty_contexts(self):
        """Test type-sensitive policies create appropriate empty contexts."""
        selector = ContextSelector(ContextPolicy.TYPE_2)
        ctx = selector.empty_context()
        
        assert isinstance(ctx, TypeContext)
        assert ctx.depth == 2
    
    def test_receiver_sensitive_empty_contexts(self):
        """Test receiver-sensitive policies create appropriate empty contexts."""
        selector = ContextSelector(ContextPolicy.RECEIVER_1)
        ctx = selector.empty_context()
        
        assert isinstance(ctx, ReceiverContext)
        assert ctx.depth == 1
    
    def test_hybrid_empty_contexts(self):
        """Test hybrid policies create appropriate empty contexts."""
        selector = ContextSelector(ContextPolicy.HYBRID_CALL1_OBJ1)
        ctx = selector.empty_context()
        
        assert isinstance(ctx, HybridContext)
        assert ctx.call_k == 1
        assert ctx.obj_depth == 1


class TestCallContextSelectionCallString:
    """Tests for call context selection with call-string policies."""
    
    def test_insensitive_no_context_change(self, call_site_factory):
        """Test 0-CFA returns same context."""
        selector = ContextSelector(ContextPolicy.INSENSITIVE)
        ctx = selector.empty_context()
        cs = call_site_factory("caller")
        
        new_ctx = selector.select_call_context(ctx, cs, "callee")
        assert new_ctx == ctx
    
    def test_call_sensitive_appends_call_site(self, call_site_factory):
        """Test k-CFA appends call site."""
        selector = ContextSelector(ContextPolicy.CALL_2)
        ctx = selector.empty_context()
        cs = call_site_factory("caller")
        
        new_ctx = selector.select_call_context(ctx, cs, "callee")
        
        assert isinstance(new_ctx, CallStringContext)
        assert len(new_ctx) == 1
        assert new_ctx.call_sites[0] == cs
    
    def test_call_sensitive_chain(self, call_site_factory):
        """Test k-CFA builds call chain."""
        selector = ContextSelector(ContextPolicy.CALL_2)
        ctx = selector.empty_context()
        
        cs1 = call_site_factory("caller1")
        cs2 = call_site_factory("caller2")
        
        ctx = selector.select_call_context(ctx, cs1, "callee1")
        ctx = selector.select_call_context(ctx, cs2, "callee2")
        
        assert len(ctx) == 2
        assert ctx.call_sites == (cs1, cs2)
    
    def test_call_sensitive_respects_k_limit(self, call_site_factory):
        """Test k-CFA respects k limit."""
        selector = ContextSelector(ContextPolicy.CALL_1)
        ctx = selector.empty_context()
        
        cs1 = call_site_factory("caller1")
        cs2 = call_site_factory("caller2")
        
        ctx = selector.select_call_context(ctx, cs1, "callee1")
        ctx = selector.select_call_context(ctx, cs2, "callee2")
        
        # Should keep only most recent
        assert len(ctx) == 1
        assert ctx.call_sites[0] == cs2


class TestCallContextSelectionObjectSensitive:
    """Tests for call context selection with object-sensitive policies."""
    
    def test_object_sensitive_with_receiver(self, call_site_factory):
        """Test object-sensitive uses receiver allocation site."""
        selector = ContextSelector(ContextPolicy.OBJ_1)
        ctx = selector.empty_context()
        cs = call_site_factory("caller")
        receiver_alloc = "test.py:10:0:obj:MyClass"
        
        new_ctx = selector.select_call_context(
            ctx, cs, "method", receiver_alloc=receiver_alloc
        )
        
        assert isinstance(new_ctx, ObjectContext)
        assert len(new_ctx.alloc_sites) == 1
        assert new_ctx.alloc_sites[0] == receiver_alloc
    
    def test_object_sensitive_without_receiver(self, call_site_factory):
        """Test object-sensitive uses proxy allocation without receiver."""
        selector = ContextSelector(ContextPolicy.OBJ_1)
        ctx = selector.empty_context()
        cs = call_site_factory("caller")
        
        new_ctx = selector.select_call_context(ctx, cs, "function")
        
        assert isinstance(new_ctx, ObjectContext)
        assert len(new_ctx.alloc_sites) == 1
        # Should have proxy allocation
        assert "call:" in new_ctx.alloc_sites[0]
    
    def test_object_sensitive_chain(self, call_site_factory):
        """Test object-sensitive builds allocation chain."""
        selector = ContextSelector(ContextPolicy.OBJ_2)
        ctx = selector.empty_context()
        
        cs1 = call_site_factory("caller1")
        cs2 = call_site_factory("caller2")
        alloc1 = "test.py:10:0:obj:Class1"
        alloc2 = "test.py:20:0:obj:Class2"
        
        ctx = selector.select_call_context(ctx, cs1, "method1", receiver_alloc=alloc1)
        ctx = selector.select_call_context(ctx, cs2, "method2", receiver_alloc=alloc2)
        
        assert len(ctx.alloc_sites) == 2
        assert ctx.alloc_sites == (alloc1, alloc2)


class TestCallContextSelectionTypeSensitive:
    """Tests for call context selection with type-sensitive policies."""
    
    def test_type_sensitive_with_receiver_type(self, call_site_factory):
        """Test type-sensitive uses receiver type."""
        selector = ContextSelector(ContextPolicy.TYPE_1)
        ctx = selector.empty_context()
        cs = call_site_factory("caller")
        
        new_ctx = selector.select_call_context(
            ctx, cs, "method", receiver_type="MyClass"
        )
        
        assert isinstance(new_ctx, TypeContext)
        assert len(new_ctx.types) == 1
        assert new_ctx.types[0] == "MyClass"
    
    def test_type_sensitive_without_receiver_type(self, call_site_factory):
        """Test type-sensitive uses callee name without receiver type."""
        selector = ContextSelector(ContextPolicy.TYPE_1)
        ctx = selector.empty_context()
        cs = call_site_factory("caller")
        
        new_ctx = selector.select_call_context(ctx, cs, "function_name")
        
        assert isinstance(new_ctx, TypeContext)
        assert len(new_ctx.types) == 1
        assert new_ctx.types[0] == "function_name"


class TestCallContextSelectionReceiverSensitive:
    """Tests for call context selection with receiver-sensitive policies."""
    
    def test_receiver_sensitive_with_receiver(self, call_site_factory):
        """Test receiver-sensitive uses receiver allocation site."""
        selector = ContextSelector(ContextPolicy.RECEIVER_1)
        ctx = selector.empty_context()
        cs = call_site_factory("caller")
        receiver_alloc = "test.py:10:0:obj:MyClass"
        
        new_ctx = selector.select_call_context(
            ctx, cs, "method", receiver_alloc=receiver_alloc
        )
        
        assert isinstance(new_ctx, ReceiverContext)
        assert len(new_ctx.receivers) == 1
        assert new_ctx.receivers[0] == receiver_alloc
    
    def test_receiver_sensitive_without_receiver(self, call_site_factory):
        """Test receiver-sensitive returns same context without receiver."""
        selector = ContextSelector(ContextPolicy.RECEIVER_1)
        ctx = selector.empty_context()
        cs = call_site_factory("caller")
        
        new_ctx = selector.select_call_context(ctx, cs, "function")
        
        # Should return same context
        assert new_ctx == ctx


class TestCallContextSelectionHybrid:
    """Tests for call context selection with hybrid policies."""
    
    def test_hybrid_appends_call_site(self, call_site_factory):
        """Test hybrid policy appends call site."""
        selector = ContextSelector(ContextPolicy.HYBRID_CALL1_OBJ1)
        ctx = selector.empty_context()
        cs = call_site_factory("caller")
        
        new_ctx = selector.select_call_context(ctx, cs, "callee")
        
        assert isinstance(new_ctx, HybridContext)
        assert len(new_ctx.call_sites) == 1
        assert new_ctx.call_sites[0] == cs
    
    def test_hybrid_appends_receiver(self, call_site_factory):
        """Test hybrid policy appends receiver allocation."""
        selector = ContextSelector(ContextPolicy.HYBRID_CALL1_OBJ1)
        ctx = selector.empty_context()
        cs = call_site_factory("caller")
        receiver_alloc = "test.py:10:0:obj:MyClass"
        
        new_ctx = selector.select_call_context(
            ctx, cs, "method", receiver_alloc=receiver_alloc
        )
        
        assert len(new_ctx.call_sites) == 1
        assert len(new_ctx.alloc_sites) == 1
        assert new_ctx.alloc_sites[0] == receiver_alloc
    
    def test_hybrid_respects_both_limits(self, call_site_factory):
        """Test hybrid policy respects both k and depth limits."""
        selector = ContextSelector(ContextPolicy.HYBRID_CALL1_OBJ1)
        ctx = selector.empty_context()
        
        # Add multiple call sites (k=1)
        cs1 = call_site_factory("caller1")
        cs2 = call_site_factory("caller2")
        alloc1 = "alloc1"
        alloc2 = "alloc2"
        
        ctx = selector.select_call_context(ctx, cs1, "m1", receiver_alloc=alloc1)
        ctx = selector.select_call_context(ctx, cs2, "m2", receiver_alloc=alloc2)
        
        # Should keep only 1 call site and 1 alloc site (most recent)
        assert len(ctx.call_sites) == 1
        assert len(ctx.alloc_sites) == 1


class TestAllocContextSelection:
    """Tests for allocation context selection."""
    
    def test_alloc_context_non_object_policies(self, call_site_factory):
        """Test alloc context returns same context for non-object policies."""
        selector = ContextSelector(ContextPolicy.CALL_2)
        ctx = selector.empty_context()
        cs = call_site_factory("caller")
        ctx = selector.select_call_context(ctx, cs, "callee")
        
        alloc_ctx = selector.select_alloc_context(ctx, "alloc1")
        
        # Should return same context
        assert alloc_ctx == ctx
    
    def test_alloc_context_object_sensitive(self):
        """Test alloc context appends allocation site for object-sensitive."""
        selector = ContextSelector(ContextPolicy.OBJ_1)
        ctx = selector.empty_context()
        alloc_site = "test.py:10:0:obj:MyClass"
        
        alloc_ctx = selector.select_alloc_context(ctx, alloc_site)
        
        assert isinstance(alloc_ctx, ObjectContext)
        assert len(alloc_ctx.alloc_sites) == 1
        assert alloc_ctx.alloc_sites[0] == alloc_site
    
    def test_alloc_context_hybrid(self, call_site_factory):
        """Test alloc context appends to object part of hybrid context."""
        selector = ContextSelector(ContextPolicy.HYBRID_CALL1_OBJ1)
        ctx = selector.empty_context()
        
        # Add call site first
        cs = call_site_factory("caller")
        ctx = selector.select_call_context(ctx, cs, "callee")
        
        # Add allocation
        alloc_site = "test.py:10:0:obj:MyClass"
        alloc_ctx = selector.select_alloc_context(ctx, alloc_site)
        
        assert len(alloc_ctx.call_sites) == 1  # Call part unchanged
        assert len(alloc_ctx.alloc_sites) == 1
        assert alloc_ctx.alloc_sites[0] == alloc_site


class TestContextSelectorUsagePatterns:
    """Test realistic usage patterns with context selector."""
    
    def test_function_call_chain_2cfa(self, call_site_factory):
        """Test realistic call chain with 2-CFA."""
        selector = ContextSelector(ContextPolicy.CALL_2)
        
        # Start with entry point
        ctx = selector.empty_context()
        
        # main -> foo
        cs_main_foo = call_site_factory("main")
        ctx = selector.select_call_context(ctx, cs_main_foo, "foo")
        assert len(ctx) == 1
        
        # foo -> bar
        cs_foo_bar = call_site_factory("foo")
        ctx = selector.select_call_context(ctx, cs_foo_bar, "bar")
        assert len(ctx) == 2
        
        # bar -> baz (should truncate oldest)
        cs_bar_baz = call_site_factory("bar")
        ctx = selector.select_call_context(ctx, cs_bar_baz, "baz")
        assert len(ctx) == 2
        assert ctx.call_sites[0] == cs_foo_bar
        assert ctx.call_sites[1] == cs_bar_baz
    
    def test_method_call_chain_1obj(self, call_site_factory):
        """Test realistic method call chain with 1-obj."""
        selector = ContextSelector(ContextPolicy.OBJ_1)
        ctx = selector.empty_context()
        
        # Create obj1, call method1
        cs1 = call_site_factory("main")
        obj1_alloc = "test.py:10:0:obj:ClassA"
        ctx = selector.select_call_context(ctx, cs1, "method1", receiver_alloc=obj1_alloc)
        
        # In method1, create obj2, call method2
        cs2 = call_site_factory("method1")
        obj2_alloc = "test.py:20:0:obj:ClassB"
        ctx = selector.select_call_context(ctx, cs2, "method2", receiver_alloc=obj2_alloc)
        
        # Should have only most recent receiver (depth=1)
        assert len(ctx.alloc_sites) == 1
        assert ctx.alloc_sites[0] == obj2_alloc
    
    def test_hybrid_method_calls(self, call_site_factory):
        """Test hybrid policy with method calls."""
        selector = ContextSelector(ContextPolicy.HYBRID_CALL1_OBJ1)
        ctx = selector.empty_context()
        
        # main calls obj.method
        cs1 = call_site_factory("main")
        obj_alloc = "test.py:10:0:obj:MyClass"
        ctx = selector.select_call_context(ctx, cs1, "method", receiver_alloc=obj_alloc)
        
        assert len(ctx.call_sites) == 1
        assert len(ctx.alloc_sites) == 1
    
    def test_allocation_in_object_sensitive(self):
        """Test tracking allocations in object-sensitive analysis."""
        selector = ContextSelector(ContextPolicy.OBJ_2)
        ctx = selector.empty_context()
        
        # Allocate obj1
        alloc1 = "test.py:10:0:obj:Builder"
        ctx = selector.select_alloc_context(ctx, alloc1)
        
        # Allocate obj2
        alloc2 = "test.py:15:0:obj:Product"
        ctx = selector.select_alloc_context(ctx, alloc2)
        
        assert len(ctx.alloc_sites) == 2
        assert ctx.alloc_sites == (alloc1, alloc2)

