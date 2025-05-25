from typing import Dict, Set, List, Optional, Tuple, Any, Union, DefaultDict
import ast

from pythonstan.ir.ir_statements import (
    IRStatement, IRScope, IRFunc, IRClass, IRModule, Label, 
    AbstractIRAssign, IRAssign, IRStoreAttr, IRLoadAttr, IRCall,
    IRStoreSubscr, IRLoadSubscr, IRReturn, IRYield, IRAwait,
    IRDel, IRImport, IRAnno, IRRaise, IRPass, JumpIfTrue, JumpIfFalse,
    Goto, IRCatchException, IRPhi
)
from pythonstan.ir.ir_visitor import IRVisitor
from pythonstan.analysis.ai.value import (
    Value, Object, ObjectType, 
    ConstantObject, BuiltinObject, ClassObject, FunctionObject, InstanceObject,
    NumericProperty, StringProperty, ContainerProperty,
    create_int_value, create_float_value, create_str_value, create_bool_value,
    create_none_value, create_list_value, create_dict_value, create_set_value,
    create_tuple_value, create_function_value, create_class_value, create_instance_value,
    create_unknown_value
)
from pythonstan.analysis.ai.state import (
    AbstractState, Context, ContextType, FlowSensitivity,
    Scope, MemoryModel, ClassHierarchy, CallGraph, ControlFlowState
)

