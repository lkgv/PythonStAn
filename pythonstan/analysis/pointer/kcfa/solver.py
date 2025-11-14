"""Constraint-based pointer analysis solver.

This module implements the core solver for pointer analysis using
constraint-based propagation.
"""

import logging
from typing import Set, Dict, Any, TYPE_CHECKING, Optional, List, Tuple

from pythonstan.ir.ir_statements import IRFunc, IRModule, IRClass, IRAssign

from .state import PointerAnalysisState, PointsToSet
from .constraints import (Constraint, CopyConstraint, LoadConstraint, StoreConstraint,
                          AllocConstraint, CallConstraint, LoadSubscrConstraint, StoreSubscrConstraint)
from .variable import Variable, VariableKind, VariableFactory, FieldAccess
from .config import Config
from .heap_model import Field, attr, position, key, elem, value
from pythonstan.graph.call_graph import AbstractCallGraph, CallEdge, CallKind
from .ir_translator import IRTranslator
from .context_selector import ContextSelector, CallSite, AbstractContext
from .context import Ctx, Scope
from .class_hierarchy import ClassHierarchyManager
from .builtin_api_handler import BuiltinSummaryManager
from .unknown_tracker import UnknownTracker, UnknownKind
from .object import *
from .solver_interface import ISolverQuery

__all__ = ["PointerSolver", "SolverQuery"]

logger = logging.getLogger(__name__)


class Worklist:
    items: Dict[Ctx[Variable], Tuple['Scope', Ctx[Variable], PointsToSet]]

    def __init__(self):
        self.items = {}

    def add(self, content: Tuple['Scope', Ctx[Variable], PointsToSet]):
        scope, var, pts = content
        if var in self.items:
            _, _, orig_pts = self.items.pop(var)
            self.items[var] = (scope, var, orig_pts.union(pts))
        else:
            self.items[var] = content
    
    def pop(self) -> Tuple['Scope', Ctx[Variable], PointsToSet]:
        _, v = self.items.popitem()
        return v
    
    def empty(self) -> bool:
        return len(self.items) == 0
    
    def __len__(self) -> int:
        return len(self.items)


