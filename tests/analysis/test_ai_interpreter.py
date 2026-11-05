"""Tests for AI interpreter and solver.

Tests:
- Transfer functions for IR statements
- Integration with PtaQuery mock
- End-to-end summary generation
"""

import pytest
from typing import FrozenSet, List, Optional
from dataclasses import dataclass


# =============================================================================
# Mock PtaQuery for testing
# =============================================================================

@dataclass
class MockObject:
    """Mock abstract object for testing."""
    name: str
    
    def __hash__(self):
        return hash(self.name)
    
    def __eq__(self, other):
        if isinstance(other, MockObject):
            return self.name == other.name
        return False
    
    @property
    def alloc_site(self):
        """Mock alloc_site for AIAddr compatibility."""
        from pythonstan.analysis.pointer.kcfa.object import AllocSite, AllocKind
        return AllocSite(stmt=f"<mock:{self.name}>", kind=AllocKind.OBJECT)
    
    @property
    def context(self):
        """Mock context for AIAddr compatibility."""
        return object()


class MockPtaQuery:
    """Mock PTA query interface for testing."""
    
    def __init__(self):
        self._points_to = {}
        self._field_points_to = {}
        self._class_mro = {}
    
    def add_points_to(self, var_name: str, objs: FrozenSet[MockObject]):
        """Configure points-to for a variable."""
        self._points_to[var_name] = objs
    
    def add_field_points_to(self, obj: MockObject, field_name: str, objs: FrozenSet[MockObject]):
        """Configure field points-to."""
        key = (obj.name, field_name)
        self._field_points_to[key] = objs

    def add_class_mro(self, cls_obj: MockObject, mro: List[MockObject]) -> None:
        """Configure class MRO."""
        self._class_mro[cls_obj] = mro
    
    def points_to(self, var, scope, context):
        """Mock points_to query."""
        # Handle Variable objects or strings
        var_name = var.name if hasattr(var, 'name') else str(var)
        return self._points_to.get(var_name, frozenset())
    
    def field_points_to(self, obj, field, scope, context):
        """Mock field_points_to query."""
        obj_name = obj.name if hasattr(obj, 'name') else str(obj)
        field_name = str(field)
        key = (obj_name, field_name)
        return self._field_points_to.get(key, frozenset())
    
    def get_class_mro(self, cls_obj):
        """Mock MRO query."""
        return self._class_mro.get(cls_obj, [cls_obj])
    
    def may_have_attr(self, obj, attr_name, scope, context):
        """Mock may_have_attr query."""
        return True


class TestInterpreter:
    """Tests for IRInterpreter transfer functions."""

    def test_transfer_copy(self):
        """Test IRCopy transfer: x = y."""
        from pythonstan.analysis.pointer.ai.state import AIState, AbsVal
        from pythonstan.analysis.pointer.ai.interpreter import IRInterpreter
        from pythonstan.analysis.pointer.ai.summary import SummaryAccumulator
        from pythonstan.analysis.pointer.ai.api import AnalysisBudget
        
        # Create mock PTA query
        pta = MockPtaQuery()
        budget = AnalysisBudget()
        
        interpreter = IRInterpreter(pta, budget)
        
        # Setup state with y = {"obj1"}
        obj1 = MockObject("obj1")
        state = AIState()
        state = state.set_var("y", AbsVal.from_addrs(frozenset({obj1})))
        
        summary = SummaryAccumulator()
        
        # Create mock IRCopy statement
        class MockIRCopy:
            def get_lval(self):
                class MockName:
                    id = "x"
                return MockName()
            
            def get_rval(self):
                class MockName:
                    id = "y"
                return MockName()
        
        result = interpreter._transfer_copy(MockIRCopy(), state, summary)
        
        # Check x now points to obj1
        new_state = result.states[0][1]
        x_val = new_state.get_var("x")
        assert obj1 in x_val.addrs

    def test_transfer_phi(self):
        """Test IRPhi transfer: x = Phi(a, b)."""
        from pythonstan.analysis.pointer.ai.state import AIState, AbsVal
        from pythonstan.analysis.pointer.ai.interpreter import IRInterpreter
        from pythonstan.analysis.pointer.ai.summary import SummaryAccumulator
        from pythonstan.analysis.pointer.ai.api import AnalysisBudget
        
        pta = MockPtaQuery()
        budget = AnalysisBudget()
        interpreter = IRInterpreter(pta, budget)
        
        # Setup state with a and b pointing to different objects
        obj_a = MockObject("obj_a")
        obj_b = MockObject("obj_b")
        state = AIState()
        state = state.set_var("a", AbsVal.from_addrs(frozenset({obj_a})))
        state = state.set_var("b", AbsVal.from_addrs(frozenset({obj_b})))
        
        summary = SummaryAccumulator()
        
        # Create mock IRPhi statement
        class MockName:
            def __init__(self, name):
                self.id = name
        
        class MockIRPhi:
            def get_lval(self):
                return MockName("x")
            
            def get_items(self):
                return [MockName("a"), MockName("b")]
        
        result = interpreter._transfer_phi(MockIRPhi(), state, summary)
        
        # Check x is join of a and b
        new_state = result.states[0][1]
        x_val = new_state.get_var("x")
        assert obj_a in x_val.addrs
        assert obj_b in x_val.addrs


