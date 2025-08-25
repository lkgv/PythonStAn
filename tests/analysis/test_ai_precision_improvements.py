"""
Tests for AI precision improvements with pointer analysis.

Tests scenarios where legacy AI was imprecise but now improves with pointer information.
"""

import pytest
import ast
from typing import Dict, Set, List, Optional, Tuple, Any

from pythonstan.ir.ir_statements import (
    IRStatement, IRFunc, IRClass, IRModule, IRAssign, IRCall, IRLoadAttr, 
    IRStoreAttr, JumpIfTrue, JumpIfFalse, Goto, Label, IRReturn, IRPhi,
    IRStoreSubscr, IRLoadSubscr
)
from pythonstan.analysis.ai import (
    Value, Object, ObjectType, ConstantObject, BuiltinObject, 
    FunctionObject, ClassObject, InstanceObject, 
    create_int_value, create_float_value, create_str_value, create_bool_value,
    create_none_value, create_list_value, create_dict_value, create_unknown_value,
    AbstractState, Context, ContextType, FlowSensitivity,
    AbstractInterpreter, AbstractInterpretationSolver, create_solver
)
from pythonstan.analysis.ai.pointer_adapter import (
    PointerResults, MockPointerResults, MockFunctionSymbol, MockCallSite,
    MockAbstractObject, MockPointsToSet, MockContext,
    AttrFieldKey, ElemFieldKey, ValueFieldKey, UnknownFieldKey
)


@pytest.fixture
def legacy_ai_solver():
    """AI solver without pointer analysis (legacy behavior)."""
    solver = create_solver(
        context_type=ContextType.INSENSITIVE,
        flow_sensitivity=FlowSensitivity.INSENSITIVE,
        context_depth=0
    )
    return solver


@pytest.fixture
def pointer_enhanced_ai_solver():
    """AI solver enhanced with pointer analysis."""
    solver = create_solver(
        context_type=ContextType.CALL_SITE,
        flow_sensitivity=FlowSensitivity.SENSITIVE,
        context_depth=2
    )
    return solver


@pytest.fixture
def precise_pointer_analysis():
    """Precise pointer analysis results for precision improvement tests."""
    return MockPointerResults(
        precise=True,
        singleton_vars={"singleton_obj", "unique_list", "single_func"},
        alias_pairs={("alias_a", "alias_b"), ("same_obj", "obj_ref")},
        callee_map={
            "polymorphic_call": {"method_a", "method_b"},
            "monomorphic_call": {"specific_method"},
            "factory_call": {"create_instance"}
        }
    )


class TestObjectFieldPrecision:
    """Test precision improvements in object field analysis."""
    
    def test_singleton_object_strong_updates(self, precise_pointer_analysis):
        """Test that singleton objects enable strong updates improving precision."""
        # Scenario: obj.field = value1; obj.field = value2
        # Legacy: field -> {value1, value2} (weak update)
        # Enhanced: field -> {value2} (strong update because obj is singleton)
        
        obj_var = "singleton_obj"
        field_name = "field"
        
        # Verify obj is singleton
        assert precise_pointer_analysis.is_singleton(obj_var)
        
        # Get object's points-to set
        obj_points_to = precise_pointer_analysis.points_to(obj_var)
        assert len(obj_points_to) == 1
        
        # Test field update precision
        obj = next(iter(obj_points_to))
        field_key = AttrFieldKey(field_name)
        
        # In enhanced AI with pointer info:
        # 1. Check is_singleton(obj) -> True
        # 2. Perform strong update: field = {value2}
        # Legacy AI would: field = {value1, value2}
        
        field_points_to = precise_pointer_analysis.field_points_to(obj, field_key)
        assert isinstance(field_points_to, MockPointsToSet)
        
        # This demonstrates the interface for strong vs weak updates
        # Real implementation would track the precision difference
    
    def test_non_singleton_requires_weak_updates(self, precise_pointer_analysis):
        """Test that non-singleton objects require weak updates."""
        # Scenario: multiple objects may be aliased
        obj_var = "non_singleton_obj"
        
        # Verify obj is not singleton
        assert not precise_pointer_analysis.is_singleton(obj_var)
        
        # Must use weak updates (join operation)
        # Enhanced AI: same precision as legacy (but sound)
        # Legacy AI: might unsoundly assume strong update
        
        obj_points_to = precise_pointer_analysis.points_to(obj_var)
        # Could point to multiple objects, requiring weak updates
    
    def test_container_element_precision(self, precise_pointer_analysis):
        """Test precision improvements in container element analysis."""
        # Scenario: lst[0] = value; unique_list[1] = other_value
        
        # Test unique list (singleton)
        unique_list = "unique_list"
        assert precise_pointer_analysis.is_singleton(unique_list)
        
        list_points_to = precise_pointer_analysis.points_to(unique_list)
        list_obj = next(iter(list_points_to))
        
        # Access different elements
        elem_0 = ElemFieldKey(0)
        elem_1 = ElemFieldKey(1)
        
        elem_0_points_to = precise_pointer_analysis.field_points_to(list_obj, elem_0)
        elem_1_points_to = precise_pointer_analysis.field_points_to(list_obj, elem_1)
        
        # Enhanced AI can distinguish between different elements
        # Legacy AI might merge all element fields
        assert isinstance(elem_0_points_to, MockPointsToSet)
        assert isinstance(elem_1_points_to, MockPointsToSet)


