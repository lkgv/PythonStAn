import pytest
import ast
from typing import Dict, Set, List, Optional

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

# Helper functions for creating mock IR statements
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
    return IRFunc(node, name, args)

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

def create_mock_store_attr(obj="obj", attr="attr", rval="value"):
    return IRStoreAttr(
        ast.Attribute(
            value=ast.Name(id=obj, ctx=ast.Load()),
            attr=attr,
            ctx=ast.Store()
        ),
        obj, attr, rval
    )

def create_mock_load_attr(lval="result", obj="obj", attr="attr"):
    return IRLoadAttr(
        ast.Attribute(
            value=ast.Name(id=obj, ctx=ast.Load()),
            attr=attr,
            ctx=ast.Load()
        ),
        lval, obj, attr
    )

def create_mock_store_subscr(obj="container", idx="idx", rval="value"):
    return IRStoreSubscr(
        ast.Subscript(
            value=ast.Name(id=obj, ctx=ast.Load()),
            slice=ast.Name(id=idx, ctx=ast.Load()),
            ctx=ast.Store()
        ),
        obj, idx, rval
    )

def create_mock_load_subscr(lval="result", obj="container", idx="idx"):
    return IRLoadSubscr(
        ast.Subscript(
            value=ast.Name(id=obj, ctx=ast.Load()),
            slice=ast.Name(id=idx, ctx=ast.Load()),
            ctx=ast.Load()
        ),
        lval, obj, idx
    )

def create_mock_return(value="result"):
    return IRReturn(
        ast.Return(
            value=ast.Name(id=value, ctx=ast.Load())
        ),
        ast.Name(id=value, ctx=ast.Load())
    )

# Test patterns for variable propagation
class TestVariablePropagation:
    def setup_method(self):
        self.state = AbstractState(ContextType.CALL_SITE, FlowSensitivity.SENSITIVE)
        self.interpreter = AbstractInterpreter(self.state)
    
    def test_simple_assignment_chain(self):
        """Test a chain of assignments to propagate values"""
        # a = 42
        # b = a
        # c = b
        
        # Set up initial value
        self.state.set_variable("a", create_int_value(42))
        
        # Create and interpret assignments
        assign1 = create_mock_assign("b", "a")
        assign2 = create_mock_assign("c", "b")
        
        self.interpreter.visit(assign1)
        self.interpreter.visit(assign2)
        
        # Verify propagation
        c_value = self.state.get_variable("c")
        assert c_value is not None
        obj = list(c_value.objects)[0]
        assert isinstance(obj, ConstantObject)
        assert obj.const_type == int
        assert obj.numeric_property.lower_bound == 42
    
    def test_conditional_propagation(self):
        """Test value propagation with conditional branches"""
        # x = 10
        # if condition:
        #     y = x
        # else:
        #     y = 20
        # z = y
        
        # Set up initial values
        self.state.set_variable("x", create_int_value(10))
        self.state.set_variable("condition", create_bool_value())
        
        # Create condition (JumpIfFalse) and labels
        label1 = Label(1)
        label2 = Label(2)
        condition_jump = JumpIfFalse(ast.Name(id="condition", ctx=ast.Load()), label1)
        
        # Create true branch: y = x
        assign_true = create_mock_assign("y", "x")
        
        # Create jump to merge point
        goto = Goto(label2)
        
        # Create false branch: y = 20
        false_val = "y_false"
        self.state.set_variable(false_val, create_int_value(20))
        assign_false = create_mock_assign("y", false_val)
        
        # Create merge: z = y
        assign_merge = create_mock_assign("z", "y")
        
        # Set up control flow
        self.state.control_flow.set_current_function("test_func")
        self.state.control_flow.register_label("test_func", label1, 3)
        self.state.control_flow.register_label("test_func", label2, 5)
        
        # Execute statements
        self.interpreter.visit(condition_jump, 0)
        self.interpreter.visit(assign_true, 1)
        self.interpreter.visit(goto, 2)
        self.interpreter.visit(assign_false, 3)
        self.interpreter.visit(assign_merge, 5)
        
        # Verify propagation
        z_value = self.state.get_variable("z")
        assert z_value is not None
        
        # z should be a merged value containing both 10 and 20
        values = set()
        for obj in z_value.objects:
            if isinstance(obj, ConstantObject) and obj.const_type == int:
                values.add(obj.numeric_property.lower_bound)
        
        # Expect both values since we're analyzing both paths
        assert 10 in values or z_value.objects[0].numeric_property.lower_bound <= 10
        assert 20 in values or z_value.objects[0].numeric_property.upper_bound >= 20