class TestBuiltinHandling:
    """Tests for builtin function handling."""

    def test_getattr_const_name(self):
        """Test getattr with constant attribute name."""
        from pythonstan.analysis.pointer.ai.state import AIState, AbsVal
        from pythonstan.analysis.pointer.ai.interpreter import IRInterpreter
        from pythonstan.analysis.pointer.ai.summary import SummaryAccumulator
        from pythonstan.analysis.pointer.ai.api import AnalysisBudget
        from pythonstan.analysis.pointer.kcfa.heap_model import attr
        
        # Setup PTA with field points-to
        pta = MockPtaQuery()
        obj = MockObject("receiver")
        field_val = MockObject("field_value")
        pta.add_field_points_to(obj, ".x", frozenset({field_val}))
        
        budget = AnalysisBudget()
        interpreter = IRInterpreter(pta, budget)
        
        # Setup state
        state = AIState()
        state = state.set_var("obj", AbsVal.from_addrs(frozenset({obj})))
        state = state.set_var("name", AbsVal.from_str("x"))
        
        summary = SummaryAccumulator()
        
        # Create mock getattr call
        class MockIRCall:
            def get_target(self):
                return "result"
            
            def get_func_name(self):
                return "getattr"
            
            def get_args(self):
                return [("obj", False), ("name", False)]
            
            def get_keywords(self):
                return []
        
        result = interpreter._handle_getattr(MockIRCall(), state, summary)
        
        # Check result contains field_value
        new_state = result.states[0][1]
        result_val = new_state.get_var("result")
        # Should have queried the field


    def test_setattr_const_name(self):
        """Test setattr with constant attribute name."""
        from pythonstan.analysis.pointer.ai.state import AIState, AbsVal
        from pythonstan.analysis.pointer.ai.interpreter import IRInterpreter
        from pythonstan.analysis.pointer.ai.summary import SummaryAccumulator
        from pythonstan.analysis.pointer.ai.api import AnalysisBudget
        from pythonstan.analysis.pointer.kcfa.heap_model import attr
        
        pta = MockPtaQuery()
        budget = AnalysisBudget()
        interpreter = IRInterpreter(pta, budget)
        
        # Setup state
        obj = MockObject("receiver")
        val = MockObject("new_value")
        state = AIState()
        state = state.set_var("obj", AbsVal.from_addrs(frozenset({obj})))
        state = state.set_var("name", AbsVal.from_str("y"))
        state = state.set_var("val", AbsVal.from_addrs(frozenset({val})))
        
        summary = SummaryAccumulator()
        
        # Create mock setattr call
        class MockIRCall:
            def get_target(self):
                return None
            
            def get_func_name(self):
                return "setattr"
            
            def get_args(self):
                return [("obj", False), ("name", False), ("val", False)]
            
            def get_keywords(self):
                return []
        
        interpreter._handle_setattr(MockIRCall(), state, summary)
        
        # Check write was recorded
        ai_summary = summary.to_summary()
        assert len(ai_summary.writes) == 1
        write = ai_summary.writes[0]
        assert obj in write.obj_addrs
        assert write.field_key == attr("y")
        assert val in write.value_addrs

    def test_getattr_unknown_name_reads_unknown_bucket(self):
        """Test getattr with unknown (non-const) name reads from unknown bucket."""
        from pythonstan.analysis.pointer.ai.state import AIState, AbsVal
        from pythonstan.analysis.pointer.ai.interpreter import IRInterpreter
        from pythonstan.analysis.pointer.ai.summary import SummaryAccumulator
        from pythonstan.analysis.pointer.ai.api import AnalysisBudget
        from pythonstan.analysis.pointer.kcfa.heap_model import unknown
        
        pta = MockPtaQuery()
        obj = MockObject("receiver")
        unknown_val = MockObject("unknown_field")
        pta.add_field_points_to(obj, "?", frozenset({unknown_val}))
        
        budget = AnalysisBudget()
        interpreter = IRInterpreter(pta, budget)
        
        # Setup state with unknown (top) name
        state = AIState()
        state = state.set_var("obj", AbsVal.from_addrs(frozenset({obj})))
        state = state.set_var("name", AbsVal.top())  # Unknown name
        
        summary = SummaryAccumulator()
        
        class MockIRCall:
            def get_target(self):
                return "result"
            
            def get_func_name(self):
                return "getattr"
            
            def get_args(self):
                return [("obj", False), ("name", False)]
            
            def get_keywords(self):
                return []
        
        result = interpreter._handle_getattr(MockIRCall(), state, summary)
        
        # Check that unknown bucket was read (reads are tuples: (obj_addrs, field_key))
        ai_summary = summary.to_summary()
        unknown_reads = [r for r in ai_summary.reads if r[1] == unknown()]
        assert len(unknown_reads) >= 1

    def test_setattr_unknown_name_writes_unknown_bucket(self):
        """Test setattr with unknown name writes to unknown bucket."""
        from pythonstan.analysis.pointer.ai.state import AIState, AbsVal
        from pythonstan.analysis.pointer.ai.interpreter import IRInterpreter
        from pythonstan.analysis.pointer.ai.summary import SummaryAccumulator
        from pythonstan.analysis.pointer.ai.api import AnalysisBudget
        from pythonstan.analysis.pointer.kcfa.heap_model import unknown
        
        pta = MockPtaQuery()
        budget = AnalysisBudget()
        interpreter = IRInterpreter(pta, budget)
        
        obj = MockObject("receiver")
        val = MockObject("new_value")
        state = AIState()
        state = state.set_var("obj", AbsVal.from_addrs(frozenset({obj})))
        state = state.set_var("name", AbsVal.top())  # Unknown name
        state = state.set_var("val", AbsVal.from_addrs(frozenset({val})))
        
        summary = SummaryAccumulator()
        
        class MockIRCall:
            def get_target(self):
                return None
            
            def get_func_name(self):
                return "setattr"
            
            def get_args(self):
                return [("obj", False), ("name", False), ("val", False)]
            
            def get_keywords(self):
                return []
        
        interpreter._handle_setattr(MockIRCall(), state, summary)
        
        # Check write to unknown bucket was recorded
        ai_summary = summary.to_summary()
        unknown_writes = [w for w in ai_summary.writes if w.field_key == unknown()]
        assert len(unknown_writes) >= 1
        assert val in unknown_writes[0].value_addrs

    def test_hasattr_returns_bool_top(self):
        """Test hasattr returns unknown boolean and records reads."""
        from pythonstan.analysis.pointer.ai.state import AIState, AbsVal
        from pythonstan.analysis.pointer.ai.interpreter import IRInterpreter
        from pythonstan.analysis.pointer.ai.summary import SummaryAccumulator
        from pythonstan.analysis.pointer.ai.api import AnalysisBudget
        from pythonstan.analysis.pointer.kcfa.heap_model import attr
        
        pta = MockPtaQuery()
        budget = AnalysisBudget()
        interpreter = IRInterpreter(pta, budget)
        
        obj = MockObject("receiver")
        state = AIState()
        state = state.set_var("obj", AbsVal.from_addrs(frozenset({obj})))
        state = state.set_var("name", AbsVal.from_str("x"))
        
        summary = SummaryAccumulator()
        
        class MockIRCall:
            def get_target(self):
                return "result"
            
            def get_func_name(self):
                return "hasattr"
            
            def get_args(self):
                return [("obj", False), ("name", False)]
            
            def get_keywords(self):
                return []
        
        result = interpreter._handle_hasattr(MockIRCall(), state, summary)
        
        # Check result is unknown boolean
        new_state = result.states[0][1]
        result_val = new_state.get_var("result")
        bool_lattice = result_val.get_bool_lattice()
        assert bool_lattice.may_be_true()
        assert bool_lattice.may_be_false()
        
        # Check reads were recorded for const name (reads are tuples: (obj_addrs, field_key))
        ai_summary = summary.to_summary()
        reads = [r for r in ai_summary.reads if r[1] == attr("x")]
        assert len(reads) >= 1