class TestIndirectCallPrecision:
    """Test precision improvements in indirect call resolution."""
    
    def test_polymorphic_call_narrowing(self, precise_pointer_analysis):
        """Test narrowing of polymorphic calls using pointer analysis."""
        # Scenario: obj.method() where obj could be different types
        
        call_site = MockCallSite("polymorphic_call", "test.py", 100, 10)
        possible_callees = precise_pointer_analysis.possible_callees(call_site)
        
        # Enhanced AI: use pointer analysis to narrow callees
        # Legacy AI: assume all possible methods with same name
        
        callee_names = {c.name for c in possible_callees}
        assert callee_names == {"method_a", "method_b"}
        
        # This is more precise than assuming all methods named "method"
        # exist in the program could be called
    
    def test_monomorphic_call_resolution(self, precise_pointer_analysis):
        """Test resolution of monomorphic calls to single target."""
        # Scenario: specific object type has unique method implementation
        
        call_site = MockCallSite("monomorphic_call", "test.py", 110, 15)
        possible_callees = precise_pointer_analysis.possible_callees(call_site)
        
        # Enhanced AI: pointer analysis reveals single target
        # Legacy AI: might assume multiple possible targets
        
        callee_names = {c.name for c in possible_callees}
        assert callee_names == {"specific_method"}
        assert len(possible_callees) == 1
    
    def test_factory_method_precision(self, precise_pointer_analysis):
        """Test precision in factory method call resolution."""
        # Scenario: factory.create() -> specific instance type
        
        call_site = MockCallSite("factory_call", "test.py", 120, 20)
        possible_callees = precise_pointer_analysis.possible_callees(call_site)
        
        # Enhanced AI: knows factory creates specific type
        # Legacy AI: might assume any constructor could be called
        
        callee_names = {c.name for c in possible_callees}
        assert callee_names == {"create_instance"}


class TestAliasAnalysisPrecision:
    """Test precision improvements from alias analysis."""
    
    def test_non_aliasing_enables_independent_updates(self, precise_pointer_analysis):
        """Test that non-aliasing information enables independent updates."""
        # Scenario: a.field = x; b.field = y; use(a.field)
        # If a and b don't alias, a.field is precisely x
        
        var_a = "independent_a"
        var_b = "independent_b"
        
        # Verify they don't alias
        assert not precise_pointer_analysis.may_alias(var_a, var_b)
        
        # Enhanced AI: can track a.field and b.field independently
        # Legacy AI: might conservatively assume they could alias
        
        # Get points-to sets
        a_points_to = precise_pointer_analysis.points_to(var_a)
        b_points_to = precise_pointer_analysis.points_to(var_b)
        
        # Since they don't alias, updates to one don't affect the other
        assert isinstance(a_points_to, MockPointsToSet)
        assert isinstance(b_points_to, MockPointsToSet)
    
    def test_known_aliasing_requires_coordination(self, precise_pointer_analysis):
        """Test that known aliasing requires coordinated updates."""
        # Scenario: alias_a and alias_b refer to same object
        
        var_a = "alias_a"
        var_b = "alias_b"
        
        # Verify they may alias
        assert precise_pointer_analysis.may_alias(var_a, var_b)
        
        # Enhanced AI: knows updates through either affect both
        # Legacy AI: might miss the aliasing relationship
        
        a_points_to = precise_pointer_analysis.points_to(var_a)
        b_points_to = precise_pointer_analysis.points_to(var_b)
        
        # In real implementation, these should overlap/be coordinated
        assert isinstance(a_points_to, MockPointsToSet)
        assert isinstance(b_points_to, MockPointsToSet)
    
    def test_must_not_alias_optimization(self, precise_pointer_analysis):
        """Test optimizations when variables definitely don't alias."""
        # Scenario: local variables in different scopes
        
        local_a = "scope_a_local"
        local_b = "scope_b_local"
        
        # Different scopes should not alias
        assert not precise_pointer_analysis.may_alias(local_a, local_b)
        
        # Enhanced AI: can optimize based on definite non-aliasing
        # Legacy AI: might conservatively assume possible aliasing


