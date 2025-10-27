"""Tests for context types and context abstractions."""

import pytest
from pythonstan.analysis.pointer.kcfa import (
    CallSite,
    AbstractContext,
    CallStringContext,
    ObjectContext,
    TypeContext,
    ReceiverContext,
    HybridContext,
)


class TestCallSite:
    """Tests for CallSite dataclass."""
    
    def test_basic_creation(self):
        """Test creating basic call site."""
        cs = CallSite("test.py:10:5:call", "my_func")
        assert cs.site_id == "test.py:10:5:call"
        assert cs.fn == "my_func"
        assert cs.bb is None
        assert cs.idx == 0
    
    def test_creation_with_bb(self):
        """Test creating call site with basic block."""
        cs = CallSite("test.py:10:5:call", "my_func", "bb0", 3)
        assert cs.bb == "bb0"
        assert cs.idx == 3
    
    def test_string_representation_without_bb(self):
        """Test __str__ without basic block."""
        cs = CallSite("test.py:10:5:call", "my_func")
        cs_str = str(cs)
        assert "test.py:10:5:call" in cs_str
        assert "#0" in cs_str
    
    def test_string_representation_with_bb(self):
        """Test __str__ with basic block."""
        cs = CallSite("test.py:10:5:call", "my_func", "bb0", 3)
        cs_str = str(cs)
        assert "test.py:10:5:call" in cs_str
        assert "bb0" in cs_str
        assert "#3" in cs_str
    
    def test_equality(self):
        """Test call site equality."""
        cs1 = CallSite("test.py:10:5:call", "func")
        cs2 = CallSite("test.py:10:5:call", "func")
        cs3 = CallSite("test.py:11:5:call", "func")
        
        assert cs1 == cs2
        assert cs1 != cs3
    
    def test_frozen(self):
        """Test that CallSite is immutable."""
        cs = CallSite("test.py:10:5:call", "func")
        with pytest.raises(AttributeError):
            cs.site_id = "other"


class TestCallStringContext:
    """Tests for CallStringContext (k-CFA)."""
    
    def test_empty_context_creation(self):
        """Test creating empty call-string context."""
        ctx = CallStringContext((), 2)
        assert ctx.is_empty()
        assert len(ctx) == 0
        assert ctx.k == 2
    
    def test_string_representation_empty(self):
        """Test __str__ for empty context."""
        ctx = CallStringContext((), 2)
        assert str(ctx) == "[]"
    
    def test_append_to_empty(self, call_site_factory):
        """Test appending to empty context."""
        ctx = CallStringContext((), 2)
        cs = call_site_factory("caller")
        
        new_ctx = ctx.append(cs)
        assert not new_ctx.is_empty()
        assert len(new_ctx) == 1
        assert new_ctx.call_sites[0] == cs
    
    def test_append_within_k_limit(self, call_site_factory):
        """Test appending within k limit."""
        ctx = CallStringContext((), 2)
        cs1 = call_site_factory("caller1")
        cs2 = call_site_factory("caller2")
        
        ctx = ctx.append(cs1)
        ctx = ctx.append(cs2)
        
        assert len(ctx) == 2
        assert ctx.call_sites == (cs1, cs2)
    
    def test_append_exceeding_k_limit(self, call_site_factory):
        """Test that appending beyond k truncates oldest call site."""
        ctx = CallStringContext((), 2)
        cs1 = call_site_factory("caller1")
        cs2 = call_site_factory("caller2")
        cs3 = call_site_factory("caller3")
        
        ctx = ctx.append(cs1)
        ctx = ctx.append(cs2)
        ctx = ctx.append(cs3)
        
        # Should keep most recent 2
        assert len(ctx) == 2
        assert ctx.call_sites == (cs2, cs3)
    
    def test_append_to_insensitive_context(self, call_site_factory):
        """Test appending to 0-CFA context (insensitive)."""
        ctx = CallStringContext((), 0)
        cs = call_site_factory("caller")
        
        new_ctx = ctx.append(cs)
        # Should remain empty
        assert new_ctx.is_empty()
        assert len(new_ctx) == 0
    
    def test_string_representation_with_calls(self, call_site_factory):
        """Test __str__ with call sites."""
        ctx = CallStringContext((), 2)
        cs1 = call_site_factory("caller1")
        cs2 = call_site_factory("caller2")
        
        ctx = ctx.append(cs1).append(cs2)
        ctx_str = str(ctx)
        
        assert "[" in ctx_str
        assert "]" in ctx_str
        assert "→" in ctx_str
    
    def test_equality(self, call_site_factory):
        """Test context equality."""
        cs = call_site_factory("caller")
        
        ctx1 = CallStringContext((cs,), 2)
        ctx2 = CallStringContext((cs,), 2)
        ctx3 = CallStringContext((cs,), 1)  # Different k
        ctx4 = CallStringContext((), 2)  # Empty
        
        assert ctx1 == ctx2
        assert ctx1 != ctx3
        assert ctx1 != ctx4
    
    def test_hashable(self, call_site_factory):
        """Test that context can be used in sets/dicts."""
        cs = call_site_factory("caller")
        
        ctx1 = CallStringContext((cs,), 2)
        ctx2 = CallStringContext((cs,), 2)
        
        contexts = {ctx1, ctx2}
        assert len(contexts) == 1
    
    def test_frozen(self, call_site_factory):
        """Test that CallStringContext is immutable."""
        ctx = CallStringContext((), 2)
        with pytest.raises(AttributeError):
            ctx.k = 3
    
    def test_append_is_immutable(self, call_site_factory):
        """Test that append creates new context."""
        ctx1 = CallStringContext((), 2)
        cs = call_site_factory("caller")
        ctx2 = ctx1.append(cs)
        
        # Original unchanged
        assert ctx1.is_empty()
        # New has call site
        assert len(ctx2) == 1


