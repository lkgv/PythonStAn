import pytest
import ast
from typing import Dict, Set, List, Optional, Tuple

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

# Helper functions to create IR structures
def create_mock_module(name="test_module"):
    # Create a simple empty module AST
    module_ast = ast.Module(body=[], type_ignores=[])
    return IRModule(qualname=name, module=module_ast, name=name)

def create_mock_function(name="test_func", args=None):
    if args is None:
        args = []
    node = ast.FunctionDef(
        name=name,
        args=ast.arguments(
            posonlyargs=[],
            args=[ast.arg(arg=arg) for arg in args],
            kwonlyargs=[],
            kw_defaults=[],
            defaults=[]
        ),
        body=[ast.Pass()],
        decorator_list=[],
        returns=None,
        type_comment=None
    )
    return IRFunc(name, node)

def create_mock_assign(lval="x", rval="y"):
    assign_stmt = ast.Assign(
        targets=[ast.Name(id=lval, ctx=ast.Store())],
        value=ast.Name(id=rval, ctx=ast.Load())
    )
    return IRAssign(assign_stmt)

def create_mock_call(target="result", func_name="test_func", args=None):
    if args is None:
        args = []
    if target:
        # Call with assignment
        call_stmt = ast.Assign(
            targets=[ast.Name(id=target, ctx=ast.Store())],
            value=ast.Call(
                func=ast.Name(id=func_name, ctx=ast.Load()),
                args=[ast.Name(id=arg, ctx=ast.Load()) for arg in args],
                keywords=[]
            )
        )
    else:
        # Call without assignment
        call_stmt = ast.Call(
            func=ast.Name(id=func_name, ctx=ast.Load()),
            args=[ast.Name(id=arg, ctx=ast.Load()) for arg in args],
            keywords=[]
        )
    ir_call = IRCall(call_stmt)
    
    # Make sure target is accessible
    if not hasattr(ir_call, 'get_target'):
        ir_call.get_target = lambda: target
    elif target and not ir_call.get_target():
        # Override the method if it returns None but we have a target
        ir_call.get_target = lambda: target
    
    # Make sure args are accessible
    if not hasattr(ir_call, 'get_args'):
        ir_call.get_args = lambda: [(arg, False) for arg in args]
    
    return ir_call

def create_mock_return(value="result"):
    return_stmt = ast.Return(
        value=ast.Name(id=value, ctx=ast.Load())
    )
    ir_return = IRReturn(return_stmt)
    # Make sure the value is accessible via both get_value() and directly
    if not hasattr(ir_return, 'get_value'):
        ir_return.get_value = lambda: ir_return.value
    return ir_return

# Helper to create a function value for testing
def create_function_value(ir_func: IRFunc) -> Value:
    """Create a Value containing a function object"""
    obj = FunctionObject(ir_func)
    return Value({obj})

# CallSite for testing
class CallSite:
    """Represents a call site in the call graph"""
    
    def __init__(self, caller_qualname: str, call_stmt: IRCall, stmt_index: int, context=None):
        self.caller_qualname = caller_qualname
        self.call_stmt = call_stmt
        self.stmt_index = stmt_index
        self.context = context