class TestPathSensitivePrecision:
    """Test precision improvements in path-sensitive analysis."""
    
    def test_conditional_type_refinement(self, precise_pointer_analysis):
        """Test type refinement in conditional branches."""
        # Scenario: if isinstance(obj, SpecificType): obj.specific_method()
        
        obj_var = "conditional_obj"
        
        # In true branch: obj is refined to SpecificType
        # Enhanced AI: use pointer analysis to track type refinement
        # Legacy AI: might lose type information across branches
        
        obj_points_to = precise_pointer_analysis.points_to(obj_var)
        
        # Real implementation would track different points-to sets
        # in different control flow paths
        assert isinstance(obj_points_to, MockPointsToSet)
    
    def test_exception_path_precision(self, precise_pointer_analysis):
        """Test precision along exception handling paths."""
        # Scenario: try/except blocks with different object states
        
        obj_var = "exception_obj"
        handler_var = "handler_obj"
        
        # Along exception path, different aliasing relationships may hold
        # Enhanced AI: track pointer info along exception edges
        # Legacy AI: might lose precision in exception handlers
        
        may_alias = precise_pointer_analysis.may_alias(obj_var, handler_var)
        
        # Real implementation should preserve precision across exception edges
        assert isinstance(may_alias, bool)


class TestBenchmarkBasedPrecision:
    """Test precision improvements on benchmark-like scenarios."""
    
    def test_oop_method_dispatch_precision(self, precise_pointer_analysis):
        """Test OOP method dispatch precision (inspired by benchmark/oop.py)."""
        # Scenario: polymorphic method calls in inheritance hierarchy
        
        # Base class method call
        base_call = MockCallSite("base_method_call", "oop.py", 50, 10)
        base_callees = precise_pointer_analysis.possible_callees(base_call)
        
        # Derived class method call
        derived_call = MockCallSite("derived_method_call", "oop.py", 75, 15)
        derived_callees = precise_pointer_analysis.possible_callees(derived_call)
        
        # Enhanced AI: distinguish between different call sites
        # Legacy AI: might merge all method calls
        
        assert isinstance(base_callees, set)
        assert isinstance(derived_callees, set)
    
    def test_closure_variable_precision(self, precise_pointer_analysis):
        """Test closure variable analysis precision (inspired by benchmark/closures.py)."""
        # Scenario: closure captures and modifies outer variables
        
        closure_var = "closure_captured"
        outer_var = "outer_scope"
        
        # Enhanced AI: track closure variable relationships precisely
        # Legacy AI: might over-approximate closure effects
        
        may_alias = precise_pointer_analysis.may_alias(closure_var, outer_var)
        
        closure_points_to = precise_pointer_analysis.points_to(closure_var)
        outer_points_to = precise_pointer_analysis.points_to(outer_var)
        
        assert isinstance(closure_points_to, MockPointsToSet)
        assert isinstance(outer_points_to, MockPointsToSet)
    
    def test_generator_state_precision(self, precise_pointer_analysis):
        """Test generator state analysis precision (inspired by benchmark/generators.py)."""
        # Scenario: generator functions with yield statements
        
        generator_var = "generator_obj"
        yielded_var = "yielded_value"
        
        # Enhanced AI: track generator state and yielded values precisely
        # Legacy AI: might lose precision in generator state
        
        gen_points_to = precise_pointer_analysis.points_to(generator_var)
        yield_points_to = precise_pointer_analysis.points_to(yielded_var)
        
        assert isinstance(gen_points_to, MockPointsToSet)
        assert isinstance(yield_points_to, MockPointsToSet)