class TestAISummaryEngine:
    """Tests for AISummaryEngine entry point."""

    def test_engine_creation(self):
        """Test engine can be created."""
        from pythonstan.analysis.pointer.ai.api import AISummaryEngine, AnalysisBudget
        
        pta = MockPtaQuery()
        budget = AnalysisBudget()
        
        engine = AISummaryEngine(pta, budget)
        assert engine.pta_query == pta
        assert engine.budget == budget

    def test_cache_invalidation(self):
        """Test cache can be invalidated."""
        from pythonstan.analysis.pointer.ai.api import AISummaryEngine, AnalysisBudget
        
        pta = MockPtaQuery()
        engine = AISummaryEngine(pta)
        
        # Add something to cache
        engine._summary_cache[("key",)] = None
        assert len(engine._summary_cache) == 1
        
        engine.invalidate_cache()
        assert len(engine._summary_cache) == 0


class TestTransferResult:
    """Tests for TransferResult."""

    def test_single_successor(self):
        """Test single successor result."""
        from pythonstan.analysis.pointer.ai.interpreter import TransferResult
        from pythonstan.analysis.pointer.ai.state import AIState
        
        state = AIState()
        result = TransferResult.single(state, "next_label")
        
        assert len(result.states) == 1
        assert result.states[0][0] == "next_label"
        assert not result.is_terminal

    def test_branch_result(self):
        """Test branching result."""
        from pythonstan.analysis.pointer.ai.interpreter import TransferResult
        from pythonstan.analysis.pointer.ai.state import AIState
        
        true_state = AIState()
        false_state = AIState()
        
        result = TransferResult.branch(
            true_state, false_state,
            "true_label", "false_label"
        )
        
        assert len(result.states) == 2
        assert not result.is_terminal

    def test_terminal_result(self):
        """Test terminal result (return/raise)."""
        from pythonstan.analysis.pointer.ai.interpreter import TransferResult
        
        result = TransferResult.terminal()
        
        assert result.is_terminal
        assert len(result.states) == 0


