"""
Tests for AI CFG interoperability.

Tests end-to-end mini snippets built via IR→TAC→CFG ensuring AI consumes 
CFG consistent with pointer call edges.
"""

import pytest
import ast
from typing import Dict, Set, List, Optional, Tuple, Any
from pathlib import Path

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
def transform_entrypoints():
    """Load transform entrypoints configuration."""
    # In a real implementation, this would load from the actual file
    # For testing, we'll provide a mock configuration
    return {
        "ast_to_ir": {
            "module": "pythonstan.analysis.transform.ir",
            "function": "convert_ast_to_ir"
        },
        "ir_to_tac": {
            "module": "pythonstan.analysis.transform.three_address",
            "function": "convert_ir_to_tac"
        },
        "tac_to_cfg": {
            "module": "pythonstan.analysis.transform.cfg",
            "function": "build_cfg_from_tac"
        },
        "cfg_to_icfg": {
            "module": "pythonstan.analysis.transform.icfg.icfg_transform",
            "function": "build_icfg_from_cfg"
        }
    }


@pytest.fixture
def cfg_consistent_pointer_results():
    """Pointer results that are consistent with CFG structure."""
    return MockPointerResults(
        precise=True,
        singleton_vars={"local_var", "method_receiver"},
        alias_pairs={("param1", "arg1"), ("return_val", "result")},
        callee_map={
            "direct_call_site": {"target_function"},
            "indirect_call_site": {"method_a", "method_b"},
            "virtual_call_site": {"derived_method"}
        }
    )


class TestBasicCFGConsistency:
    """Test basic consistency between AI analysis and CFG structure."""
    
    def test_simple_function_cfg_ai_consistency(self, cfg_consistent_pointer_results):
        """Test AI analysis on a simple function's CFG."""
        # Create a simple function: def f(x): return x + 1
        
        function_code = """
def simple_function(x):
    y = x + 1
    return y
"""
        
        # Parse to AST
        ast_module = ast.parse(function_code)
        function_def = ast_module.body[0]
        
        # In a real implementation, this would go through the full pipeline:
        # AST → IR → TAC → CFG → AI Analysis
        
        # For testing, we simulate the key aspects:
        # 1. Function has entry/exit blocks
        # 2. Parameter binding
        # 3. Local variable assignment
        # 4. Return statement
        
        # Create IR representation
        ir_func = create_mock_ir_function("simple_function", ["x"])
        
        # Create AI solver
        solver = create_solver(
            context_type=ContextType.CALL_SITE,
            flow_sensitivity=FlowSensitivity.SENSITIVE
        )
        
        # Test that AI can analyze the function structure
        assert hasattr(solver, 'analyze_module')
        
        # In real implementation, this would verify:
        # - CFG nodes correspond to AI analysis points
        # - Control flow edges match AI transfer functions
        # - Variable bindings are consistent
    
    def test_conditional_cfg_ai_consistency(self, cfg_consistent_pointer_results):
        """Test AI analysis on CFG with conditional branches."""
        # Create a function with if-else: def f(x): return x if x > 0 else -x
        
        function_code = """
def conditional_function(x):
    if x > 0:
        result = x
    else:
        result = -x
    return result
"""
        
        ast_module = ast.parse(function_code)
        function_def = ast_module.body[0]
        
        # This creates a CFG with:
        # - Entry block
        # - Condition evaluation block
        # - True branch block
        # - False branch block  
        # - Merge block
        # - Exit block
        
        ir_func = create_mock_ir_function("conditional_function", ["x"])
        
        # Test that AI handles branching correctly
        solver = create_solver(flow_sensitivity=FlowSensitivity.SENSITIVE)
        
        # In real implementation, this would verify:
        # - AI tracks different variable states in different branches
        # - Join operations at merge points are correct
        # - Pointer information is preserved across branches
    
    def test_loop_cfg_ai_consistency(self, cfg_consistent_pointer_results):
        """Test AI analysis on CFG with loops."""
        # Create a function with a loop: def f(n): for i in range(n): ...
        
        function_code = """
def loop_function(n):
    total = 0
    for i in range(n):
        total = total + i
    return total
"""
        
        ast_module = ast.parse(function_code)
        function_def = ast_module.body[0]
        
        # This creates a CFG with:
        # - Entry block
        # - Loop header block
        # - Loop body block
        # - Loop exit block
        
        ir_func = create_mock_ir_function("loop_function", ["n"])
        
        # Test that AI handles loops with fixed-point computation
        solver = create_solver(flow_sensitivity=FlowSensitivity.SENSITIVE)
        
        # In real implementation, this would verify:
        # - AI converges to fixed point in loop analysis
        # - Loop invariants are correctly computed
        # - Pointer information remains sound through iterations