class TestObjectContext:
    """Tests for ObjectContext (object sensitivity)."""
    
    def test_empty_context_creation(self):
        """Test creating empty object context."""
        ctx = ObjectContext((), 2)
        assert ctx.is_empty()
        assert ctx.depth == 2
    
    def test_string_representation_empty(self):
        """Test __str__ for empty context."""
        ctx = ObjectContext((), 2)
        assert str(ctx) == "<>"
    
    def test_append_to_empty(self):
        """Test appending allocation site to empty context."""
        ctx = ObjectContext((), 2)
        new_ctx = ctx.append("test.py:10:0:obj")
        
        assert not new_ctx.is_empty()
        assert len(new_ctx.alloc_sites) == 1
        assert new_ctx.alloc_sites[0] == "test.py:10:0:obj"
    
    def test_append_within_depth_limit(self):
        """Test appending within depth limit."""
        ctx = ObjectContext((), 2)
        ctx = ctx.append("alloc1")
        ctx = ctx.append("alloc2")
        
        assert len(ctx.alloc_sites) == 2
        assert ctx.alloc_sites == ("alloc1", "alloc2")
    
    def test_append_exceeding_depth_limit(self):
        """Test that appending beyond depth truncates oldest."""
        ctx = ObjectContext((), 2)
        ctx = ctx.append("alloc1")
        ctx = ctx.append("alloc2")
        ctx = ctx.append("alloc3")
        
        # Should keep most recent 2
        assert len(ctx.alloc_sites) == 2
        assert ctx.alloc_sites == ("alloc2", "alloc3")
    
    def test_string_representation_with_sites(self):
        """Test __str__ with allocation sites."""
        ctx = ObjectContext((), 2)
        ctx = ctx.append("test.py:10:0:obj:MyClass")
        ctx = ctx.append("test.py:20:0:obj:OtherClass")
        
        ctx_str = str(ctx)
        assert "<" in ctx_str
        assert ">" in ctx_str
        assert "," in ctx_str
    
    def test_equality(self):
        """Test context equality."""
        ctx1 = ObjectContext(("alloc1",), 2)
        ctx2 = ObjectContext(("alloc1",), 2)
        ctx3 = ObjectContext(("alloc1",), 1)  # Different depth
        ctx4 = ObjectContext(("alloc2",), 2)  # Different site
        
        assert ctx1 == ctx2
        assert ctx1 != ctx3
        assert ctx1 != ctx4
    
    def test_hashable(self):
        """Test that context can be used in sets/dicts."""
        ctx1 = ObjectContext(("alloc1",), 2)
        ctx2 = ObjectContext(("alloc1",), 2)
        
        contexts = {ctx1, ctx2}
        assert len(contexts) == 1