class PointerSolver:
    def __init__(
        self,
        state: 'PointerAnalysisState',
        config: 'Config',
        variable_factory: Optional['VariableFactory'] = None,
        ir_translator: Optional['IRTranslator'] = None,
        context_selector: Optional['ContextSelector'] = None,
        function_registry: Optional[Dict[str, Any]] = None,
        class_hierarchy: Optional['ClassHierarchyManager'] = None,
        builtin_manager: Optional['BuiltinSummaryManager'] = None
    ):
        """Initialize solver.
        
        Args:
            state: Analysis state
            config: Configuration
            ir_translator: IR translator for analyzing called functions
            context_selector: Context selector for call/alloc contexts
            function_registry: Map of function names to IR functions
            class_hierarchy: Class hierarchy manager for MRO
            builtin_manager: Builtin summary manager
        """
        self.state = state
        self.config = config
        self.ir_translator = ir_translator
        self.context_selector = context_selector
        self.builtin_manager = builtin_manager
        self.variable_factory = variable_factory or VariableFactory()
        # self._worklist: Set[Tuple['Scope', Ctx[Variable], PointsToSet]] = set()
        self._worklist: Worklist = Worklist()
        self._static_constraints: Set[Tuple['Scope', 'AbstractContext', 'Constraint']] = set()
        self._iteration = 0
        self._stats: Dict[str, int] = {
            "iterations": 0,
            "constraints_applied": 0
        }
        self._modules = set()
        self._unknown_tracker = UnknownTracker()
    
    def add_constraint(self, scope: 'Scope', context: 'AbstractContext', constraint: 'Constraint') -> None:
        if isinstance(constraint, CopyConstraint):
            self._static_constraints.add((scope, context, constraint))
        elif isinstance(constraint, AllocConstraint):
            self._static_constraints.add((scope, context, constraint))
        else:
            if isinstance(constraint, LoadConstraint):
                target = self.state.get_variable(scope, context, constraint.target)
                self.state.constraints.add(scope, target, constraint)
            elif isinstance(constraint, StoreConstraint):
                source = self.state.get_variable(scope, context, constraint.source)
                self.state.constraints.add(scope, source, constraint)
            elif isinstance(constraint, CallConstraint):
                callee = self.state.get_variable(scope, context, constraint.callee)
                self.state.constraints.add(scope, callee, constraint)
            elif isinstance(constraint, LoadSubscrConstraint):
                index = self.state.get_variable(scope, context, constraint.index)
                self.state.constraints.add(scope, index, constraint)
            elif isinstance(constraint, StoreSubscrConstraint):
                index = self.state.get_variable(scope, context, constraint.index)
                self.state.constraints.add(scope, index, constraint)

    def solve_to_fixpoint(self) -> None:
        # self.process_static_constraints()
        
        logger.info("Starting constraint solving")
        max_iter = 1000000 # self.config.max_iterations
        
        while ((not self._worklist.empty()) or self._static_constraints) and self._iteration < max_iter:
            self._iteration += 1
            # Log progress periodically
            if self._iteration % 1000 == 0:
                logger.info(f"Iteration {self._iteration}, worklist size {len(self._worklist)}, objs: {len(self.state._heap.objects)}, call_edges: {len(self.state.call_graph.edges)}, plain_call_edges: {self.state.call_graph.num_plain_edges()}")
            
            if self._static_constraints:
                scope, ctx, constraint = self._static_constraints.pop()
                self._apply_static(scope, scope.context, constraint)

            # if not self._worklist.empty():
            else:
                scope, var, pts = self._worklist.pop()
                diff = pts - self.state.get_points_to(var)

                if not diff.is_empty():
                    self.state.set_points_to(var, diff)

                    constraints_to_process = list(self.state.constraints.get_by_variable(var))
                    for constraint in constraints_to_process:
                        self._apply_constraint(scope, var, constraint, diff)
                        
                    for target_var in self.state.pointer_flow_graph.get_succs(var):
                        if isinstance(target_var.content, FieldAccess):
                            obj = target_var.content.obj

                            # bind new class object into class method objects
                            if isinstance(obj, ClassObject):
                                diff = diff.inherit_to(obj)
                            # bind new class object into instance method objects
                            elif isinstance(obj, InstanceObject):
                                diff = diff.deliver_into(obj)

                        self._worklist.add((target_var.scope, target_var, diff))

            # else:
            #     scope, ctx, constraint = self._static_constraints.pop()
            #     self._apply_static(scope, ctx, constraint)
        
        if self._iteration >= max_iter:
            logger.warning(f"Reached max iterations {max_iter}")
        
        logger.info(f"Processed {len(self._modules)} modules: {self._modules}")
        logger.info(f"Call graph: {self.state._call_graph} node: {len(self.state._call_graph.get_nodes())} edge: {self.state._call_graph.get_number_of_edges()} abslote: {self.state._call_graph.num_plain_edges()}")
        logger.info(f"Pointer flow graph: {self.state._pointer_flow_graph} node: {len(self.state._pointer_flow_graph.get_nodes())} edge: {len(self.state._pointer_flow_graph.get_edges())}")
        
        self._stats["iterations"] = self._iteration
        logger.info(f"Converged after {self._iteration} iterations")

    def _apply_static(self, scope: 'Scope', context: 'AbstractContext', constraint: 'Constraint'):
        if isinstance(constraint, AllocConstraint):
            self._apply_alloc(scope, context, constraint)
        elif isinstance(constraint, CopyConstraint):
            self._apply_copy(scope, context, constraint)

    def _apply_constraint(self, scope: 'Scope', variable: Ctx[Any], constraint: 'Constraint', diff: 'PointsToSet') -> bool:
        # Here shoud add supports for Imports

        if isinstance(constraint, LoadConstraint):
            return self._apply_load(scope, variable, constraint, diff)
        elif isinstance(constraint, StoreConstraint):
            return self._apply_store(scope, variable, constraint, diff)
        elif isinstance(constraint, CallConstraint):
            return self._apply_call(scope, variable, constraint, diff)
        elif isinstance(constraint, LoadSubscrConstraint):
            return self._apply_load_subscr(scope, variable, constraint, diff)
        elif isinstance(constraint, StoreSubscrConstraint):
            return self._apply_store_subscr(scope, variable, constraint, diff)
        else:
            logger.warning(f"Unknown constraint type: {type(constraint)}")
            return False

    def _apply_copy(self, scope: 'Scope', context: 'AbstractContext', c: 'CopyConstraint'):
        """Apply copy constraint: target = source."""
        src = self.state.get_variable(scope, context, c.source)
        tgt = self.state.get_variable(scope, context, c.target)
        self.state.pointer_flow_graph.add_edge(src, tgt)
        
    def _apply_alloc(self, scope: 'Scope', context: 'AbstractContext', c: 'AllocConstraint'):
        """Apply allocation constraint: target = new Object."""

        orig_obj = self.state._heap.get_obj(scope, context, c.alloc_site)
        if orig_obj is not None:  #  and not isinstance(orig_obj, AbstractObject):
            return

        if c.alloc_site.kind == AllocKind.FUNCTION:
            obj = self._alloc_function(scope, context, c)
        
        elif c.alloc_site.kind == AllocKind.METHOD:
            obj = self._alloc_method(scope, context, c)

        elif c.alloc_site.kind == AllocKind.CLASS:
            # complex class translation logic, for processing base classes
            obj = self._alloc_class(scope, context, c)
        
        elif c.alloc_site.kind == AllocKind.MODULE:
            obj = self._alloc_module(scope, context, c)
        
        elif c.alloc_site.kind == AllocKind.CONSTANT:
            obj = self._alloc_constant(scope, context, c)
        
        elif c.alloc_site.kind == AllocKind.LIST:
            obj = self._alloc_list(scope, context, c)
        
        elif c.alloc_site.kind == AllocKind.TUPLE:
            obj = self._alloc_tuple(scope, context, c)
        
        elif c.alloc_site.kind == AllocKind.DICT:
            obj = self._alloc_dict(scope, context, c)
        
        elif c.alloc_site.kind == AllocKind.SET:
            obj = self._alloc_set(scope, context, c)
        
        elif c.alloc_site.kind == AllocKind.OBJECT:
            # logic for instance allocation is located in _apply_call
            obj = None 
            
        else:
            obj = None # AbstractObject(alloc_site=c.alloc_site, context=context)

        if obj is not None:
            self.state._heap.set_obj(scope, context, c.alloc_site, obj)
            pts = PointsToSet.singleton(obj)
            target = self.state.get_variable(scope, context, c.target)
            self.state.obj_scope[obj] = scope
            self._worklist.add((scope, target, pts))
    
    def _alloc_constant(self, scope: 'Scope', context: 'AbstractContext', c: 'AllocConstraint') -> 'ConstantObject':
        stmt: 'IRAssign' = c.alloc_site.stmt
        obj = ConstantObject(self.context_selector.empty_context(), c.alloc_site, stmt.get_rval().value)
        return obj
    
    def _alloc_list(self, scope: 'Scope', context: 'AbstractContext', c: 'AllocConstraint') -> 'ListObject':
        """Allocate list object."""
        obj = ListObject(context, c.alloc_site)
        return obj
    
    def _alloc_tuple(self, scope: 'Scope', context: 'AbstractContext', c: 'AllocConstraint') -> 'TupleObject':
        """Allocate tuple object."""
        obj = TupleObject(context, c.alloc_site)
        return obj
    
    def _alloc_dict(self, scope: 'Scope', context: 'AbstractContext', c: 'AllocConstraint') -> 'DictObject':
        """Allocate dict object."""
        obj = DictObject(context, c.alloc_site)
        return obj
    
    def _alloc_set(self, scope: 'Scope', context: 'AbstractContext', c: 'AllocConstraint') -> 'SetObject':
        """Allocate set object."""
        obj = SetObject(context, c.alloc_site)
        return obj
    
    def _alloc_method(self, scope: 'Scope', context: 'AbstractContext', c: 'AllocConstraint') -> 'MethodObject':
        ir_func = c.alloc_site.stmt
        # logger.info(f"alloc function {c}")
        assert isinstance(ir_func, IRFunc), f"AllocSite to be allocated as function {c.alloc_site} should be IRFunc, {type(ir_func)} got!"

        obj = MethodObject(context, c.alloc_site, scope, c.alloc_site.stmt, scope.obj, None)

        # TODO change the way of loading cell vars into directly put
        
        # process cell vars into the closure
        cell_vars = {}
        for var_name in ir_func.get_cell_vars():
            var = self.variable_factory.make_variable(var_name, VariableKind.CELL)
            cell_vars[var_name] = self.state.get_variable(scope.parent, context, var)
        self.state.set_cell_vars(obj, cell_vars)
        
        # collect global vars into the closure
        global_vars = {}
        for var_name in ir_func.get_global_vars():
            var = self.variable_factory.make_variable(var_name, VariableKind.GLOBAL)
            global_vars[var_name] = self.state.get_variable(scope.parent, context, var)
        self.state.set_global_vars(obj, global_vars)
            
        # collect nonlocal vars into the closure
        nonlocal_vars = {}
        for var_name in ir_func.get_nonlocal_vars():
            var = self.variable_factory.make_variable(var_name, VariableKind.NONLOCAL)
            nonlocal_vars[var_name] = self.state.get_variable(scope.parent, context, var)
        self.state.set_nonlocal_vars(obj, nonlocal_vars)

        # Processing contents at once

        func_obj = obj
        func_ir = ir_func
        call_context = self.context_selector.select_call_context(ir_func, context, scope.obj, None)
        # func_ir = self.function_registry[func_name]
        alloc_site = func_obj.alloc_site
        # callee_scope = Scope.new(func_obj, method_scope.module, call_context, func_ir, method_scope)
        callee_scope = Scope.new(func_obj, scope.module, call_context, func_ir, scope)
        old_scope = self.ir_translator._current_scope
        # old_context = self.ir_translator._current_context        
        self.ir_translator._current_scope = alloc_site.stmt
        # self.ir_translator._current_context = call_context
        
        try:
            body_constraints = self.ir_translator.translate_function(func_ir)
        except Exception as e:
            body_constraints = []
        finally:
            self.ir_translator._current_scope = old_scope
            # self.ir_translator._current_context = old_context
        for constraint in body_constraints:
            self.add_constraint(callee_scope, call_context, constraint)


        return obj

    def _alloc_function(self, scope: 'Scope', context: 'AbstractContext', c: 'AllocConstraint') -> 'FunctionObject':
        ir_func = c.alloc_site.stmt
        # logger.info(f"alloc function {c}")
        assert isinstance(ir_func, IRFunc), f"AllocSite to be allocated as function {c.alloc_site} should be IRFunc, {type(ir_func)} got!"

        obj = FunctionObject(context, c.alloc_site, scope, c.alloc_site.stmt)
        
        # process cell vars into the closure
        cell_vars = {}
        for var_name in ir_func.get_cell_vars():
            var = self.variable_factory.make_variable(var_name, VariableKind.CELL)
            cell_vars[var_name] = self.state.get_variable(scope, context, var)
        self.state.set_cell_vars(obj, cell_vars)
        
        # collect global vars into the closure
        global_vars = {}
        for var_name in ir_func.get_global_vars():
            var = self.variable_factory.make_variable(var_name, VariableKind.GLOBAL)
            global_vars[var_name] = self.state.get_variable(scope, context, var)
        self.state.set_global_vars(obj, global_vars)
            
        # collect nonlocal vars into the closure
        nonlocal_vars = {}
        for var_name in ir_func.get_nonlocal_vars():
            var = self.variable_factory.make_variable(var_name, VariableKind.NONLOCAL)
            nonlocal_vars[var_name] = self.state.get_variable(scope, context, var)
        self.state.set_nonlocal_vars(obj, nonlocal_vars)



        # Processing contents at once
        func_obj = obj
        func_ir = ir_func
        call_context = self.context_selector.select_call_context(ir_func, context, None, None)
        # func_ir = self.function_registry[func_name]
        # method_scope = self.state.obj_scope[func_obj]
        alloc_site = func_obj.alloc_site
        # callee_scope = Scope.new(func_obj, method_scope.module, call_context, func_ir, method_scope)
        callee_scope = Scope.new(func_obj, scope.module, call_context, func_ir, scope)
        old_scope = self.ir_translator._current_scope
        # old_context = self.ir_translator._current_context        
        self.ir_translator._current_scope = alloc_site.stmt
        # self.ir_translator._current_context = call_context
        
        try:
            body_constraints = self.ir_translator.translate_function(func_ir)
        except Exception as e:
            body_constraints = []
        finally:
            self.ir_translator._current_scope = old_scope
            # self.ir_translator._current_context = old_context

        for constraint in body_constraints:
            self.add_constraint(callee_scope, call_context, constraint)


        return obj
    
    def _alloc_class(self, scope: 'Scope', context: 'AbstractContext', c: 'AllocConstraint') -> 'ClassObject':
        ir_cls = c.alloc_site.stmt
        # logger.info(f"alloc function {c}")
        assert isinstance(ir_cls, IRClass), f"AllocSite to be allocated as class {c.alloc_site} should be IRClass, {type(ir_cls)} got!"

        obj = ClassObject(context, c.alloc_site, scope, c.alloc_site.stmt)
        
        # process cell vars into the closure
        cell_vars = {}
        for var_name in ir_cls.get_cell_vars():
            var = self.variable_factory.make_variable(var_name, VariableKind.CELL)
            cell_vars[var_name] = self.state.get_variable(scope, context, var)
        self.state.set_cell_vars(obj, cell_vars)
        
        # collect global vars into the closure
        global_vars = {}
        for var_name in ir_cls.get_global_vars():
            var = self.variable_factory.make_variable(var_name, VariableKind.GLOBAL)
            global_vars[var_name] = self.state.get_variable(scope, context, var)
        self.state.set_global_vars(obj, global_vars)
            
        # collect nonlocal vars into the closure
        nonlocal_vars = {}
        for var_name in ir_cls.get_nonlocal_vars():
            var = self.variable_factory.make_variable(var_name, VariableKind.NONLOCAL)
            nonlocal_vars[var_name] = self.state.get_variable(scope, context, var)
        self.state.set_nonlocal_vars(obj, nonlocal_vars)

        # resolve the content of module
        ctx_scope = Scope.new(obj, scope.module, context, ir_cls, scope)
        self.state.set_internal_scope(obj, ctx_scope)

        # inner_var = self.state.get_variable(ctx_scope, context, self.variable_factory.make_variable("$class", VariableKind.LOCAL))
        # self._worklist.add((scope, inner_var, PointsToSet.singleton(obj)))

        # translate the IRs in the imported module        
        for constraint in self.ir_translator.translate_class(ir_cls):
            self.add_constraint(ctx_scope, context, constraint)
        
        for inner_var in self.ir_translator.used_variables:
            ctx_field = self.state.get_field(scope, context, obj, attr(inner_var.name))
            ctx_inner_var = self.state.get_variable(ctx_scope, context, inner_var)
            self.state.pointer_flow_graph.add_edge(ctx_inner_var, ctx_field)

        return obj

    def _alloc_module(self, scope: 'Scope', context: 'AbstractContext', c: 'AllocConstraint') -> 'ModuleObject':
        assert c.alloc_site.kind == AllocKind.MODULE, "AllocSite kind in module allocation should be Module"
        assert c.alloc_site.stmt is not None, "AllocSite should have IRImport as stmt"

        module_ir = self.state.scope_manager.module_graph.get_succ_module(scope.module.stmt, c.alloc_site.stmt)
                
        if module_ir is None:
            self._unknown_tracker.record(
                UnknownKind.CALLEE_NON_CALLABLE,
                str(c.alloc_site),
                f"Attempting to import unknown module: {c.alloc_site.stmt}",
                context=str(context)
            )
            
            if self.config.verbose:
                logger.warning(f"[UNKNOWN] Module not found at {c.alloc_site}")
            
            unknown_obj = AbstractObject(c.alloc_site, scope.context)
            return unknown_obj

        self._modules.add(module_ir)
        module_obj = ModuleObject(context, c.alloc_site, module_ir)

        # resolve the content of module
        module_ctx = context
        ctx_scope = Scope.new(module_obj, None, module_ctx, module_ir, None)

        self.state.set_internal_scope(module_obj, ctx_scope)

        # translate the IRs in the imported module        
        for constraint in self.ir_translator.translate_module(module_ir, c.alloc_site.stmt):
            self.add_constraint(ctx_scope, module_ctx, constraint)
        
        return module_obj
    
    def _apply_load_subscr(self, scope: 'Scope', variable: 'Ctx', c: 'LoadSubscrConstraint', pts: 'PointsToSet'):
        """Apply load constraint: target = base[index]."""
        for index_obj in pts:
            if isinstance(index_obj, ConstantObject):
                field = key(index_obj.value)
                self.add_constraint(scope, scope.context, LoadConstraint(c.base, field, c.target))
            else:
                self.add_constraint(scope, scope.context, LoadConstraint(c.base, elem(), c.target))

    
    def _apply_store_subscr(self, scope: 'Scope', variable: 'Ctx', c: 'StoreSubscrConstraint', pts: 'PointsToSet'):
        """Apply store constraint: base[index] = source."""
        for index_obj in pts:
            if isinstance(index_obj, ConstantObject):
                field = key(index_obj.value)
                self.add_constraint(scope, scope.context, StoreConstraint(c.base, field, c.source))
            else:
                self.add_constraint(scope, scope.context, StoreConstraint(c.base, elem(), c.source))
    
    def _apply_load(self, scope: 'Scope', variable: 'Ctx', c: 'LoadConstraint', pts: 'PointsToSet'):
        """Apply load constraint: target = base.field or target = base[index]."""
        context = scope.context
        target_var = self.state.get_variable(scope, context, c.target)
        
        for base_obj in pts:
            field_access = self.state.get_field(scope, context, base_obj, c.field)
            self.state.pointer_flow_graph.add_edge(field_access, target_var)
    
    def _apply_store(self, scope: 'Scope', variable: 'Ctx', c: 'StoreConstraint', pts: 'PointsToSet'):
        """Apply store constraint: base.field = source or base[index] = source."""
        context = scope.context
        source_var = self.state.get_variable(scope, context, c.source)

        for base_obj in pts:
            field_access = self.state.get_field(scope, context, base_obj, c.field)
            self.state.pointer_flow_graph.add_edge(source_var, field_access)
    
    def _apply_call(self, scope: 'Scope', variable: 'Ctx', c: 'CallConstraint', pts: 'PointsToSet') -> bool:
        """Apply call constraint: target = callee(args...)."""
        context = scope.context
        # TODO renew call graph

        # logger.info(f"Applying call constraint: {c.call_site} -> {pts}")
         
        changed = False
        for callee_obj in pts:

            # logger.info(f"Handling function call: {c.call_site} -> {callee_obj.alloc_site.stmt}\n    {type(callee_obj)} {type(callee_obj.alloc_site.stmt)}")

            if callee_obj.kind == AllocKind.FUNCTION:
                changed = self._handle_function_call(scope, context, c, callee_obj)
            elif callee_obj.kind == AllocKind.CLASS:
                changed = self._handle_class_instantiation(scope, context, c, callee_obj)
            elif callee_obj.kind == AllocKind.METHOD:
                changed = self._handle_method_call(scope, context, c, callee_obj)
            elif callee_obj.kind == AllocKind.BOUND_METHOD:
                changed = self._handle_bound_method_call(c, callee_obj)
            elif callee_obj.kind == AllocKind.BUILTIN:
                changed = self._handle_builtin_call(c, callee_obj)
            # TODO add the __callable__ magic method
            else:
                self._unknown_tracker.record(
                    UnknownKind.CALLEE_NON_CALLABLE,
                    c.call_site,
                    f"Attempting to call non-callable: {callee_obj.kind.value}",
                    context=str(callee_obj)
                )
                
                if self.config.verbose:
                    logger.warning(f"[UNKNOWN] Non-callable at {c.call_site}: {callee_obj}")
                
                '''
                if c.target:
                    unknown_alloc = AllocSite(
                        file=c.call_site,
                        line=0,
                        col=0,
                        kind=AllocKind.UNKNOWN,
                        scope=scope,
                        name=f"unknown_noncallable_{c.call_site}",
                        stmt=None
                    )
                    target_var = self.state.get_variable(scope, context, c.target)
                    unknown_obj = AbstractObject(unknown_alloc, scope.context)
                    self._worklist.add((scope, target_var, PointsToSet.singleton(unknown_obj)))
                    changed = True
                '''
        
        return changed
    
    def _handle_method_call(self, scope: 'Scope', context: 'AbstractContext', call: 'CallConstraint', method_obj: 'MethodObject') -> bool:
        # logger.info(f"Handling method call: {call.call_site} -> {method_obj.alloc_site.stmt}")
        if not isinstance(method_obj, MethodObject):
            logger.info(f"is not method object, {type(func_obj)} got!")
            return False
        
        func_ir: IRFunc = method_obj.alloc_site.stmt
        func_name = func_ir.get_qualname()

        if func_ir.is_static_method:
            return self._handle_function_call(scope, context, call, method_obj)
        
        if func_ir.is_class_method:
            holder_obj = method_obj.class_obj
        else:
            holder_obj = method_obj.instance_obj
            if not holder_obj:
                holder_obj = method_obj.class_obj
        
        if not holder_obj:
            logger.info(f"No holder got in {method_obj}")
            return False
        
        call_site = CallSite(call.call_site, len(call.args))

        args = [self.state.get_variable(scope, context, arg) for arg in call.args]
        # args.insert(0, holder_obj)

        call_context = self.context_selector.select_call_context(
            call_site,
            context,
            holder_obj,
            params=frozenset(args)
        )
        
        logger.debug(f"Handling function call: {call.call_site} -> {method_obj.alloc_site.stmt}")
        
        method_scope = self.state.get_internal_scope(holder_obj)
        callee_scope = Scope.new(method_obj, method_scope.module, call_context, func_ir, method_scope)
        assert holder_obj

        call_edge = CallEdge(kind=CallKind.FUNCTION, callsite=Ctx(context, scope, call.call_site), callee=callee_scope)
        # if self.state.call_graph.has_edge(edge):
        #     return False


        # Put all cell and global vars into scope
        cell_vars = self.state.get_cell_vars(method_obj)
        for name, var in cell_vars.items():
            target_var = self.state.get_variable(callee_scope, call_context, self.variable_factory.make_variable(name))
            self.state.pointer_flow_graph.add_edge(var, target_var)
        
        nonlocal_vars = self.state.get_nonlocal_vars(method_obj)
        for name, var in nonlocal_vars.items():
            self.state.set_variable(callee_scope, call_context, var.content, var)
    
        global_vars = self.state.get_global_vars(method_obj)
        for name, var in global_vars.items():
            var = self.state.get_variable(scope.module, scope.module.context, self.variable_factory.make_variable(name))
            self.state.set_variable(callee_scope, call_context, var.content, var)


        alloc_site = method_obj.alloc_site
        
        old_scope = self.ir_translator._current_scope
        self.ir_translator._current_scope = alloc_site.stmt

        try:
            body_constraints = self.ir_translator.translate_function(func_ir)
        except Exception as e:
            self._unknown_tracker.record(
                UnknownKind.TRANSLATION_ERROR,
                call.call_site,
                f"Error translating function body: {str(e)}",
                context=func_name
            )
            
            if self.config.verbose:
                logger.warning(f"[UNKNOWN] Translation error for {func_name}: {e}")
            
            body_constraints = []
        finally:
            self.ir_translator._current_scope = old_scope
            # self.ir_translator._current_context = old_context
        
        changed = False
        for constraint in body_constraints:
            self.add_constraint(callee_scope, call_context, constraint)
            changed = True
        
        if hasattr(func_ir, 'args') and hasattr(func_ir.args, 'args') and len(func_ir.args.args) > 0:
            param_names = [arg.arg for arg in func_ir.args.args]
            self_name = self.variable_factory.make_variable(param_names.pop(0))
            self_var = self.state.get_variable(callee_scope, call_context, self_name)
            self._worklist.add((callee_scope, self_var, PointsToSet.singleton(holder_obj)))
            for i, param_name in enumerate(param_names):
                if i < len(args):
                    arg_var = args[i]
                    param = self.variable_factory.make_variable(param_name)
                    param_var = self.state.get_variable(callee_scope, call_context, param)
                    self.state.pointer_flow_graph.add_edge(arg_var, param_var)
        
        if call.target:
            ret = self.variable_factory.make_variable("$return")
            ret_var = self.state.get_variable(callee_scope, call_context, ret)
            target = self.variable_factory.make_variable(call.target)
            target_var = self.state.get_variable(scope, context, target)
            self.state.pointer_flow_graph.add_edge(ret_var, target_var)

        self.state.call_graph.add_edge(call_edge)
        logger.debug(f"Adding call edge: {call_edge}")

        return changed
    
    def _handle_function_call(self, scope: 'Scope', context: 'AbstractContext', call: 'CallConstraint', func_obj: 'AbstractObject') -> bool:
        """Handle function call: analyze function body with parameter bindings.
            1. Selects calling context
            2. Translates function body to constraints
            3. Generates parameter passing constraints
            4. Connects return value to caller
            5. Adds call edge to call graph
        """
        # logger.info(f"Handling function call: {call.call_site} -> {func_obj.alloc_site.stmt}")
        if not isinstance(func_obj, FunctionObject):
            logger.info(f"is not function object, {type(func_obj)} got!")
            return False
        
        func_ir: IRFunc = func_obj.alloc_site.stmt
        func_name = func_ir.get_name()
        alloc_site = func_obj.alloc_site
        # logger.info(f"Handling function call: {call.call_site} -> {alloc_site}")
        args = [self.state.get_variable(scope, context, arg) for arg in call.args]
        
        call_site = CallSite(call.call_site, len(call.args))
        call_context = self.context_selector.select_call_context(
            call_site,
            context,
            None,  # No receiver ffor regular functions
            params=frozenset(args)
        )
        
        logger.debug(f"Handling function call: {call.call_site} -> {func_obj.alloc_site.stmt}")
        
        # func_ir = self.function_registry[func_name]
        method_scope = self.state.obj_scope[func_obj]
        alloc_site = func_obj.alloc_site
        # callee_scope = Scope.new(func_obj, method_scope.module, call_context, func_ir, method_scope)
        callee_scope = Scope.new(func_obj, scope.module, call_context, func_ir, scope)
        call_edge = CallEdge(kind=CallKind.FUNCTION, callsite=Ctx(context, scope, call.call_site), callee=callee_scope)
        # if self.state.call_graph.has_edge(edge):
        #     return False

        # Put all cell and global vars into scope
        cell_vars = self.state.get_cell_vars(func_obj)
        for name, var in cell_vars.items():
            target_var = self.state.get_variable(callee_scope, call_context, self.variable_factory.make_variable(name))
            self.state.pointer_flow_graph.add_edge(var, target_var)
        
        nonlocal_vars = self.state.get_nonlocal_vars(func_obj)
        for name, var in nonlocal_vars.items():
            self.state.set_variable(callee_scope, call_context, var.content, var)
    
        global_vars = self.state.get_global_vars(func_obj)
        for name, var in global_vars.items():
            var = self.state.get_variable(scope.module, scope.module.context, self.variable_factory.make_variable(name))
            self.state.set_variable(callee_scope, call_context, var.content, var)

        old_scope = self.ir_translator._current_scope
        # old_context = self.ir_translator._current_context        
        self.ir_translator._current_scope = alloc_site.stmt
        # self.ir_translator._current_context = call_context
        
        try:
            body_constraints = self.ir_translator.translate_function(func_ir)
        except Exception as e:
            self._unknown_tracker.record(
                UnknownKind.TRANSLATION_ERROR,
                call.call_site,
                f"Error translating function body: {str(e)}",
                context=func_name
            )
            
            if self.config.verbose:
                logger.warning(f"[UNKNOWN] Translation error for {func_name}: {e}")
            
            body_constraints = []
        finally:
            self.ir_translator._current_scope = old_scope
            # self.ir_translator._current_context = old_context
        
        changed = False
        for constraint in body_constraints:
            self.add_constraint(callee_scope, call_context, constraint)
            changed = True
        
        if hasattr(func_ir, 'args') and hasattr(func_ir.args, 'args'):
            param_names = [arg.arg for arg in func_ir.args.args]
            for i, param_name in enumerate(param_names):
                if i < len(args):
                    param = self.variable_factory.make_variable(param_name)
                    param_var = self.state.get_variable(callee_scope, call_context, param)
                    arg_var = args[i]
                    self.state.pointer_flow_graph.add_edge(arg_var, param_var)
        
        if call.target:
            ret = self.variable_factory.make_variable("$return")
            ret_var = self.state.get_variable(callee_scope, call_context, ret)
            target = self.variable_factory.make_variable(call.target)
            target_var = self.state.get_variable(scope, context, target)
            self.state.pointer_flow_graph.add_edge(ret_var, target_var)
        
        self.state.call_graph.add_edge(call_edge)
        logger.debug(f"Adding call edge: {call_edge}")

        return changed
    
    def _handle_class_instantiation(self, scope: 'Scope', context: 'AbstractContext', call: 'CallConstraint', class_obj: 'AbstractObject') -> bool:
        """Handle class instantiation: create instance + call __init__."""
        if not call.target:
            return False

        instance_alloc = AllocSite(call.call_site, AllocKind.INSTANCE)
        
        if self.context_selector:
            call_site = CallSite(call.call_site, len(call.args))
            alloc_context = self.context_selector.select_alloc_context(context, instance_alloc, class_obj)
        else:
            alloc_context = context
        
        instance_obj = InstanceObject(alloc_context, instance_alloc, class_obj)

        target_var = self.state.get_variable(scope, context, call.target)        
        changed = self._worklist.add((scope, target_var, PointsToSet.singleton(instance_obj)))

        params = [self.state.get_variable(scope, context, arg) for arg in call.args]
        params.insert(0, instance_obj)
        instance_parent = self.state.get_internal_scope(class_obj).parent
        assert instance_parent, f"{self.state.get_internal_scope(class_obj)} : {class_obj} has no parent"
        instance_ctx = self.context_selector.select_call_context(call.call_site, context, instance_obj, frozenset(params))
        instance_scope = Scope.new(instance_obj, instance_parent.module, instance_ctx, class_obj.alloc_site.stmt, instance_parent)
        self.state.set_internal_scope(instance_obj, instance_scope)

        # Call __init__ function
        init_field = self.state.get_field(instance_ctx, instance_ctx, instance_obj, attr("__init__"))
        var_name = f"$init@{instance_obj}:{class_obj}"
        init_var = self.variable_factory.make_variable(var_name)
        ctx_init_var = self.state.get_variable(scope, context, init_var)
        self.state.pointer_flow_graph.add_edge(init_field, ctx_init_var)
        self.add_constraint(scope, context, CallConstraint(init_var, call.args, None, call.call_site))

        '''
        if self.class_hierarchy and self.class_hierarchy.has_class(class_obj):
            init_pts = self.state.get_field(class_obj, attr("__init__"))
            
            if not init_pts.is_empty():
                instance_var = Variable(
                    name="$instance",
                    scope=scope,
                    context=call.target.context,
                    kind=VariableKind.TEMPORARY
                )
                
                self._worklist.add((scope, instance_var, PointsToSet.singleton(instance_obj)))

                init_args = (instance_var,) + call.args
                init_call = CallConstraint(
                    callee=Variable(
                        name="$init",
                        scope=call.callee.scope,
                        context=call.callee.context,
                        kind=VariableKind.TEMPORARY
                    ),
                    args=init_args,
                    target=None,  # __init__ returns None
                    call_site=call.call_site + "_init"
                )
                
                init_var = init_call.callee
                self.state.set_points_to(init_var, init_pts)
                
                self.add_constraint(init_call)
                changed = True
        '''
        
        return changed
    
    def _handle_bound_method_call(self, call: 'CallConstraint', method_obj: 'AbstractObject') -> bool:
        """Handle bound method call: extract __func__ and __self__, call with self prepended."""
        func_pts = self.state.get_field(method_obj, attr("__func__"))
        self_pts = self.state.get_field(method_obj, attr("__self__"))
        
        if func_pts.is_empty():
            logger.debug("Bound method has no __func__")
            return False
        
        func_var = Variable(
            name="$func",
            scope=call.callee.scope,
            context=call.callee.context,
            kind=VariableKind.TEMPORARY
        )
        self_var = Variable(
            name="$self",
            scope=call.callee.scope,
            context=call.callee.context,
            kind=VariableKind.TEMPORARY
        )
        self.state.set_points_to(func_var, func_pts)
        self.state.set_points_to(self_var, self_pts)
        
        method_call = CallConstraint(
            callee=func_var,
            args=(self_var,) + call.args,
            target=call.target,
            call_site=call.call_site + "_method"
        )
        self.add_constraint(method_call)
        return True
    
    def _handle_builtin_call(self, call: 'CallConstraint', builtin_obj: 'AbstractObject') -> bool:
        """Handle builtin call: use summary to generate constraints."""
        if not self.builtin_manager:
            logger.debug("Cannot handle builtin call: no builtin manager")
            return False

        builtin_name = builtin_obj.alloc_site.stmt.get_name()
        if not builtin_name:
            return False
        summary = self.builtin_manager.get_summary(builtin_name)
        if not summary:
            logger.debug(f"No summary for builtin: {builtin_name}")
            return False
        
        constraints = summary.apply(call.target, list(call.args), call.callee.context)
        for constraint in constraints:
            self.add_constraint(constraint)
        
        return len(constraints) > 0
    
    def query(self) -> ISolverQuery:
        return SolverQuery(self.state, self._stats, self._unknown_tracker)


