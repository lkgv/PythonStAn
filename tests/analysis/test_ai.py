import pytest
import ast
import os
from pathlib import Path

from pythonstan.ir.ir_statements import (
    IRStatement, IRFunc, IRClass, IRModule, IRAssign, IRCall, IRLoadAttr, 
    IRStoreAttr, JumpIfTrue, JumpIfFalse, Goto, Label, IRReturn
)
from pythonstan.utils.var_collector import VarCollector
from pythonstan.analysis.ai import (
    Value, Object, ObjectType, ConstantObject, BuiltinObject, 
    FunctionObject, ClassObject, InstanceObject, 
    NumericProperty, StringProperty, ContainerProperty,
    create_int_value, create_float_value, create_str_value, create_bool_value,
    create_none_value, create_list_value, create_dict_value, create_unknown_value,
    create_function_value,
    AbstractState, Context, ContextType, FlowSensitivity,
    AbstractInterpreter, AbstractInterpretationSolver, create_solver
)

# Helper function to get the absolute path to benchmark files
def get_benchmark_path(filename):
    project_root = Path(__file__).parent.parent.parent.absolute()
    return project_root / 'benchmark' / filename

# Mock IR statement creation helpers
def create_mock_module(name="test_module"):
    module_node = ast.Module(body=[], type_ignores=[])
    return IRModule(name, module_node)

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
            defaults=[],
            vararg=None,
            kwarg=None
        ),
        body=[ast.Pass()],
        decorator_list=[],
        returns=None,
        type_comment=None
    )
    ast.fix_missing_locations(node)
    return IRFunc(name, node)

def create_mock_class(name="TestClass", bases=None):
    if bases is None:
        bases = []
    node = ast.ClassDef(
        name=name,
        bases=[ast.Name(id=base, ctx=ast.Load()) for base in bases],
        keywords=[],
        body=[ast.Pass()],
        decorator_list=[]
    )
    ast.fix_missing_locations(node)
    return IRClass(name, node)

def create_mock_assign(lval="x", rval="y"):
    node = ast.Assign(
        targets=[ast.Name(id=lval, ctx=ast.Store())],
        value=ast.Name(id=rval, ctx=ast.Load()),
        type_comment=None
    )
    ast.fix_missing_locations(node)
    
    # Create a subclass that allows us to set properties directly
    class MockIRAssign(IRAssign):
        def __init__(self, stmt):
            # Skip the normal initialization
            self.stmt = stmt
            # Set required attributes manually
            self.lval = ast.Name(id=lval, ctx=ast.Store())
            self.rval = ast.Name(id=rval, ctx=ast.Load())
            # Initialize collectors
            self.store_collector = VarCollector("store")
            self.store_collector.visit(self.lval)
            self.load_collector = VarCollector("load")
            self.load_collector.visit(self.rval)
        
        # Add these methods to make it compatible with the visitor
        def get_lval(self):
            return self.lval
            
        def get_rval(self):
            return self.rval
            
    return MockIRAssign(node)

def create_mock_call(target="result", func_name="test_func", args=None):
    if args is None:
        args = []
        
    # Create a call node
    call_node = ast.Call(
        func=ast.Name(id=func_name, ctx=ast.Load()),
        args=[ast.Name(id=arg, ctx=ast.Load()) for arg in args],
        keywords=[]
    )
    
    # Create an assignment node
    assign_node = ast.Assign(
        targets=[ast.Name(id=target, ctx=ast.Store())],
        value=call_node,
        type_comment=None
    )
    
    ast.fix_missing_locations(assign_node)
    
    # Create a subclass that allows us to set properties directly
    class MockIRCall(IRCall):
        def __init__(self, stmt):
            # Skip the normal initialization
            self.stmt = stmt
            self.call = call_node
            self.target = target
            self.func_name = func_name
            self.args = [(arg, False) for arg in args]
            self.keywords = []
            # Initialize collector
            self.load_collector = VarCollector("load")
            self.load_collector.visit(self.call)
        
        # Add these methods to make it compatible with the visitor
        def get_target(self):
            return self.target
            
        def get_func_name(self):
            return self.func_name
            
        def get_args(self):
            return self.args
            
        def get_keywords(self):
            return self.keywords
            
    return MockIRCall(assign_node)