class TestTypeContext:
    """Tests for TypeContext (type sensitivity)."""
    
    def test_empty_context_creation(self):
        """Test creating empty type context."""
        ctx = TypeContext((), 2)
        assert ctx.is_empty()
        assert ctx.depth == 2
    
    def test_string_representation_empty(self):
        """Test __str__ for empty context."""
        ctx = TypeContext((), 2)
        assert str(ctx) == "<:>"
    
    def test_append_to_empty(self):
        """Test appending type to empty context."""
        ctx = TypeContext((), 2)
        new_ctx = ctx.append("MyClass")
        
        assert not new_ctx.is_empty()
        assert len(new_ctx.types) == 1
        assert new_ctx.types[0] == "MyClass"
    
    def test_append_within_depth_limit(self):
        """Test appending within depth limit."""
        ctx = TypeContext((), 2)
        ctx = ctx.append("Class1")
        ctx = ctx.append("Class2")
        
        assert len(ctx.types) == 2
        assert ctx.types == ("Class1", "Class2")
    
    def test_append_exceeding_depth_limit(self):
        """Test that appending beyond depth truncates oldest."""
        ctx = TypeContext((), 2)
        ctx = ctx.append("Class1")
        ctx = ctx.append("Class2")
        ctx = ctx.append("Class3")
        
        # Should keep most recent 2
        assert len(ctx.types) == 2
        assert ctx.types == ("Class2", "Class3")
    
    def test_string_representation_with_types(self):
        """Test __str__ with types."""
        ctx = TypeContext((), 2)
        ctx = ctx.append("BaseClass")
        ctx = ctx.append("DerivedClass")
        
        ctx_str = str(ctx)
        assert "<" in ctx_str
        assert ">" in ctx_str
        assert ":" in ctx_str
        assert "BaseClass" in ctx_str
        assert "DerivedClass" in ctx_str
    
    def test_equality(self):
        """Test context equality."""
        ctx1 = TypeContext(("MyClass",), 2)
        ctx2 = TypeContext(("MyClass",), 2)
        ctx3 = TypeContext(("MyClass",), 1)  # Different depth
        ctx4 = TypeContext(("OtherClass",), 2)  # Different type
        
        assert ctx1 == ctx2
        assert ctx1 != ctx3
        assert ctx1 != ctx4