# Tests for interprocedural analysis
class TestInterproceduralAnalysis:
    def setup_method(self):
        self.solver = create_solver(
            context_type=ContextType.CALL_SITE,
            flow_sensitivity=FlowSensitivity.SENSITIVE,
            max_iterations=10,
            max_recursion_depth=2
        )
    
    def test_simple_function_call_propagation(self):
        """Test that values propagate from caller to callee and back"""
        # Create a module
        module = create_mock_module("test_module")
        
        # Create a simple function: 
        # def add_one(x):
        #     return x + 1
        add_one_func = create_mock_function("add_one", ["x"])
        add_one_func_qualname = "test_module.add_one"
        
        # Create the function body:
        # x_plus_one = x + 1
        # return x_plus_one
        add_one_assign = create_mock_assign("x_plus_one", "x_plus_one_val")
        add_one_return = create_mock_return("x_plus_one")
        add_one_statements = [add_one_assign, add_one_return]
        
        # Store function statements
        self.solver.func_statements[add_one_func_qualname] = add_one_statements
        
        # Create main function:
        # def main():
        #     a = 5
        #     b = add_one(a)
        #     return b
        main_func = create_mock_function("main", [])
        main_func_qualname = "test_module.main"
        
        # Create main function body
        main_assign_a = create_mock_assign("a", "a_val")
        main_call = create_mock_call("b", "add_one", ["a"])
        main_return = create_mock_return("b")
        main_statements = [main_assign_a, main_call, main_return]
        
        # Store main statements
        self.solver.func_statements[main_func_qualname] = main_statements
        
        # Register functions
        self.solver.state.set_variable("add_one", create_function_value(add_one_func))
        self.solver.state.set_variable("main", create_function_value(main_func))
        
        # Set up initial values
        self.solver.state.set_variable("a_val", create_int_value(5))
        self.solver.state.set_variable("x_plus_one_val", create_int_value(6))
        
        # Build CFGs
        self.solver._build_cfg(main_func_qualname, main_statements)
        self.solver._build_cfg(add_one_func_qualname, add_one_statements)
        
        # Register call graph edges
        call_site = CallSite(
            caller_qualname=main_func_qualname,
            call_stmt=main_call,
            stmt_index=1,
            context=self.solver.state.current_context
        )
        self.solver.state.call_graph.add_call_edge(
            main_func_qualname, 
            add_one_func_qualname, 
            main_call, 
            1, 
            self.solver.state.current_context
        )
        
        # Perform intraprocedural analysis on main
        self.solver._analyze_scope(main_func_qualname, main_statements)
        
        # Perform interprocedural analysis
        self.solver._perform_interprocedural_analysis()
        
        # Check that b contains the correct value
        b_value = self.solver.state.get_variable("b")
        assert b_value is not None
        
        # b should be 6
        found = False
        for obj in b_value.objects:
            if isinstance(obj, ConstantObject) and obj.const_type == int:
                # Either exact value 6 or bounds that include 6
                if hasattr(obj.numeric_property, 'exact_values') and obj.numeric_property.exact_values:
                    assert 6 in obj.numeric_property.exact_values
                else:
                    assert obj.numeric_property.lower_bound <= 6 <= obj.numeric_property.upper_bound
                found = True
        assert found
    
    def test_recursive_function_analysis(self):
        """Test that recursive functions are handled correctly up to max depth"""
        # Create a module
        module = create_mock_module("test_module")
        
        # Create a recursive function:
        # def recursive(n):
        #     if n <= 0:
        #         return 0
        #     return n + recursive(n-1)
        recursive_func = create_mock_function("recursive", ["n"])
        recursive_func_qualname = "test_module.recursive"
        
        # Create the function body:
        # (simplified version - actual recursion would require more complex IR)
        # result = recursive(n_minus_one)
        # return result
        recursive_call = create_mock_call("result", "recursive", ["n_minus_one"])
        recursive_return = create_mock_return("result")
        recursive_statements = [recursive_call, recursive_return]
        
        # Store function statements
        self.solver.func_statements[recursive_func_qualname] = recursive_statements
        
        # Create main function:
        # def main():
        #     x = recursive(3)
        #     return x
        main_func = create_mock_function("main", [])
        main_func_qualname = "test_module.main"
        
        # Create main function body
        main_call = create_mock_call("x", "recursive", ["start_val"])
        main_return = create_mock_return("x")
        main_statements = [main_call, main_return]
        
        # Store main statements
        self.solver.func_statements[main_func_qualname] = main_statements
        
        # Register functions
        self.solver.state.set_variable("recursive", create_function_value(recursive_func))
        self.solver.state.set_variable("main", create_function_value(main_func))
        
        # Set up initial values
        self.solver.state.set_variable("start_val", create_int_value(3))
        self.solver.state.set_variable("n_minus_one", create_int_value(2))  # Simulate n-1
        
        # Build CFGs
        self.solver._build_cfg(main_func_qualname, main_statements)
        self.solver._build_cfg(recursive_func_qualname, recursive_statements)
        
        # Register call graph edges
        call_site = CallSite(
            caller_qualname=main_func_qualname,
            call_stmt=main_call,
            stmt_index=0,
            context=self.solver.state.current_context
        )
        self.solver.state.call_graph.add_call_edge(
            main_func_qualname, 
            recursive_func_qualname, 
            main_call, 
            0, 
            self.solver.state.current_context
        )
        
        # Also register recursive call
        recursive_call_site = CallSite(
            caller_qualname=recursive_func_qualname,
            call_stmt=recursive_call,
            stmt_index=0,
            context=self.solver.state.current_context
        )
        self.solver.state.call_graph.add_call_edge(
            recursive_func_qualname, 
            recursive_func_qualname, 
            recursive_call, 
            0, 
            self.solver.state.current_context
        )
        
        # Perform intraprocedural analysis on main
        self.solver._analyze_scope(main_func_qualname, main_statements)
        
        # Perform interprocedural analysis - this should detect recursion and stop at max depth
        self.solver._perform_interprocedural_analysis()
        
        # Check that we successfully analyzed the function and didn't get stuck in infinite recursion
        # The exact result isn't as important as the fact that we terminated correctly
        x_value = self.solver.state.get_variable("x")
        assert x_value is not None