# Test patterns for object handling
class TestObjectHandling:
    def setup_method(self):
        self.state = AbstractState(ContextType.CALL_SITE, FlowSensitivity.SENSITIVE)
        self.interpreter = AbstractInterpreter(self.state)
    
    def test_attribute_access(self):
        """Test object attribute access patterns"""
        # obj.attr = 42
        # x = obj.attr
        
        # Create an object with attributes
        obj_value = create_unknown_value()  # Start with unknown object
        self.state.set_variable("obj", obj_value)
        
        # Create and interpret attribute store
        store_attr = create_mock_store_attr("obj", "attr", "attr_value")
        self.state.set_variable("attr_value", create_int_value(42))
        
        self.interpreter.visit(store_attr)
        
        # Create and interpret attribute load
        load_attr = create_mock_load_attr("x", "obj", "attr")
        self.interpreter.visit(load_attr)
        
        # Verify attribute access
        x_value = self.state.get_variable("x")
        assert x_value is not None
        
        # x should be 42
        found = False
        for obj in x_value.objects:
            if isinstance(obj, ConstantObject) and obj.const_type == int:
                assert obj.numeric_property.lower_bound == 42
                found = True
        assert found
    
    def test_container_operations(self):
        """Test container operations"""
        # container = []
        # container[idx] = 42
        # x = container[idx]
        
        # Create a container and index
        container_value = create_list_value()
        idx_value = create_int_value(0)
        self.state.set_variable("container", container_value)
        self.state.set_variable("idx", idx_value)
        
        # Create element value
        self.state.set_variable("element", create_int_value(42))
        
        # Store element in container
        store_subscr = create_mock_store_subscr("container", "idx", "element")
        self.interpreter.visit(store_subscr)
        
        # Load element from container
        load_subscr = create_mock_load_subscr("x", "container", "idx")
        self.interpreter.visit(load_subscr)
        
        # Verify container operation
        x_value = self.state.get_variable("x")
        assert x_value is not None
        
        # Check container properties were updated
        container_obj = list(container_value.objects)[0]
        assert container_obj.container_property.min_size >= 1
        
        # x should be 42 or at least an int value that came from container
        assert any(isinstance(obj, ConstantObject) and obj.const_type == int 
                  for obj in x_value.objects)

# Test patterns for function calls
class TestFunctionCalls:
    def setup_method(self):
        self.state = AbstractState(ContextType.CALL_SITE, FlowSensitivity.SENSITIVE)
        self.interpreter = AbstractInterpreter(self.state)
        self.state.control_flow.set_current_function("main")
    
    def test_simple_function_call(self):
        """Test simple function call with arguments and return value"""
        # def test_func(a):
        #     return a
        # arg = 42
        # result = test_func(arg)
        
        # Create function
        func = create_mock_function("test_func", ["a"])
        func_value = create_function_value(func)
        self.state.set_variable("test_func", func_value)
        
        # Create argument
        self.state.set_variable("arg", create_int_value(42))
        
        # Create function call
        call = create_mock_call("result", "test_func", ["arg"])
        
        # Execute call
        self.interpreter.visit(call)
        
        # Verify call was registered
        assert len(self.state.call_graph.callers.get(func.get_qualname(), [])) > 0
        
        # The result will be unknown since we don't execute the function body
        result = self.state.get_variable("result")
        assert result is not None
    
    def test_builtin_function_calls(self):
        """Test calls to built-in functions with special handling"""
        # lst = [1, 2, 3]
        # length = len(lst)
        
        # Create a list with known size
        list_val = create_list_value()
        list_obj = list(list_val.objects)[0]
        list_obj.container_property.min_size = 3
        list_obj.container_property.max_size = 3
        self.state.set_variable("lst", list_val)
        
        # Create len() call
        len_call = create_mock_call("length", "len", ["lst"])
        
        # Execute call
        self.interpreter.visit(len_call)
        
        # Verify result
        length = self.state.get_variable("length")
        assert length is not None
        len_obj = list(length.objects)[0]
        assert len_obj.numeric_property.lower_bound == 3
        assert len_obj.numeric_property.upper_bound == 3