class TestReceiverContext:
    """Tests for ReceiverContext (receiver-object sensitivity)."""
    
    def test_empty_context_creation(self):
        """Test creating empty receiver context."""
        ctx = ReceiverContext((), 2)
        assert ctx.is_empty()
        assert ctx.depth == 2
    
    def test_string_representation_empty(self):
        """Test __str__ for empty context."""
        ctx = ReceiverContext((), 2)
        assert str(ctx) == "<rcv:>"
    
    def test_append_to_empty(self):
        """Test appending receiver site to empty context."""
        ctx = ReceiverContext((), 2)
        new_ctx = ctx.append("test.py:10:0:obj")
        
        assert not new_ctx.is_empty()
        assert len(new_ctx.receivers) == 1
        assert new_ctx.receivers[0] == "test.py:10:0:obj"
    
    def test_append_within_depth_limit(self):
        """Test appending within depth limit."""
        ctx = ReceiverContext((), 2)
        ctx = ctx.append("rcv1")
        ctx = ctx.append("rcv2")
        
        assert len(ctx.receivers) == 2
        assert ctx.receivers == ("rcv1", "rcv2")
    
    def test_append_exceeding_depth_limit(self):
        """Test that appending beyond depth truncates oldest."""
        ctx = ReceiverContext((), 2)
        ctx = ctx.append("rcv1")
        ctx = ctx.append("rcv2")
        ctx = ctx.append("rcv3")
        
        # Should keep most recent 2
        assert len(ctx.receivers) == 2
        assert ctx.receivers == ("rcv2", "rcv3")
    
    def test_string_representation_with_receivers(self):
        """Test __str__ with receivers."""
        ctx = ReceiverContext((), 2)
        ctx = ctx.append("test.py:10:0:obj:Receiver1")
        ctx = ctx.append("test.py:20:0:obj:Receiver2")
        
        ctx_str = str(ctx)
        assert "<rcv:" in ctx_str
        assert ">" in ctx_str
        assert "," in ctx_str
    
    def test_equality(self):
        """Test context equality."""
        ctx1 = ReceiverContext(("rcv1",), 2)
        ctx2 = ReceiverContext(("rcv1",), 2)
        ctx3 = ReceiverContext(("rcv1",), 1)  # Different depth
        ctx4 = ReceiverContext(("rcv2",), 2)  # Different receiver
        
        assert ctx1 == ctx2
        assert ctx1 != ctx3
        assert ctx1 != ctx4


class TestHybridContext:
    """Tests for HybridContext (call-string + object sensitivity)."""
    
    def test_empty_context_creation(self):
        """Test creating empty hybrid context."""
        ctx = HybridContext((), (), 1, 1)
        assert ctx.is_empty()
        assert ctx.call_k == 1
        assert ctx.obj_depth == 1
    
    def test_string_representation_empty(self):
        """Test __str__ for empty context."""
        ctx = HybridContext((), (), 1, 1)
        ctx_str = str(ctx)
        assert "[]" in ctx_str
        assert "<>" in ctx_str
    
    def test_append_call_to_empty(self, call_site_factory):
        """Test appending call site to empty context."""
        ctx = HybridContext((), (), 1, 1)
        cs = call_site_factory("caller")
        new_ctx = ctx.append_call(cs)
        
        assert len(new_ctx.call_sites) == 1
        assert new_ctx.call_sites[0] == cs
        assert len(new_ctx.alloc_sites) == 0  # Object part unchanged
    
    def test_append_object_to_empty(self):
        """Test appending allocation site to empty context."""
        ctx = HybridContext((), (), 1, 1)
        new_ctx = ctx.append_object("alloc1")
        
        assert len(new_ctx.alloc_sites) == 1
        assert new_ctx.alloc_sites[0] == "alloc1"
        assert len(new_ctx.call_sites) == 0  # Call part unchanged
    
    def test_append_both_call_and_object(self, call_site_factory):
        """Test appending both call and object components."""
        ctx = HybridContext((), (), 1, 1)
        cs = call_site_factory("caller")
        
        ctx = ctx.append_call(cs)
        ctx = ctx.append_object("alloc1")
        
        assert len(ctx.call_sites) == 1
        assert len(ctx.alloc_sites) == 1
    
    def test_call_truncation_at_k_limit(self, call_site_factory):
        """Test call-string truncation at k limit."""
        ctx = HybridContext((), (), 1, 2)
        cs1 = call_site_factory("caller1")
        cs2 = call_site_factory("caller2")
        
        ctx = ctx.append_call(cs1)
        ctx = ctx.append_call(cs2)
        
        # Should keep only most recent (k=1)
        assert len(ctx.call_sites) == 1
        assert ctx.call_sites[0] == cs2
    
    def test_object_truncation_at_depth_limit(self):
        """Test object context truncation at depth limit."""
        ctx = HybridContext((), (), 2, 1)
        
        ctx = ctx.append_object("alloc1")
        ctx = ctx.append_object("alloc2")
        
        # Should keep only most recent (depth=1)
        assert len(ctx.alloc_sites) == 1
        assert ctx.alloc_sites[0] == "alloc2"
    
    def test_string_representation_with_both(self, call_site_factory):
        """Test __str__ with both call and object components."""
        ctx = HybridContext((), (), 1, 1)
        cs = call_site_factory("caller")
        
        ctx = ctx.append_call(cs)
        ctx = ctx.append_object("alloc1")
        
        ctx_str = str(ctx)
        assert "[" in ctx_str
        assert "]" in ctx_str
        assert "<" in ctx_str
        assert ">" in ctx_str
    
    def test_equality(self, call_site_factory):
        """Test context equality."""
        cs = call_site_factory("caller")
        
        ctx1 = HybridContext((cs,), ("alloc1",), 1, 1)
        ctx2 = HybridContext((cs,), ("alloc1",), 1, 1)
        ctx3 = HybridContext((cs,), ("alloc1",), 2, 1)  # Different call_k
        ctx4 = HybridContext((cs,), ("alloc2",), 1, 1)  # Different alloc
        
        assert ctx1 == ctx2
        assert ctx1 != ctx3
        assert ctx1 != ctx4
    
    def test_hashable(self, call_site_factory):
        """Test that context can be used in sets/dicts."""
        cs = call_site_factory("caller")
        
        ctx1 = HybridContext((cs,), ("alloc1",), 1, 1)
        ctx2 = HybridContext((cs,), ("alloc1",), 1, 1)
        
        contexts = {ctx1, ctx2}
        assert len(contexts) == 1
    
    def test_is_empty_checks_both_components(self):
        """Test is_empty checks both call and object components."""
        ctx1 = HybridContext((), (), 1, 1)
        assert ctx1.is_empty()
        
        ctx2 = HybridContext((), ("alloc1",), 1, 1)
        assert not ctx2.is_empty()
        
        cs = CallSite("test", "func")
        ctx3 = HybridContext((cs,), (), 1, 1)
        assert not ctx3.is_empty()


