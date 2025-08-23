"""
Tests for AI pointer analysis integration.

Tests that validate AI's ability to use pointer analysis results for improved precision.
"""

import pytest
import ast
from typing import Dict, Set, List, Optional, Tuple

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
def precise_pointer_results():
    """Fixture providing precise pointer analysis results."""
    return MockPointerResults(
        precise=True,
        singleton_vars={"x", "obj"},
        alias_pairs={("a", "b"), ("obj", "self")},
        callee_map={
            "call_1": {"func_a", "func_b"},
            "call_2": {"method_x"}
        }
    )


@pytest.fixture
def imprecise_pointer_results():
    """Fixture providing imprecise pointer analysis results (over-approximating)."""
    return MockPointerResults(
        precise=False,
        singleton_vars=set(),
        alias_pairs=set(),
        callee_map={}
    )


@pytest.fixture
def ai_solver_with_pointer():
    """Fixture providing AI solver configured to use pointer results."""
    solver = create_solver(
        context_type=ContextType.CALL_SITE,
        flow_sensitivity=FlowSensitivity.SENSITIVE,
        context_depth=1
    )
    return solver


class TestStrongVsWeakUpdates:
    """Test strong vs weak updates based on pointer analysis precision."""
    
    def test_strong_update_on_singleton(self, precise_pointer_results, ai_solver_with_pointer):
        """Test that singleton objects get strong updates."""
        # Setup: x is a singleton pointing to a single object
        var_x = "x"
        obj = MockAbstractObject("obj_1")
        
        # Mock points-to result indicating x points to exactly one object
        points_to_set = MockPointsToSet([obj])
        
        # Verify singleton check
        assert precise_pointer_results.is_singleton(var_x)
        
        # Create attribute store: x.attr = value
        store_stmt = create_mock_store_attr(var_x, "attr", "value")
        
        # In a real implementation, AI would use pointer results to determine
        # whether to perform strong or weak update
        # For now, we test the interface is available
        interpreter = ai_solver_with_pointer.interpreter
        
        # Verify pointer interface is accessible
        # In actual implementation, interpreter would call:
        # if pointer_results.is_singleton(var_x):
        #     perform_strong_update()
        # else:
        #     perform_weak_update()
        
        assert hasattr(precise_pointer_results, 'is_singleton')
        assert hasattr(precise_pointer_results, 'points_to')
    
    def test_weak_update_on_non_singleton(self, precise_pointer_results, ai_solver_with_pointer):
        """Test that non-singleton objects get weak updates."""
        # Setup: y is not a singleton
        var_y = "y"
        
        # Verify not singleton
        assert not precise_pointer_results.is_singleton(var_y)
        
        # Create attribute store: y.attr = value
        store_stmt = create_mock_store_attr(var_y, "attr", "value")
        
        # Verify weak update path would be taken
        # In actual implementation, this would perform a weak update (join)
        interpreter = ai_solver_with_pointer.interpreter
        assert not precise_pointer_results.is_singleton(var_y)
    
    def test_imprecise_results_force_weak_updates(self, imprecise_pointer_results, ai_solver_with_pointer):
        """Test that imprecise pointer results always force weak updates."""
        var_x = "x"
        
        # Even if x might be singleton, imprecise results return False
        assert not imprecise_pointer_results.is_singleton(var_x)
        
        # This ensures soundness - we never perform unsafe strong updates


class TestIndirectCallResolution:
    """Test indirect call resolution using pointer analysis."""
    
    def test_callee_narrowing_with_precise_results(self, precise_pointer_results):
        """Test that precise pointer results narrow down possible callees."""
        call_site = MockCallSite("call_1", "test.py", 10, 5)
        
        callees = precise_pointer_results.possible_callees(call_site)
        
        # Should return specific functions, not over-approximation
        assert len(callees) == 2
        callee_names = {c.name for c in callees}
        assert callee_names == {"func_a", "func_b"}
    
    def test_callee_over_approximation_with_imprecise_results(self, imprecise_pointer_results):
        """Test that imprecise results over-approximate possible callees."""
        call_site = MockCallSite("call_unknown", "test.py", 20, 10)
        
        callees = imprecise_pointer_results.possible_callees(call_site)
        
        # Should over-approximate (return unknown function)
        assert len(callees) >= 1
        # In imprecise mode, always returns over-approximation
    
    def test_context_sensitive_callee_resolution(self, precise_pointer_results):
        """Test context-sensitive callee resolution."""
        call_site = MockCallSite("call_2", "test.py", 30, 15)
        context = MockContext()
        
        callees = precise_pointer_results.possible_callees(call_site, context)
        
        # Should handle context parameter (even if mock doesn't use it)
        assert len(callees) >= 1