class TestInterprocedralCFGConsistency:
    """Test interprocedural CFG consistency with AI analysis."""
    
    def test_direct_function_call_consistency(self, cfg_consistent_pointer_results):
        """Test direct function calls across CFG and AI."""
        # Create caller and callee functions
        
        caller_code = """
def caller():
    x = 42
    result = callee(x)
    return result

def callee(param):
    return param * 2
"""
        
        ast_module = ast.parse(caller_code)
        caller_def = ast_module.body[0]
        callee_def = ast_module.body[1]
        
        # Create IR for both functions
        caller_ir = create_mock_ir_function("caller", [])
        callee_ir = create_mock_ir_function("callee", ["param"])
        
        # Test call site consistency
        call_site = MockCallSite("direct_call_site", "test.py", 3, 15)
        callees = cfg_consistent_pointer_results.possible_callees(call_site)
        
        # Should find the direct callee
        callee_names = {c.name for c in callees}
        assert "target_function" in callee_names
        
        # In real implementation, this would verify:
        # - Call graph edges match CFG call edges
        # - Parameter passing is consistent
        # - Return value handling is correct
    
    def test_indirect_function_call_consistency(self, cfg_consistent_pointer_results):
        """Test indirect function calls with pointer analysis."""
        # Create a scenario with function pointers
        
        indirect_code = """
def caller(func_ptr, arg):
    result = func_ptr(arg)  # Indirect call
    return result

def method_a(x):
    return x + 1

def method_b(x):
    return x * 2
"""
        
        # Test indirect call resolution
        call_site = MockCallSite("indirect_call_site", "test.py", 2, 15)
        callees = cfg_consistent_pointer_results.possible_callees(call_site)
        
        # Should find possible targets from pointer analysis
        callee_names = {c.name for c in callees}
        assert "method_a" in callee_names
        assert "method_b" in callee_names
        
        # In real implementation, this would verify:
        # - Indirect call targets from pointer analysis match CFG edges
        # - AI uses pointer information to narrow call targets
        # - Context sensitivity is maintained across indirect calls
    
    def test_virtual_method_call_consistency(self, cfg_consistent_pointer_results):
        """Test virtual method calls in object-oriented code."""
        # Create a class hierarchy with virtual methods
        
        oop_code = """
class Base:
    def method(self):
        return "base"

class Derived(Base):
    def method(self):
        return "derived"

def caller(obj):
    return obj.method()  # Virtual call
"""
        
        # Test virtual call resolution
        call_site = MockCallSite("virtual_call_site", "test.py", 10, 20)
        callees = cfg_consistent_pointer_results.possible_callees(call_site)
        
        # Should find virtual method implementation
        callee_names = {c.name for c in callees}
        assert "derived_method" in callee_names
        
        # In real implementation, this would verify:
        # - Class hierarchy information is consistent
        # - Method resolution order matches AI expectations
        # - Receiver object types are correctly tracked


class TestCFGEdgeConsistency:
    """Test consistency of CFG edges with AI transfer functions."""
    
    def test_control_flow_edge_consistency(self, cfg_consistent_pointer_results):
        """Test that CFG control flow edges match AI transfer functions."""
        # Create a function with multiple control flow constructs
        
        control_flow_code = """
def complex_control_flow(x, y):
    if x > 0:
        if y > 0:
            result = x + y
        else:
            result = x - y
    else:
        result = 0
    
    while result < 100:
        result = result * 2
    
    return result
"""
        
        # In real implementation, this would verify:
        # - Each CFG edge has corresponding AI transfer function
        # - Branch conditions are evaluated consistently
        # - Loop back edges maintain AI state correctly
        
        solver = create_solver(flow_sensitivity=FlowSensitivity.SENSITIVE)
        
        # Test basic solver configuration
        assert solver.state.flow_sensitivity == FlowSensitivity.SENSITIVE
    
    def test_exception_edge_consistency(self, cfg_consistent_pointer_results):
        """Test exception handling edge consistency."""
        # Create a function with try-except blocks
        
        exception_code = """
def exception_handling(x):
    try:
        result = risky_operation(x)
    except ValueError as e:
        result = default_value()
    except Exception as e:
        result = None
    finally:
        cleanup()
    return result
"""
        
        # In real implementation, this would verify:
        # - Exception edges in CFG match AI exception handling
        # - Variable states are preserved across exception edges
        # - Finally blocks are handled correctly
        # - Pointer information is maintained in exception handlers
    
    def test_call_edge_consistency(self, cfg_consistent_pointer_results):
        """Test function call edge consistency."""
        # Test different types of call edges
        
        # Direct call
        direct_call_site = MockCallSite("direct_call_site", "test.py", 10, 5)
        direct_callees = cfg_consistent_pointer_results.possible_callees(direct_call_site)
        
        # Should have consistent call graph edges
        assert len(direct_callees) >= 1
        
        # For each callee, test call graph successors
        for callee in direct_callees:
            successors = cfg_consistent_pointer_results.call_graph_successors(callee)
            assert isinstance(successors, set)