class TestContextUsagePatterns:
    """Test realistic context usage patterns."""
    
    def test_call_string_sensitivity_chain(self, call_site_factory):
        """Test modeling call chain with k-CFA."""
        ctx = CallStringContext((), 2)
        
        # main -> foo -> bar
        cs_main_to_foo = call_site_factory("main", "bb0")
        cs_foo_to_bar = call_site_factory("foo", "bb1")
        
        ctx = ctx.append(cs_main_to_foo)
        ctx = ctx.append(cs_foo_to_bar)
        
        assert len(ctx) == 2
        assert ctx.call_sites[0] == cs_main_to_foo
        assert ctx.call_sites[1] == cs_foo_to_bar
    
    def test_object_sensitivity_allocation_chain(self):
        """Test modeling allocation chain with object sensitivity."""
        ctx = ObjectContext((), 2)
        
        # Builder pattern: builder.build().configure()
        builder_alloc = "test.py:10:0:obj:Builder"
        obj_alloc = "test.py:15:0:obj:Product"
        
        ctx = ctx.append(builder_alloc)
        ctx = ctx.append(obj_alloc)
        
        assert len(ctx.alloc_sites) == 2
    
    def test_type_sensitivity_inheritance_chain(self):
        """Test modeling type chain with type sensitivity."""
        ctx = TypeContext((), 3)
        
        # Receiver type chain through inheritance
        ctx = ctx.append("Base")
        ctx = ctx.append("Derived")
        ctx = ctx.append("FinalClass")
        
        assert len(ctx.types) == 3
        assert ctx.types == ("Base", "Derived", "FinalClass")
    
    def test_context_comparison_across_types(self, call_site_factory):
        """Test that different context types are not equal."""
        cs = call_site_factory("caller")
        
        call_ctx = CallStringContext((cs,), 2)
        obj_ctx = ObjectContext(("alloc1",), 2)
        
        # Different types should not be equal
        assert call_ctx != obj_ctx