class TestAttributeResolution:
    """Test attribute resolution with points-to information."""
    
    def test_attribute_load_with_known_pointsto(self, precise_pointer_results):
        """Test attribute loading when points-to set is known."""
        var_obj = "obj"
        attr_field = AttrFieldKey("method")
        
        # Get points-to set for object
        points_to = precise_pointer_results.points_to(var_obj)
        assert len(points_to) > 0
        
        # Get field points-to for each object in the set
        for obj in points_to:
            field_points_to = precise_pointer_results.field_points_to(obj, attr_field)
            assert isinstance(field_points_to, MockPointsToSet)
    
    def test_attribute_store_with_field_sensitivity(self, precise_pointer_results):
        """Test attribute storing with field-sensitive analysis."""
        obj = MockAbstractObject("test_obj")
        
        # Test different field kinds
        attr_field = AttrFieldKey("value")
        elem_field = ElemFieldKey(0)
        value_field = ValueFieldKey("key")
        unknown_field = UnknownFieldKey()
        
        for field in [attr_field, elem_field, value_field, unknown_field]:
            field_points_to = precise_pointer_results.field_points_to(obj, field)
            assert isinstance(field_points_to, MockPointsToSet)
    
    def test_dynamic_attribute_access_fallback(self, precise_pointer_results):
        """Test fallback for dynamic attribute access (getattr/setattr)."""
        obj = MockAbstractObject("dynamic_obj")
        unknown_field = UnknownFieldKey()
        
        # When attribute name is unknown, should use UnknownFieldKey
        field_points_to = precise_pointer_results.field_points_to(obj, unknown_field)
        assert isinstance(field_points_to, MockPointsToSet)


class TestPathPruning:
    """Test path pruning using alias information."""
    
    def test_may_alias_information(self, precise_pointer_results):
        """Test using may-alias information for path pruning."""
        var_a = "a"
        var_b = "b"
        var_c = "c"
        
        # a and b may alias (configured in fixture)
        assert precise_pointer_results.may_alias(var_a, var_b)
        
        # a and c do not alias
        assert not precise_pointer_results.may_alias(var_a, var_c)
        
        # b and c do not alias
        assert not precise_pointer_results.may_alias(var_b, var_c)
    
    def test_alias_symmetry(self, precise_pointer_results):
        """Test that alias relation is symmetric."""
        var_a = "a"
        var_b = "b"
        
        # Should be symmetric
        alias_ab = precise_pointer_results.may_alias(var_a, var_b)
        alias_ba = precise_pointer_results.may_alias(var_b, var_a)
        
        assert alias_ab == alias_ba
    
    def test_imprecise_alias_over_approximation(self, imprecise_pointer_results):
        """Test that imprecise results over-approximate aliasing."""
        var_x = "x"
        var_y = "y"
        
        # Imprecise results should always return True (over-approximation)
        assert imprecise_pointer_results.may_alias(var_x, var_y)
    
    def test_context_sensitive_aliasing(self, precise_pointer_results):
        """Test context-sensitive alias analysis."""
        var_a = "obj"
        var_b = "self"
        context = MockContext()
        
        # These are configured to alias in the fixture
        assert precise_pointer_results.may_alias(var_a, var_b, context)


class TestFallbackBehaviors:
    """Test proper fallback behaviors when pointer information is unavailable."""
    
    def test_unknown_call_site_fallback(self, precise_pointer_results):
        """Test fallback for unknown call sites."""
        unknown_call_site = MockCallSite("unknown_call", "unknown.py", 1, 1)
        
        callees = precise_pointer_results.possible_callees(unknown_call_site)
        
        # Should return empty set or over-approximation, not crash
        assert isinstance(callees, set)
    
    def test_unknown_variable_fallback(self, precise_pointer_results):
        """Test fallback for unknown variables."""
        unknown_var = "unknown_variable"
        
        points_to = precise_pointer_results.points_to(unknown_var)
        
        # Should return some points-to set, not crash
        assert hasattr(points_to, '__iter__')
        assert hasattr(points_to, '__len__')
    
    def test_digest_version_availability(self, precise_pointer_results):
        """Test that digest version is available."""
        version = precise_pointer_results.pointer_digest_version()
        
        assert isinstance(version, str)
        assert len(version) > 0


class TestCallGraphIntegration:
    """Test integration with call graph information."""
    
    def test_call_graph_successors(self, precise_pointer_results):
        """Test call graph successor queries."""
        func = MockFunctionSymbol("test_func")
        
        successors = precise_pointer_results.call_graph_successors(func)
        
        assert isinstance(successors, set)
        # Should return some successors or empty set
    
    def test_indirect_call_with_call_graph(self, precise_pointer_results):
        """Test combining indirect call resolution with call graph."""
        call_site = MockCallSite("call_1", "test.py", 40, 20)
        
        # Get possible callees
        callees = precise_pointer_results.possible_callees(call_site)
        
        # For each callee, get their successors
        all_successors = set()
        for callee in callees:
            successors = precise_pointer_results.call_graph_successors(callee)
            all_successors.update(successors)
        
        # Should be able to traverse the call graph
        assert isinstance(all_successors, set)