# Tests for context-sensitive interprocedural analysis
class TestContextSensitiveInterproceduralAnalysis:
    def setup_method(self):
        self.solver = create_solver(
            context_type=ContextType.CALL_SITE,
            flow_sensitivity=FlowSensitivity.SENSITIVE,
            context_depth=2
        )
    
    def test_multiple_call_sites(self):
        """Test that function calls from different call sites are analyzed separately"""
        # Create a module
        module = create_mock_module("test_module")
        
        # Create a simple function:
        # def identity(x):
        #     return x
        identity_func = create_mock_function("identity", ["x"])
        identity_func_qualname = "test_module.identity"
        
        # Create the function body:
        # return x
        identity_return = create_mock_return("x")
        identity_statements = [identity_return]
        
        # Store function statements
        self.solver.func_statements[identity_func_qualname] = identity_statements
        
        # Create main function:
        # def main():
        #     a = 5
        #     b = 10
        #     result1 = identity(a)  # Call site 1
        #     result2 = identity(b)  # Call site 2
        #     return result1 + result2
        main_func = create_mock_function("main", [])
        main_func_qualname = "test_module.main"
        
        # Create main function body
        main_assign_a = create_mock_assign("a", "a_val")
        main_assign_b = create_mock_assign("b", "b_val")
        main_call1 = create_mock_call("result1", "identity", ["a"])
        main_call2 = create_mock_call("result2", "identity", ["b"])
        main_return = create_mock_return("result")
        main_statements = [main_assign_a, main_assign_b, main_call1, main_call2, main_return]
        
        # Store main statements
        self.solver.func_statements[main_func_qualname] = main_statements
        
        # Register functions
        self.solver.state.set_variable("identity", create_function_value(identity_func))
        self.solver.state.set_variable("main", create_function_value(main_func))
        
        # Set up initial values
        self.solver.state.set_variable("a_val", create_int_value(5))
        self.solver.state.set_variable("b_val", create_int_value(10))
        
        # Build CFGs
        self.solver._build_cfg(main_func_qualname, main_statements)
        self.solver._build_cfg(identity_func_qualname, identity_statements)
        
        # Register call graph edges
        call_site1 = CallSite(
            caller_qualname=main_func_qualname,
            call_stmt=main_call1,
            stmt_index=2,
            context=self.solver.state.current_context
        )
        self.solver.state.call_graph.add_call_edge(
            main_func_qualname, 
            identity_func_qualname, 
            main_call1, 
            2, 
            self.solver.state.current_context
        )
        
        call_site2 = CallSite(
            caller_qualname=main_func_qualname,
            call_stmt=main_call2,
            stmt_index=3,
            context=self.solver.state.current_context
        )
        self.solver.state.call_graph.add_call_edge(
            main_func_qualname, 
            identity_func_qualname, 
            main_call2, 
            3, 
            self.solver.state.current_context
        )
        
        # Perform intraprocedural analysis on main
        self.solver._analyze_scope(main_func_qualname, main_statements)
        
        # Perform interprocedural analysis
        self.solver._perform_interprocedural_analysis()
        
        # Check that result1 and result2 contain the correct values
        result1_value = self.solver.state.get_variable("result1")
        result2_value = self.solver.state.get_variable("result2")
        
        assert result1_value is not None
        assert result2_value is not None
        
        # Check result1 value
        found1 = False
        for obj in result1_value.objects:
            if isinstance(obj, ConstantObject) and obj.const_type == int:
                # Either exact value 5 or bounds that include 5
                if hasattr(obj.numeric_property, 'exact_values') and obj.numeric_property.exact_values:
                    assert 5 in obj.numeric_property.exact_values
                else:
                    assert obj.numeric_property.lower_bound <= 5 <= obj.numeric_property.upper_bound
                found1 = True
        assert found1
        
        # Check result2 value
        found2 = False
        for obj in result2_value.objects:
            if isinstance(obj, ConstantObject) and obj.const_type == int:
                # Either exact value 10 or bounds that include 10
                if hasattr(obj.numeric_property, 'exact_values') and obj.numeric_property.exact_values:
                    assert 10 in obj.numeric_property.exact_values
                else:
                    assert obj.numeric_property.lower_bound <= 10 <= obj.numeric_property.upper_bound
                found2 = True
        assert found2
    
    def test_nested_calls_with_context(self):
        """Test that context stacking works for nested function calls"""
        # Create a module
        module = create_mock_module("test_module")
        
        # Create three functions:
        # def f(x): return g(x)
        # def g(y): return h(y)
        # def h(z): return z
        
        # Function f
        f_func = create_mock_function("f", ["x"])
        f_func_qualname = "test_module.f"
        f_call = create_mock_call("f_result", "g", ["x"])
        f_return = create_mock_return("f_result")
        f_statements = [f_call, f_return]
        self.solver.func_statements[f_func_qualname] = f_statements
        
        # Function g
        g_func = create_mock_function("g", ["y"])
        g_func_qualname = "test_module.g"
        g_call = create_mock_call("g_result", "h", ["y"])
        g_return = create_mock_return("g_result")
        g_statements = [g_call, g_return]
        self.solver.func_statements[g_func_qualname] = g_statements
        
        # Function h
        h_func = create_mock_function("h", ["z"])
        h_func_qualname = "test_module.h"
        h_return = create_mock_return("z")
        h_statements = [h_return]
        self.solver.func_statements[h_func_qualname] = h_statements
        
        # Create main function:
        # def main():
        #     result = f(42)
        #     return result
        main_func = create_mock_function("main", [])
        main_func_qualname = "test_module.main"
        main_call = create_mock_call("main_result", "f", ["value"])
        main_return = create_mock_return("main_result")
        main_statements = [main_call, main_return]
        self.solver.func_statements[main_func_qualname] = main_statements
        
        # Register functions
        self.solver.state.set_variable("f", create_function_value(f_func))
        self.solver.state.set_variable("g", create_function_value(g_func))
        self.solver.state.set_variable("h", create_function_value(h_func))
        self.solver.state.set_variable("main", create_function_value(main_func))
        
        # Set up initial value
        self.solver.state.set_variable("value", create_int_value(42))
        
        # Build CFGs
        self.solver._build_cfg(main_func_qualname, main_statements)
        self.solver._build_cfg(f_func_qualname, f_statements)
        self.solver._build_cfg(g_func_qualname, g_statements)
        self.solver._build_cfg(h_func_qualname, h_statements)
        
        # Register call graph edges
        main_to_f = CallSite(
            caller_qualname=main_func_qualname,
            call_stmt=main_call,
            stmt_index=0,
            context=self.solver.state.current_context
        )
        self.solver.state.call_graph.add_call_edge(
            main_func_qualname, 
            f_func_qualname, 
            main_call, 
            0, 
            self.solver.state.current_context
        )
        
        f_to_g = CallSite(
            caller_qualname=f_func_qualname,
            call_stmt=f_call,
            stmt_index=0,
            context=self.solver.state.current_context
        )
        self.solver.state.call_graph.add_call_edge(
            f_func_qualname, 
            g_func_qualname, 
            f_call, 
            0, 
            self.solver.state.current_context
        )
        
        g_to_h = CallSite(
            caller_qualname=g_func_qualname,
            call_stmt=g_call,
            stmt_index=0,
            context=self.solver.state.current_context
        )
        self.solver.state.call_graph.add_call_edge(
            g_func_qualname, 
            h_func_qualname, 
            g_call, 
            0, 
            self.solver.state.current_context
        )
        
        # Perform intraprocedural analysis on main
        self.solver._analyze_scope(main_func_qualname, main_statements)
        
        # Perform interprocedural analysis
        self.solver._perform_interprocedural_analysis()
        
        # Check that main_result contains the correct value
        result_value = self.solver.state.get_variable("main_result")
        assert result_value is not None
        
        # The value should propagate through all calls and return 42
        found = False
        for obj in result_value.objects:
            if isinstance(obj, ConstantObject) and obj.const_type == int:
                # Either exact value 42 or bounds that include 42
                if hasattr(obj.numeric_property, 'exact_values') and obj.numeric_property.exact_values:
                    assert 42 in obj.numeric_property.exact_values
                else:
                    assert obj.numeric_property.lower_bound <= 42 <= obj.numeric_property.upper_bound
                found = True
        assert found 