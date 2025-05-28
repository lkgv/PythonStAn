import pytest
import ast
import os
from pathlib import Path
import sys

from pythonstan.ir.ir_statements import (
    IRModule, IRFunc, IRAssign, IRCall, IRReturn, JumpIfTrue, JumpIfFalse,
    Goto, Label, IRStoreAttr, IRLoadAttr, IRStoreSubscr, IRLoadSubscr, IRClass
)
from pythonstan.analysis.ai import (
    AbstractInterpretationSolver, create_solver,
    ContextType, FlowSensitivity,
    create_int_value, create_str_value, create_bool_value, create_none_value,
    create_list_value, create_dict_value, create_unknown_value
)

# Helper function to get the absolute path to benchmark files
def get_benchmark_path(filename):
    project_root = Path(__file__).parent.parent.parent.absolute()
    return project_root / 'benchmark' / filename

# Helper to get parsed AST from a benchmark file
def parse_benchmark_file(filename):
    benchmark_path = get_benchmark_path(filename)
    with open(benchmark_path, 'r') as f:
        code = f.read()
    return ast.parse(code, filename=str(benchmark_path))

# Simple IR conversion stub (actual implementation would use the full IR pipeline)
def convert_to_ir(node, filename):
    # This is a simplified stub - the real implementation would convert AST to IR
    # For test purposes, we'll just create a basic module and return empty statements
    module_name = Path(filename).stem
    module = IRModule(module_name)
    statements = []  # In real implementation, this would contain actual IR statements
    return module, statements

# Tests focused on benchmarks
class TestAIBenchmarks:
    
    @pytest.mark.parametrize("context_type,flow_sensitivity", [
        (ContextType.INSENSITIVE, FlowSensitivity.INSENSITIVE),
        (ContextType.CALL_SITE, FlowSensitivity.SENSITIVE),
        (ContextType.OBJECT_SENSITIVE, FlowSensitivity.SENSITIVE)
    ])
    def test_solver_configurations(self, context_type, flow_sensitivity):
        """Test that different solver configurations can be created"""
        solver = create_solver(
            context_type=context_type,
            flow_sensitivity=flow_sensitivity,
            context_depth=1
        )
        
        assert solver.state.context_type == context_type
        assert solver.state.flow_sensitivity == flow_sensitivity
    
    @pytest.mark.parametrize("benchmark_file", [
        "control_flow.py",
        "dataflow.py",
        "complex_expressions.py"
    ])
    def test_benchmark_parsing(self, benchmark_file):
        """Test that benchmark files can be parsed without errors"""
        try:
            ast_module = parse_benchmark_file(benchmark_file)
            assert isinstance(ast_module, ast.Module)
        except Exception as e:
            pytest.fail(f"Failed to parse {benchmark_file}: {e}")
    
    def test_control_flow_analysis(self):
        """Test analysis of control flow constructs"""
        # Skip if integration test not enabled or full pipeline not available
        pytest.skip("Integration test - requires full pipeline")
        
        module_ast = parse_benchmark_file("control_flow.py")
        module, statements = convert_to_ir(module_ast, "control_flow.py")
        
        # Create solver with call-site sensitivity and flow sensitivity
        solver = create_solver(
            context_type=ContextType.CALL_SITE,
            flow_sensitivity=FlowSensitivity.SENSITIVE
        )
        
        # Perform analysis
        final_state = solver.analyze_module(module, statements)
        
        # Verify call graph has been built
        assert final_state.call_graph is not None
        # Additional assertions about specific results would go here
    
    def test_dataflow_analysis(self):
        """Test analysis of data flow patterns"""
        # Skip if integration test not enabled or full pipeline not available
        pytest.skip("Integration test - requires full pipeline")
        
        module_ast = parse_benchmark_file("dataflow.py")
        module, statements = convert_to_ir(module_ast, "dataflow.py")
        
        # Create solver with object sensitivity for more precise analysis
        solver = create_solver(
            context_type=ContextType.OBJECT_SENSITIVE,
            flow_sensitivity=FlowSensitivity.SENSITIVE
        )
        
        # Perform analysis
        final_state = solver.analyze_module(module, statements)
        
        # Verify memory model has tracked variables
        assert final_state.memory is not None
        # Additional assertions about specific results would go here
    
    def test_callgraph_analysis(self):
        """Test analysis of function calls and call graph construction"""
        # Skip if integration test not enabled or full pipeline not available
        pytest.skip("Integration test - requires full pipeline")
        
        module_ast = parse_benchmark_file("callgraph.py")
        module, statements = convert_to_ir(module_ast, "callgraph.py")
        
        # Create solver
        solver = create_solver()
        
        # Perform analysis
        final_state = solver.analyze_module(module, statements)
        
        # Verify call graph contains expected functions
        callgraph = final_state.call_graph
        # In real test, we would check for specific function names here
        assert callgraph is not None
    
    def test_oop_analysis(self):
        """Test analysis of object-oriented patterns"""
        # Skip if integration test not enabled or full pipeline not available
        pytest.skip("Integration test - requires full pipeline")
        
        module_ast = parse_benchmark_file("oop.py")
        module, statements = convert_to_ir(module_ast, "oop.py")
        
        # Create solver with object sensitivity for better OOP analysis
        solver = create_solver(
            context_type=ContextType.OBJECT_SENSITIVE,
            flow_sensitivity=FlowSensitivity.SENSITIVE
        )
        
        # Perform analysis
        final_state = solver.analyze_module(module, statements)
        
        # Verify class hierarchy has been built
        assert final_state.class_hierarchy is not None
        # Additional assertions about specific class relationships would go here

