"""IR to constraint translation.

This module translates IR events to pointer constraints for analysis.
"""

from typing import List, TYPE_CHECKING, Optional, Tuple
import logging, ast

from .constraints import *
from .context import AbstractContext
from .config import Config
from .variable import Variable, Scope, VariableFactory, VariableKind
from .heap_model import elem, value, attr
from pythonstan.ir.ir_statements import *
from .object import AllocSite, AllocKind

logger = logging.getLogger(__name__)

__all__ = ["IRTranslator"]


class IRTranslator:
    """Translates IR to pointer constraints."""
    def __init__(self, config: 'Config'):    
        self.config = config
        self._var_factory = VariableFactory()
        self._current_scope: Optional['Scope'] = None
        self._current_context: Optional['AbstractContext'] = None
        self._current_module: Optional['IRModule'] = None
        self._import_depth = 0  # Track import depth for recursion limit
        
        from pythonstan.world import World
        self.scope_manager = World().scope_manager
        self.namespace_manager = World().namespace_manager
        self._visited_scope = set[(IRScope, 'AbstractContext')]()
    
    def translate_function(self, func: IRFunc, context: 'AbstractContext') -> List['Constraint']:        
        constraints = []
        func_name = func.get_qualname()
        self._current_scope = Scope(name=func_name, stmt=func, context=context, kind="function")
        self._current_context = context
        stmts = self.scope_manager.get_ir(func, 'ir')
        if stmts is not None:
            for stmt in stmts:
                constraints.extend(self._process_stmt(stmt))
        return constraints
    
    def translate_module(self, module: IRModule,
                         context: Optional['AbstractContext'] = None,
                         import_stmt: Optional['IRImport'] = None
                         ) -> Tuple[List['Constraint'], Optional['Variable']]:
        assert isinstance(module, IRModule), f"Module is not an IRModule: {type(module)}"
        constraints = []
        
        self._current_scope = Scope(name=module.get_qualname(), stmt=module, context=context, kind="module")
        self._current_module = module
        
        # Set context (or use empty context if not provided)
        if context is None:
            from .context import CallStringContext
            context = CallStringContext(call_sites=(), k=0)
        
        from .context import CallSite
        self._current_context = context
        
        self._make_variable(module.get_qualname())
        
        # Extract target variable for import statement
        if import_stmt is not None and import_stmt.name is not None:
            target_var = self._make_variable(import_stmt.name)
        else:
            target_var = None
            
        # avoid infinite recursion
        if (module, context) in self._visited_scope:
            return [], target_var
        self._visited_scope.add((module, context))

        ir = self.scope_manager.get_ir(module, 'ir')
        if ir is not None:
            # print(f"Process module[{module.get_qualname()}] with ir {ir}")
            for stmt in ir:
                constraints.extend(self._process_stmt(stmt))
            
        return constraints, target_var
    
    def _make_variable(self, name: str) -> 'Variable':
        if self._current_scope is None or self._current_context is None:
            raise RuntimeError("No active scope/context for variable creation")

        kind = VariableKind.TEMPORARY if name.startswith('$') else VariableKind.LOCAL
        return self._var_factory.make_variable(
            name=name,
            scope=self._current_scope,
            context=self._current_context,
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
        lval = stmt.get_lval().id
        rval = stmt.get_rval().id
        
        source_var = self._make_variable(rval)
        target_var = self._make_variable(lval)
        
        return [CopyConstraint(source=source_var, target=target_var)]

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
                for elt_expr in rval.elts:
                    if isinstance(elt_expr, ast.Name):
                        elem_var = self._make_variable(elt_expr.id)
                        constraints.append(StoreConstraint(
                            base=target_var,
                            field=elem(),
                            source=elem_var
                        ))
            
        elif isinstance(rval, ast.Dict):
            alloc_site = AllocSite.from_ir_node(stmt, AllocKind.DICT)
            constraints.append(AllocConstraint(target=target_var, alloc_site=alloc_site))
            if hasattr(rval, 'values'):
                for val_expr in rval.values:
                    if isinstance(val_expr, ast.Name):
                        val_var = self._make_variable(val_expr.id)
                        constraints.append(StoreConstraint(
                            base=target_var,
                            field=value(),
                            source=val_var
                        ))
                        
        # TODO Tuple should set index as field (eg. t = (a, b) is t._1 = a, t._2 = b)
        elif isinstance(rval, ast.Tuple):
            alloc_site = AllocSite.from_ir_node(stmt, AllocKind.TUPLE)
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
            alloc_site = AllocSite.from_ir_node(stmt, AllocKind.OBJECT, name=f"constant_{rval.value}")
            constraints.append(AllocConstraint(target=target_var, alloc_site=alloc_site))
        
        return constraints
    
    def _translate_load_attr(self, stmt: IRLoadAttr) -> List['Constraint']:
        """Translate IRLoadAttr: target = base.attr"""       
        lval = stmt.get_lval().id
        obj_name = stmt.get_obj().id
        attr_name = stmt.get_attr()
        
        base_var = self._make_variable(obj_name)
        target_var = self._make_variable(lval)
        field = attr(attr_name) if attr_name else attr("<unknown>")
        
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
        lval = stmt.get_target()
        callee_expr = stmt.get_func_name()
        args = stmt.get_args()
        
        callee_var = self._make_variable(callee_expr)
        arg_vars = tuple(self._make_variable(arg) for arg, _ in args)
        
        target_var = self._make_variable(lval) if lval else None
        
        call_site_id = f"{self._current_scope.name}:{id(stmt)}"
        
        return [CallConstraint(
            callee=callee_var,
            args=arg_vars,
            target=target_var,
            call_site=call_site_id
        )]
    
    def _translate_return(self, stmt: IRReturn) -> List['Constraint']:
        """Translate IRReturn: return value"""
        rval = stmt.get_value()
        
        if rval:
            source_var = self._make_variable(rval)
            return_var = self._make_variable("$return")
            return [CopyConstraint(source=source_var, target=return_var)]
        
        return []
    
    def _translate_load_subscr(self, stmt: IRLoadSubscr) -> List['Constraint']:        
        lval = stmt.get_lval().id
        container_name = stmt.get_obj().id
        
        subslice = stmt.get_slice()  # TODO resolve the subslice in detail
        
        container_var = self._make_variable(container_name)
        target_var = self._make_variable(lval)
        
        constraints = []

        constraints.append(LoadConstraint(
            base=container_var,
            field=elem(),
            target=target_var
        ))
        
        getitem_method_var = self._make_variable(f"$getitem_{id(stmt)}")
        constraints.append(LoadConstraint(
            base=container_var,
            field=attr("__getitem__"),
            target=getitem_method_var
        ))
        if hasattr(stmt, 'get_slice'):
            try:
                index_expr = stmt.get_slice()
                if hasattr(index_expr, 'id'):
                    index_var = self._make_variable(index_expr.id)
                else:
                    index_var = self._make_variable(f"$index_{id(stmt)}")
                
                call_site = f"{self._current_scope.name}:getitem:{id(stmt)}"
                constraints.append(CallConstraint(
                    callee=getitem_method_var,
                    args=(index_var,),
                    target=target_var,
                    call_site=call_site
                ))
            except Exception as e:
                logger.debug(f"Error generating __getitem__ call: {e}")
        
        return constraints
    
    def _translate_store_subscr(self, stmt: IRStoreSubscr) -> List['Constraint']:
        """Translate IRStoreSubscr: container[index] = value"""
        container_name = stmt.get_obj().id
        value_name = stmt.get_rval().id
        
        container_var = self._make_variable(container_name)
        value_var = self._make_variable(value_name)
        
        constraints = []
        
        constraints.append(StoreConstraint(
            base=container_var,
            field=elem(),
            source=value_var
        ))
        
        setitem_method_var = self._make_variable(f"$setitem_{id(stmt)}")
        constraints.append(LoadConstraint(
            base=container_var,
            field=attr("__setitem__"),
            target=setitem_method_var
        ))
        if hasattr(stmt, 'get_slice'):
            try:
                index_expr = stmt.get_slice()
                if hasattr(index_expr, 'id'):
                    index_var = self._make_variable(index_expr.id)
                else:
                    index_var = self._make_variable(f"$index_{id(stmt)}")
                
                call_site = f"{self._current_scope.name}:setitem:{id(stmt)}"
                
                constraints.append(CallConstraint(
                    callee=setitem_method_var,
                    args=(index_var, value_var),
                    target=None,
                    call_site=call_site
                ))
            except Exception as e:
                logger.debug(f"Error generating __setitem__ call: {e}")
        
        return constraints
    
    def _translate_function_def(self, stmt: IRFunc) -> Tuple[Variable, List['Constraint']]:
        """Translate function definition: allocate function object."""
        constraints = []
        
        func_alloc = AllocSite.from_ir_node(stmt, AllocKind.FUNCTION, self._current_scope, stmt.name)
        func_var = self._make_variable(stmt.name)
        constraints.append(AllocConstraint(target=func_var, alloc_site=func_alloc))
        
        ir = self.scope_manager.get_ir(stmt, 'ir')
        if ir is not None:
            for stmt in ir:
                constraints.extend(self._process_stmt(stmt))
        
        if hasattr(stmt, 'cell_vars') and stmt.cell_vars:
            for freevar_name in stmt.cell_vars:
                try:
                    cell_alloc = AllocSite(
                        file=self._current_scope.name,
                        line=0,
                        col=0,
                        kind=AllocKind.CELL,
                        name=f"cell_{freevar_name}"
                    )
                    cell_var = self._make_variable(f"$cell_{freevar_name}")
                    constraints.append(AllocConstraint(target=cell_var, alloc_site=cell_alloc))
                    
                    outer_var = self._make_variable(freevar_name)
                    constraints.append(StoreConstraint(
                        base=cell_var,
                        field=attr("contents"),
                        source=outer_var
                    ))
                    
                    constraints.append(StoreConstraint(
                        base=func_var,
                        field=attr(f"__closure__{freevar_name}"),
                        source=cell_var
                    ))
                except Exception as e:
                    logger.debug(f"Error handling closure variable {freevar_name}: {e}")
        
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
                        target=result_var,
                        call_site=call_site
                    ))
                    
                    current_var = result_var
                except Exception as e:
                    logger.debug(f"Error handling decorator {idx} for {stmt.name}: {e}")
            
            # Rebind function name to final decorated result
            if len(stmt.decorator_list) > 0:
                constraints.append(CopyConstraint(source=current_var, target=func_var))
        
        return func_var, constraints
    
    def _translate_class_def(self, stmt: IRClass) -> Tuple[Variable, List['Constraint']]:
        """Translate class definition: allocate class object and bind methods."""
        
        """NOTE: HERE IS A BIG BUG, SHOUD NOT RESOLVE AST"""
        """ declare the thole class and generate some constraints to bind some methods to the class """
       
        constraints = []
        logger.info(stmt)
        
        class_alloc = AllocSite.from_ir_node(stmt, AllocKind.CLASS, stmt.name)
        class_var = self._make_variable(stmt.name)
        constraints.append(AllocConstraint(target=class_var, alloc_site=class_alloc))
        
        for subscope in self.scope_manager.get_subscopes(stmt):
            # NOTICE: staticmethod and classmethod are not supported yet
            if isinstance(subscope, IRFunc):
                fn_var, fn_constraints = self._translate_function_def(subscope)
                constraints.extend(fn_constraints)
                Field = attr(self._make_variable(f"{stmt.name}.{subscope.name}"))
                constraints.append(StoreConstraint(
                    base=class_var,
                    field=Field,
                    source= fn_var
                ))
            elif isinstance(subscope, IRClass):
                class_var, class_constraints = self._translate_class_def(subscope)
                constraints.extend(class_constraints)
                Field = attr(self._make_variable(f"{stmt.name}.{subscope.name}"))
                constraints.append(StoreConstraint(
                    base=class_var,
                    field=Field,
                    source=class_var
                ))
            else:
                logger.debug(f"Unknown subscope type: {type(subscope)}")
        
        
        # TODO [CRITICAL] the method of treating inheritances is totally wrong, we should use the class hierarchy manager to handle this.
        for base_name in stmt.get_bases():
            if not isinstance(base_name, ast.Name):
                continue # TODO resolve the base name in detail
            
            base_var = self._make_variable(base_name.id)
            constraints.append(StoreConstraint(
                base=class_var,
                field=attr("__bases__"),
                source=base_var
            ))
        return class_var, constraints
    
    def _translate_import(self, stmt: IRImport) -> List['Constraint']:
        """Translate import statement with transitive analysis. """
        constraints = []
        target_var = None
        
        # translate the IRs in the imported module
        module_ir = self.scope_manager.get_module_graph().get_succ_module(self._current_scope.stmt, stmt)
        if module_ir is None:
            alloc_site = AllocSite(
                file=self._current_module.filename,
                line=stmt.get_ast().lineno,
                col=stmt.get_ast().col_offset,
                kind=AllocKind.UNKNOWN,
                scope = self._current_scope,
                name=f"unknown_import_[{stmt}]"
            )
        else:            
            try:
                old_scope = self._current_scope
                old_context = self._current_context
                old_module = self._current_module
                
                from .context import CallSite
                self._current_context = self._current_context.append(
                    CallSite(site_id=f"{module_ir.get_qualname()}:{stmt.get_ast().lineno}:{stmt.get_ast().col_offset}:import", fn=module_ir.get_qualname()))
                try:
                    module_constraints, target_var = self.translate_module(
                        module_ir, context=self._current_context, import_stmt=stmt)
                    constraints.extend(module_constraints)
                    logger.debug(f"Analyzed imported module {module_ir.get_qualname()} ({len(module_constraints)} constraints)")
                finally:
                    self._current_scope = old_scope
                    self._current_context = old_context
                    self._current_module = old_module
                    
            except Exception as e:
                logger.debug(f"Could not analyze module {module_ir.get_qualname()}: {e}")
                raise ValueError(f"Error translating import: {e}")
                                        
            alloc_site = AllocSite(
                file=self._current_module.filename,
                line=stmt.get_ast().lineno,
                col=stmt.get_ast().col_offset,
                kind=AllocKind.MODULE,
                scope=self._current_scope,
                name=module_ir.get_qualname()
            )

        # allocate the module variable
        if stmt.module is None:
            module_var = self._make_variable(stmt.name)
            constraints.append(AllocConstraint(target=module_var, alloc_site=alloc_site))
        elif stmt.asname is None:
            module_var = self._make_variable(stmt.module)
            constraints.append(AllocConstraint(target=module_var, alloc_site=alloc_site))
            local_var = self._make_variable(stmt.name)
            if target_var is not None:
                constraints.append(CopyConstraint(source=target_var, target=local_var))
            else:
                constraints.append(LoadConstraint(base=module_var, field=attr(stmt.name), target=local_var))
        else:
            module_var = self._make_variable(stmt.module)
            constraints.append(AllocConstraint(target=module_var, alloc_site=alloc_site))
            local_var = self._make_variable(stmt.asname)
            if target_var is not None:
                constraints.append(CopyConstraint(source=target_var, target=local_var))
            else:
                constraints.append(LoadConstraint(base=module_var, field=attr(stmt.name), target=local_var))

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
            target=target_var,
            call_site=call_site
        ))
        
        return constraints
    
    def _translate_binary_op(
        self, 
        left_var: 'Variable', 
        right_var: 'Variable', 
        target_var: 'Variable',
        op_name: str
    ) -> List['Constraint']:
        constraints = []
        
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
            target=target_var,
            call_site=call_site
        ))
        
        return constraints