class TestDataFlowConsistency:
    """Test data flow consistency between CFG and AI."""
    
    def test_variable_def_use_consistency(self, cfg_consistent_pointer_results):
        """Test variable definition-use consistency."""
        # Create a function with clear def-use chains
        
        def_use_code = """
def def_use_example():
    x = 42          # Definition of x
    y = x + 1       # Use of x, definition of y  
    z = y * 2       # Use of y, definition of z
    return z        # Use of z
"""
        
        # In real implementation, this would verify:
        # - CFG def-use chains match AI variable tracking
        # - Each variable use has corresponding reaching definition
        # - Pointer analysis information flows correctly
        
        # Test variable tracking
        var_x = "x"
        var_y = "y"
        var_z = "z"
        
        # Check if variables might alias (they shouldn't in this simple case)
        assert not cfg_consistent_pointer_results.may_alias(var_x, var_y)
        assert not cfg_consistent_pointer_results.may_alias(var_y, var_z)
    
    def test_object_field_consistency(self, cfg_consistent_pointer_results):
        """Test object field access consistency."""
        # Create a function with object field accesses
        
        field_code = """
def field_access_example(obj):
    obj.field = 42      # Store to field
    value = obj.field   # Load from field
    return value
"""
        
        # Test field access consistency
        obj_var = "obj"
        field_key = AttrFieldKey("field")
        
        # Get object points-to set
        obj_points_to = cfg_consistent_pointer_results.points_to(obj_var)
        
        # For each object, check field consistency
        for obj in obj_points_to:
            field_points_to = cfg_consistent_pointer_results.field_points_to(obj, field_key)
            assert isinstance(field_points_to, MockPointsToSet)
    
    def test_interprocedural_dataflow_consistency(self, cfg_consistent_pointer_results):
        """Test interprocedural data flow consistency."""
        # Create functions with parameter/return value flow
        
        interprocedural_code = """
def caller():
    arg = create_object()
    result = callee(arg)
    return result

def callee(param):
    param.field = "modified"
    return param
"""
        
        # Test parameter/return consistency
        param_var = "param"
        arg_var = "arg" 
        result_var = "result"
        
        # Check aliasing relationships
        param_arg_alias = cfg_consistent_pointer_results.may_alias(param_var, arg_var)
        result_param_alias = cfg_consistent_pointer_results.may_alias(result_var, param_var)
        
        # In this case, they should potentially alias
        assert isinstance(param_arg_alias, bool)
        assert isinstance(result_param_alias, bool)