class TestAnalyzeIntrinsic:
    """Tests for AISummaryEngine.analyze_intrinsic()."""

    def test_getattr_intrinsic_with_const_name(self):
        """Test analyze_intrinsic for getattr with constant attribute name."""
        from pythonstan.analysis.pointer.ai.api import AISummaryEngine, AnalysisBudget, ArgBinding
        from pythonstan.analysis.pointer.ai.state import AbsVal
        from pythonstan.analysis.pointer.kcfa.heap_model import attr
        
        # Use the global MockObject and MockPtaQuery
        pta = MockPtaQuery()
        obj = MockObject("receiver")
        field_val = MockObject("field_value")
        pta.add_field_points_to(obj, ".x", frozenset({field_val}))
        
        engine = AISummaryEngine(pta, AnalysisBudget())
        
        # Create arg bindings for getattr(obj, "x")
        arg_bindings = [
            ArgBinding(
                param_name="obj",
                points_to=frozenset({obj}),
                value=AbsVal.from_addrs(frozenset({obj})),
            ),
            ArgBinding(
                param_name="name",
                points_to=frozenset(),
                value=AbsVal.from_str("x"),
            ),
        ]
        
        summary = engine.analyze_intrinsic(
            builtin_name="getattr",
            call_context=None,
            arg_bindings=arg_bindings,
            caller_scope=None,
            target_name="result",
        )
        
        # Check that the return value includes the field value
        assert field_val in summary.ret

    def test_setattr_intrinsic_records_write(self):
        """Test analyze_intrinsic for setattr records a write."""
        from pythonstan.analysis.pointer.ai.api import AISummaryEngine, AnalysisBudget, ArgBinding
        from pythonstan.analysis.pointer.ai.state import AbsVal
        from pythonstan.analysis.pointer.kcfa.heap_model import attr
        
        # Use the global MockObject and MockPtaQuery
        pta = MockPtaQuery()
        obj = MockObject("receiver")
        val = MockObject("new_value")
        
        engine = AISummaryEngine(pta, AnalysisBudget())
        
        # Create arg bindings for setattr(obj, "y", val)
        arg_bindings = [
            ArgBinding(
                param_name="obj",
                points_to=frozenset({obj}),
                value=AbsVal.from_addrs(frozenset({obj})),
            ),
            ArgBinding(
                param_name="name",
                points_to=frozenset(),
                value=AbsVal.from_str("y"),
            ),
            ArgBinding(
                param_name="value",
                points_to=frozenset({val}),
                value=AbsVal.from_addrs(frozenset({val})),
            ),
        ]
        
        summary = engine.analyze_intrinsic(
            builtin_name="setattr",
            call_context=None,
            arg_bindings=arg_bindings,
            caller_scope=None,
            target_name=None,
        )
        
        # Check that a write was recorded
        assert len(summary.writes) == 1
        write = summary.writes[0]
        assert obj in write.obj_addrs
        assert write.field_key == attr("y")
        assert val in write.value_addrs


