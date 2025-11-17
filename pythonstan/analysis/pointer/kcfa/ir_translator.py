"""IR to constraint translation.

This module translates IR events to pointer constraints for analysis.
"""

from typing import List, TYPE_CHECKING, Optional, Tuple, Dict
import logging, ast

from .constraints import *
from .config import Config
from .variable import Variable, VariableFactory, VariableKind
from .heap_model import elem, attr, key
from pythonstan.ir.ir_statements import *
from .object import AllocSite, AllocKind

logger = logging.getLogger(__name__)

__all__ = ["IRTranslator"]

BINOP_TABLE = {
    ast.Add: "__add__",       # +  
    ast.Sub: "__sub__",       # -  
    ast.Mult: "__mul__",      # *  
    ast.Div: "__truediv__",   # /  
    ast.FloorDiv: "__floordiv__",  # //  
    ast.Mod: "__mod__",       # %  
    ast.Pow: "__pow__",       # **  
    ast.LShift: "__lshift__", # <<  
    ast.RShift: "__rshift__", # >>  
    ast.BitOr: "__or__",      # |  
    ast.BitXor: "__xor__",    # ^  
    ast.BitAnd: "__and__",    # &  
    ast.MatMult: "__matmul__", # @ (矩阵乘法)  
    ast.Eq: "__eq__",         # ==  
    ast.NotEq: "__ne__",      # !=  
    ast.Lt: "__lt__",         # <  
    ast.LtE: "__le__",        # <=  
    ast.Gt: "__gt__",         # >  
    ast.GtE: "__ge__",        # >=  
    ast.In: "__contains__",    # in  
}