class SolverQuery(ISolverQuery):
    def __init__(self, state: 'PointerAnalysisState', stats: Dict[str, int], unknown_tracker: 'UnknownTracker'):
        self._state = state
        self._stats = stats
        self._unknown_tracker = unknown_tracker
    
    def points_to(self, var: 'Variable') -> 'PointsToSet':
        return self._state.get_points_to(var)
    
    def get_field(self, obj: 'AbstractObject', field: 'Field') -> 'PointsToSet':
        return self._state.get_field(obj, field)
    
    def may_alias(self, v1: 'Variable', v2: 'Variable') -> bool:
        pts1 = self._state.get_points_to(v1)
        pts2 = self._state.get_points_to(v2)
        
        # Check for intersection
        return (not pts1.intersection(pts2).is_empty())
    
    def call_graph(self) -> 'AbstractCallGraph':
        return self._state.call_graph
    
    def get_statistics(self) -> Dict[str, Any]:
        state_stats = self._state.get_statistics()
        unknown_stats = self._unknown_tracker.get_summary()
        return {
            **state_stats,
            **self._stats,
            **unknown_stats
        }
    
    def get_unknown_summary(self) -> Dict[str, int]:
        return self._unknown_tracker.get_summary()
    
    def get_unknown_details(self) -> List[Dict]:
        return self._unknown_tracker.get_detailed_report()