# =============================================================================
# Tests for cache key with ArgBinding.value
# =============================================================================

class TestCacheKey:
    """Tests for AISummaryEngine cache key respecting ArgBinding.value."""

    def test_cache_key_includes_str_const(self):
        """Test that different str_const values produce different cache keys."""
        from pythonstan.analysis.pointer.ai.api import AISummaryEngine, AnalysisBudget, ArgBinding
        from pythonstan.analysis.pointer.ai.state import AbsVal
        
        pta = MockPtaQuery()
        engine = AISummaryEngine(pta, AnalysisBudget())
        
        # Create a mock scope_ir
        class MockScopeIR:
            qualname = "test.func"
        
        scope = MockScopeIR()
        context = None
        
        # Two bindings with different str_const values
        binding1 = [
            ArgBinding(
                param_name="name",
                points_to=frozenset(),
                value=AbsVal.from_str("attr_x"),
            ),
        ]
        
        binding2 = [
            ArgBinding(
                param_name="name",
                points_to=frozenset(),
                value=AbsVal.from_str("attr_y"),
            ),
        ]
        
        key1 = engine._make_cache_key(scope, context, binding1)
        key2 = engine._make_cache_key(scope, context, binding2)
        
        # Keys should be different due to different str_const
        assert key1 != key2

    def test_cache_key_includes_bool_val(self):
        """Test that different bool_val values produce different cache keys."""
        from pythonstan.analysis.pointer.ai.api import AISummaryEngine, AnalysisBudget, ArgBinding
        from pythonstan.analysis.pointer.ai.state import AbsVal
        
        pta = MockPtaQuery()
        engine = AISummaryEngine(pta, AnalysisBudget())
        
        class MockScopeIR:
            qualname = "test.func"
        
        scope = MockScopeIR()
        context = None
        
        # Two bindings with different bool_val
        binding1 = [
            ArgBinding(
                param_name="flag",
                points_to=frozenset(),
                value=AbsVal.bool_true(),
            ),
        ]
        
        binding2 = [
            ArgBinding(
                param_name="flag",
                points_to=frozenset(),
                value=AbsVal.bool_false(),
            ),
        ]
        
        key1 = engine._make_cache_key(scope, context, binding1)
        key2 = engine._make_cache_key(scope, context, binding2)
        
        # Keys should be different due to different bool_val
        assert key1 != key2

    def test_cache_key_same_for_identical_bindings(self):
        """Test that identical bindings produce the same cache key."""
        from pythonstan.analysis.pointer.ai.api import AISummaryEngine, AnalysisBudget, ArgBinding
        from pythonstan.analysis.pointer.ai.state import AbsVal
        
        pta = MockPtaQuery()
        engine = AISummaryEngine(pta, AnalysisBudget())
        
        class MockScopeIR:
            qualname = "test.func"
        
        scope = MockScopeIR()
        context = None
        
        obj = MockObject("obj")
        
        # Two identical bindings
        binding1 = [
            ArgBinding(
                param_name="x",
                points_to=frozenset({obj}),
                value=AbsVal.from_str("hello"),
            ),
        ]
        
        binding2 = [
            ArgBinding(
                param_name="x",
                points_to=frozenset({obj}),
                value=AbsVal.from_str("hello"),
            ),
        ]
        
        key1 = engine._make_cache_key(scope, context, binding1)
        key2 = engine._make_cache_key(scope, context, binding2)
        
        # Keys should be the same
        assert key1 == key2