class AbstractInterpreter(IRVisitor):
    """
    Interpreter for abstract interpretation of IR statements.
    This class implements the operations for each IR statement type.
    """
    
    def __init__(self, state: AbstractState):
        self.state = state
        self.current_return_value: Optional[Value] = None
        self.raised_exception: Optional[Value] = None
        self.current_stmt_idx: Optional[int] = None
    
    def visit(self, ir: IRStatement, stmt_idx: int = None):
        """Visit an IR statement and perform the corresponding operation"""
        # Set current statement for control flow tracking
        self.current_stmt_idx = stmt_idx
        if stmt_idx is not None and self.state.control_flow.current_function:
            self.state.control_flow.set_current_stmt(stmt_idx)
        
        # Dispatch to specific visitor method based on statement type
        return super().visit(ir)
    
    def visit_default(self, ir: IRStatement):
        """Default visitor method for statements without specific implementation"""
        # By default, add successors to worklist in flow-sensitive mode
        if self.current_stmt_idx is not None and self.state.flow_sensitivity == FlowSensitivity.SENSITIVE:
            fn_name = self.state.control_flow.current_function
            if fn_name:
                # Add next statement to worklist (sequential flow)
                next_stmt_idx = self.current_stmt_idx + 1
                self.state.control_flow.add_to_worklist(fn_name, next_stmt_idx)
        return None
    
    def visit_IRAssign(self, ir: IRAssign):
        """
        Perform abstract interpretation for variable assignment: lval = rval
        """
        # Get rvalue
        rval_name = ir.get_rval().id
        rval = self.state.get_variable(rval_name)
        
        if rval is None:
            # Variable not found, use unknown value
            rval = create_unknown_value()
        
        # Get lvalue
        lval_name = ir.get_lval().id
        
        # Set variable
        self.state.set_variable(lval_name, rval)
        
        # Continue with normal control flow
        return self.visit_default(ir)
    
    def visit_IRStoreAttr(self, ir: IRStoreAttr):
        """
        Perform abstract interpretation for attribute store: obj.attr = rval
        """
        # Get object
        obj_name = ir.get_obj().id
        obj_value = self.state.get_variable(obj_name)
        
        if obj_value is None:
            # Object not found, nothing to do
            return self.visit_default(ir)
        
        # Get rvalue
        rval_name = ir.get_rval().id
        rval = self.state.get_variable(rval_name)
        
        if rval is None:
            # Variable not found, use unknown value
            rval = create_unknown_value()
        
        # Set attribute on all objects in the value
        attr_name = ir.get_attr()
        for obj in obj_value.objects:
            obj.set_attr(attr_name, rval)
        
        # Continue with normal control flow
        return self.visit_default(ir)
    
    def visit_IRLoadAttr(self, ir: IRLoadAttr):
        """
        Perform abstract interpretation for attribute load: lval = obj.attr
        """
        # Get object
        obj_name = ir.get_obj().id
        obj_value = self.state.get_variable(obj_name)
        
        if obj_value is None:
            # Object not found, use unknown value for result
            result = create_unknown_value()
        else:
            # Get attribute from object
            attr_name = ir.get_attr()
            result = obj_value.get_attribute(attr_name)
            
            # If attribute not found, use unknown value
            if not result or not result.objects:
                result = create_unknown_value()
        
        # Set lvalue
        lval_name = ir.get_lval().id
        self.state.set_variable(lval_name, result)
        
        # Continue with normal control flow
        return self.visit_default(ir)
    
    def visit_IRStoreSubscr(self, ir: IRStoreSubscr):
        """
        Perform abstract interpretation for subscript store: obj[slice] = rval
        """
        # Get object
        obj_name = ir.get_obj().id
        obj_value = self.state.get_variable(obj_name)
        
        if obj_value is None:
            # Object not found, nothing to do
            return self.visit_default(ir)
        
        # Get rvalue
        rval_name = ir.get_rval().id
        rval = self.state.get_variable(rval_name)
        
        if rval is None:
            # Variable not found, use unknown value
            rval = create_unknown_value()
        
        # Update container properties for all container objects
        for obj in obj_value.objects:
            if isinstance(obj, BuiltinObject) and obj.builtin_type in (list, dict, set):
                # Add element type information
                for robj in rval.objects:
                    obj.container_property.element_types.add(robj.obj_type)
                
                # Update element values by merging
                obj.container_property.element_values = obj.container_property.element_values.merge(rval)
                
                # Container size may have increased if it's not a reassignment
                # Conservatively assume it might grow
                obj.container_property.min_size = max(1, obj.container_property.min_size)
                if obj.container_property.max_size is not None:
                    obj.container_property.max_size += 1
        
        # Continue with normal control flow
        return self.visit_default(ir)
    
    def visit_IRLoadSubscr(self, ir: IRLoadSubscr):
        """
        Perform abstract interpretation for subscript load: lval = obj[slice]
        """
        # Get object
        obj_name = ir.get_obj().id
        obj_value = self.state.get_variable(obj_name)
        
        if obj_value is None:
            # Object not found, use unknown value for result
            result = create_unknown_value()
        else:
            # Get element values from containers
            result = create_unknown_value()
            
            for obj in obj_value.objects:
                if isinstance(obj, BuiltinObject):
                    # Use the container's element values as result
                    result = result.merge(obj.container_property.element_values)
            
            # If no container elements found, use unknown value
            if not result.objects:
                result = create_unknown_value()
        
        # Set lvalue
        lval_name = ir.get_lval().id
        self.state.set_variable(lval_name, result)
        
        # Continue with normal control flow
        return self.visit_default(ir)
    
    def visit_IRCall(self, ir: IRCall):
        """
        Perform abstract interpretation for function call: [target =] func_name(args, keywords)
        """
        # Get function
        func_name = ir.get_func_name()
        func_value = self.state.get_variable(func_name)
        
        # Get arguments
        args = []
        for arg_name, is_starred in ir.get_args():
            if arg_name.startswith("<Constant:"):
                # Handle constant arguments
                const_str = arg_name[len("<Constant: "):-1]
                try:
                    # Try to parse as int, float, or string
                    if const_str.isdigit():
                        arg_value = create_int_value(int(const_str))
                    elif const_str.replace('.', '', 1).isdigit():
                        arg_value = create_float_value(float(const_str))
                    else:
                        # Remove quotes if it's a string
                        if (const_str.startswith('"') and const_str.endswith('"')) or \
                           (const_str.startswith("'") and const_str.endswith("'")):
                            const_str = const_str[1:-1]
                        arg_value = create_str_value(const_str)
                except:
                    arg_value = create_unknown_value()
            else:
                # Regular variable
                arg_value = self.state.get_variable(arg_name)
                if arg_value is None:
                    arg_value = create_unknown_value()
            
            args.append(arg_value)
        
        # Get keywords
        keywords = {}
        for kw_name, kw_value_name in ir.get_keywords():
            kw_value = self.state.get_variable(kw_value_name)
            if kw_value is None:
                kw_value = create_unknown_value()
            
            if kw_name is not None:
                keywords[kw_name] = kw_value
            else:
                # **kwargs expansion - not precisely modeled in abstract interpretation
                pass
        
        # Determine result value based on function type
        result = create_unknown_value()
        
        if func_value and func_value.objects:
            # We have information about the function
            for obj in func_value.objects:
                if isinstance(obj, FunctionObject):
                    # Register the call in the call graph
                    fn_qualname = self.state.control_flow.current_function
                    if fn_qualname:
                        self.state.register_call(
                            fn_qualname, 
                            obj.qualname, 
                            ir, 
                            self.current_stmt_idx,
                            self.state.current_context
                        )
                    
                    # In a real interprocedural analysis, we would:
                    # 1. Push the current context onto the call stack
                    # 2. Create a new context for the callee
                    # 3. Enter the callee function
                    # 4. Analyze the callee function
                    # 5. Get the return value
                    # 6. Return to the caller
                    
                    # For now, just use unknown value as result
                    # The solver will handle interprocedural analysis
                
                # Handle some well-known built-in functions with special logic
                elif isinstance(obj, BuiltinObject) or obj.obj_type == ObjectType.EXTERNAL_FUNCTION:
                    # Special handling for some known functions
                    if func_name == 'len' and len(args) == 1:
                        result = self._handle_len_function(args[0])
                    elif func_name == 'isinstance' and len(args) == 2:
                        result = create_bool_value()  # Could be True or False
                    elif func_name == 'int' and len(args) >= 1:
                        result = self._handle_int_function(args[0])
                    elif func_name == 'str' and len(args) >= 1:
                        result = create_str_value()
                    elif func_name == 'list' and len(args) <= 1:
                        result = self._handle_list_function(args[0] if args else None)
                    elif func_name == 'dict' and len(args) <= 1:
                        result = create_dict_value()
                    elif func_name == 'set' and len(args) <= 1:
                        result = create_set_value()
                    elif func_name == 'tuple' and len(args) <= 1:
                        result = create_tuple_value()
                    elif func_name == 'bool' and len(args) >= 1:
                        result = create_bool_value()
                    elif func_name == 'sum' and len(args) >= 1:
                        result = create_int_value() # Could be int or float depending on input
        
        # Set target if there is one
        target = ir.get_target()
        if target:
            self.state.set_variable(target, result)
        
        # Continue with normal control flow
        return self.visit_default(ir)
    
    def _handle_len_function(self, arg: Value) -> Value:
        """Special handling for len() function"""
        result_obj = ConstantObject(int)
        
        # Update numeric property based on container properties
        min_len = 0
        max_len = None
        
        for obj in arg.objects:
            if isinstance(obj, BuiltinObject):
                # Get container property
                container_prop = obj.container_property
                min_len = max(min_len, container_prop.min_size)
                
                if max_len is not None and container_prop.max_size is not None:
                    max_len = max(max_len, container_prop.max_size)
                elif container_prop.max_size is None:
                    max_len = None
            elif isinstance(obj, ConstantObject) and obj.const_type == str:
                # Handle string length
                string_prop = obj.string_property
                min_len = max(min_len, string_prop.min_length)
                
                if max_len is not None and string_prop.max_length is not None:
                    max_len = max(max_len, string_prop.max_length)
                elif string_prop.max_length is None:
                    max_len = None
        
        # Update numeric property
        result_obj.numeric_property.lower_bound = min_len
        if max_len is not None:
            result_obj.numeric_property.upper_bound = max_len
        
        # Length can't be negative
        result_obj.numeric_property.may_be_negative = False
        
        # Length can be zero only if min_len is 0
        result_obj.numeric_property.may_be_zero = (min_len == 0)
        
        # Length is always positive unless it's zero
        result_obj.numeric_property.may_be_positive = True
        
        return Value({result_obj})
    
    def _handle_int_function(self, arg: Value) -> Value:
        """Special handling for int() function"""
        result_obj = ConstantObject(int)
        
        # Check if we can determine more precise bounds
        for obj in arg.objects:
            if isinstance(obj, ConstantObject):
                if obj.const_type == int:
                    # int(integer) returns the same integer
                    result_obj.numeric_property = obj.numeric_property
                elif obj.const_type == float:
                    # int(float) truncates towards zero
                    if obj.numeric_property.lower_bound > 0:
                        result_obj.numeric_property.lower_bound = int(obj.numeric_property.lower_bound)
                        result_obj.numeric_property.upper_bound = int(obj.numeric_property.upper_bound)
                    elif obj.numeric_property.upper_bound < 0:
                        result_obj.numeric_property.lower_bound = int(obj.numeric_property.lower_bound)
                        result_obj.numeric_property.upper_bound = int(obj.numeric_property.upper_bound)
                    else:
                        # Could truncate to 0 if the float is between -1 and 1
                        result_obj.numeric_property.may_be_zero = True
                elif obj.const_type == str:
                    # int(string) is hard to determine statically
                    # If we have exact string values, we could try to parse them
                    if obj.string_property.exact_values:
                        exact_ints = set()
                        for s in obj.string_property.exact_values:
                            try:
                                exact_ints.add(int(s))
                            except:
                                pass
                        if exact_ints:
                            result_obj.numeric_property.exact_values = exact_ints
                    
                    # Otherwise, just use default int properties
        
        return Value({result_obj})
    
    def _handle_list_function(self, arg: Optional[Value]) -> Value:
        """Special handling for list() function"""
        result_obj = BuiltinObject(list)
        
        if arg:
            # list(iterable) - copy elements from iterable
            for obj in arg.objects:
                if isinstance(obj, BuiltinObject):
                    # Copy container properties
                    result_obj.container_property = obj.container_property
                    break
        
        return Value({result_obj})
    
    def visit_IRReturn(self, ir: IRReturn):
        """
        Perform abstract interpretation for return statement: return [value]
        """
        # Get return value if any
        value = ir.get_value()
        if value:
            value_name = value.id
            value_obj = self.state.get_variable(value_name)
            if value_obj:
                self.current_return_value = value_obj
            else:
                self.current_return_value = create_unknown_value()
        else:
            # Return None
            self.current_return_value = create_none_value()
        
        # In a flow-sensitive analysis, we would not continue to successor statements
        # But for now, we'll just continue with normal control flow
        return self.visit_default(ir)
    
    def visit_IRRaise(self, ir: IRRaise):
        """
        Perform abstract interpretation for raise statement: raise exc [from cause]
        """
        # Get exception
        exc = ir.exc
        if exc and isinstance(exc, ast.Name):
            exc_name = exc.id
            exc_value = self.state.get_variable(exc_name)
            if exc_value:
                self.raised_exception = exc_value
            else:
                self.raised_exception = create_unknown_value()
        else:
            self.raised_exception = create_unknown_value()
        
        # In a flow-sensitive analysis, we would transfer control to exception handler
        # But for now, we'll just continue with normal control flow
        return self.visit_default(ir)
    
    def visit_IRCatchException(self, ir: IRCatchException):
        """
        Perform abstract interpretation for exception handler: catch exp from L1 to L2 goto L3
        """
        # Get labels
        from_label = ir.from_label
        to_label = ir.to_label
        goto_label = ir.goto_label
        
        # In a flow-sensitive analysis, we would:
        # 1. If an exception was raised in the try block, transfer control to the handler
        # 2. If no exception was raised, continue with normal control flow
        # But for now, we'll just continue with normal control flow
        return self.visit_default(ir)
    
    def visit_JumpIfTrue(self, ir: JumpIfTrue):
        """
        Perform abstract interpretation for conditional jump: if test goto label
        """
        # Get test expression
        test = ir.test
        
        # Evaluate test expression (simplified)
        test_result = None
        if isinstance(test, ast.Name):
            test_name = test.id
            test_value = self.state.get_variable(test_name)
            if test_value:
                # Check if test is definitely True or False
                test_result = self._evaluate_boolean_value(test_value)
        
        # Get label
        label = ir.label
        
        # In a flow-sensitive analysis:
        fn_name = self.state.control_flow.current_function
        if fn_name:
            # Add both branches to worklist (we don't know which will be taken)
            
            # Add target of jump (taken branch)
            label_idx = self.state.control_flow.get_label_idx(fn_name, label.idx)
            if label_idx is not None:
                self.state.control_flow.add_to_worklist(fn_name, label_idx)
            
            # Add next statement (not taken branch)
            self.state.control_flow.add_to_worklist(fn_name, self.current_stmt_idx + 1)
        
        # No return value needed
        return None
    
    def visit_JumpIfFalse(self, ir: JumpIfFalse):
        """
        Perform abstract interpretation for conditional jump: if not test goto label
        """
        # Similar to JumpIfTrue but with negated condition
        # Get test expression
        test = ir.test
        
        # Evaluate test expression (simplified)
        test_result = None
        if isinstance(test, ast.Name):
            test_name = test.id
            test_value = self.state.get_variable(test_name)
            if test_value:
                # Check if test is definitely True or False
                test_result = self._evaluate_boolean_value(test_value)
        
        # Get label
        label = ir.label
        
        # In a flow-sensitive analysis:
        fn_name = self.state.control_flow.current_function
        if fn_name:
            # Add both branches to worklist (we don't know which will be taken)
            
            # Add target of jump (taken branch)
            label_idx = self.state.control_flow.get_label_idx(fn_name, label.idx)
            if label_idx is not None:
                self.state.control_flow.add_to_worklist(fn_name, label_idx)
            
            # Add next statement (not taken branch)
            self.state.control_flow.add_to_worklist(fn_name, self.current_stmt_idx + 1)
        
        # No return value needed
        return None
    
    def visit_Goto(self, ir: Goto):
        """
        Perform abstract interpretation for unconditional jump: goto label
        """
        # Get label
        label = ir.label
        
        # In a flow-sensitive analysis:
        fn_name = self.state.control_flow.current_function
        if fn_name and label:
            # Add target of jump to worklist
            label_idx = self.state.control_flow.get_label_idx(fn_name, label.idx)
            if label_idx is not None:
                self.state.control_flow.add_to_worklist(fn_name, label_idx)
        
        # No return value needed
        return None
    
    def visit_Label(self, ir: Label):
        """
        Perform abstract interpretation for label: label_idx:
        """
        # Register label in control flow state
        fn_name = self.state.control_flow.current_function
        if fn_name:
            self.state.control_flow.register_label(fn_name, ir, self.current_stmt_idx)
        
        # Continue with normal control flow
        return self.visit_default(ir)
    
    def visit_IRDel(self, ir: IRDel):
        """
        Perform abstract interpretation for delete statement: del value
        """
        # In abstract interpretation, we don't actually delete variables
        # We just mark them as potentially undefined
        
        # Get variable name
        value = ir.value
        if isinstance(value, ast.Name):
            var_name = value.id
            # We could set the variable to a special "undefined" value
            # But for now, we'll just leave it as is
        
        # Continue with normal control flow
        return self.visit_default(ir)
    
    def visit_IRImport(self, ir: IRImport):
        """
        Perform abstract interpretation for import statement: import [module.]name as [asname]
        """
        # Get import information
        module = ir.module
        name = ir.name
        asname = ir.asname if ir.asname else name
        
        # In abstract interpretation, we create an unknown value for imports
        # A more precise analysis would resolve imports and use actual types
        import_value = create_unknown_value()
        
        # Set imported name in current scope
        self.state.set_variable(asname, import_value)
        
        # Continue with normal control flow
        return self.visit_default(ir)
    
    def visit_IRAnno(self, ir: IRAnno):
        """
        Perform abstract interpretation for type annotation: target: anno
        """
        # Type annotations don't affect runtime behavior
        # So we don't need to do anything in abstract interpretation
        
        # Continue with normal control flow
        return self.visit_default(ir)
    
    def visit_IRYield(self, ir: IRYield):
        """
        Perform abstract interpretation for yield statement: [target =] yield value
        """
        # Get yield value if any
        result = create_unknown_value()
        if ir.value:
            value_name = ir.value.id
            value_obj = self.state.get_variable(value_name)
            if value_obj:
                # For yield, the result depends on what's sent to the generator
                # For yield from, the result is whatever the sub-generator yields
                if ir.is_yield_from():
                    # For yield from, the result can be any value yielded from the sub-generator
                    # For simplicity, we use an unknown value
                    result = create_unknown_value()
                else:
                    # For regular yield, the result is whatever is sent back to the generator
                    # For simplicity, we use an unknown value
                    result = create_unknown_value()
            else:
                result = create_unknown_value()
        
        # Set target if there is one
        target = ir.target
        if target and isinstance(target, ast.Name):
            target_name = target.id
            self.state.set_variable(target_name, result)
        
        # Continue with normal control flow
        return self.visit_default(ir)
    
    def visit_IRAwait(self, ir: IRAwait):
        """
        Perform abstract interpretation for await statement: [target =] await value
        """
        # Get await value
        result = create_unknown_value()
        value = ir.get_value()
        if isinstance(value, ast.Name):
            value_name = value.id
            value_obj = self.state.get_variable(value_name)
            if value_obj:
                # For await, the result is the resolved value of the awaitable
                # For simplicity, we use an unknown value
                result = create_unknown_value()
            else:
                result = create_unknown_value()
        
        # Set target if there is one
        target = ir.get_target()
        if target:
            target_name = target.id
            self.state.set_variable(target_name, result)
        
        # Continue with normal control flow
        return self.visit_default(ir)
    
    def visit_IRPass(self, ir: IRPass):
        """
        Perform abstract interpretation for pass statement: pass
        """
        # Pass does nothing
        # Continue with normal control flow
        return self.visit_default(ir)
    
    def visit_IRPhi(self, ir: IRPhi):
        """
        Perform abstract interpretation for phi node: lval = Phi(items)
        """
        # Get phi items
        items = ir.get_items()
        
        # Merge values from all items
        result = create_unknown_value()
        for item in items:
            if item:
                item_name = item.id
                item_value = self.state.get_variable(item_name)
                if item_value:
                    result = result.merge(item_value)
        
        # Set lvalue
        lval_name = ir.get_lval().id
        self.state.set_variable(lval_name, result)
        
        # Continue with normal control flow
        return self.visit_default(ir)
    
    def visit_IRFunc(self, ir: IRFunc):
        """
        Perform abstract interpretation for function definition: def name(...): ...
        """
        # Create function object
        func_obj = FunctionObject(ir)
        func_value = create_function_value(ir)
        
        # Set function in current scope
        self.state.set_variable(ir.name, func_value)
        
        # Continue with normal control flow
        return self.visit_default(ir)
    
    def visit_IRClass(self, ir: IRClass):
        """
        Perform abstract interpretation for class definition: class name(...): ...
        """
        # Register class in class hierarchy
        class_obj = self.state.register_class(ir)
        class_value = create_class_value(ir)
        
        # Set class in current scope
        self.state.set_variable(ir.name, class_value)
        
        # Continue with normal control flow
        return self.visit_default(ir)
    
    def visit_IRModule(self, ir: IRModule):
        """
        Perform abstract interpretation for module: module name
        """
        # Initialize state for module
        self.state.initialize_for_module(ir)
        
        # Continue with normal control flow
        return self.visit_default(ir, stmt_idx)
    
    def _evaluate_boolean_value(self, value: Value) -> Optional[bool]:
        """
        Evaluate if a value is definitely True or definitely False.
        Returns True if definitely True, False if definitely False, None if unknown.
        """
        # Check for definite True/False values
        definitely_true = False
        definitely_false = False
        
        for obj in value.objects:
            if isinstance(obj, ConstantObject):
                if obj.const_type == bool:
                    if hasattr(obj, 'bool_value'):
                        if obj.bool_value is True:
                            definitely_true = True
                        elif obj.bool_value is False:
                            definitely_false = True
                elif obj.const_type == int:
                    # Check if number is definitely zero or definitely non-zero
                    if not obj.numeric_property.may_be_zero and (
                        obj.numeric_property.may_be_positive or obj.numeric_property.may_be_negative):
                        definitely_true = True
                    elif (not obj.numeric_property.may_be_positive and 
                          not obj.numeric_property.may_be_negative and 
                          obj.numeric_property.may_be_zero):
                        definitely_false = True
                elif obj.const_type == str:
                    # Check if string is definitely empty or definitely non-empty
                    if obj.string_property.min_length > 0:
                        definitely_true = True
                    elif obj.string_property.max_length == 0:
                        definitely_false = True
                elif obj.const_type == type(None):
                    # None is always False
                    definitely_false = True
            elif isinstance(obj, BuiltinObject):
                # Check if container is definitely empty or definitely non-empty
                if obj.container_property.min_size > 0:
                    definitely_true = True
                elif obj.container_property.max_size == 0:
                    definitely_false = True
        
        # If we have conflicting information, return None (unknown)
        if definitely_true and definitely_false:
            return None
        elif definitely_true:
            return True
        elif definitely_false:
            return False
        else:
            return None 