# Tests for Value and Object classes
class TestValueAndObject:
    def test_value_creation(self):
        int_val = create_int_value(42)
        assert len(int_val.objects) == 1
        obj = list(int_val.objects)[0]
        assert isinstance(obj, ConstantObject)
        assert obj.const_type == int
        
        # Test numeric properties
        assert obj.numeric_property.lower_bound == 42
        assert obj.numeric_property.upper_bound == 42
        assert not obj.numeric_property.may_be_negative
        assert not obj.numeric_property.may_be_zero
        assert obj.numeric_property.may_be_positive
    
    def test_value_merging(self):
        val1 = create_int_value(5)
        val2 = create_int_value(10)
        merged = val1.merge(val2)
        
        assert len(merged.objects) == 1
        obj = list(merged.objects)[0]
        assert obj.numeric_property.lower_bound == 5
        assert obj.numeric_property.upper_bound == 10
        
        # Test merging different types
        str_val = create_str_value("test")
        complex_merged = merged.merge(str_val)
        assert len(complex_merged.objects) == 2
        
        # Object types in the merged value
        types = {obj.obj_type for obj in complex_merged.objects}
        assert ObjectType.CONSTANT in types
    
    def test_container_values(self):
        list_val = create_list_value()
        obj = list(list_val.objects)[0]
        assert isinstance(obj, BuiltinObject)
        assert obj.builtin_type == list
        
        # Test container properties
        assert obj.container_property.min_size == 0
        assert obj.container_property.max_size == 0
        assert len(obj.container_property.element_types) == 0
        
        # Add an element to container
        int_val = create_int_value(42)
        for obj in list_val.objects:
            if isinstance(obj, BuiltinObject):
                # Update container properties
                for int_obj in int_val.objects:
                    obj.container_property.element_types.add(int_obj.obj_type)
                obj.container_property.element_values = int_val
                obj.container_property.min_size = 1
                obj.container_property.max_size = 1
        
        # Check updated container properties
        obj = list(list_val.objects)[0]
        assert obj.container_property.min_size == 1
        assert obj.container_property.max_size == 1
        assert ObjectType.CONSTANT in obj.container_property.element_types

# Tests for AbstractState class
class TestAbstractState:
    def test_state_creation(self):
        state = AbstractState(ContextType.CALL_SITE, FlowSensitivity.SENSITIVE)
        assert state.context_type == ContextType.CALL_SITE
        assert state.flow_sensitivity == FlowSensitivity.SENSITIVE
        assert state.current_context is not None
    
    def test_variable_tracking(self):
        state = AbstractState(ContextType.CALL_SITE, FlowSensitivity.SENSITIVE)
        
        # Set and retrieve variable
        int_val = create_int_value(42)
        state.set_variable("x", int_val)
        retrieved = state.get_variable("x")
        
        assert retrieved is not None
        assert len(retrieved.objects) == 1
        obj = list(retrieved.objects)[0]
        assert isinstance(obj, ConstantObject)
        assert obj.const_type == int
        assert obj.numeric_property.lower_bound == 42
    
    def test_context_sensitivity(self):
        state = AbstractState(ContextType.CALL_SITE, FlowSensitivity.SENSITIVE)
        
        # Create contexts
        context1 = state.create_context(1)
        context2 = state.create_context(2)
        
        # Set variables in different contexts
        state.set_current_context(context1)
        state.set_variable("x", create_int_value(1))
        
        state.set_current_context(context2)
        state.set_variable("x", create_int_value(2))
        
        # Retrieve variables in different contexts
        state.set_current_context(context1)
        x1 = state.get_variable("x")
        obj1 = list(x1.objects)[0]
        
        state.set_current_context(context2)
        x2 = state.get_variable("x")
        obj2 = list(x2.objects)[0]
        
        assert obj1.numeric_property.lower_bound == 1
        assert obj2.numeric_property.lower_bound == 2