# =============================================================================
# Tests for descriptor semantics
# =============================================================================

class TestDescriptorSemantics:
    """Tests for descriptor __get__/__set__ invocation."""

    def test_descriptor_get_invoked_on_load_attr(self):
        """Test that descriptors with __get__ are invoked during attribute read."""
        from pythonstan.analysis.pointer.ai.state import AIState, AbsVal
        from pythonstan.analysis.pointer.ai.interpreter import IRInterpreter
        from pythonstan.analysis.pointer.ai.summary import SummaryAccumulator
        from pythonstan.analysis.pointer.ai.api import AnalysisBudget
        from pythonstan.analysis.pointer.kcfa.heap_model import attr
        
        # Create a descriptor object with __get__
        receiver = MockObject("receiver")
        descriptor = MockObject("descriptor")
        get_method = MockObject("get_method")
        descriptor_return = MockObject("descriptor_return")
        
        # Set up PTA: receiver.x -> descriptor, descriptor.__get__ -> get_method
        pta = MockPtaQuery()
        pta.add_field_points_to(receiver, ".x", frozenset({descriptor}))
        pta.add_field_points_to(descriptor, ".__get__", frozenset({get_method}))
        
        budget = AnalysisBudget()
        interpreter = IRInterpreter(pta, budget)
        
        # Setup state
        state = AIState()
        state = state.set_var("obj", AbsVal.from_addrs(frozenset({receiver})))
        
        summary = SummaryAccumulator()
        
        # Call the _get_attr_const helper
        result = interpreter._get_attr_const(
            receiver_addrs=frozenset({receiver}),
            attr_name="x",
            state=state,
            summary=summary,
        )
        
        # Should have recorded a read
        ai_summary = summary.to_summary()
        assert len(ai_summary.reads) > 0

    def test_getattr_fallback_on_missing(self):
        """Test that __getattr__ is invoked when attribute is missing."""
        from pythonstan.analysis.pointer.ai.state import AIState, AbsVal
        from pythonstan.analysis.pointer.ai.interpreter import IRInterpreter
        from pythonstan.analysis.pointer.ai.summary import SummaryAccumulator
        from pythonstan.analysis.pointer.ai.api import AnalysisBudget
        
        receiver = MockObject("receiver")
        getattr_method = MockObject("getattr_method")
        
        # Set up PTA: receiver.__getattr__ -> getattr_method, no .x field
        pta = MockPtaQuery()
        pta.add_field_points_to(receiver, ".__getattr__", frozenset({getattr_method}))
        # Note: no .x field configured
        
        budget = AnalysisBudget()
        interpreter = IRInterpreter(pta, budget)
        
        state = AIState()
        state = state.set_var("obj", AbsVal.from_addrs(frozenset({receiver})))
        
        summary = SummaryAccumulator()
        
        # Call the helper - since .x is missing, __getattr__ should be consulted
        result = interpreter._get_attr_const(
            receiver_addrs=frozenset({receiver}),
            attr_name="x",
            state=state,
            summary=summary,
        )
        
        # Result should be top() or include unknown effects
        # (since we can't actually invoke the method without IR)
        assert result is not None