# Helper function to create mock IR statements
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
        decorator_list=[]
    )
    return IRFunc(f"test_module.{name}", node, args)

def create_mock_assign(lval="x", rval="y"):
    return IRAssign(
        ast.Assign(
            targets=[ast.Name(id=lval, ctx=ast.Store())],
            value=ast.Name(id=rval, ctx=ast.Load())
        ),
        lval, rval
    )

def create_mock_call(target="result", func_name="test_func", args=None):
    if args is None:
        args = []
    return IRCall(
        ast.Call(
            func=ast.Name(id=func_name, ctx=ast.Load()),
            args=[ast.Name(id=arg, ctx=ast.Load()) for arg in args],
            keywords=[]
        ),
        target, func_name, [(arg, False) for arg in args], []
    )

def create_function_value(func):
    """Create a function value for the given IRFunc"""
    from pythonstan.analysis.ai import FunctionObject, Value
    obj = FunctionObject(func_name=func.name, func=func)
    value = Value()
    value.add_object(obj)
    return value

# More specific tests for benchmark examples
class TestSpecificBenchmarkScenarios:
    
    def test_if_else_flow(self):
        """Test analysis of if-else control flow"""
        # Implementation based on control_flow.py benchmark
        
        # Create solver with flow sensitivity for branching
        solver = create_solver(
            context_type=ContextType.CALL_SITE,
            flow_sensitivity=FlowSensitivity.SENSITIVE
        )
        
        # Create a module and function scope
        module_ast = ast.Module(body=[], type_ignores=[])
        module = IRModule("test_module", module_ast)
        solver.state.initialize_for_module(module)
        
        func = create_mock_function("test_func", [])
        solver.state.memory.create_function_scope(func, "test_module")
        solver.state.memory.set_current_scope(func.get_qualname())
        
        # Create statements representing:
        # if a > 10:
        #     result = a + 5
        # else:
        #     result = a - 5
        
        # Create labels
        true_label = Label(1)
        false_label = Label(2)
        end_label = Label(3)
        
        # Create condition variable
        solver.state.set_variable("a", create_int_value(15))  # a = 15
        solver.state.set_variable("result", create_int_value(0))  # result = 0
        
        # Create true branch value (a + 5)
        solver.state.set_variable("true_val", create_int_value(20))  # 15 + 5 = 20
        
        # Create false branch value (a - 5)
        solver.state.set_variable("false_val", create_int_value(10))  # 15 - 5 = 10
        
        # Create statements
        statements = [
            JumpIfFalse(ast.Compare(
                left=ast.Name(id="a", ctx=ast.Load()),
                ops=[ast.Gt()],
                comparators=[ast.Num(n=10)]
            ), false_label),
            # True branch
            create_mock_assign("result", "true_val"),
            Goto(end_label),
            # False branch
            false_label,
            create_mock_assign("result", "false_val"),
            # End
            end_label
        ]
        
        # Set up for analysis
        solver.state.control_flow.set_current_function("test_func")
        
        # Build CFG
        solver._build_cfg("test_func", statements)
        
        # Perform analysis
        solver._analyze_scope("test_func", statements)
        
        # Check result
        result_value = solver.state.get_variable("result")
        
        # Since a > 10 is true (a = 15), result should be 20
        assert result_value is not None
        
        # Check that result is 20 (the true branch value)
        result_obj = list(result_value.objects)[0]
        assert result_obj.numeric_property.lower_bound == 20
        assert result_obj.numeric_property.upper_bound == 20
    
    def test_loop_analysis(self):
        """Test analysis of loops with bounded and unbounded iterations"""
        # Implementation based on control_flow.py benchmark
        
        # Create solver with flow sensitivity
        solver = create_solver(
            context_type=ContextType.INSENSITIVE,
            flow_sensitivity=FlowSensitivity.SENSITIVE,
            max_iterations=5  # Limit iterations for loops
        )
        
        # Create a module and function scope
        module_ast = ast.Module(body=[], type_ignores=[])
        module = IRModule("test_module", module_ast)
        solver.state.initialize_for_module(module)
        
        func = create_mock_function("test_func", [])
        solver.state.memory.create_function_scope(func, "test_module")
        solver.state.memory.set_current_scope(func.get_qualname())
        
        # Create statements representing:
        # sum_val = 0
        # for i in range(3):  # bounded loop
        #     sum_val += i
        
        # Create labels for loop
        loop_start = Label(1)
        loop_body = Label(2)
        loop_end = Label(3)
        
        # Set up variables
        solver.state.set_variable("sum_val", create_int_value(0))  # sum_val = 0
        solver.state.set_variable("i", create_int_value(0))        # i = 0
        solver.state.set_variable("n", create_int_value(3))        # n = 3 (loop bound)
        solver.state.set_variable("one", create_int_value(1))      # constant 1
        
        # Create statements for loop
        statements = [
            # Initialize sum_val = 0
            create_mock_assign("sum_val", "sum_val_init"),
            # Initialize i = 0
            create_mock_assign("i", "i_init"),
            # Loop start
            loop_start,
            # Check i < n
            JumpIfFalse(ast.Compare(
                left=ast.Name(id="i", ctx=ast.Load()),
                ops=[ast.Lt()],
                comparators=[ast.Name(id="n", ctx=ast.Load())]
            ), loop_end),
            # Loop body
            loop_body,
            # sum_val += i
            create_mock_assign("sum_val", "sum_val_inc"),
            # i += 1
            create_mock_assign("i", "i_inc"),
            # Go back to loop start
            Goto(loop_start),
            # Loop end
            loop_end
        ]
        
        # Set up for analysis
        solver.state.control_flow.set_current_function("test_func")
        
        # Set up loop increment values
        # This mocks the effect of sum_val += i for each iteration
        solver.state.set_variable("sum_val_init", create_int_value(0))
        solver.state.set_variable("sum_val_inc", create_int_value(3))  # Final sum should be 0+1+2 = 3
        solver.state.set_variable("i_init", create_int_value(0))
        solver.state.set_variable("i_inc", create_int_value(1))  # i increments by 1 each iteration
        
        # Build CFG
        solver._build_cfg("test_func", statements)
        
        # Perform analysis
        solver._analyze_scope("test_func", statements)
        
        # Check results
        sum_val = solver.state.get_variable("sum_val")
        i_val = solver.state.get_variable("i")
        
        # After loop, sum_val should be 3 (0+1+2) and i should be 3
        assert sum_val is not None
        assert i_val is not None
        
        # Check that sum_val is 3
        sum_obj = list(sum_val.objects)[0]
        assert sum_obj.numeric_property.lower_bound <= 3
        assert sum_obj.numeric_property.upper_bound >= 3
        
        # Check that i is 3
        i_obj = list(i_val.objects)[0]
        assert i_obj.numeric_property.lower_bound <= 3
        assert i_obj.numeric_property.upper_bound >= 3
    
    def test_exception_handling(self):
        """Test analysis of try-except blocks"""
        # Implementation based on control_flow.py benchmark
        
        # Create solver
        solver = create_solver(
            context_type=ContextType.INSENSITIVE,
            flow_sensitivity=FlowSensitivity.SENSITIVE
        )
        
        # Create a module and function scope
        module = IRModule("test_module")
        solver.state.initialize_for_module(module)
        
        func = create_mock_function("test_func", [])
        solver.state.memory.create_function_scope(func, "test_module")
        solver.state.memory.set_current_scope(func.get_qualname())
        
        # Create statements representing:
        # try:
        #     result = 100 / x
        # except ZeroDivisionError:
        #     result = float('inf')
        
        # Create labels
        try_start = Label(1)
        except_handler = Label(2)
        finally_handler = Label(3)
        end_label = Label(4)
        
        # Set up variables
        solver.state.set_variable("x", create_int_value(0))      # x = 0 (will cause division by zero)
        solver.state.set_variable("result", create_unknown_value())
        solver.state.set_variable("normal_result", create_unknown_value())
        solver.state.set_variable("except_result", create_float_value(float('inf')))  # Infinity result
        
        # Create statements
        statements = [
            # Try block
            try_start,
            # result = 100 / x (may raise exception)
            create_mock_assign("result", "normal_result"),
            Goto(finally_handler),
            # Except block
            except_handler,
            # result = float('inf')
            create_mock_assign("result", "except_result"),
            # Finally block
            finally_handler,
            # End
            end_label
        ]
        
        # Set up for analysis
        solver.state.control_flow.set_current_function("test_func")
        
        # Build CFG with exception edge
        solver._build_cfg("test_func", statements)
        
        # Add exception edge manually (try → except)
        solver.state.control_flow.add_edge("test_func", 1, 3)  # From division to except handler
        
        # Perform analysis
        solver._analyze_scope("test_func", statements)
        
        # Check result
        result_value = solver.state.get_variable("result")
        assert result_value is not None
        
        # Since x = 0, should take exception path and result should be float('inf')
        # Check if any object is a float with infinity value
        has_infinity = False
        for obj in result_value.objects:
            if hasattr(obj, 'numeric_property') and obj.numeric_property.may_be_positive:
                if hasattr(obj.numeric_property, 'exact_values'):
                    if float('inf') in obj.numeric_property.exact_values:
                        has_infinity = True
                        break
        
        # The analysis should detect that the except block might execute
        assert has_infinity or len(result_value.objects) > 1
    
    def test_complex_dataflow(self):
        """Test analysis of data flow through complex paths"""
        # Implementation based on dataflow.py benchmark
        
        # Create solver
        solver = create_solver(
            context_type=ContextType.CALL_SITE,
            flow_sensitivity=FlowSensitivity.SENSITIVE
        )
        
        # Create a module and function scope
        module = IRModule("test_module")
        solver.state.initialize_for_module(module)
        
        func = create_mock_function("test_func", [])
        solver.state.memory.create_function_scope(func, "test_module")
        solver.state.memory.set_current_scope(func.get_qualname())
        
        # Create statements representing:
        # a = 10
        # b = 20
        # if a > b:
        #     c = 30
        # else:
        #     c = 40
        # c = 50  # Overwrites previous c
        # return c
        
        # Create labels
        true_label = Label(1)
        false_label = Label(2)
        end_if_label = Label(3)
        
        # Set up variables
        solver.state.set_variable("a", create_int_value(10))
        solver.state.set_variable("b", create_int_value(20))
        solver.state.set_variable("c_true", create_int_value(30))
        solver.state.set_variable("c_false", create_int_value(40))
        solver.state.set_variable("c_final", create_int_value(50))
        
        # Create statements
        statements = [
            # a = 10
            create_mock_assign("a", "a"),
            # b = 20
            create_mock_assign("b", "b"),
            # if a > b
            JumpIfFalse(ast.Compare(
                left=ast.Name(id="a", ctx=ast.Load()),
                ops=[ast.Gt()],
                comparators=[ast.Name(id="b", ctx=ast.Load())]
            ), false_label),
            # True branch: c = 30
            true_label,
            create_mock_assign("c", "c_true"),
            Goto(end_if_label),
            # False branch: c = 40
            false_label,
            create_mock_assign("c", "c_false"),
            # End if
            end_if_label,
            # c = 50 (overwrites)
            create_mock_assign("c", "c_final"),
            # return c
            IRReturn(ast.Return(value=ast.Name(id="c", ctx=ast.Load())), ast.Name(id="c", ctx=ast.Load()))
        ]
        
        # Set up for analysis
        solver.state.control_flow.set_current_function("test_func")
        
        # Build CFG
        solver._build_cfg("test_func", statements)
        
        # Perform analysis
        solver._analyze_scope("test_func", statements)
        
        # Check result for c
        c_value = solver.state.get_variable("c")
        assert c_value is not None
        
        # c should be 50 (the final assignment should overwrite previous values)
        c_obj = list(c_value.objects)[0]
        assert c_obj.numeric_property.lower_bound == 50
        assert c_obj.numeric_property.upper_bound == 50
    
    def test_recursive_functions(self):
        """Test analysis of recursive function calls"""
        # Implementation based on callgraph.py benchmark
        
        # Create solver with recursion limits
        solver = create_solver(
            context_type=ContextType.CALL_SITE,
            flow_sensitivity=FlowSensitivity.INSENSITIVE,
            max_recursion_depth=3
        )
        
        # Create a module and function scope
        module = IRModule("test_module")
        solver.state.initialize_for_module(module)
        
        # Create factorial function:
        # def factorial(n):
        #     if n <= 1:
        #         return 1
        #     return n * factorial(n-1)
        factorial_func = create_mock_function("factorial", ["n"])
        factorial_qualname = "test_module.factorial"
        
        # Create main function
        main_func = create_mock_function("main", [])
        main_qualname = "test_module.main"
        
        # Create function scopes
        solver.state.memory.create_function_scope(factorial_func, "test_module")
        solver.state.memory.create_function_scope(main_func, "test_module")
        solver.state.memory.set_current_scope(main_qualname)
        
        # Create the base case check label
        base_case_label = Label(1)
        recursive_case_label = Label(2)
        
        # Create recursive factorial statements
        factorial_statements = [
            # if n <= 1
            JumpIfFalse(ast.Compare(
                left=ast.Name(id="n", ctx=ast.Load()),
                ops=[ast.LtE()],
                comparators=[ast.Num(n=1)]
            ), recursive_case_label),
            # Base case: return 1
            IRReturn(ast.Return(value=ast.Num(n=1)), ast.Num(n=1)),
            # Recursive case
            recursive_case_label,
            # Call factorial(n-1)
            create_mock_call("rec_result", "factorial", ["n_minus_1"]),
            # Return n * rec_result
            IRReturn(
                ast.Return(value=ast.BinOp(
                    left=ast.Name(id="n", ctx=ast.Load()),
                    op=ast.Mult(),
                    right=ast.Name(id="rec_result", ctx=ast.Load())
                )),
                ast.Name(id="rec_result", ctx=ast.Load())
            )
        ]
        
        # Store factorial statements
        solver.func_statements[factorial_qualname] = factorial_statements
        
        # Create main statements
        main_statements = [
            # Call factorial(5)
            create_mock_call("result", "factorial", ["n_val"]),
            # Return result
            IRReturn(ast.Return(value=ast.Name(id="result", ctx=ast.Load())), ast.Name(id="result", ctx=ast.Load()))
        ]
        
        # Store main statements
        solver.func_statements[main_qualname] = main_statements
        
        # Register functions
        solver.state.set_variable("factorial", create_function_value(factorial_func))
        solver.state.set_variable("main", create_function_value(main_func))
        
        # Set up initial values
        solver.state.set_variable("n_val", create_int_value(5))  # Initial n value
        solver.state.set_variable("n_minus_1", create_int_value(4))  # n-1 value
        
        # Build CFGs
        solver._build_cfg(factorial_qualname, factorial_statements)
        solver._build_cfg(main_qualname, main_statements)
        
        # Add call graph edges
        main_call_site = solver.state.call_graph.CallSite(
            caller_qualname=main_qualname,
            call_stmt=main_statements[0],
            stmt_index=0,
            context=solver.state.current_context
        )
        solver.state.call_graph.add_call(main_qualname, factorial_qualname, main_call_site)
        
        # Add recursive call edge
        recursive_call_site = solver.state.call_graph.CallSite(
            caller_qualname=factorial_qualname,
            call_stmt=factorial_statements[3],
            stmt_index=3,
            context=solver.state.current_context
        )
        solver.state.call_graph.add_call(factorial_qualname, factorial_qualname, recursive_call_site)
        
        # Analyze main
        solver._analyze_scope(main_qualname, main_statements)
        
        # Perform interprocedural analysis with recursion
        solver._perform_interprocedural_analysis()
        
        # Check that analysis terminated despite recursion
        result_value = solver.state.get_variable("result")
        assert result_value is not None
        
        # The result should be a numeric value (exact value depends on recursion depth)
        assert any(obj.obj_type.name == "CONSTANT" or obj.obj_type.name == "BUILTIN" for obj in result_value.objects)
    
    def test_inheritance_analysis(self):
        """Test analysis of class inheritance patterns"""
        # Implementation based on oop.py benchmark
        
        # Create solver with object sensitivity
        solver = create_solver(
            context_type=ContextType.OBJECT_SENSITIVE,
            flow_sensitivity=FlowSensitivity.SENSITIVE
        )
        
        # Create a module and initialize
        module = IRModule("test_module")
        solver.state.initialize_for_module(module)
        
        # Create classes for inheritance hierarchy:
        # class Animal:
        #     def make_sound(self): return "..."
        # class Dog(Animal):
        #     def make_sound(self): return "Woof!"
        # class Cat(Animal):
        #     def make_sound(self): return "Meow!"
        
        # Create Animal class
        animal_class_stmt = IRClass(
            "test_module.Animal",
            ast.ClassDef(name="Animal", bases=[], keywords=[],
                body=[ast.Pass()], decorator_list=[])
        )
        
        # Create Dog class inheriting from Animal
        dog_class_stmt = IRClass(
            "test_module.Dog",
            ast.ClassDef(name="Dog", bases=[ast.Name(id="Animal", ctx=ast.Load())], keywords=[],
                body=[ast.Pass()], decorator_list=[])
        )
        
        # Create Cat class inheriting from Animal
        cat_class_stmt = IRClass(
            "test_module.Cat",
            ast.ClassDef(name="Cat", bases=[ast.Name(id="Animal", ctx=ast.Load())], keywords=[],
                body=[ast.Pass()], decorator_list=[])
        )
        
        # Create class hierarchy in solver state
        solver.state.class_hierarchy.add_class("Animal", animal_class_stmt)
        solver.state.class_hierarchy.add_class("Dog", dog_class_stmt)
        solver.state.class_hierarchy.add_class("Cat", cat_class_stmt)
        
        # Register inheritance relationships
        solver.state.class_hierarchy.add_inheritance("Dog", "Animal")
        solver.state.class_hierarchy.add_inheritance("Cat", "Animal")
        
        # Create function scopes for methods
        make_sound_animal = create_mock_function("make_sound", ["self"])
        make_sound_dog = create_mock_function("make_sound", ["self"])
        make_sound_cat = create_mock_function("make_sound", ["self"])
        
        solver.state.memory.create_function_scope(make_sound_animal, "test_module")
        solver.state.memory.create_function_scope(make_sound_dog, "test_module")
        solver.state.memory.create_function_scope(make_sound_cat, "test_module")
        
        # Set current scope to module for setting variables
        solver.state.memory.set_current_scope("test_module")
        
        # Define method implementations
        animal_make_sound = create_function_value(make_sound_animal)
        dog_make_sound = create_function_value(make_sound_dog)
        cat_make_sound = create_function_value(make_sound_cat)
        
        # Add methods to classes
        solver.state.class_hierarchy.add_method("Animal", "make_sound", animal_make_sound)
        solver.state.class_hierarchy.add_method("Dog", "make_sound", dog_make_sound)
        solver.state.class_hierarchy.add_method("Cat", "make_sound", cat_make_sound)
        
        # Create instances
        solver.state.set_variable("animal", create_class_instance_value("Animal"))
        solver.state.set_variable("dog", create_class_instance_value("Dog"))
        solver.state.set_variable("cat", create_class_instance_value("Cat"))
        
        # Test method resolution and polymorphism
        # Check inheritance relationships
        assert solver.state.class_hierarchy.is_subclass("Dog", "Animal")
        assert solver.state.class_hierarchy.is_subclass("Cat", "Animal")
        
        # Check method resolution
        animal_method = solver.state.class_hierarchy.get_method("Animal", "make_sound")
        dog_method = solver.state.class_hierarchy.get_method("Dog", "make_sound")
        cat_method = solver.state.class_hierarchy.get_method("Cat", "make_sound")
        
        assert animal_method is not None
        assert dog_method is not None
        assert cat_method is not None
        
        # Check polymorphism - Dog and Cat should have their own method implementations
        assert dog_method != animal_method
        assert cat_method != animal_method

# Helper function to create class instance values
def create_class_instance_value(class_name):
    """Create a value representing an instance of the given class"""
    from pythonstan.analysis.ai import InstanceObject, Value
    obj = InstanceObject(class_name=class_name)
    value = Value()
    value.add_object(obj)
    return value 