# Tests for AbstractInterpreter class
class TestAbstractInterpreter:
    def setup_method(self):
        self.state = AbstractState(ContextType.CALL_SITE, FlowSensitivity.SENSITIVE)
        self.interpreter = AbstractInterpreter(self.state)
    
    def test_visit_assign(self):
        # Create IR assign statement
        assign = create_mock_assign("x", "y")
        
        # Set up variable y
        self.state.set_variable("y", create_int_value(42))
        
        # Interpret the assignment
        self.interpreter.visit(assign)
        
        # Check the result
        x_value = self.state.get_variable("x")
        assert x_value is not None
        obj = list(x_value.objects)[0]
        assert obj.numeric_property.lower_bound == 42
    
    def test_visit_call(self):
        # Create function and call IR
        func = create_mock_function("test_func", ["a"])
        call = create_mock_call("result", "test_func", ["arg"])
        
        # Set up variables
        self.state.set_variable("test_func", create_function_value(func))
        self.state.set_variable("arg", create_int_value(5))
        
        # Set up control flow state
        self.state.control_flow.set_current_function("main")
        
        # Interpret the call
        self.interpreter.visit(call)
        
        # The result should be unknown since we don't do interprocedural analysis here
        result = self.state.get_variable("result")
        assert result is not None
        assert any(obj.obj_type == ObjectType.UNKNOWN for obj in result.objects)
    
    def test_special_function_calls(self):
        # Test len() function
        list_val = create_list_value()
        obj = list(list_val.objects)[0]
        obj.container_property.min_size = 2
        obj.container_property.max_size = 5
        
        self.state.set_variable("my_list", list_val)
        
        len_call = create_mock_call("length", "len", ["my_list"])
        self.interpreter.visit(len_call)
        
        length = self.state.get_variable("length")
        assert length is not None
        len_obj = list(length.objects)[0]
        assert len_obj.numeric_property.lower_bound == 2
        assert len_obj.numeric_property.upper_bound == 5

# Tests for AbstractInterpretationSolver class
class TestAbstractInterpretationSolver:
    def test_solver_creation(self):
        solver = create_solver(
            context_type=ContextType.CALL_SITE,
            flow_sensitivity=FlowSensitivity.SENSITIVE,
            context_depth=1
        )
        assert solver.state.context_type == ContextType.CALL_SITE
        assert solver.state.flow_sensitivity == FlowSensitivity.SENSITIVE
        assert solver.max_iterations == 100  # Default value
    
    def test_cfg_building(self):
        solver = create_solver()
        
        # Create some statements with control flow
        stmt1 = create_mock_assign("x", "a")
        cond = JumpIfTrue(ast.Name(id="x", ctx=ast.Load()), Label(1))
        label = Label(1)
        stmt2 = create_mock_assign("y", "b")
        
        statements = [stmt1, cond, stmt2, label]
        
        # Build CFG
        solver._build_cfg("test_func", statements)
        
        # Check edges
        edges = solver.state.control_flow.get_edges("test_func")
        assert (0, 1) in edges  # stmt1 -> cond
        assert (1, 3) in edges  # cond -> label
        assert (1, 2) in edges  # cond -> stmt2
        assert (2, 3) in edges  # stmt2 -> label

# Integration tests using benchmark files
class TestBenchmarkIntegration:
    @pytest.mark.parametrize("benchmark_file", [
        "control_flow.py",
        "dataflow.py",
        "callgraph.py",
    ])
    def test_analyze_benchmark(self, benchmark_file):
        pytest.skip("This test requires the full PythonStAn pipeline and is for demonstration only")
        
        # This test would use the full PythonStAn pipeline to analyze a benchmark file
        # It's included as an example but marked as skipped since it requires the complete pipeline
        
        from pythonstan.world.pipeline import Pipeline
        
        benchmark_path = get_benchmark_path(benchmark_file)
        config = {
            "filename": str(benchmark_path),
            "project_path": str(benchmark_path.parent.parent),
            "analysis": [
                {
                    "name": "abstract_interpretation",
                    "id": "AbstractInterpretationAnalysis",
                    "options": {
                        "context_type": "call-site",
                        "flow_sensitivity": "sensitive"
                    }
                }
            ]
        }
        
        pipeline = Pipeline(config=config)
        pipeline.run()
        
        # Assert that analysis completed successfully
        assert pipeline.get_world() is not None 