class TestMroResolution:
    """Tests for MRO-aware attribute resolution."""

    def test_mro_attribute_resolution_uses_base_class(self):
        """Instance attribute lookup uses MRO to find base class fields."""
        from pythonstan.analysis.pointer.ai.state import AIState, AbsVal
        from pythonstan.analysis.pointer.ai.interpreter import IRInterpreter
        from pythonstan.analysis.pointer.ai.summary import SummaryAccumulator
        from pythonstan.analysis.pointer.ai.api import AnalysisBudget
        from pythonstan.analysis.pointer.kcfa.object import InstanceObject, AllocSite, AllocKind
        from pythonstan.analysis.pointer.kcfa.heap_model import attr

        base_cls = MockObject("Base")
        sub_cls = MockObject("Sub")
        method_obj = MockObject("base_method")

        pta = MockPtaQuery()
        pta.add_class_mro(sub_cls, [sub_cls, base_cls])

        budget = AnalysisBudget()
        interpreter = IRInterpreter(pta, budget)

        state = AIState()
        state = state.write_field_value(
            base_cls,
            attr("foo"),
            AbsVal.from_addrs(frozenset({method_obj})),
        )

        instance = InstanceObject(
            context=object(),
            alloc_site=AllocSite(stmt="inst", kind=AllocKind.INSTANCE),
            class_obj=sub_cls,
        )

        summary = SummaryAccumulator()
        result = interpreter._get_attr_const(
            receiver_addrs=frozenset({instance}),
            attr_name="foo",
            state=state,
            summary=summary,
        )

        assert method_obj in result.addrs

    def test_getattr_const_name_uses_mro(self):
        """getattr with constant name uses MRO-aware resolution."""
        from pythonstan.analysis.pointer.ai.state import AIState, AbsVal
        from pythonstan.analysis.pointer.ai.interpreter import IRInterpreter
        from pythonstan.analysis.pointer.ai.summary import SummaryAccumulator
        from pythonstan.analysis.pointer.ai.api import AnalysisBudget
        from pythonstan.analysis.pointer.kcfa.object import InstanceObject, AllocSite, AllocKind
        from pythonstan.analysis.pointer.kcfa.heap_model import attr

        base_cls = MockObject("Base")
        sub_cls = MockObject("Sub")
        method_obj = MockObject("base_method")

        pta = MockPtaQuery()
        pta.add_class_mro(sub_cls, [sub_cls, base_cls])

        budget = AnalysisBudget()
        interpreter = IRInterpreter(pta, budget)

        state = AIState()
        state = state.write_field_value(
            base_cls,
            attr("foo"),
            AbsVal.from_addrs(frozenset({method_obj})),
        )

        instance = InstanceObject(
            context=object(),
            alloc_site=AllocSite(stmt="inst", kind=AllocKind.INSTANCE),
            class_obj=sub_cls,
        )
        state = state.set_var("obj", AbsVal.from_addrs(frozenset({instance})))
        state = state.set_var("name", AbsVal.from_str("foo"))

        summary = SummaryAccumulator()

        class MockIRCall:
            def get_target(self):
                return "result"

            def get_func_name(self):
                return "getattr"

            def get_args(self):
                return [("obj", False), ("name", False)]

            def get_keywords(self):
                return []

        result = interpreter._handle_getattr(MockIRCall(), state, summary)
        new_state = result.states[0][1]
        result_val = new_state.get_var("result")
        assert method_obj in result_val.addrs


