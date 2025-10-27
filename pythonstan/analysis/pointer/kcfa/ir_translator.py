"""IR to constraint translation.

This module translates IR events to pointer constraints for analysis.
"""

from typing import List, TYPE_CHECKING, Optional, Tuple
import logging, ast

from .constraints import *
from .context import AbstractContext
from .config import Config
from .variable import Variable, Scope, VariableFactory, VariableKind
from .module_finder import ModuleFinder
from pythonstan.ir.ir_statements import (
    IRCopy, IRAssign, IRLoadAttr, IRStoreAttr,
    IRCall, IRReturn, IRLoadSubscr, IRStoreSubscr,
    IRFunc, IRClass
)
from .object import AllocSite, AllocKind
from .heap_model import attr

logger = logging.getLogger(__name__)

__all__ = ["IRTranslator"]


class IRTranslator:
    """Translates IR to pointer constraints."""
    
    def __init__(self, config: 'Config', module_finder: Optional['ModuleFinder'] = None):    
        self.config = config
        self._var_factory = VariableFactory()
        self._current_scope: Optional['Scope'] = None
        self._current_context: Optional['AbstractContext'] = None
        self.module_finder = module_finder
        self._import_depth = 0  # Track import depth for recursion limit
        
        from pythonstan.world import World
        self.scope_namager = World().scope_manager
        self.namespace_manager = World().namespace_manager
    
    def translate_function(self, func: IRFunc, context: 'AbstractContext') -> List['Constraint']:        
        constraints = []
        func_name = getattr(func, 'name', '<unknown>')
        self._current_scope = Scope(name=func_name, kind="function")
        self._current_context = context
        stmts = self.scope_namager.get_ir(func, 'ir')
        if stmts is not None:
            for stmt in stmts:
                if isinstance(stmt, IRCopy):
                    constraints.extend(self._translate_copy(stmt))
                elif isinstance(stmt, IRAssign):
                    constraints.extend(self._translate_assign(stmt))
                elif isinstance(stmt, IRLoadAttr):
                    constraints.extend(self._translate_load_attr(stmt))
                elif isinstance(stmt, IRStoreAttr):
                    constraints.extend(self._translate_store_attr(stmt))
                elif isinstance(stmt, IRCall):
                    constraints.extend(self._translate_call(stmt))
                elif isinstance(stmt, IRReturn):
                    constraints.extend(self._translate_return(stmt))
                elif isinstance(stmt, IRLoadSubscr):
                    constraints.extend(self._translate_load_subscr(stmt))
                elif isinstance(stmt, IRStoreSubscr):
                    constraints.extend(self._translate_store_subscr(stmt))
                elif isinstance(stmt, IRFunc):
                    constraints.extend(self._translate_function_def(stmt)[1])
                elif isinstance(stmt, IRClass):
                    constraints.extend(self._translate_class_def(stmt))
        return constraints
    
    def translate_module(self, module) -> List['Constraint']:
        constraints = []
        
        module_name = getattr(module, 'name', '__main__')
        self._current_scope = Scope(name=module_name, kind="module")

        try:
            subscopes = self.scope_manager.get_subscopes(module)
            if subscopes:
                for subscope in subscopes:
                    if isinstance(subscope, IRFunc):
                        constraints.extend(self._translate_function_def(subscope)[1])
                    elif isinstance(subscope, IRClass):
                        constraints.extend(self._translate_class_def(subscope)[1])
        except Exception as e:
            logger.debug(f"Could not get subscopes from World: {e}")
        
        if hasattr(module, 'ast') and hasattr(module.ast, 'body'):
            for stmt in module.ast.body:
                if isinstance(stmt, (ast.Import, ast.ImportFrom)):
                    constraints.extend(self._translate_import(stmt))
        
        return constraints
    
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
    
    def _translate_copy(self, stmt) -> List['Constraint']:
        """Translate IRCopy: target = source"""
        lval = stmt.get_lval()
        rval = stmt.get_rval()
        
        source_var = self._make_variable(rval)
        target_var = self._make_variable(lval)
        
        return [CopyConstraint(source=source_var, target=target_var)]
    
    def _translate_assign(self, stmt) -> List['Constraint']:
        """Translate IRAssign: may allocate objects"""        
        constraints = []
        lval = stmt.get_lval()
        rval = stmt.get_rval()
        
        target_var = self._make_variable(lval)
        
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
        
        return constraints
    
    def _translate_load_attr(self, stmt) -> List['Constraint']:
        """Translate IRLoadAttr: target = base.attr"""
        from .constraints import LoadConstraint
        from .heap_model import attr
        
        lval = stmt.get_lval()
        obj_name = stmt.get_obj()
        attr_name = stmt.get_attr()
        
        base_var = self._make_variable(obj_name)
        target_var = self._make_variable(lval)
        field = attr(attr_name) if attr_name else attr("<unknown>")
        
        return [LoadConstraint(base=base_var, field=field, target=target_var)]
    
    def _translate_store_attr(self, stmt) -> List['Constraint']:
        """Translate IRStoreAttr: base.attr = source"""
        from .constraints import StoreConstraint
        from .heap_model import attr
        
        obj_name = stmt.get_obj()
        attr_name = stmt.get_attr()
        value_name = stmt.get_rval()
        
        base_var = self._make_variable(obj_name)
        source_var = self._make_variable(value_name)
        field = attr(attr_name) if attr_name else attr("<unknown>")
        
        return [StoreConstraint(base=base_var, field=field, source=source_var)]
    
    def _translate_call(self, stmt) -> List['Constraint']:
        """Translate IRCall: target = callee(args...)"""
        from .constraints import CallConstraint
        
        lval = stmt.get_lval()
        callee_expr = stmt.get_callee()
        args = stmt.get_args()
        
        callee_var = self._make_variable(callee_expr)
        arg_vars = tuple(self._make_variable(arg) for arg in args)
        target_var = self._make_variable(lval) if lval else None
        
        call_site_id = f"{self._current_scope.name}:{id(stmt)}"
        
        return [CallConstraint(
            callee=callee_var,
            args=arg_vars,
            target=target_var,
            call_site=call_site_id
        )]
    
    def _translate_return(self, stmt) -> List['Constraint']:
        """Translate IRReturn: return value"""
        from .constraints import CopyConstraint
        
        rval = stmt.get_rval()
        
        if rval:
            source_var = self._make_variable(rval)
            return_var = self._make_variable("$return")
            return [CopyConstraint(source=source_var, target=return_var)]
        
        return []
    
    def _translate_load_subscr(self, stmt) -> List['Constraint']:
        from .constraints import LoadConstraint, CallConstraint
        from .heap_model import elem, value, attr
        
        lval = stmt.get_lval()
        container_name = stmt.get_container()
        
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
    
    def _translate_store_subscr(self, stmt) -> List['Constraint']:
        """Translate IRStoreSubscr: container[index] = value"""
        from .constraints import StoreConstraint, LoadConstraint, CallConstraint
        from .heap_model import elem, value, attr
        
        container_name = stmt.get_container()
        value_name = stmt.get_rval()
        
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
        from .constraints import AllocConstraint, StoreConstraint, CallConstraint, CopyConstraint
        from .object import AllocSite, AllocKind
        from .heap_model import attr
        import ast
        
        constraints = []
        
        func_alloc = AllocSite.from_ir_node(stmt, AllocKind.FUNCTION, stmt.name)
        func_var = self._make_variable(stmt.name)
        constraints.append(AllocConstraint(target=func_var, alloc_site=func_alloc))
        
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
                            from .constraints import LoadConstraint
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
    
    def _translate_class_def(self, stmt) -> Tuple[Variable, List['Constraint']]:
        """Translate class definition: allocate class object and bind methods."""
        
        """NOTE: HERE IS A BIG BUG, SHOUD NOT RESOLVE AST"""
       
        constraints = []
        
        class_alloc = AllocSite.from_ir_node(stmt, AllocKind.CLASS, stmt.name)
        class_var = self._make_variable(stmt.name)
        constraints.append(AllocConstraint(target=class_var, alloc_site=class_alloc))
        
        for subscope in self.scope_namager.get_subscopes(stmt):
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
                        
        if hasattr(stmt, 'bases') and stmt.bases:
            for base_name in stmt.get_bases():
                base_var = self._make_variable(base_name)
                constraints.append(StoreConstraint(
                    base=class_var,
                    field=attr("__bases__"),
                    source=base_var
                ))        
        return class_var, constraints
    
    def _translate_import(self, stmt) -> List['Constraint']:
        """Translate import statement with transitive analysis. """
        from .constraints import AllocConstraint, LoadConstraint
        from .object import AllocSite, AllocKind
        from .heap_model import attr
        import ast
        
        constraints = []
        
        if not hasattr(self, '_import_depth'):
            self._import_depth = 0
        
        if self.config.max_import_depth >= 0 and self._import_depth >= self.config.max_import_depth:
            logger.debug(f"Skipping import (depth {self._import_depth} >= max {self.config.max_import_depth})")
            return constraints
        
        if isinstance(stmt, ast.Import):
            for alias in stmt.names:
                local_name = alias.asname if alias.asname else alias.name
                module_name = alias.name
                constraints.extend(self._import_module(module_name, local_name))
        
        elif isinstance(stmt, ast.ImportFrom):
            level = getattr(stmt, 'level', 0)
            module_name = stmt.module or ''
            
            # Handle relative imports
            if level > 0 and self.module_finder:
                current_ns = self._current_scope.name if self._current_scope else '__main__'
                resolved = self.module_finder.resolve_relative_import(current_ns, module_name, level)
                if resolved:
                    module_name = resolved
                else:
                    logger.debug(f"Could not resolve relative import level={level}, module={module_name}")
                    return constraints
            
            if module_name:
                for alias in stmt.names:
                    item_name = alias.name
                    local_name = alias.asname if alias.asname else alias.name
                    constraints.extend(self._import_from(module_name, item_name, local_name))
        
        return constraints
    
    def _import_module(self, module_name: str, local_name: str) -> List['Constraint']:
        """Import entire module with transitive analysis."""
        constraints = []
        
        module_var = self._make_variable(local_name)
        module_alloc = AllocSite(
            file="<import>",
            line=0,
            col=0,
            kind=AllocKind.MODULE,
            name=module_name
        )
        constraints.append(AllocConstraint(target=module_var, alloc_site=module_alloc))
        
        if self.module_finder and self.config.max_import_depth != 0:
            try:
                module_ir = self.module_finder.get_module_ir(module_name)
                if module_ir:
                    old_depth = self._import_depth
                    self._import_depth += 1
                    
                    old_scope = self._current_scope
                    old_context = self._current_context
                    
                    try:
                        module_constraints = self.translate_module(module_ir)
                        constraints.extend(module_constraints)
                        logger.debug(f"Analyzed imported module {module_name} ({len(module_constraints)} constraints)")
                    finally:
                        self._import_depth = old_depth
                        self._current_scope = old_scope
                        self._current_context = old_context
            except Exception as e:
                logger.debug(f"Could not analyze module {module_name}: {e}")
        
        return constraints
    
    def _import_from(self, module_name: str, item_name: str, local_name: str) -> List['Constraint']:
        """Import specific item from module with transitive analysis."""
        constraints = []
        
        module_var = self._make_variable(f"$module_{module_name}")
        module_alloc = AllocSite(
            file="<import>",
            line=0,
            col=0,
            kind=AllocKind.MODULE,
            name=module_name
        )
        constraints.append(AllocConstraint(target=module_var, alloc_site=module_alloc))
        
        if self.module_finder and self.config.max_import_depth != 0:
            try:
                resolved = self.module_finder.resolve_import_from(module_name, item_name)
                if resolved:
                    resolved_ns, module_path = resolved
                    module_ir = self.module_finder.get_module_ir(resolved_ns)
                else:
                    module_ir = self.module_finder.get_module_ir(module_name)
                
                if module_ir:
                    old_depth = self._import_depth
                    self._import_depth += 1
                    
                    old_scope = self._current_scope
                    old_context = self._current_context
                    
                    try:
                        module_constraints = self.translate_module(module_ir)
                        constraints.extend(module_constraints)
                        logger.debug(f"Analyzed module {module_name} for 'from' import ({len(module_constraints)} constraints)")
                    finally:
                        self._import_depth = old_depth
                        self._current_scope = old_scope
                        self._current_context = old_context
            except Exception as e:
                logger.debug(f"Could not analyze module {module_name} for 'from' import: {e}")
        
        local_var = self._make_variable(local_name)
        constraints.append(LoadConstraint(
            base=module_var,
            field=attr(item_name),
            target=local_var
        ))
        
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