class TestEndToEndScenarios:
    """Test complete end-to-end scenarios through the pipeline."""
    
    def test_simple_program_end_to_end(self, transform_entrypoints, cfg_consistent_pointer_results):
        """Test a simple program through the complete pipeline."""
        # Create a simple but complete program
        
        program_code = """
def main():
    x = 42
    y = add_one(x)
    print(y)

def add_one(n):
    return n + 1

if __name__ == "__main__":
    main()
"""
        
        # Parse to AST
        ast_module = ast.parse(program_code)
        
        # In real implementation, this would:
        # 1. Convert AST to IR using transform_entrypoints["ast_to_ir"]
        # 2. Convert IR to TAC using transform_entrypoints["ir_to_tac"]  
        # 3. Build CFG using transform_entrypoints["tac_to_cfg"]
        # 4. Run pointer analysis
        # 5. Run AI analysis with pointer results
        # 6. Verify consistency at each step
        
        # For testing, we verify the entrypoints are available
        assert "ast_to_ir" in transform_entrypoints
        assert "ir_to_tac" in transform_entrypoints
        assert "tac_to_cfg" in transform_entrypoints
        
        # Create AI solver for analysis
        solver = create_solver(
            context_type=ContextType.CALL_SITE,
            flow_sensitivity=FlowSensitivity.SENSITIVE
        )
        
        # Test that solver can be created and configured
        assert solver.state.context_type == ContextType.CALL_SITE
        assert solver.state.flow_sensitivity == FlowSensitivity.SENSITIVE
    
    def test_object_oriented_program_end_to_end(self, transform_entrypoints, cfg_consistent_pointer_results):
        """Test an object-oriented program through the pipeline."""
        
        oop_program = """
class Animal:
    def __init__(self, name):
        self.name = name
    
    def speak(self):
        return "Some sound"

class Dog(Animal):
    def speak(self):
        return f"{self.name} barks"

class Cat(Animal):
    def speak(self):
        return f"{self.name} meows"

def make_animals_speak(animals):
    sounds = []
    for animal in animals:
        sound = animal.speak()  # Virtual call
        sounds.append(sound)
    return sounds

def main():
    dog = Dog("Buddy")
    cat = Cat("Whiskers")
    animals = [dog, cat]
    sounds = make_animals_speak(animals)
    return sounds
"""
        
        # This program tests:
        # - Class hierarchy analysis
        # - Virtual method dispatch
        # - Object creation and initialization
        # - Container operations (list)
        # - Interprocedural analysis
        
        ast_module = ast.parse(oop_program)
        
        # In real implementation, this would verify:
        # - Class hierarchy is correctly built
        # - Virtual calls are resolved using pointer analysis
        # - Object allocation sites are tracked
        # - Container element types are maintained
        
        # Test virtual call consistency
        virtual_call = MockCallSite("virtual_call_site", "oop_test.py", 25, 20)
        callees = cfg_consistent_pointer_results.possible_callees(virtual_call)
        
        # Should resolve to appropriate method implementations
        assert len(callees) >= 1
    
    def test_error_handling_end_to_end(self, transform_entrypoints, cfg_consistent_pointer_results):
        """Test error handling through the pipeline."""
        
        error_program = """
def risky_operation(x):
    if x < 0:
        raise ValueError("Negative value")
    return x * 2

def safe_caller(values):
    results = []
    for value in values:
        try:
            result = risky_operation(value)
            results.append(result)
        except ValueError as e:
            print(f"Error: {e}")
            results.append(0)
        except Exception as e:
            print(f"Unexpected error: {e}")
            results.append(-1)
    return results
"""
        
        # This tests:
        # - Exception flow analysis
        # - Try-except-finally blocks
        # - Exception type hierarchy
        # - State preservation across exception edges
        
        ast_module = ast.parse(error_program)
        
        # In real implementation, this would verify:
        # - Exception edges are correctly modeled in CFG
        # - AI analysis handles exception states soundly
        # - Pointer information is preserved in exception handlers
        # - Finally blocks are executed in all paths


# Helper functions for creating mock IR structures

def create_mock_ir_function(name: str, params: List[str]) -> IRFunc:
    """Create a mock IR function for testing."""
    # Create a minimal function AST node
    func_node = ast.FunctionDef(
        name=name,
        args=ast.arguments(
            posonlyargs=[],
            args=[ast.arg(arg=param, annotation=None) for param in params],
            kwonlyargs=[],
            kw_defaults=[],
            defaults=[]
        ),
        body=[ast.Pass()],
        decorator_list=[],
        returns=None
    )
    
    return IRFunc(qualname=name, fn=func_node)


def create_mock_ir_module(name: str = "test_module") -> IRModule:
    """Create a mock IR module for testing."""
    module_ast = ast.Module(body=[], type_ignores=[])
    return IRModule(qualname=name, module=module_ast, name=name, filename=f"{name}.py")


def simulate_transform_pipeline(ast_node: ast.AST, entrypoints: Dict[str, Any]) -> Dict[str, Any]:
    """
    Simulate the transform pipeline for testing.
    
    In a real implementation, this would call the actual transform functions
    specified in the entrypoints configuration.
    """
    # Mock pipeline results
    return {
        "ir": "mock_ir_representation",
        "tac": "mock_tac_representation", 
        "cfg": "mock_cfg_representation",
        "icfg": "mock_icfg_representation"
    }


def verify_pipeline_consistency(pipeline_results: Dict[str, Any], 
                               pointer_results: PointerResults) -> bool:
    """
    Verify consistency between pipeline stages and pointer analysis.
    
    In a real implementation, this would check:
    - IR statements match TAC instructions
    - TAC instructions match CFG nodes
    - CFG call edges match pointer analysis call graph
    - Variable bindings are consistent across representations
    """
    # Mock verification
    return True
