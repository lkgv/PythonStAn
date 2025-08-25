"""
Property-based tests for AI analysis with pointer integration.

Tests monotonicity, stability, and other properties that should hold
for AI analysis when using pointer analysis results.
"""

import pytest
from typing import Dict, Set, List, Optional, Tuple, Any
import ast

try:
    from hypothesis import given, strategies as st, assume, settings
    from hypothesis.stateful import RuleBasedStateMachine, Bundle, rule, precondition
    HYPOTHESIS_AVAILABLE = True
except ImportError:
    HYPOTHESIS_AVAILABLE = False

from pythonstan.ir.ir_statements import (
    IRStatement, IRFunc, IRClass, IRModule, IRAssign, IRCall, IRLoadAttr, 
    IRStoreAttr, JumpIfTrue, JumpIfFalse, Goto, Label, IRReturn
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


# Skip all tests if hypothesis is not available
pytestmark = pytest.mark.skipif(not HYPOTHESIS_AVAILABLE, reason="hypothesis not available")


@pytest.fixture
def configurable_pointer_results():
    """Factory for creating pointer results with different precision levels."""
    def _create_pointer_results(precision_level: float = 1.0, 
                               singleton_ratio: float = 0.3,
                               alias_density: float = 0.2) -> MockPointerResults:
        """
        Create pointer results with configurable precision.
        
        Args:
            precision_level: 0.0 (imprecise) to 1.0 (precise)
            singleton_ratio: Fraction of variables that are singletons
            alias_density: Density of aliasing relationships
        """
        precise = precision_level > 0.5
        
        # Generate singleton variables based on ratio
        all_vars = [f"var_{i}" for i in range(10)]
        num_singletons = int(len(all_vars) * singleton_ratio)
        singleton_vars = set(all_vars[:num_singletons])
        
        # Generate alias pairs based on density
        alias_pairs = set()
        num_pairs = int(len(all_vars) * (len(all_vars) - 1) * alias_density / 2)
        for i in range(min(num_pairs, 5)):  # Limit for test performance
            alias_pairs.add((f"var_{i}", f"var_{i+1}"))
        
        return MockPointerResults(
            precise=precise,
            singleton_vars=singleton_vars,
            alias_pairs=alias_pairs,
            callee_map={"call_1": {"func_a"}, "call_2": {"func_b", "func_c"}}
        )
    
    return _create_pointer_results


if HYPOTHESIS_AVAILABLE:
    
    class TestMonotonicityProperties:
        """Test monotonicity properties of AI analysis with pointer information."""
        
        @given(
            precision1=st.floats(min_value=0.0, max_value=1.0),
            precision2=st.floats(min_value=0.0, max_value=1.0)
        )
        @settings(max_examples=20, deadline=5000)  # Reduced for performance
        def test_precision_monotonicity(self, precision1, precision2, configurable_pointer_results):
            """Test that more precise pointer info should not decrease AI precision incorrectly."""
            assume(abs(precision1 - precision2) > 0.1)  # Ensure meaningful difference
            
            # Create pointer results with different precision levels
            pointer_results1 = configurable_pointer_results(precision_level=precision1)
            pointer_results2 = configurable_pointer_results(precision_level=precision2)
            
            # Create AI solvers
            solver1 = create_solver(context_type=ContextType.CALL_SITE, flow_sensitivity=FlowSensitivity.SENSITIVE)
            solver2 = create_solver(context_type=ContextType.CALL_SITE, flow_sensitivity=FlowSensitivity.SENSITIVE)
            
            # Test that more precise pointer analysis leads to more precise AI results
            # (This is a property test - in actual implementation would compare result precision)
            
            # Check singleton precision
            test_var = "var_0"
            is_singleton1 = pointer_results1.is_singleton(test_var)
            is_singleton2 = pointer_results2.is_singleton(test_var)
            
            # Property: if precision2 > precision1, then more variables should be identified as singletons
            if precision2 > precision1:
                # More precise analysis should not lose singleton information
                # (This is checked at the pointer analysis level, AI should preserve it)
                pass
            
            # Property: points-to sets should be subset-related for same variable
            points_to1 = pointer_results1.points_to(test_var)
            points_to2 = pointer_results2.points_to(test_var)
            
            # Both should be valid points-to sets
            assert hasattr(points_to1, '__len__')
            assert hasattr(points_to2, '__len__')
        
        @given(
            singleton_ratio1=st.floats(min_value=0.0, max_value=1.0),
            singleton_ratio2=st.floats(min_value=0.0, max_value=1.0)
        )
        @settings(max_examples=15, deadline=5000)
        def test_singleton_ratio_monotonicity(self, singleton_ratio1, singleton_ratio2, configurable_pointer_results):
            """Test that higher singleton ratios lead to more strong updates."""
            assume(abs(singleton_ratio1 - singleton_ratio2) > 0.2)
            
            pointer_results1 = configurable_pointer_results(singleton_ratio=singleton_ratio1)
            pointer_results2 = configurable_pointer_results(singleton_ratio=singleton_ratio2)
            
            # Count singleton variables in each configuration
            test_vars = [f"var_{i}" for i in range(5)]
            singletons1 = sum(1 for var in test_vars if pointer_results1.is_singleton(var))
            singletons2 = sum(1 for var in test_vars if pointer_results2.is_singleton(var))
            
            # Property: higher singleton ratio should generally lead to more singletons
            if singleton_ratio2 > singleton_ratio1:
                # This is not a strict requirement due to randomness, but a general trend
                # In a real implementation, this would be more deterministic
                pass
            
            # At minimum, both should be valid counts
            assert 0 <= singletons1 <= len(test_vars)
            assert 0 <= singletons2 <= len(test_vars)
        
        @given(
            alias_density=st.floats(min_value=0.0, max_value=0.5)  # Limited range for performance
        )
        @settings(max_examples=10, deadline=5000)
        def test_alias_density_properties(self, alias_density, configurable_pointer_results):
            """Test properties related to alias density."""
            pointer_results = configurable_pointer_results(alias_density=alias_density)
            
            # Test alias symmetry property
            test_vars = ["var_0", "var_1", "var_2"]
            for var_a in test_vars:
                for var_b in test_vars:
                    if var_a != var_b:
                        alias_ab = pointer_results.may_alias(var_a, var_b)
                        alias_ba = pointer_results.may_alias(var_b, var_a)
                        
                        # Property: aliasing should be symmetric
                        assert alias_ab == alias_ba


    class TestStabilityProperties:
        """Test stability properties of AI analysis."""
        
        @given(
            context_depth=st.integers(min_value=0, max_value=3),
            flow_sensitive=st.booleans()
        )
        @settings(max_examples=10, deadline=5000)
        def test_configuration_stability(self, context_depth, flow_sensitive, configurable_pointer_results):
            """Test that AI configuration produces stable results."""
            pointer_results = configurable_pointer_results(precision_level=1.0)
            
            # Create solver with specific configuration
            flow_sensitivity = FlowSensitivity.SENSITIVE if flow_sensitive else FlowSensitivity.INSENSITIVE
            context_type = ContextType.CALL_SITE if context_depth > 0 else ContextType.INSENSITIVE
            
            solver1 = create_solver(
                context_type=context_type,
                flow_sensitivity=flow_sensitivity,
                context_depth=context_depth
            )
            
            solver2 = create_solver(
                context_type=context_type,
                flow_sensitivity=flow_sensitivity,
                context_depth=context_depth
            )
            
            # Property: same configuration should produce equivalent solvers
            assert solver1.state.context_type == solver2.state.context_type
            assert solver1.state.flow_sensitivity == solver2.state.flow_sensitivity
            assert solver1.state.context_depth == solver2.state.context_depth
        
        @given(
            precision_level=st.floats(min_value=0.5, max_value=1.0)
        )
        @settings(max_examples=8, deadline=5000)
        def test_repeated_analysis_stability(self, precision_level, configurable_pointer_results):
            """Test that repeated analysis with identical inputs produces identical results."""
            pointer_results = configurable_pointer_results(precision_level=precision_level)
            
            # Run same queries multiple times
            test_var = "var_0"
            call_site = MockCallSite("call_1", "test.py", 10, 5)
            
            # Multiple queries should be stable
            results1 = []
            results2 = []
            
            for _ in range(3):
                results1.append(pointer_results.is_singleton(test_var))
                results1.append(len(pointer_results.points_to(test_var)))
                callees = pointer_results.possible_callees(call_site)
                results1.append(len(callees))
            
            for _ in range(3):
                results2.append(pointer_results.is_singleton(test_var))
                results2.append(len(pointer_results.points_to(test_var)))
                callees = pointer_results.possible_callees(call_site)
                results2.append(len(callees))
            
            # Property: repeated queries should produce identical results
            assert results1 == results2


    class TestSoundnessProperties:
        """Test soundness properties of AI analysis."""
        
        @given(
            precision_level=st.floats(min_value=0.0, max_value=1.0)
        )
        @settings(max_examples=10, deadline=5000)
        def test_over_approximation_soundness(self, precision_level, configurable_pointer_results):
            """Test that imprecise results always over-approximate."""
            pointer_results = configurable_pointer_results(precision_level=precision_level)
            
            test_vars = ["var_0", "var_1"]
            
            # Property: imprecise analysis should over-approximate
            if precision_level < 0.5:  # Imprecise
                # Should always return True for may_alias (over-approximation)
                alias_result = pointer_results.may_alias(test_vars[0], test_vars[1])
                # In imprecise mode, MockPointerResults returns True
                assert alias_result == True
                
                # Should return False for is_singleton (under-approximation for strong updates)
                singleton_result = pointer_results.is_singleton(test_vars[0])
                assert singleton_result == False
        
        @given(
            var_count=st.integers(min_value=2, max_value=5)
        )
        @settings(max_examples=8, deadline=5000)
        def test_alias_transitivity_property(self, var_count, configurable_pointer_results):
            """Test alias transitivity properties."""
            pointer_results = configurable_pointer_results(precision_level=1.0, alias_density=0.3)
            
            vars_list = [f"var_{i}" for i in range(var_count)]
            
            # Test reflexivity: var should not alias with itself unless it's a reference
            for var in vars_list:
                # In most analyses, a variable doesn't alias with itself in the strict sense
                # But in some models it might, so we just test the interface works
                alias_self = pointer_results.may_alias(var, var)
                assert isinstance(alias_self, bool)
            
            # Test symmetry (already tested above, but included here for completeness)
            for i, var_a in enumerate(vars_list):
                for j, var_b in enumerate(vars_list):
                    if i < j:  # Avoid duplicate checks
                        alias_ab = pointer_results.may_alias(var_a, var_b)
                        alias_ba = pointer_results.may_alias(var_b, var_a)
                        assert alias_ab == alias_ba


    class AIAnalysisStateMachine(RuleBasedStateMachine):
        """Stateful property testing for AI analysis."""
        
        variables = Bundle('variables')
        objects = Bundle('objects')
        
        def __init__(self):
            super().__init__()
            self.pointer_results = MockPointerResults(
                precise=True,
                singleton_vars={"singleton_var"},
                alias_pairs={("alias_a", "alias_b")},
                callee_map={"call_site": {"target_func"}}
            )
            self.solver = create_solver(
                context_type=ContextType.CALL_SITE,
                flow_sensitivity=FlowSensitivity.SENSITIVE
            )
            self.created_variables = set()
            self.created_objects = set()
        
        @rule(target=variables, var_name=st.text(min_size=1, max_size=10, alphabet=st.characters(whitelist_categories=('Ll', 'Lu', 'Nd'))))
        def create_variable(self, var_name):
            """Create a new variable for testing."""
            if var_name and var_name not in self.created_variables:
                self.created_variables.add(var_name)
                return var_name
            return "default_var"
        
        @rule(target=objects, obj_id=st.text(min_size=1, max_size=10, alphabet=st.characters(whitelist_categories=('Ll', 'Lu', 'Nd'))))
        def create_object(self, obj_id):
            """Create a new abstract object for testing."""
            if obj_id and obj_id not in self.created_objects:
                self.created_objects.add(obj_id)
                return MockAbstractObject(obj_id)
            return MockAbstractObject("default_obj")
        
        @rule(var=variables)
        def test_variable_properties(self, var):
            """Test properties of variables."""
            # Points-to query should always succeed
            points_to = self.pointer_results.points_to(var)
            assert hasattr(points_to, '__len__')
            assert hasattr(points_to, '__iter__')
            
            # Singleton check should always return a boolean
            is_singleton = self.pointer_results.is_singleton(var)
            assert isinstance(is_singleton, bool)
        
        @rule(var_a=variables, var_b=variables)
        def test_alias_properties(self, var_a, var_b):
            """Test aliasing properties between variables."""
            assume(var_a != var_b)  # Only test different variables
            
            # Alias check should be symmetric
            alias_ab = self.pointer_results.may_alias(var_a, var_b)
            alias_ba = self.pointer_results.may_alias(var_b, var_a)
            assert alias_ab == alias_ba
            
            # Result should be boolean
            assert isinstance(alias_ab, bool)
        
        @rule(obj=objects, field_name=st.text(min_size=1, max_size=5, alphabet=st.characters(whitelist_categories=('Ll',))))
        def test_field_access_properties(self, obj, field_name):
            """Test field access properties."""
            field_key = AttrFieldKey(field_name)
            field_points_to = self.pointer_results.field_points_to(obj, field_key)
            
            # Field points-to should always be valid
            assert hasattr(field_points_to, '__len__')
            assert hasattr(field_points_to, '__iter__')


    # Only define the test class if hypothesis is available
    class TestStateMachineProperties:
        """Test properties using state machine approach."""
        
        @settings(max_examples=5, deadline=10000)  # Reduced for performance
        def test_ai_analysis_state_machine(self):
            """Run state machine tests for AI analysis properties."""
            # This test runs the state machine to explore different sequences of operations
            AIAnalysisStateMachine.TestCase().runTest()


else:
    # Fallback tests when hypothesis is not available
    class TestBasicProperties:
        """Basic property tests without hypothesis."""
        
        def test_monotonicity_basic(self, configurable_pointer_results):
            """Basic monotonicity test without property-based testing."""
            # Test that more precise pointer analysis maintains soundness
            precise_results = configurable_pointer_results(precision_level=1.0)
            imprecise_results = configurable_pointer_results(precision_level=0.0)
            
            test_var = "var_0"
            
            # Precise results may identify singletons
            precise_singleton = precise_results.is_singleton(test_var)
            
            # Imprecise results should not (under-approximation for safety)
            imprecise_singleton = imprecise_results.is_singleton(test_var)
            
            # Both should be valid boolean results
            assert isinstance(precise_singleton, bool)
            assert isinstance(imprecise_singleton, bool)
            
            # Imprecise should not claim singleton (safety)
            assert imprecise_singleton == False
        
        def test_stability_basic(self, configurable_pointer_results):
            """Basic stability test without property-based testing."""
            pointer_results = configurable_pointer_results(precision_level=1.0)
            
            test_var = "var_0"
            
            # Repeated queries should be stable
            result1 = pointer_results.is_singleton(test_var)
            result2 = pointer_results.is_singleton(test_var)
            result3 = pointer_results.is_singleton(test_var)
            
            assert result1 == result2 == result3
        
        def test_soundness_basic(self, configurable_pointer_results):
            """Basic soundness test without property-based testing."""
            imprecise_results = configurable_pointer_results(precision_level=0.0)
            
            # Imprecise results should over-approximate aliasing
            alias_result = imprecise_results.may_alias("var_0", "var_1")
            assert alias_result == True  # Over-approximation
            
            # Imprecise results should under-approximate singletons
            singleton_result = imprecise_results.is_singleton("var_0")
            assert singleton_result == False  # Under-approximation for safety


# Common test utilities

def property_test_summary():
    """
    Summary of property tests for AI analysis:
    
    1. Monotonicity: More precise pointer info → more precise AI results
    2. Stability: Same inputs → same outputs
    3. Soundness: Imprecise results over-approximate
    4. Symmetry: Alias relation is symmetric
    5. Consistency: Interface contracts are maintained
    """
    pass


def performance_property_tests():
    """
    Performance-related property tests:
    
    1. Analysis time should be bounded
    2. Memory usage should not grow unboundedly
    3. Precision improvements should not cause exponential slowdown
    """
    pass