class IRTranslator:
    """Translates IR to pointer constraints."""
    def __init__(self, config: 'Config'):    
        self.config = config
        self._var_factory = VariableFactory()
        self._current_scope: Optional['IRScope'] = None
        self._current_module: Optional['IRModule'] = None
        self._scope_constraints: Dict[IRScope, List['Constraint']] = {}
        self._import_depth = 0  # Track import depth for recursion limit
        
        from pythonstan.world import World
        self.scope_manager = World().scope_manager
        self.namespace_manager = World().namespace_manager
    
    def translate_function(self, func: IRFunc) -> List['Constraint']:        
        constraints = []
        
        # avoid infinite recursion
        if func in self._scope_constraints:
            return self._scope_constraints[func]
        
        self._current_scope = func
        stmts = self.scope_manager.get_ir(func, 'ir')
        if stmts is not None:
            for stmt in stmts:
                constraints.extend(self._process_stmt(stmt))
        
        self._scope_constraints[func] = constraints
        return constraints
    
    def translate_module(self, module: IRModule, import_stmt: Optional['IRImport'] = None
                         ) -> List['Constraint']:
        assert isinstance(module, IRModule), f"Module is not an IRModule: {type(module)}"

        # avoid infinite recursion
        if module in self._scope_constraints:
            return self._scope_constraints[module]
        
        constraints = []
        ir = self.scope_manager.get_ir(module, 'ir')

        if ir is not None:
            self._current_scope = module
            self._current_module = module
            for stmt in ir:
                constraints.extend(self._process_stmt(stmt))

            self._scope_constraints[module] = constraints
        
        return constraints
    
        
    def translate_class(self, cls_stmt: IRClass) -> Tuple[Variable, List['Constraint']]:
        """Translate class definition: allocate class object and bind methods."""
        assert isinstance(cls_stmt, IRClass), f"Class is not an IRClass: {type(cls_stmt)}"

        if cls_stmt in self._scope_constraints:
            return self._scope_constraints[cls_stmt]
        
        constraints = []
        self.used_variables = []
        irs = self.scope_manager.get_ir(cls_stmt, "ir")

        if irs is not None:
            old_scope = self._current_scope
            self._current_scope = cls_stmt
            for stmt in irs:
                constraints.extend(self._process_stmt(stmt))
            self._current_scope = old_scope

            self._scope_constraints[cls_stmt] = constraints
        
        return constraints
    
    def _make_variable(self, name: str) -> 'Variable':
        if self._current_scope is None:
            raise RuntimeError("No active scope for variable creation")
        
        if isinstance(self._current_scope, IRModule):
            kind = VariableKind.GLOBAL
        elif name in self._current_scope.get_nonlocal_vars():
            kind = VariableKind.NONLOCAL
        elif name in self._current_scope.get_global_vars():
            kind = VariableKind.GLOBAL
        else:
            kind = VariableKind.LOCAL

        '''
        if name.startswith('$'):
            kind = VariableKind.TEMPORARY
        elif name in self._current_scope.get_nonlocal_vars():
            kind = VariableKind.NONLOCAL
        elif name in self._current_scope.get_cell_vars():
            kind = VariableKind.CELL
        elif name in self._current_scope.get_global_vars():
            kind = VariableKind.GLOBAL
        else:
            kind = VariableKind.LOCAL
        '''
            
        return self._var_factory.make_variable(
            name=name,
            kind=kind
        )
    
    def _process_stmt(self, stmt: IRStatement) -> List['Constraint']:
        ret = []
        
        if isinstance(stmt, IRCopy):
            ret = self._translate_copy(stmt)
        elif isinstance(stmt, IRAssign):
            ret = self._translate_assign(stmt)
        elif isinstance(stmt, IRLoadAttr):
            ret = self._translate_load_attr(stmt)
        elif isinstance(stmt, IRStoreAttr):
            ret = self._translate_store_attr(stmt)
        elif isinstance(stmt, IRCall):
            ret = self._translate_call(stmt)
        elif isinstance(stmt, IRReturn):
            ret = self._translate_return(stmt)
        elif isinstance(stmt, IRLoadSubscr):
            ret = self._translate_load_subscr(stmt)
        elif isinstance(stmt, IRStoreSubscr):
            ret = self._translate_store_subscr(stmt)
        elif isinstance(stmt, IRFunc):
            _, ret = self._translate_function_def(stmt)
        elif isinstance(stmt, IRClass):
            _, ret = self._translate_class_def(stmt)
        elif isinstance(stmt, IRImport):
            ret = self._translate_import(stmt)
        elif isinstance(stmt, IRYield):
            ret = self._translate_yield(stmt)
        elif isinstance(stmt, IRAwait):
            ret = self._translate_await(stmt)
        
        for c in ret:
            assert isinstance(c, Constraint), f"Constraint is not a constraint: {type(c)}, stmt: {stmt}"

        return ret
    
    def _translate_copy(self, stmt: IRCopy) -> List['Constraint']:
        """Translate IRCopy: target = source"""
        constraints = []
        
        lval = stmt.get_lval().id
        rval = stmt.get_rval().id
        
        source_var = self._make_variable(rval)
        target_var = self._make_variable(lval)
        constraints.append(CopyConstraint(source=source_var, target=target_var))
        
        # for fields of class, we need to store the value to the class
        if (isinstance(self._current_scope, IRClass) and 
            not target_var.name.startswith("$")): # only store the field to the class if it is not a temporary variable
            self.used_variables.append(target_var)
        
        return constraints

    def _translate_yield(self, stmt: IRYield) -> List['Constraint']:
        """Translate IRYield: yield value"""
        # raise NotImplementedError("Yield statement is not supported yet")
        return []
    
    def _translate_await(self, stmt: IRAwait) -> List['Constraint']:
        """Translate IRAwait: await value"""
        # raise NotImplementedError("Await statement is not supported yet")
        return []
    
    def _translate_assign(self, stmt: IRAssign) -> List['Constraint']:
        """Translate IRAssign: may allocate objects"""
        constraints = []
        lval = stmt.get_lval()
        rval = stmt.get_rval()
        
        target_var = self._make_variable(lval.id)
        
        if isinstance(rval, ast.List):
            alloc_site = AllocSite.from_ir_node(stmt, AllocKind.LIST)
            constraints.append(AllocConstraint(target=target_var, alloc_site=alloc_site))
            if hasattr(rval, 'elts'):
                for i, elt_expr in enumerate(rval.elts):
                    if isinstance(elt_expr, ast.Name):
                        elem_var = self._make_variable(elt_expr.id)
                        if self.config.index_sensitive:
                            # Store at specific position field                            
                            constraints.append(StoreConstraint(
                                base=target_var,
                                field=key(i),
                                source=elem_var
                            ))
                        # Also store at generic elem() for soundness
                        constraints.append(StoreConstraint(
                            base=target_var,
                            field=elem(),
                            source=elem_var
                        ))
                        
        elif isinstance(rval, ast.BinOp):
            lvar = self._make_variable(rval.left.id)
            rvar = self._make_variable(rval.right.id)
            target = self._make_variable(lval.id)
            self._translate_binary_op(lvar, rvar, target, rval.op)
            
        elif isinstance(rval, ast.Dict):
            alloc_site = AllocSite.from_ir_node(stmt, AllocKind.DICT)
            constraints.append(AllocConstraint(target=target_var, alloc_site=alloc_site))
            if hasattr(rval, 'keys') and hasattr(rval, 'values'):
                for i, (key_expr, val_expr) in enumerate(zip(rval.keys, rval.values)):
                    if isinstance(val_expr, ast.Name):
                        val_var = self._make_variable(val_expr.id)
                        # Use key field for constant string keys, value() as fallback
                        if isinstance(key_expr, ast.Constant) and self.config.index_sensitive:
                            field = key(key_expr.value)
                            constraints.append(StoreConstraint(
                                base=target_var,
                                field=field,
                                source=val_var
                            ))
                        constraints.append(StoreConstraint(
                            base=target_var,
                            field=elem(),
                            source=val_var
                        ))
                        
        # Tuple uses position-based fields for precise tracking
        elif isinstance(rval, ast.Tuple):
            alloc_site = AllocSite.from_ir_node(stmt, AllocKind.TUPLE)
            constraints.append(AllocConstraint(target=target_var, alloc_site=alloc_site))
            if hasattr(rval, 'elts'):
                for i, elt_expr in enumerate(rval.elts):
                    if isinstance(elt_expr, ast.Name):
                        elem_var = self._make_variable(elt_expr.id)                        
                        if self.config.index_sensitive:
                            # Store at specific position field
                            constraints.append(StoreConstraint(
                                base=target_var,
                                field=key(i),
                                source=elem_var
                            ))
                        # Also store at generic elem() for soundness
                        constraints.append(StoreConstraint(
                            base=target_var,
                            field=elem(),
                            source=elem_var
                        ))
            
        elif isinstance(rval, ast.Set):
            alloc_site = AllocSite.from_ir_node(stmt, AllocKind.SET)
            constraints.append(AllocConstraint(target=target_var, alloc_site=alloc_site))
            if hasattr(rval, 'elts'):
                for elt_expr in rval.elts:
                    if isinstance(elt_expr, ast.Name):
                        elem_var = self._make_variable(elt_expr.id)
                        constraints.append(StoreConstraint(
                            base=target_var,
                            field=elem(),
                            source=elem_var
                        ))
        
        elif isinstance(rval, ast.Constant):
            alloc_site = AllocSite.from_ir_node(stmt, AllocKind.CONSTANT)
            constraints.append(AllocConstraint(target=target_var, alloc_site=alloc_site))
        
        # for fields of class, we need to store the value to the class
        if (isinstance(self._current_scope, IRClass) and 
            not target_var.name.startswith("$")): # only store the field to the class if it is not a temporary variable
            self.used_variables.append(target_var)
        
        return constraints
    
    def _translate_load_attr(self, stmt: IRLoadAttr) -> List['Constraint']:
        """Translate IRLoadAttr: target = base.attr"""       
        lval = stmt.get_lval().id
        obj_name = stmt.get_obj().id
        attr_name = stmt.get_attr()
        
        base_var = self._make_variable(obj_name)
        target_var = self._make_variable(lval)
        field = attr(attr_name) if attr_name else attr("<unknown>")

        if (isinstance(self._current_scope, IRClass) and 
            not target_var.name.startswith("$")): # only store the field to the class if it is not a temporary variable
            self.used_variables.append(target_var)
        
        return [LoadConstraint(base=base_var, field=field, target=target_var)]
    
    def _translate_store_attr(self, stmt: IRStoreAttr) -> List['Constraint']:
        """Translate IRStoreAttr: base.attr = source"""
        obj_name = stmt.get_obj().id
        attr_name = stmt.get_attr()
        value_name = stmt.get_rval().id
        
        base_var = self._make_variable(obj_name)
        source_var = self._make_variable(value_name)
        field = attr(attr_name) if attr_name else attr("<unknown>")
        
        return [StoreConstraint(base=base_var, field=field, source=source_var)]
    
    def _translate_call(self, stmt: IRCall) -> List['Constraint']:
        """Translate IRCall: target = callee(args...)"""
        # TODO all conditions of arguments should be translated to constraints
        constraints = []
        
        lval = stmt.get_target()
        callee_expr = stmt.get_func_name()
        args = stmt.get_args()
        
        callee_var = self._make_variable(callee_expr)
        arg_vars = tuple(self._make_variable(arg) for arg, _ in args)
        
        target_var = self._make_variable(lval) if lval else None
        
        call_site_id = f"{self._current_scope.get_qualname()}:{stmt.get_ast().lineno}:{stmt.get_ast().col_offset}:{stmt}"
        keyword_vars = {kw_name: self._make_variable(kw_val) 
                        for kw_name, kw_val in stmt.get_keywords() 
                        if kw_name is not None}
        constraints.append(CallConstraint(
            callee=callee_var,
            args=arg_vars,
            kwargs=frozenset(keyword_vars.items()),
            target=target_var,
            call_site=call_site_id
        ))
        
        # for fields of class, we need to store the value to the class
        if (isinstance(self._current_scope, IRClass) and 
            not target_var.name.startswith("$")): # only store the field to the class if it is not a temporary variable
            self.used_variables.append(target_var)
        
        return constraints
    
    def _translate_return(self, stmt: IRReturn) -> List['Constraint']:
        """Translate IRReturn: return value"""
        rval = stmt.get_value()
        
        if rval:
            source_var = self._make_variable(rval)
            return_var = self._make_variable("$return")
            return [CopyConstraint(source=source_var, target=return_var)]
        
        return []
    
    def _translate_load_subscr(self, stmt: IRLoadSubscr) -> List['Constraint']:        
        """Translate IRLoadSubscr: target = container[index]"""
        lval = stmt.get_lval().id
        container_name = stmt.get_obj().id
        
        subslice = stmt.get_slice()
        
        container_var = self._make_variable(container_name)
        target_var = self._make_variable(lval)
        
        constraints = []
        
        # Extract index variable for dynamic field resolution
        if hasattr(subslice, 'id'):
            index_var = self._make_variable(subslice.id)
        else:
            # For non-name indexes (constants, etc.), create a temporary
            index_var = self._make_variable(f"$index_{id(stmt)}")
        
        # Generate LoadSubscrConstraint with index variable for dynamic resolution
        if self.config.index_sensitive:
            constraints.append(LoadSubscrConstraint(
                base=container_var,
                index=index_var,
                target=target_var
            ))
        else:
            constraints.append(LoadConstraint(
                base=container_var,
                field=elem(),
                target=target_var
            ))
        
        # Also generate __getitem__ call for custom container types
        getitem_method_var = self._make_variable(f"$getitem_{id(stmt)}")
        constraints.append(LoadConstraint(
            base=container_var,
            field=attr("__getitem__"),
            target=getitem_method_var,
            index=None
        ))
        
        try:
            call_site = f"{self._current_scope.name}:getitem:{id(stmt)}"
            constraints.append(CallConstraint(
                callee=getitem_method_var,
                args=(index_var,),
                kwargs=frozenset(),
                target=target_var,
                call_site=call_site
            ))
        except Exception as e:
            logger.debug(f"Error generating __getitem__ call: {e}")
        
        # for fields of class, we need to store the value to the class
        if (isinstance(self._current_scope, IRClass) and 
            not target_var.name.startswith("$")): # only store the field to the class if it is not a temporary variable
            self.used_variables.append(target_var)
        
        return constraints
    
    def _translate_store_subscr(self, stmt: IRStoreSubscr) -> List['Constraint']:
        """Translate IRStoreSubscr: container[index] = value"""
        container_name = stmt.get_obj().id
        value_name = stmt.get_rval().id
        
        container_var = self._make_variable(container_name)
        value_var = self._make_variable(value_name)
        
        constraints = []
        
        # Extract index variable for dynamic field resolution
        index_expr = stmt.get_slice()
        if hasattr(index_expr, 'id'):
            index_var = self._make_variable(index_expr.id)
        else:
            # For non-name indexes (constants, etc.), create a temporary
            index_var = self._make_variable(f"$index_{id(stmt)}")
        
        # Generate StoreSubscrConstraint with index variable for dynamic resolution
        constraints.append(StoreSubscrConstraint(
            base=container_var,
            index=index_var,
            source=value_var
        ))
        if self.config.index_sensitive:
            constraints.append(StoreSubscrConstraint(
                base=container_var,
                index=index_var,
                source=value_var
            ))
        else:
            constraints.append(StoreConstraint(
                base=container_var,
                field=elem(),
                source=value_var
            ))
        
        # Also generate __setitem__ call for custom container types
        setitem_method_var = self._make_variable(f"$setitem_{id(stmt)}")
        constraints.append(LoadConstraint(
            base=container_var,
            field=attr("__setitem__"),
            target=setitem_method_var,
            index=None
        ))
        
        try:
            call_site = f"{self._current_scope.name}:setitem:{id(stmt)}"
            
            constraints.append(CallConstraint(
                callee=setitem_method_var,
                args=(index_var, value_var),
                kwargs=frozenset(),
                target=None,
                call_site=call_site
            ))
        except Exception as e:
            logger.debug(f"Error generating __setitem__ call: {e}")
        
        return constraints
    
    def _translate_function_def(self, stmt: IRFunc) -> Tuple[Variable, List['Constraint']]:
        """Translate function definition: allocate function object."""
        constraints = []
        
        if isinstance(self._current_scope, IRClass) and (not stmt.is_static_method):
            func_alloc = AllocSite.from_ir_node(stmt, AllocKind.METHOD)
        else:
            func_alloc = AllocSite.from_ir_node(stmt, AllocKind.FUNCTION)
        func_var = self._make_variable(stmt.name)
        constraints.append(AllocConstraint(target=func_var, alloc_site=func_alloc))
        
        if hasattr(stmt, 'decorator_list') and stmt.decorator_list:
            current_var = func_var
            for idx, decorator_expr in enumerate(reversed(stmt.decorator_list)):
                try:
                    # Extract decorator variable/name
                    if isinstance(decorator_expr, ast.Name):
                        decorator_var = self._make_variable(decorator_expr.id)
                    elif isinstance(decorator_expr, ast.Attribute):
                        # e.g., @obj.decorator
                        # Create temporary and load decorator from object
                        decorator_var = self._make_variable(f"$decorator_{stmt.name}_{idx}")
                        if isinstance(decorator_expr.value, ast.Name):
                            obj_var = self._make_variable(decorator_expr.value.id)
                            constraints.append(LoadConstraint(
                                base=obj_var,
                                field=attr(decorator_expr.attr),
                                target=decorator_var
                            ))
                    elif isinstance(decorator_expr, ast.Call):
                        # e.g., @decorator(args)
                        # Create temporary for decorator factory result
                        decorator_var = self._make_variable(f"$decorator_{stmt.name}_{idx}")
                        # Skip detailed call analysis for now
                        logger.debug(f"Complex decorator call skipped for {stmt.name}")
                        continue
                    else:
                        # Complex decorator expression - create temporary
                        decorator_var = self._make_variable(f"$decorator_{stmt.name}_{idx}")
                        logger.debug(f"Complex decorator expression for {stmt.name}")
                    
                    # result = decorator(current)
                    result_var = self._make_variable(f"{stmt.name}_decorated_{idx}")
                    call_site = f"{self._current_scope.name}:decorator:{id(decorator_expr)}"
                    
                    constraints.append(CallConstraint(
                        callee=decorator_var,
                        args=(current_var,),
                        kwargs=frozenset(),
                        target=result_var,
                        call_site=call_site
                    ))
                    
                    current_var = result_var
                except Exception as e:
                    logger.debug(f"Error handling decorator {idx} for {stmt.name}: {e}")
            
            # Rebind function name to final decorated result
            if len(stmt.decorator_list) > 0:
                constraints.append(CopyConstraint(source=current_var, target=func_var))
                
        # for fields of class, we need to store the value to the class
        if (isinstance(self._current_scope, IRClass) and 
            not func_var.name.startswith("$")): # only store the field to the class if it is not a temporary variable
            self.used_variables.append(func_var)
        
        return func_var, constraints
    
    def _translate_class_def(self, ir_cls: IRClass) -> Tuple[Variable, List['Constraint']]:
        """Translate class definition: allocate class object and bind methods."""
        constraints = []
        
        class_alloc = AllocSite.from_ir_node(ir_cls, AllocKind.CLASS)
        class_var = self._make_variable(ir_cls.name)
        constraints.append(AllocConstraint(target=class_var, alloc_site=class_alloc))

        
        # TODO [CRITICAL] the method of treating inheritances is totally wrong, we should use the class hierarchy manager to handle this.
        '''
        for base_name in ir_cls.get_bases():
            if not isinstance(base_name, ast.Name):
                continue # TODO resolve the base name in detail
            
            base_var = self._make_variable(base_name.id)
            constraints.append(StoreConstraint(
                base=class_var,
                field=attr("__bases__"),
                source=base_var
            ))
        '''
        
        # for fields of class, we need to store the value to the class
        if (isinstance(self._current_scope, IRClass) and 
            not class_var.name.startswith("$")): # only store the field to the class if it is not a temporary variable
            self.used_variables.append(class_var)
        
        return class_var, constraints
    
    def _translate_import(self, stmt: IRImport) -> List['Constraint']:
        """Translate import statement with transitive analysis. """
        constraints = []

        # translate the IRs in the imported module
        module_ir = self.scope_manager.get_module_graph().get_succ_module(self._current_scope, stmt)
        if module_ir is None:
            alloc_site = AllocSite.from_ir_node(stmt, AllocKind.UNKNOWN)
        else:
            alloc_site = AllocSite.from_ir_node(stmt, AllocKind.MODULE)

        # allocate the module variable
        if stmt.module is None:
            if stmt.asname is None:
                module_var = self._make_variable(stmt.name)
            else:
                module_var = self._make_variable(stmt.asname)
            constraints.append(AllocConstraint(target=module_var, alloc_site=alloc_site))
        elif stmt.asname is None:
            module_var = self._make_variable(stmt.module)
            constraints.append(AllocConstraint(target=module_var, alloc_site=alloc_site))
            local_var = self._make_variable(stmt.name)
            constraints.append(LoadConstraint(base=module_var, field=attr(stmt.name), target=local_var))
        else:
            module_var = self._make_variable(stmt.module)
            constraints.append(AllocConstraint(target=module_var, alloc_site=alloc_site))
            local_var = self._make_variable(stmt.asname)
            constraints.append(LoadConstraint(base=module_var, field=attr(stmt.name), target=local_var))
        
        # for fields of class, we need to store the value to the class
        if (isinstance(self._current_scope, IRClass) and 
            not local_var.name.startswith("$")): # only store the field to the class if it is not a temporary variable
            self.used_variables.append(local_var)

        return constraints
    
    def _translate_with_enter(self, context_manager_var: 'Variable', target_var: 'Variable') -> List['Constraint']:
        """Generate constraints for context manager __enter__.        
        Used for: with obj as target: ... 
        Generates: temp = obj.__enter__; target = temp()        
        """
        constraints = []
        
        enter_method_var = self._make_variable(f"$enter_{id(context_manager_var)}")
        constraints.append(LoadConstraint(
            base=context_manager_var,
            field=attr("__enter__"),
            target=enter_method_var
        ))
        
        call_site = f"{self._current_scope.name}:enter:{id(context_manager_var)}"
        constraints.append(CallConstraint(
            callee=enter_method_var,
            args=(),
            kwargs=frozenset(),
            target=target_var,
            call_site=call_site
        ))
        
        return constraints
    
    def _translate_with_exit(self, context_manager_var: 'Variable') -> List['Constraint']:
        """Generate constraints for context manager __exit__.        
        Used for: with obj: ... (at exit)
        Generates: temp = obj.__exit__; temp(None, None, None)
        """
        constraints = []
        
        exit_method_var = self._make_variable(f"$exit_{id(context_manager_var)}")
        constraints.append(LoadConstraint(
            base=context_manager_var,
            field=attr("__exit__"),
            target=exit_method_var
        ))
        
        call_site = f"{self._current_scope.name}:exit:{id(context_manager_var)}"
        constraints.append(CallConstraint(
            callee=exit_method_var,
            args=(),
            kwargs=frozenset(),
            target=None,
            call_site=call_site
        ))
        
        return constraints
    
    def _translate_iter(self, iterable_var: 'Variable', target_var: 'Variable') -> List['Constraint']:
        """Generate constraints for iterator creation.
        Used for: for item in obj: ...
        Generates: iter_temp = obj.__iter__; target = iter_temp()
        """
        constraints = []
        
        iter_method_var = self._make_variable(f"$iter_{id(iterable_var)}")
        constraints.append(LoadConstraint(
            base=iterable_var,
            field=attr("__iter__"),
            target=iter_method_var
        ))
        
        call_site = f"{self._current_scope.name}:iter:{id(iterable_var)}"
        constraints.append(CallConstraint(
            callee=iter_method_var,
            args=(),
            kwargs=frozenset(),
            target=target_var,
            call_site=call_site
        ))
        
        return constraints
    
    def _translate_next(self, iterator_var: 'Variable', target_var: 'Variable') -> List['Constraint']:
        """Generate constraints for iterator next."""
        constraints = []
        
        next_method_var = self._make_variable(f"$next_{id(iterator_var)}")
        constraints.append(LoadConstraint(
            base=iterator_var,
            field=attr("__next__"),
            target=next_method_var
        ))
        
        call_site = f"{self._current_scope.name}:next:{id(iterator_var)}"
        constraints.append(CallConstraint(
            callee=next_method_var,
            args=(),
            kwargs=frozenset(),
            target=target_var,
            call_site=call_site
        ))
        
        return constraints
    
    def _translate_binary_op(
        self, 
        left_var: 'Variable', 
        right_var: 'Variable', 
        target_var: 'Variable',
        op: ast.operator
    ) -> List['Constraint']:
        constraints = []

        op_name = BINOP_TABLE.get(op, None)
        if not op_name:
            return constraints
        
        method_var = self._make_variable(f"${op_name}_{id(left_var)}")
        constraints.append(LoadConstraint(
            base=left_var,
            field=attr(op_name),
            target=method_var
        ))
        
        call_site = f"{self._current_scope.name}:{op_name}:{id(left_var)}"
        constraints.append(CallConstraint(
            callee=method_var,
            args=(right_var,),
            kwargs=frozenset(),
            target=target_var,
            call_site=call_site
        ))

        if (isinstance(self._current_scope, IRClass) and 
            not target_var.name.startswith("$")): # only store the field to the class if it is not a temporary variable
            self.used_variables.append(target_var)
        
        return constraints