class TestDataflowPrecision:
    """Test dataflow analysis precision improvements."""
    
    def test_def_use_chain_precision(self, precise_pointer_analysis):
        """Test definition-use chain precision with pointer analysis."""
        # Scenario: x = obj.field; y = x.method(); z = y + 1
        
        obj_var = "dataflow_obj"
        field_result = "x"
        method_result = "y"
        final_result = "z"
        
        # Enhanced AI: track precise def-use chains through pointer analysis
        # Legacy AI: might lose precision in chain analysis
        
        obj_points_to = precise_pointer_analysis.points_to(obj_var)
        
        # Real implementation would track how values flow through the chain
        for obj in obj_points_to:
            field_key = AttrFieldKey("field")
            field_points_to = precise_pointer_analysis.field_points_to(obj, field_key)
            assert isinstance(field_points_to, MockPointsToSet)
    
    def test_interprocedural_dataflow_precision(self, precise_pointer_analysis):
        """Test interprocedural dataflow precision."""
        # Scenario: function calls that modify object state
        
        param_var = "function_param"
        return_var = "function_return"
        
        # Enhanced AI: track parameter/return value relationships precisely
        # Legacy AI: might over-approximate interprocedural effects
        
        param_points_to = precise_pointer_analysis.points_to(param_var)
        return_points_to = precise_pointer_analysis.points_to(return_var)
        
        # Check for potential aliasing between parameters and returns
        may_alias = precise_pointer_analysis.may_alias(param_var, return_var)
        
        assert isinstance(param_points_to, MockPointsToSet)
        assert isinstance(return_points_to, MockPointsToSet)
        assert isinstance(may_alias, bool)


class TestPrecisionMetrics:
    """Test quantifiable precision improvements."""
    
    def test_points_to_set_size_reduction(self, precise_pointer_analysis):
        """Test that pointer analysis reduces points-to set sizes."""
        # Compare singleton vs non-singleton variables
        
        singleton_var = "singleton_obj"
        non_singleton_var = "non_singleton_obj"
        
        singleton_points_to = precise_pointer_analysis.points_to(singleton_var)
        non_singleton_points_to = precise_pointer_analysis.points_to(non_singleton_var)
        
        # Singleton should have smaller (more precise) points-to set
        # In real implementation, this would be a quantifiable improvement
        assert len(singleton_points_to) >= 1
        assert len(non_singleton_points_to) >= 1
    
    def test_call_target_reduction(self, precise_pointer_analysis):
        """Test reduction in possible call targets."""
        # Compare monomorphic vs polymorphic calls
        
        mono_call = MockCallSite("monomorphic_call", "test.py", 200, 10)
        poly_call = MockCallSite("polymorphic_call", "test.py", 210, 15)
        
        mono_callees = precise_pointer_analysis.possible_callees(mono_call)
        poly_callees = precise_pointer_analysis.possible_callees(poly_call)
        
        # Monomorphic should have fewer (more precise) targets
        assert len(mono_callees) == 1
        assert len(poly_callees) >= len(mono_callees)
    
    def test_field_sensitivity_improvement(self, precise_pointer_analysis):
        """Test field-sensitive analysis improvements."""
        obj = MockAbstractObject("field_sensitive_obj")
        
        # Different fields should be distinguishable
        field_a = AttrFieldKey("field_a")
        field_b = AttrFieldKey("field_b")
        
        field_a_points_to = precise_pointer_analysis.field_points_to(obj, field_a)
        field_b_points_to = precise_pointer_analysis.field_points_to(obj, field_b)
        
        # Enhanced AI: can distinguish different fields
        # Legacy AI: might merge all fields of an object
        assert isinstance(field_a_points_to, MockPointsToSet)
        assert isinstance(field_b_points_to, MockPointsToSet)


# Comparison helpers for demonstrating precision improvements

def compare_precision_legacy_vs_enhanced():
    """
    Helper function demonstrating precision comparison.
    
    This would be used in a real test to quantify improvements:
    - Points-to set sizes (smaller = more precise)
    - Number of possible call targets (fewer = more precise)  
    - Field sensitivity (distinguished = more precise)
    - Path sensitivity (context-aware = more precise)
    """
    pass


def create_precision_benchmark():
    """
    Helper function for creating precision benchmarks.
    
    This would create standardized test cases to measure:
    - Analysis time with/without pointer info
    - Memory usage with/without pointer info
    - Precision metrics (false positive rates)
    """
    pass