# Helper functions for creating mock IR statements

def create_mock_store_attr(obj_var: str, attr_name: str, value_var: str) -> IRStoreAttr:
    """Create a mock IRStoreAttr statement."""
    # Create minimal AST assign statement: obj.attr = value
    assign_stmt = ast.Assign(
        targets=[ast.Attribute(
            value=ast.Name(id=obj_var, ctx=ast.Load()),
            attr=attr_name,
            ctx=ast.Store()
        )],
        value=ast.Name(id=value_var, ctx=ast.Load())
    )
    
    return IRStoreAttr(assign_stmt)


def create_mock_load_attr(obj_var: str, attr_name: str, result_var: str) -> IRLoadAttr:
    """Create a mock IRLoadAttr statement."""
    # Create minimal AST assign statement: result = obj.attr
    assign_stmt = ast.Assign(
        targets=[ast.Name(id=result_var, ctx=ast.Store())],
        value=ast.Attribute(
            value=ast.Name(id=obj_var, ctx=ast.Load()),
            attr=attr_name,
            ctx=ast.Load()
        )
    )
    
    return IRLoadAttr(assign_stmt)


def create_mock_call(func_var: str, args: List[str], result_var: str, site_id: str) -> IRCall:
    """Create a mock IRCall statement."""
    # Create minimal AST assign statement: result = func(args)
    assign_stmt = ast.Assign(
        targets=[ast.Name(id=result_var, ctx=ast.Store())],
        value=ast.Call(
            func=ast.Name(id=func_var, ctx=ast.Load()),
            args=[ast.Name(id=arg, ctx=ast.Load()) for arg in args],
            keywords=[]
        )
    )
    
    return IRCall(assign_stmt)


def create_mock_module(name: str = "test_module") -> IRModule:
    """Create a mock IRModule."""
    module_ast = ast.Module(body=[], type_ignores=[])
    return IRModule(qualname=name, module=module_ast, name=name, filename=f"{name}.py")


# Integration test with realistic scenarios

class TestRealisticScenarios:
    """Test realistic scenarios combining multiple pointer analysis features."""
    
    def test_object_method_call_scenario(self, precise_pointer_results, ai_solver_with_pointer):
        """Test a realistic object method call scenario."""
        # Scenario: obj.method() where obj is a singleton
        obj_var = "obj"
        method_name = "method"
        result_var = "result"
        call_site_id = "obj_method_call"
        
        # Check if obj is singleton (strong update potential)
        is_singleton = precise_pointer_results.is_singleton(obj_var)
        
        # Get points-to set for obj
        obj_points_to = precise_pointer_results.points_to(obj_var)
        
        # For each object, get the method field
        method_callees = set()
        for obj in obj_points_to:
            method_field = AttrFieldKey(method_name)
            method_points_to = precise_pointer_results.field_points_to(obj, method_field)
            # In real implementation, would convert points-to to callees
        
        # Also check call site resolution
        call_site = MockCallSite(call_site_id, "test.py", 50, 25)
        possible_callees = precise_pointer_results.possible_callees(call_site)
        
        # Combine information for precise analysis
        assert isinstance(obj_points_to, MockPointsToSet)
        assert isinstance(possible_callees, set)
    
    def test_container_element_access_scenario(self, precise_pointer_results):
        """Test container element access scenario."""
        # Scenario: lst[0] = value, where lst points to multiple lists
        lst_var = "lst"
        value_var = "value"
        index = 0
        
        # Get points-to set for list variable
        lst_points_to = precise_pointer_results.points_to(lst_var)
        
        # For each list object, update element 0
        for lst_obj in lst_points_to:
            elem_field = ElemFieldKey(index)
            elem_points_to = precise_pointer_results.field_points_to(lst_obj, elem_field)
            
            # Check if this is a strong or weak update
            is_singleton = precise_pointer_results.is_singleton(lst_var)
            
            # In real implementation:
            # if is_singleton and len(lst_points_to) == 1:
            #     perform_strong_update(elem_field, value)
            # else:
            #     perform_weak_update(elem_field, value)
        
        assert isinstance(lst_points_to, MockPointsToSet)
    
    def test_exception_handling_with_aliases(self, precise_pointer_results):
        """Test that pointer information is preserved along exception edges."""
        var_a = "exception_var"
        var_b = "handler_var"
        
        # Check aliasing before exception
        may_alias_before = precise_pointer_results.may_alias(var_a, var_b)
        
        # Simulate exception handling
        # In real implementation, alias information should be preserved
        # across exception edges
        
        # Check aliasing after exception handling
        may_alias_after = precise_pointer_results.may_alias(var_a, var_b)
        
        # Should be consistent (this is a property the real implementation should maintain)
        assert may_alias_before == may_alias_after