# Test patterns for control flow handling
class TestControlFlow:
    def setup_method(self):
        self.solver = create_solver(
            context_type=ContextType.CALL_SITE,
            flow_sensitivity=FlowSensitivity.SENSITIVE
        )
    
    def test_if_else_flow(self):
        """Test flow-sensitive analysis of if-else structures"""
        # Create statements representing:
        # x = 10
        # if cond:
        #     y = x + 5
        # else:
        #     y = x - 5
        # z = y
        
        # Create labels
        cond_label = Label(1)
        else_label = Label(2)
        end_label = Label(3)
        
        # Create statements
        statements = [
            create_mock_assign("x", "x_init"),  # x = 10
            JumpIfFalse(ast.Name(id="cond", ctx=ast.Load()), else_label),
            create_mock_assign("y", "y_true"),  # y = x + 5
            Goto(end_label),
            else_label,  # Label 2
            create_mock_assign("y", "y_false"),  # y = x - 5
            end_label,  # Label 3
            create_mock_assign("z", "y")  # z = y
        ]
        
        # Set up initial values
        self.solver.state.set_variable("x_init", create_int_value(10))
        self.solver.state.set_variable("y_true", create_int_value(15))
        self.solver.state.set_variable("y_false", create_int_value(5))
        self.solver.state.set_variable("cond", create_bool_value())
        
        # Build CFG
        self.solver._build_cfg("test_func", statements)
        
        # Analyze statements
        self.solver._analyze_scope("test_func", statements)
        
        # Verify flow-sensitive analysis
        z_value = self.solver.state.get_variable("z")
        assert z_value is not None
        
        # z should be a merged value containing both possible values
        values = set()
        lower_bound = float('inf')
        upper_bound = float('-inf')
        
        for obj in z_value.objects:
            if isinstance(obj, ConstantObject) and obj.const_type == int:
                if obj.numeric_property.exact_values:
                    values.update(obj.numeric_property.exact_values)
                lower_bound = min(lower_bound, obj.numeric_property.lower_bound)
                upper_bound = max(upper_bound, obj.numeric_property.upper_bound)
        
        # Either we have exact values 5 and 15, or we have bounds that include them
        if values:
            assert 5 in values
            assert 15 in values
        else:
            assert lower_bound <= 5
            assert upper_bound >= 15

# Test patterns for context sensitivity
class TestContextSensitivity:
    def test_call_site_sensitivity(self):
        """Test call-site sensitive analysis"""
        # Create solver with call-site sensitivity
        solver = create_solver(
            context_type=ContextType.CALL_SITE,
            flow_sensitivity=FlowSensitivity.SENSITIVE,
            context_depth=1
        )
        
        # Create different contexts for different call sites
        context1 = solver.state.create_context(1)
        context2 = solver.state.create_context(2)
        
        # Set different values in different contexts
        solver.state.set_current_context(context1)
        solver.state.set_variable("x", create_int_value(1))
        
        solver.state.set_current_context(context2)
        solver.state.set_variable("x", create_int_value(2))
        
        # Verify context-sensitive values
        solver.state.set_current_context(context1)
        x1 = solver.state.get_variable("x")
        
        solver.state.set_current_context(context2)
        x2 = solver.state.get_variable("x")
        
        # Values should be different in different contexts
        obj1 = list(x1.objects)[0]
        obj2 = list(x2.objects)[0]
        
        assert obj1.numeric_property.lower_bound == 1
        assert obj2.numeric_property.lower_bound == 2
    
    def test_object_sensitivity(self):
        """Test object-sensitive analysis"""
        # Create solver with object sensitivity
        solver = create_solver(
            context_type=ContextType.OBJECT_SENSITIVE,
            flow_sensitivity=FlowSensitivity.SENSITIVE
        )
        
        # Create different contexts for different receiver objects
        context1 = solver.state.create_context_with_receiver("obj1")
        context2 = solver.state.create_context_with_receiver("obj2")
        
        # Set different values in different contexts
        solver.state.set_current_context(context1)
        solver.state.set_variable("method_result", create_int_value(1))
        
        solver.state.set_current_context(context2)
        solver.state.set_variable("method_result", create_int_value(2))
        
        # Verify context-sensitive values
        solver.state.set_current_context(context1)
        r1 = solver.state.get_variable("method_result")
        
        solver.state.set_current_context(context2)
        r2 = solver.state.get_variable("method_result")
        
        # Values should be different in different contexts
        obj1 = list(r1.objects)[0]
        obj2 = list(r2.objects)[0]
        
        assert obj1.numeric_property.lower_bound == 1
        assert obj2.numeric_property.lower_bound == 2 