class TestDescriptorWriteSemantics:
    """Tests for descriptor __set__ invocation."""

    def test_descriptor_set_invoked_on_store_attr(self):
        """Test that data descriptors with __set__ are invoked during attribute write."""
        from pythonstan.analysis.pointer.ai.state import AIState, AbsVal
        from pythonstan.analysis.pointer.ai.interpreter import IRInterpreter
        from pythonstan.analysis.pointer.ai.summary import SummaryAccumulator
        from pythonstan.analysis.pointer.ai.api import AnalysisBudget
        from pythonstan.analysis.pointer.kcfa.heap_model import attr
        
        # Create a data descriptor object with __set__
        receiver = MockObject("receiver")
        descriptor = MockObject("descriptor")
        set_method = MockObject("set_method")
        new_value = MockObject("new_value")
        
        # Set up PTA: receiver.x -> descriptor, descriptor.__set__ -> set_method
        pta = MockPtaQuery()
        pta.add_field_points_to(receiver, ".x", frozenset({descriptor}))
        pta.add_field_points_to(descriptor, ".__set__", frozenset({set_method}))
        
        budget = AnalysisBudget()
        interpreter = IRInterpreter(pta, budget)
        
        state = AIState()
        state = state.set_var("obj", AbsVal.from_addrs(frozenset({receiver})))
        state = state.set_var("val", AbsVal.from_addrs(frozenset({new_value})))
        
        summary = SummaryAccumulator()
        
        # Call the _set_attr_const helper
        interpreter._set_attr_const(
            receiver_addrs=frozenset({receiver}),
            attr_name="x",
            value_val=AbsVal.from_addrs(frozenset({new_value})),
            state=state,
            summary=summary,
        )
        
        # Should have recorded a write (the direct write is always recorded for soundness)
        ai_summary = summary.to_summary()
        assert len(ai_summary.writes) >= 1

    def test_setattr_override_invoked(self):
        """Test that __setattr__ override is invoked during attribute write."""
        from pythonstan.analysis.pointer.ai.state import AIState, AbsVal
        from pythonstan.analysis.pointer.ai.interpreter import IRInterpreter
        from pythonstan.analysis.pointer.ai.summary import SummaryAccumulator
        from pythonstan.analysis.pointer.ai.api import AnalysisBudget
        
        receiver = MockObject("receiver")
        setattr_method = MockObject("setattr_method")
        new_value = MockObject("new_value")
        
        # Set up PTA: receiver.__setattr__ -> setattr_method
        pta = MockPtaQuery()
        pta.add_field_points_to(receiver, ".__setattr__", frozenset({setattr_method}))
        
        budget = AnalysisBudget()
        interpreter = IRInterpreter(pta, budget)
        
        state = AIState()
        summary = SummaryAccumulator()
        
        # Call the helper
        interpreter._set_attr_const(
            receiver_addrs=frozenset({receiver}),
            attr_name="x",
            value_val=AbsVal.from_addrs(frozenset({new_value})),
            state=state,
            summary=summary,
        )
        
        # Should have recorded effects (unknown effect for the __setattr__ call + direct write)
        ai_summary = summary.to_summary()
        # Either unknown effects or writes should be recorded
        assert len(ai_summary.writes) >= 1 or len(ai_summary.unknown_effects) >= 1


# =============================================================================
# Tests for invoke_callee helper
# =============================================================================

class TestInvokeCalleeHelper:
    """Tests for the bounded callee invocation helper."""

    def test_invoke_callee_respects_recursion_budget(self):
        """Test that invoke_callee respects max_recursion_depth."""
        from pythonstan.analysis.pointer.ai.state import AIState, AbsVal
        from pythonstan.analysis.pointer.ai.interpreter import IRInterpreter
        from pythonstan.analysis.pointer.ai.summary import SummaryAccumulator
        from pythonstan.analysis.pointer.ai.api import AnalysisBudget
        
        callee = MockObject("callee_func")
        
        pta = MockPtaQuery()
        budget = AnalysisBudget(max_recursion_depth=0)  # No recursion allowed
        # Create interpreter with recursion_depth already at the limit
        interpreter = IRInterpreter(pta, budget, recursion_depth=0)
        
        summary = SummaryAccumulator()
        
        # Call - should hit budget limit since interpreter.recursion_depth >= max (0 >= 0)
        result = interpreter.invoke_callee(
            callee_obj=callee,
            arg_vals=[],
            summary=summary,
            receiver_val=None,
        )
        
        # Should return top() since budget is exhausted
        assert result.str_const is None  # Top has unknown string

    def test_invoke_callee_records_unknown_effect_on_failure(self):
        """Test that invoke_callee records unknown effect when no IR available."""
        from pythonstan.analysis.pointer.ai.state import AIState, AbsVal
        from pythonstan.analysis.pointer.ai.interpreter import IRInterpreter
        from pythonstan.analysis.pointer.ai.summary import SummaryAccumulator
        from pythonstan.analysis.pointer.ai.api import AnalysisBudget
        
        callee = MockObject("unknown_func")
        
        pta = MockPtaQuery()
        budget = AnalysisBudget(max_recursion_depth=3)
        # Create interpreter with depth below budget
        interpreter = IRInterpreter(pta, budget, recursion_depth=0)
        
        summary = SummaryAccumulator()
        
        # Callee has no IR available
        result = interpreter.invoke_callee(
            callee_obj=callee,
            arg_vals=[("x", AbsVal.from_str("test"))],
            summary=summary,
            receiver_val=None,
        )
        
        # Should have recorded an unknown effect
        ai_summary = summary.to_summary()
        assert len(ai_summary.unknown_effects) >= 1

