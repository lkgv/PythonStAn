"""Constraint-based pointer analysis solver.

This module implements the core solver for pointer analysis using
constraint-based propagation.
"""

import ast
import logging
import warnings
from typing import Set, Dict, Any, TYPE_CHECKING, Optional, List, Tuple, Iterable, Union

from pythonstan.ir.ir_statements import (
    IRScope,
    IRFunc,
    IRModule,
    IRClass,
    IRCall,
    IRLoadAttr,
    IRStoreAttr,
    IRLoadSubscr,
    IRStoreSubscr,
    IRAssign,
    IRCopy,
)

from .state import PointerAnalysisState, PointsToSet
from .constraints import *
from .variable import Variable, VariableKind, VariableFactory, FieldAccess
from .config import Config
from .heap_model import Field, FieldKind, attr, key, elem, value
from pythonstan.graph.call_graph import AbstractCallGraph, CallEdge, CallKind
from .ir_translator import IRTranslator
from .context_selector import ContextSelector, AbstractContext
from .context import Ctx, Scope, CallSite
from .class_hierarchy import ClassHierarchyManager
from .builtin_api_handler import BuiltinSummaryManager
from .unknown_tracker import UnknownTracker, UnknownKind
from .object import *
from .object import ObjectFactory
from .solver_interface import ISolverQuery
from .pointer_flow_graph import PointerFlowGraph, PointerFlowEdge, PointerFlowNode, NormalNode, GuardNode, SelectorNode, PointerFlowKind
from .debug_monitor import DebugMonitor
from .processor import Processor
from pythonstan.world import World

__all__ = ["PointerSolver", "SolverQuery"]

logger = logging.getLogger(__name__)


class PointerSolver:
    def __init__(
        self,
        state: 'PointerAnalysisState',
        config: 'Config',
        processor: 'Processor',
        variable_factory: Optional['VariableFactory'] = None,
        ir_translator: Optional['IRTranslator'] = None,
        context_selector: Optional['ContextSelector'] = None,
        class_hierarchy: Optional['ClassHierarchyManager'] = None,
        builtin_manager: Optional['BuiltinSummaryManager'] = None,
        debug_monitor: 'DebugMonitor' = None,
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
            debug_monitor: Optional debug monitor for tracking
        """
        self.state = state
        self.config = config
        self.processor = processor
        self.ir_translator = ir_translator
        self.context_selector = context_selector
        self.class_hierarchy = class_hierarchy
        self.builtin_manager = builtin_manager
        self.variable_factory = variable_factory or VariableFactory()
        self._unknown_tracker = UnknownTracker(
            track_unknowns=config.track_unknowns,
            log_unknown_details=config.log_unknown_details,
        )
        self._debug_monitor = debug_monitor
        
        # Initialize builtin handler with state
        if self.builtin_manager:
            self.builtin_manager.set_state(state)
        self._reset()
    
    def _reset(self) -> None:
        self._iteration = 0
        self._stats: Dict[str, int] = {
            "iterations": 0,
            "constraints_applied": 0
        }
        self._modules = set()
        self._non_callable_evidence: Dict[Tuple['Ctx[Any]', 'Scope', 'CallConstraint'], Set[str]] = {}
    
    def add_constraint(self, scope: 'Scope', context: 'AbstractContext', constraint: 'Constraint') -> None:
        self.processor.handle_new_constraint(self, scope, constraint)

        if isinstance(constraint, CopyConstraint):
            self.state._static_constraints.append((scope, context, constraint))
        elif isinstance(constraint, AllocConstraint):
            self.state._static_constraints.append((scope, context, constraint))
        else:
            if isinstance(constraint, LoadConstraint):
                base = self.state.get_variable(scope, context, constraint.base)
                self.state.constraints.add(scope, base, constraint)
            elif isinstance(constraint, StoreConstraint):
                base = self.state.get_variable(scope, context, constraint.base)
                self.state.constraints.add(scope, base, constraint)
            elif isinstance(constraint, CallConstraint):
                callee = self.state.get_variable(scope, context, constraint.callee)
                callee_pts = self.state.get_points_to(callee)
                if callee_pts.is_empty():
                    self.state.constraints.add(scope, callee, constraint)
                else:
                    # Lazy call activation: copy callee pts into a fresh temp var
                    # so the call constraint is triggered via normal propagation.
                    lazy_callee = self.variable_factory.make_variable(
                        f"$call_lazy@{constraint.call_site.short_id()}@{constraint.callee.name}",
                        VariableKind.TEMPORARY,
                    )
                    lazy_ctx = self.state.get_variable(scope, context, lazy_callee)
                    lazy_constraint = CallConstraint(
                        callee=lazy_callee,
                        args=constraint.args,
                        kwargs=constraint.kwargs,
                        target=constraint.target,
                        call_site=constraint.call_site,
                    )
                    self.state.constraints.add(scope, lazy_ctx, lazy_constraint)
                    self.state._static_constraints.append((
                        scope,
                        context,
                        CopyConstraint(source=constraint.callee, target=lazy_callee),
                    ))
            elif isinstance(constraint, InheritanceConstraint):
                base = self.state.get_variable(scope, context, constraint.base)
                self.state.constraints.add(scope, base, constraint)


    def solve_to_fixpoint(self) -> None:
        logger.info("Starting constraint solving")
        max_iter = self.config.max_iterations
        
        for _ in self:
            # Update debug monitor iteration
            if self._debug_monitor:
                self._debug_monitor.set_iteration(self._iteration)
            # Log progress periodically
            log_interval = self.config.debug_log_interval if self.config.enable_debug_monitor else 1000
            if self._iteration % log_interval == 0:
                logger.info(f"Iter {self._iteration}, WL{len(self.state._worklist)}, objs: {len(self.state._heap.objects)}, "
                            f"CE: {len(self.state.call_graph.edges)}, pCE: {self.state.call_graph.num_plain_edges()}, "
                            f"totC: {len(self.state.constraints)}, "
                            f"C: {len(self.state.constraints.get_by_type(CallConstraint))}, "
                            f"I: {len(self.state.constraints.get_by_type(InheritanceConstraint))}, "
                            f"L: {len(self.state.constraints.get_by_type(LoadConstraint))}, "
                            f"S: {len(self.state.constraints.get_by_type(StoreConstraint))}, "
                            f"LS: {len(self.state.constraints.get_by_type(LoadSubscrConstraint))}, "
                            f"SS: {len(self.state.constraints.get_by_type(StoreSubscrConstraint))}, "
                            f"AR: {len(self.state.constraints.get_by_type(AttrReadConstraint))}, "
                            f"AW: {len(self.state.constraints.get_by_type(AttrWriteConstraint))}, "
                )
                # Record iteration snapshot
                self._log_solver_state()
            if self._iteration >= max_iter:
                logger.warning(f"Reached max iterations {max_iter}")
        
        self._stats["iterations"] = self._iteration
        logger.info(f"Processed {len(self._modules)} modules: {self._modules}")
        logger.info(f"Call Constraints: {len(self.state.constraints.get_by_type(CallConstraint))}")
        abs_nodes = set([node.stmt.get_qualname() for node in self.state._call_graph.get_nodes()])
        logger.info(f"Call graph: {self.state._call_graph} node: {len(self.state._call_graph.get_nodes())} edge: {self.state._call_graph.get_number_of_edges()}")
        logger.info(f"    absolute nodes: { len(abs_nodes) } absolute edges: { self.state._call_graph.num_plain_edges() }")
        logger.info(f"Pointer flow graph: {self.state._pointer_flow_graph} node: {len(self.state._pointer_flow_graph.get_nodes())} edge: {len(self.state._pointer_flow_graph.get_edges())}")                
        logger.info(f"Converged after {self._iteration} iterations")
        
    def __iter__(self):
        self._reset()
        return self
    
    def __next__(self) -> 'PointerAnalysisState':
        if ((not self.state._worklist.empty()) or self.state._static_constraints) and self._iteration < self.config.max_iterations:
            self._iteration += 1
            if self.state._static_constraints:
                scope, ctx, constraint = self.state._static_constraints.pop()
                result = self._apply_static(scope, ctx, constraint)
                return result
            elif not self.state._worklist.empty():
                import time                
                start_time = time.time()
                
                scope, node, pts = self.state._worklist.pop()
                result, num_pts, num_constraint = self._apply_dynamic(scope, node, pts)
                                                        
                time_taken = time.time() - start_time
                if time_taken > 1 and isinstance(node, NormalNode):
                    logger.warning(f"Applying dynamic node {node.var.content} with pts {num_pts} constraints {num_constraint} took {time_taken} seconds")
                    
                return result
            
        self._finalize_unknowns_at_fixpoint()
        raise StopIteration
    
    def _apply_dynamic(self, scope: 'Scope', node: 'PointerFlowNode', pts: 'PointsToSet') -> 'PointerAnalysisState':
        if isinstance(node, NormalNode):
            assert isinstance(node.var, Ctx), f"node.var must be a Ctx, but got {type(node.var)}"
        diff = pts - self.state.get_points_to(node)
        num_pts = len(diff)
        num_constraint = 0
        if not diff.is_empty():
            self.state.set_points_to(node, diff)
            
            # apply the constraints associated with the variable
            if isinstance(node, NormalNode):
                self.processor.handle_pts(self, node.var, scope, diff)                
                self.state.set_points_to(node.var, diff)
                
                constraints = self.state.constraints.iter_scoped_by_variable(node.var)
                num_constraint += len(constraints)
                for constraint_scope, constraint in constraints:
                    self._apply_constraint(constraint_scope, node.var, constraint, diff)
            
            for succ, succ_pts in self.state.pointer_flow_graph.propagate(node, diff):
                succ_scope = succ.var.scope if isinstance(succ, NormalNode) else None
                self.state._worklist.add((succ_scope, succ, succ_pts))
        return self.state, num_pts, num_constraint

    def _log_solver_state(self):
        """Log periodic snapshot of solver state for debugging."""
        if not self._debug_monitor or not self._debug_monitor.enabled:
            return
        
        self._debug_monitor.record_iteration_snapshot(
            worklist_size=len(self.state._worklist),
            call_edges=self.state._call_graph.num_plain_edges(),
            pfg_edges=len(self.state.pointer_flow_graph.get_edges()),
            num_variables=len(self.state._env),
            num_objects=len(self.state._heap.objects)
        )

    def _apply_static(self, scope: 'Scope', context: 'AbstractContext', constraint: 'Constraint'):
        if isinstance(constraint, AllocConstraint):
            self._apply_alloc(scope, context, constraint)
        elif isinstance(constraint, CopyConstraint):
            self._apply_copy(scope, context, constraint)

    def _apply_constraint(self, scope: 'Scope', variable: Ctx[Any], constraint: 'Constraint', diff: 'PointsToSet') -> bool:  
        result = self.processor.handle_constraint(self, variable, scope, constraint, diff)
        if not result:
            # Here shoud add supports for Imports
            if isinstance(constraint, LoadConstraint):
                result = self._apply_load(scope, variable, constraint, diff)
            elif isinstance(constraint, StoreConstraint):
                result = self._apply_store(scope, variable, constraint, diff)
            elif isinstance(constraint, CallConstraint):
                result = self._apply_call(scope, variable, constraint, diff)
            elif isinstance(constraint, InheritanceConstraint):
                result = self._apply_inheritance(scope, variable, constraint, diff)
            else:
                result = False
        return result

    def _apply_copy(self, scope: 'Scope', context: 'AbstractContext', c: 'CopyConstraint'):
        """Apply copy constraint: target = source."""
        src = self.state.get_variable(scope, context, c.source)
        tgt = self.state.get_variable(scope, context, c.target)
        self.state._add_var_points_flow(src, tgt)
        
    def _apply_alloc(self, scope: 'Scope', context: 'AbstractContext', c: 'AllocConstraint'):
        """Apply allocation constraint: target = new Object."""

        target = self.state.get_variable(scope, context, c.target)

        orig_obj = self.state._heap.get_obj(scope, context, c.alloc_site)
        if orig_obj is not None:  #  and not isinstance(orig_obj, AbstractObject):
            return
        
        if self.processor.handle_allocation(self, target, scope, context, c):
            return

        if c.alloc_site.kind == AllocKind.FUNCTION:
            obj = self._alloc_function(scope, context, c)
        
        elif c.alloc_site.kind == AllocKind.METHOD:
            obj = self._alloc_method(scope, context, c)

        elif c.alloc_site.kind == AllocKind.CLASS:
            # complex class translation logic, for processing base classes
            if self.config.debug_inheritance:
                logger.info(f"[ALLOC] Allocating class: {c.target.name}")
            obj = self._alloc_class(scope, context, c)
        
        elif c.alloc_site.kind == AllocKind.MODULE:
            obj = self._alloc_module(scope, context, c)
        
        elif c.alloc_site.kind == AllocKind.CONSTANT and self.config.index_sensitive:
            obj = self._alloc_constant(scope, context, c)
        
        elif c.alloc_site.kind == AllocKind.OBJECT and False:
            # logic for instance allocation is located in _apply_call
            obj = None 
        elif not self.config.index_sensitive:
            obj = AbstractObject(alloc_site=c.alloc_site, context=context)
                
        else:
            obj = AbstractObject(alloc_site=c.alloc_site, context=context)

        if obj is not None:
            self.state._heap.set_obj(scope, context, c.alloc_site, obj)
            pts = PointsToSet.singleton(obj)
            target = self.state.get_variable(scope, context, c.target)
            
            if self.config.debug_inheritance and c.alloc_site.kind == AllocKind.CLASS:
                logger.info(f"[ALLOC] Adding class object to variable: {c.target.name} = {obj.alloc_site.stmt.name if hasattr(obj.alloc_site.stmt, 'name') else obj}")
                logger.info(f"  Target variable: {target}")
            
            # Debug monitoring: record object allocation
            if self._debug_monitor and self._debug_monitor.enabled and self._debug_monitor.track_object_flow:
                obj_id = f"{c.alloc_site.kind.value}:{id(obj)}"
                location = str(c.alloc_site)
                self._debug_monitor.record_object_allocated(
                    obj_id=obj_id,
                    obj_kind=c.alloc_site.kind.value,
                    location=location,
                    target_var=str(c.target)
                )
            self.state.obj_scope[obj] = scope
            self.handle_new_points_to(target, scope, pts)
    
    def handle_new_points_to(self, target: 'Ctx[Any]', scope: 'Scope', pts: 'PointsToSet') -> None:
        if not self.processor.handle_new_points_to(self, target, scope, pts):
            self.state._worklist.add((scope, NormalNode(target), pts))
    
    def _alloc_constant(self, scope: 'Scope', context: 'AbstractContext', c: 'AllocConstraint') -> 'ConstantObject':
        stmt: 'IRAssign' = c.alloc_site.stmt
        assert isinstance(stmt, IRAssign), f"alloc_site.stmt must be an IRAssign, but got {type(stmt)}"
        obj = ConstantObject(self.context_selector.empty_context(), c.alloc_site, stmt.get_rval().value)
        return obj

    def _infer_free_vars(self, ir_func: 'IRFunc') -> Set[str]:
        ir = self.state.scope_manager.get_ir(ir_func, "ir")
        if ir is None:
            return set()
        loads = set()
        stores = set()
        for stmt in ir:
            loads.update(stmt.get_loads())
            stores.update(stmt.get_stores())
            if isinstance(stmt, IRStoreSubscr):
                subslice = stmt.get_slice()
                if isinstance(subslice, ast.Name):
                    loads.add(subslice.id)
        free_vars = loads - stores - ir_func.get_arg_names()
        free_vars.difference_update(ir_func.get_global_vars())
        free_vars.difference_update(ir_func.get_nonlocal_vars())
        return {name for name in free_vars if name and name.isidentifier()}


    def _resolve_outer_var_kind(self, scope: 'Scope', var_name: str) -> VariableKind:
        stmt = getattr(scope, "stmt", None)
        if isinstance(stmt, IRModule):
            return VariableKind.GLOBAL
        if isinstance(stmt, IRFunc):
            if var_name in stmt.get_global_vars():
                return VariableKind.GLOBAL
            if var_name in stmt.get_nonlocal_vars():
                return VariableKind.NONLOCAL
            if var_name in stmt.get_cell_vars():
                return VariableKind.CELL
            if var_name in stmt.arg_names:
                return VariableKind.LOCAL
        return VariableKind.LOCAL

    @staticmethod
    def is_entrance(ir_func: 'IRFunc') -> bool:
        # return True
        name = ir_func.name
        if name.startswith("test") or name.startswith("__init__") or name.startswith("main"):
            return True
        return False
    
    def _alloc_method(self, scope: 'Scope', context: 'AbstractContext', c: 'AllocConstraint') -> 'MethodObject':
        ir_func = c.alloc_site.stmt
        assert isinstance(ir_func, IRFunc), f"AllocSite to be allocated as function {c.alloc_site} should be IRFunc, {type(ir_func)} got!"

        obj = MethodObject(context, c.alloc_site, scope, c.alloc_site.stmt, scope.obj, None)
        
        # process cell vars into the closure
        cell_vars = {}
        cell_var_names = ir_func.get_cell_vars()
        if not cell_var_names:
            cell_var_names = self._infer_free_vars(ir_func)
        for var_name in cell_var_names:
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
        
        if self.is_entrance(func_ir):        
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
        cell_var_names = ir_func.get_cell_vars()
        if not cell_var_names:
            cell_var_names = self._infer_free_vars(ir_func)
        for var_name in cell_var_names:
            var_kind = self._resolve_outer_var_kind(scope, var_name)
            var = self.variable_factory.make_variable(var_name, var_kind)
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
        alloc_site = func_obj.alloc_site
        callee_scope = Scope.new(func_obj, scope.module, call_context, func_ir, scope)
        old_scope = self.ir_translator._current_scope
        self.ir_translator._current_scope = alloc_site.stmt
        
        if self.is_entrance(ir_func):
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
        # logger.info(f"alloc class {c}")
        assert isinstance(ir_cls, IRClass), f"AllocSite to be allocated as class {c.alloc_site} should be IRClass, {type(ir_cls)} got!"

        obj = ClassObject(context, c.alloc_site, scope, c.alloc_site.stmt)
        
        cls_context = self.context_selector.select_alloc_context(context, obj)
        
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
        ctx_scope = Scope.new(obj, scope.module, cls_context, ir_cls, scope)
        self.state.set_internal_scope(obj, ctx_scope)

        # inner_var = self.state.get_variable(ctx_scope, context, self.variable_factory.make_variable("$class", VariableKind.LOCAL))
        # self.state._worklist.add((scope, NormalNode(inner_var), PointsToSet.singleton(obj)))

        # translate the IRs in the imported module        
        for constraint in self.ir_translator.translate_class(ir_cls):
            self.add_constraint(ctx_scope, cls_context, constraint)
        
        for inner_var in self.ir_translator.used_variables:
            ctx_field = self.state.get_field(scope, context, obj, attr(inner_var.name))
            ctx_inner_var = self.state.get_variable(ctx_scope, cls_context, inner_var)
            self.state._add_var_points_flow(ctx_inner_var, ctx_field)
            if self.config.debug_inheritance:
                logger.info(f"[CLASS] Storing field {obj.alloc_site.stmt.name}.{inner_var.name}: {ctx_inner_var} -> {ctx_field}")

        return obj

    def _alloc_module(self, scope: 'Scope', context: 'AbstractContext', c: 'AllocConstraint') -> 'ModuleObject':
        assert c.alloc_site.kind == AllocKind.MODULE, "AllocSite kind in module allocation should be Module"
        assert c.alloc_site.stmt is not None, "AllocSite should have IRImport as stmt"

        module_ir = self.state.scope_manager.module_graph.get_succ_module(scope.module.stmt, c.alloc_site.stmt)
                
        if module_ir is None:
            self._unknown_tracker.record(
                UnknownKind.IMPORT_NOT_FOUND,
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
        # module_ctx = self.context_selector.select_alloc_context(context, module_obj)
        ctx_scope = Scope.new(module_obj, None, module_ctx, module_ir, None)

        self.state.set_internal_scope(module_obj, ctx_scope)

        # translate the IRs in the imported module        
        for constraint in self.ir_translator.translate_module(module_ir, c.alloc_site.stmt):
            self.add_constraint(ctx_scope, module_ctx, constraint)
        
        return module_obj

    def _apply_inheritance(self, scope: 'Scope', variable: 'Ctx', c: 'InheritanceConstraint', pts: 'PointsToSet'):
        """Apply inheritance constraint: resolve field from base class.
        
        For each base class object in pts, get the field and create PFG edge
        to the selector node. This allows parent class fields to flow to
        the inheritance target (used by ClassObject and SuperObject).
        """
        from .object import ClassObject
        for base_obj in pts:
            # For class inheritance, use the base class's internal scope
            if isinstance(base_obj, ClassObject):
                base_internal_scope = self.state.get_internal_scope(base_obj)
                field_access = self.state.get_field(base_internal_scope, base_obj.context, base_obj, c.field)
                if self.config.debug_inheritance:
                    logger.info(f"[INHERIT] Applying inheritance: {base_obj.alloc_site.stmt.name if hasattr(base_obj.alloc_site.stmt, 'name') else base_obj}.{c.field} -> selector")
                    logger.info(f"  Base internal scope: {base_internal_scope.stmt.get_qualname() if hasattr(base_internal_scope.stmt, 'get_qualname') else base_internal_scope.stmt}")
                    logger.info(f"  Field access: {field_access}")
                    logger.info(f"  Field pts: {self.state.get_points_to(field_access)}")
            else:
                field_access = self.state.get_field(scope, scope.context, base_obj, c.field)
            edge = PointerFlowEdge(NormalNode(field_access), c.target, PointerFlowKind.NORMAL)
            # Register the edge with the selector node with its inheritance index
            c.target.add_edge(edge, c.index)
            self.state._add_points_flow_edge(edge)
    
    def _apply_load(self, scope: 'Scope', variable: 'Ctx', c: 'LoadConstraint', pts: 'PointsToSet'):
        """Apply load constraint: target = base.field or target = base[index]."""
        context = scope.context
        target_var = self.state.get_variable(scope, context, c.target)
        
        for base_obj in pts:
            # Special handling for module imports: from module import name
            # Instead of using field access, directly copy from module's variable
            if isinstance(base_obj, ModuleObject) and c.field and c.field.kind.name == 'ATTR':
                # Get the module's internal scope
                module_scope = self.state.get_internal_scope(base_obj)
                if module_scope:
                    # Create variable in module scope for the imported name
                    imported_var_name = c.field.name  # e.g., "foo" from module.foo
                    imported_var = Variable(
                        name=imported_var_name,
                        scope=module_scope.stmt,
                        context=module_scope.context,
                        kind=VariableKind.LOCAL
                    )
                    # Get the contextualized variable from module scope
                    module_var = self.state.get_variable(module_scope, module_scope.context, imported_var)
                    # Direct copy: module.var -> local.var (bypass field mechanism)
                    self.state._add_var_points_flow(module_var, target_var)
                    continue
            
            # Special handling for builtin methods on container objects
            if c.field and c.field.kind == FieldKind.ATTRIBUTE and c.field.name:
                builtin_type = None
                if isinstance(base_obj, ListObject):
                    builtin_type = "list"
                elif isinstance(base_obj, DictObject):
                    builtin_type = "dict"
                elif isinstance(base_obj, TupleObject):
                    builtin_type = "tuple"
                elif isinstance(base_obj, SetObject):
                    builtin_type = "set"
                elif isinstance(base_obj, ConstantObject):
                    if isinstance(base_obj.value, str):
                        builtin_type = "str"
                    elif isinstance(base_obj.value, bytes):
                        builtin_type = "bytes"
                    elif isinstance(base_obj.value, bytearray):
                        builtin_type = "bytearray"
                elif isinstance(base_obj, BuiltinInstanceObject):
                    builtin_type = base_obj.builtin_type
                elif isinstance(base_obj, GeneratorObject):
                    builtin_type = "generator"
                elif isinstance(base_obj, CoroutineObject):
                    builtin_type = "coroutine"

                if builtin_type:
                    builtin_methods = self.state._get_builtin_methods_for_type(builtin_type)
                    if c.field.name in builtin_methods:
                        method_alloc = AllocSite(
                            stmt=f"<builtin_method:{c.field.name}>",
                            kind=AllocKind.BUILTIN,
                        )
                        method_obj = BuiltinMethodObject(
                            context=context,
                            alloc_site=method_alloc,
                            method_name=c.field.name,
                            receiver=base_obj,
                            receiver_var=c.base,
                        )
                        self.state._worklist.add((scope, NormalNode(target_var), PointsToSet.singleton(method_obj)))
                        continue
            
            # Default behavior: use field access for classes, instances, etc.
            field_access = self.state.get_field(scope, context, base_obj, c.field)
            self.state._add_var_points_flow(field_access, target_var)
    
    def _apply_store(self, scope: 'Scope', variable: 'Ctx', c: 'StoreConstraint', pts: 'PointsToSet'):
        """Apply store constraint: base.field = source or base[index] = source."""
        context = scope.context
        source_var = self.state.get_variable(scope, context, c.source)

        for base_obj in pts:
            field_access = self.state.get_field(scope, context, base_obj, c.field)
            self.state._add_var_points_flow(source_var, field_access)
    
    def _apply_call(self, scope: 'Scope', variable: 'Ctx', c: 'CallConstraint', pts: 'PointsToSet') -> bool:
        """Apply call constraint: target = callee(args...)."""
        # logger.info(f"Applying call constraint: {c.call_site} -> {pts}")
        
        # Debug monitoring: record call constraint processing
        if self._debug_monitor and self._debug_monitor.enabled:
            self._debug_monitor.record_call_constraint_processed(
                call_site=str(c.call_site),
                callee_var=str(variable),
                callee_pts_size=len(pts)
            )
        
        # Check for empty callee
        if len(pts) == 0:
            if self._debug_monitor and self._debug_monitor.enabled:
                self._debug_monitor.record_call_failed(
                    call_site=str(c.call_site),
                    reason="empty_callee",
                    details="Callee points-to set is empty"
                )
            return False
         
        changed = False
        for callee_obj in pts:
            # logger.info(f"Handling function call: {c.call_site} -> {callee_obj.alloc_site.stmt}\n    {type(callee_obj)} {type(callee_obj.alloc_site.stmt)}")
            if self.processor.handle_call(self, variable, scope, c, callee_obj):
                changed = True                    
            # TODO add the __callable__ magic method
            else:
                self._record_non_callable_evidence(scope, variable, c, callee_obj)
                
                # Debug monitoring: record non-callable
                if self._debug_monitor and self._debug_monitor.enabled:
                    self._debug_monitor.record_call_failed(
                        call_site=str(c.call_site),
                        reason="non_callable",
                        details=f"Object kind: {callee_obj.kind.value}"
                    )
                
                if self.config.verbose:
                    logger.warning(f"[UNKNOWN] Non-callable at {c.call_site}: {callee_obj}")
        return changed

    def _record_non_callable_evidence(
        self,
        scope: 'Scope',
        variable: 'Ctx[Any]',
        call: 'CallConstraint',
        callee_obj: 'AbstractObject'
    ) -> None:
        """Collect non-callable evidence during solving without reporting."""
        if not self.config.track_unknowns:
            return
        key = (variable, scope, call)
        observed = self._non_callable_evidence.setdefault(key, set())
        observed.add(callee_obj.kind.value)

    def _is_callable_for_constraint(self, call: 'CallConstraint', callee_obj: 'AbstractObject') -> bool:
        """Side-effect-free check for callability consistent with call handling."""
        if isinstance(
            callee_obj,
            (
                MethodObject,
                FunctionObject,
                BuiltinObject,
                BuiltinFunctionObject,
                BuiltinMethodObject,
                BuiltinClassObject,
                BuiltinInstanceObject,
            ),
        ) or callee_obj.kind == AllocKind.BUILTIN:
            return True
        if isinstance(callee_obj, ClassObject):
            return True
        callee_name = getattr(call.callee, "name", str(call.callee))
        if callee_name.startswith("$call@"):
            return False
        return True

    @staticmethod
    def _is_internal_helper_callee_name(callee_name: str) -> bool:
        """Identify internal helper calls that are speculative/fallback-safe."""
        return callee_name.startswith("$") and not callee_name.startswith("$tmp")
        return callee_name.startswith(
            (
                "$getattribute@",
                "$getattr@",
                "$setattr@",
                "$descriptor_",
                "$new@",
                "$getitem"
            )
        )

    def _finalize_unknowns_at_fixpoint(self) -> None:
        """Scan unresolved constraints after convergence to report true unknowns."""
        if not self.config.track_unknowns:
            return

        # Unresolved calls: callee points-to remains empty at fixpoint.
        
        strs = set()
        
        for trigger_var, _scope, constraint in self.state.constraints.iter_scoped():
            if not isinstance(constraint, CallConstraint):
                continue
            pts = self.state.get_points_to(trigger_var)
            if not pts.is_empty():
                continue
            callee_name = getattr(constraint.callee, "name", str(constraint.callee))
            if self._is_internal_helper_callee_name(callee_name) or self.builtin_manager.has_summary(callee_name):
                continue
            
            if not trigger_var.content.name.startswith("$tmp") or not constraint.call_site.scope_name.startswith("flask"):
                continue
            
            if constraint.call_site.statement in self.state.call_graph.non_empty:
                continue
            
            record = f">>> _finalize_unknowns_at_fixpoint: {constraint.call_site.site_id}: {callee_name} not found"
            if record not in strs:
                strs.add(record)
                print(record)
            else:
                continue
            
            self._unknown_tracker.record(
                UnknownKind.CALLEE_EMPTY,
                str(constraint.call_site),
                f"Callee '{callee_name}' unresolved at fixpoint (points-to empty)",
                context=str(trigger_var)
            )
            

        # Non-callable calls: only report if no callable targets at fixpoint.
        for (trigger_var, _scope, constraint), _kinds in self._non_callable_evidence.items():
            pts = self.state.get_points_to(trigger_var)
            if pts.is_empty():
                continue
            if any(self._is_callable_for_constraint(constraint, obj) for obj in pts):
                continue
            callee_name = getattr(constraint.callee, "name", str(constraint.callee))
            if self._is_internal_helper_callee_name(callee_name):
                continue
            
            if self._is_internal_helper_callee_name(callee_name) or self.builtin_manager.has_summary(callee_name):
                continue
            
            if constraint.call_site.statement in self.state.call_graph.non_empty:
                continue
            
            if not trigger_var.content.name.startswith("$tmp") or not constraint.call_site.scope_name.startswith("flask"):
                continue
            
            record = f">>> _finalize_unknowns_at_fixpoint: {constraint.call_site.site_id}: {callee_name} not found"
            if record not in strs:
                strs.add(record)
                print(record)
            else:
                continue
            
            kind_list = ", ".join(sorted({obj.kind.value for obj in pts}))
            self._unknown_tracker.record(
                UnknownKind.CALLEE_NON_CALLABLE,
                str(constraint.call_site),
                f"No callable targets at fixpoint for '{callee_name}' (kinds: {kind_list})",
                context=str(trigger_var)
            )

 
    def _handle_class_instantiation(self, scope: 'Scope', context: 'AbstractContext', call: 'CallConstraint', class_obj: 'AbstractObject') -> bool:
        """Handle class instantiation: call __new__, then __init__ conditionally."""
        # logger.info(f"Handling class instantiation: {call.call_site} -> {class_obj.alloc_site.stmt}")

        instance_alloc = AllocSite(call.call_site.statement, AllocKind.INSTANCE)
        if self.context_selector:
            alloc_context = self.context_selector.select_alloc_context(context, instance_alloc, class_obj)
        else:
            alloc_context = context
        instance_obj = InstanceObject(alloc_context, instance_alloc, class_obj)

        cls_scope = self.state.get_internal_scope(class_obj)
        params = (
            [("$self", instance_obj)] +
            [self.state.get_variable(scope, context, arg) for arg in call.args] +
            [(k, self.state.get_variable(scope, context, arg)) for k, arg in call.kwargs]
        )

        instance_parent = cls_scope.parent
        assert instance_parent, f"{cls_scope} : {class_obj} has no parent"
        instance_ctx = self.context_selector.select_call_context(call.call_site, context, instance_obj, frozenset(params))
        instance_scope = Scope.new(instance_obj, instance_parent.module, instance_ctx, class_obj.alloc_site.stmt, instance_parent)
        self.state.set_internal_scope(instance_obj, instance_scope)

        # Seed __new__ result with a fresh instance as a conservative default
        new_result_var = self.variable_factory.make_variable(f"$new_result@{call.call_site.short_id()}")
        ctx_new_result_var = self.state.get_variable(scope, context, new_result_var)
        self.handle_new_points_to(ctx_new_result_var, scope, PointsToSet.singleton(instance_obj))

        # Load and call C.__new__(C, *args, **kwargs)
        cls_var = self.variable_factory.make_variable(f"$class@{call.call_site.short_id()}")
        ctx_cls_var = self.state.get_variable(scope, context, cls_var)
        self.handle_new_points_to(ctx_cls_var, scope, PointsToSet.singleton(class_obj))

        new_callee_var = self.variable_factory.make_variable(f"$new@{call.call_site.short_id()}")
        self.add_constraint(
            scope,
            context,
            LoadConstraint(
                base=cls_var,
                field=attr("__new__"),
                target=new_callee_var,
            ),
        )
        new_args = (cls_var,) + call.args
        self.add_constraint(
            scope,
            context,
            CallConstraint(
                callee=new_callee_var,
                args=new_args,
                kwargs=call.kwargs,
                target=new_result_var,
                call_site=call.call_site,
            ),
        )

        # Flow __new__ result to call target
        if call.target:
            target_var = self.state.get_variable(scope, context, call.target)
            self.state._add_var_points_flow(ctx_new_result_var, target_var)

        # Call __init__ only for instance-like results
        init_base_var = self.variable_factory.make_variable(f"$init_base@{call.call_site.short_id()}")
        ctx_init_base_var = self.state.get_variable(scope, context, init_base_var)

        guard = GuardNode(
            lambda edge, pts: PointsToSet.from_objects(
                [obj for obj in pts if isinstance(obj, InstanceObject)]
            )
        )
        self.state._add_points_flow_edge(
            PointerFlowEdge(NormalNode(ctx_new_result_var), guard, PointerFlowKind.NORMAL)
        )
        self.state._add_points_flow_edge(
            PointerFlowEdge(guard, NormalNode(ctx_init_base_var), PointerFlowKind.NORMAL)
        )

        init_callee_var = self.variable_factory.make_variable(f"$bound_init@{call.call_site.short_id()}")
        self.add_constraint(
            scope,
            context,
            LoadConstraint(
                base=init_base_var,
                field=attr("__init__"),
                target=init_callee_var,
            ),
        )
        self.add_constraint(
            scope,
            context,
            CallConstraint(init_callee_var, call.args, call.kwargs, None, call.call_site),
        )

        return True
    
    def _handle_builtin_call(self, scope: 'Scope', context: 'AbstractContext', call: 'CallConstraint', builtin_obj: 'AbstractObject') -> bool:
        """Handle builtin call: use builtin API handler to generate constraints.
        
        This method delegates to the BuiltinAPIHandler which creates appropriate
        constraints and PFG edges for builtin operations.
        """
        if not self.builtin_manager:
            logger.debug("Cannot handle builtin call: no builtin manager")
            return False

        # Get the builtin API handler
        handler = self.builtin_manager.get_handler()
        if not handler:
            logger.debug("Builtin handler not initialized")
            return False
        
        # Delegate to handler to generate constraints
        try:
            constraints = handler.handle_builtin_call(scope, context, call, builtin_obj)
            
            # Add all generated constraints to the solver
            for constraint in constraints:
                self.add_constraint(scope, context, constraint)
            
            return len(constraints) > 0
        finally:
            pass        
        return False
    
    def _try_ai_intrinsic(
        self,
        scope: 'Scope',
        context: 'AbstractContext',
        call: 'CallConstraint',
        builtin_obj: 'AbstractObject',
    ) -> bool:
        """Try to handle builtin call via AI intrinsic modeling.
        
        For getattr/setattr/hasattr, invokes AI's intrinsic analyzer
        which can use constant string attribute names for precision.
        
        Args:
            scope: Current scope
            context: Current context
            call: Call constraint
            builtin_obj: Builtin function object
            
        Returns:
            True if AI handled the call, False to fall back to normal builtin handler
        """
        # Gate: skip AI intrinsics if disabled in config (for KCFA vs KCFA+AI comparison)
        if not self.config.enable_ai_intrinsics:
            return False
        
        # Check if this is a BuiltinFunctionObject with a supported name
        if not isinstance(builtin_obj, BuiltinFunctionObject):
            return False
        
        func_name = builtin_obj.function_name
        if func_name not in {"getattr", "setattr", "hasattr"}:
            return False
        
        # Lazy initialization of AI engine
        if not hasattr(self, '_ai_engine'):
            from .pta_query_adapter import PtaQueryAdapter
            from pythonstan.analysis.pointer.ai.api import AISummaryEngine, AnalysisBudget
            
            adapter = PtaQueryAdapter(self.state, self.class_hierarchy, self.context_selector)
            budget = AnalysisBudget()
            self._ai_engine = AISummaryEngine(adapter, budget)
        
        # Build arg bindings from call arguments
        from pythonstan.analysis.pointer.ai.api import ArgBinding
        from pythonstan.analysis.pointer.ai.state import AbsVal
        
        arg_bindings = []
        arg_names = ["obj", "name", "value"] if func_name == "setattr" else \
                    ["obj", "name", "default"] if func_name == "getattr" else \
                    ["obj", "name"]
        
        for i, arg_var in enumerate(call.args):
            if i >= len(arg_names):
                break
            
            param_name = arg_names[i]
            ctx_arg = self.state.get_variable(scope, context, arg_var)
            arg_pts = self.state.get_points_to(ctx_arg)
            
            # For "name" argument, try to extract constant strings from ConstantObjects
            abs_val = None
            if param_name == "name":
                const_strs = set()
                for obj in arg_pts:
                    if isinstance(obj, ConstantObject) and isinstance(obj.value, str):
                        const_strs.add(obj.value)
                
                if const_strs:
                    # We have constant string(s) - use AbsVal string component
                    abs_val = AbsVal.from_strs(frozenset(const_strs))
                    # Also include addresses in case there are non-constant strings
                    if len(arg_pts) > len(const_strs):
                        abs_val = abs_val.with_addrs(frozenset(arg_pts))
            
            if abs_val is None:
                abs_val = AbsVal.from_addrs(frozenset(arg_pts))
            
            arg_bindings.append(ArgBinding(
                param_name=param_name,
                points_to=frozenset(arg_pts),
                value=abs_val,
            ))
        
        # Determine target name for return value
        target_name = None
        if call.target:
            target_name = call.target.name if hasattr(call.target, 'name') else str(call.target)
        
        try:
            # Run AI intrinsic analysis
            summary = self._ai_engine.analyze_intrinsic(
                builtin_name=func_name,
                call_context=context,
                arg_bindings=arg_bindings,
                caller_scope=scope,
                target_name=target_name,
            )
            
            changed = False
            
            # Compile return value into target variable
            if call.target and summary.ret:
                target_var = self.state.get_variable(scope, context, call.target)
                ret_pts = PointsToSet.from_objects(summary.ret)
                self.handle_new_points_to(target_var, scope, ret_pts)
                changed = True
            
            # Compile writes into field updates
            for write in summary.writes:
                for obj in write.obj_addrs:
                    field_var = self.state.get_field(scope, context, obj, write.field_key)
                    value_pts = PointsToSet.from_objects(write.value_addrs)
                    self.handle_new_points_to(field_var, scope, value_pts)
                    changed = True
            
            # If AI produced useful results, return True
            if changed or summary.ret or summary.writes:
                return True
            
            # AI didn't produce useful results, fall back to normal handler
            return False
            
        except Exception as e:
            # AI failed, fall back to normal builtin handler
            logger.debug(f"AI intrinsic failed for {func_name}: {e}")
            return False
    
    def query(self) -> ISolverQuery:
        return SolverQuery(
            self.state,
            self._stats,
            self._unknown_tracker,
            class_hierarchy=self.class_hierarchy,
        )


class SolverQuery(ISolverQuery):
    def __init__(
        self,
        state: 'PointerAnalysisState',
        stats: Dict[str, int],
        unknown_tracker: 'UnknownTracker',
        class_hierarchy: Optional['ClassHierarchyManager'] = None,
    ):
        self._state = state
        self._stats = stats
        self._unknown_tracker = unknown_tracker
        self._class_hierarchy = class_hierarchy

    def _scope_label(self, scope: IRScope) -> str:
        if isinstance(scope, IRModule):
            entry_module = World().get_entry_module()
            if scope is entry_module:
                return "__main__"
        return scope.get_qualname()

    def _resolve_var_kind(self, var_name: str, ir_scope: IRScope) -> VariableKind:
        if var_name in ir_scope.get_global_vars():
            return VariableKind.GLOBAL
        if var_name in ir_scope.get_nonlocal_vars():
            return VariableKind.NONLOCAL
        if var_name in ir_scope.get_cell_vars():
            return VariableKind.CELL
        if var_name.startswith("$"):
            return VariableKind.TEMPORARY
        return VariableKind.LOCAL

    def _resolve_scopes(
        self,
        ir_scope: IRScope,
        context: Optional['AbstractContext'],
    ) -> List['Scope']:
        scopes = self._state.get_scopes_for_ir(ir_scope)
        if context is None:
            return scopes
        return [scope for scope in scopes if scope.context == context]

    def _require_scope(self, ir_scope: IRScope, context: 'AbstractContext') -> 'Scope':
        scopes = self._resolve_scopes(ir_scope, context)
        if not scopes:
            raise ValueError(f"No scope found for {ir_scope.get_qualname()} in context {context}")
        return scopes[0]

    def _resolve_var_to_ctx(
        self,
        var_name: str,
        ir_scope: IRScope,
        context: Optional['AbstractContext'],
    ) -> Union['Ctx[Variable]', List['Ctx[Variable]']]:
        var_kind = self._resolve_var_kind(var_name, ir_scope)
        var = Variable(name=var_name, kind=var_kind)
        if context is None:
            scopes = self._state.get_scopes_for_ir(ir_scope)
            return [self._state.get_variable(scope, scope.context, var) for scope in scopes]
        scope = self._require_scope(ir_scope, context)
        return self._state.get_variable(scope, context, var)

    def _field_points_to(self, obj: 'AbstractObject', field: Field) -> 'PointsToSet':
        scope = self._state.get_internal_scope(obj) or self._state.obj_scope.get(obj)
        if scope is None:
            return PointsToSet.empty()
        field_var = self._state.get_field(scope, scope.context, obj, field)
        return self._state.get_points_to(field_var)

    def _callsite_ctx(
        self,
        call_stmt: IRCall,
        scope: IRScope,
        context: 'AbstractContext',
    ) -> 'Ctx[CallSite]':
        internal_scope = self._require_scope(scope, context)
        call_site = CallSite(statement=call_stmt, scope_name=self._scope_label(scope))
        return Ctx(context, internal_scope, call_site)

    def _edges_for_call(
        self,
        call_stmt: IRCall,
        scope: IRScope,
        context: Optional['AbstractContext'],
    ) -> Set['CallEdge']:
        expected_scope = self._scope_label(scope)
        edges = set()
        for edge in self._state.call_graph.get_edges():
            callsite_ctx = edge.get_callsite()
            if callsite_ctx.content.statement != call_stmt:
                continue
            if callsite_ctx.content.scope_name != expected_scope:
                continue
            if callsite_ctx.scope.stmt != scope:
                continue
            if context is None or callsite_ctx.context == context:
                edges.add(edge)
        return edges

    def _iter_ctx_vars_by_name(self, var: 'Variable') -> Iterable['Ctx[Variable]']:
        for ctx_var in self._state._env:
            if not isinstance(ctx_var, Ctx):
                continue
            content = ctx_var.content
            if isinstance(content, Variable) and content.name == var.name and content.kind == var.kind:
                yield ctx_var

    def _get_function_objects(
        self,
        func: IRFunc,
        context: Optional['AbstractContext'],
    ) -> List['AbstractObject']:
        objs = []
        for obj in self._state._heap.objects.values():
            if getattr(obj.alloc_site, "stmt", None) != func:
                continue
            if not isinstance(obj, (FunctionObject, MethodObject)):
                continue
            if context is None or obj.context == context:
                objs.append(obj)
        return objs

    def points_to_at(
        self,
        var_name: str,
        scope: IRScope,
        context: Optional['AbstractContext'] = None,
    ) -> 'PointsToSet':
        if context is None:
            result = PointsToSet.empty()
            for ctx_var in self._resolve_var_to_ctx(var_name, scope, None):
                result = result.union(self._state.get_points_to(ctx_var))
            return result
        ctx_var = self._resolve_var_to_ctx(var_name, scope, context)
        return self._state.get_points_to(ctx_var)

    def points_to_of_target(
        self,
        stmt: Union[IRAssign, IRCopy, IRLoadAttr, IRLoadSubscr, IRCall],
        scope: IRScope,
        context: Optional['AbstractContext'] = None,
    ) -> 'PointsToSet':
        if isinstance(stmt, IRCall):
            target = stmt.get_target()
            if target is None:
                return PointsToSet.empty()
            return self.points_to_at(target, scope, context)
        lval = stmt.get_lval()
        return self.points_to_at(lval.id, scope, context)

    def load_attr_result(
        self,
        stmt: IRLoadAttr,
        scope: IRScope,
        context: Optional['AbstractContext'] = None,
    ) -> 'PointsToSet':
        return self.points_to_of_target(stmt, scope, context)

    def store_attr_value(
        self,
        stmt: IRStoreAttr,
        scope: IRScope,
        context: Optional['AbstractContext'] = None,
    ) -> 'PointsToSet':
        return self.points_to_at(stmt.get_rval().id, scope, context)

    def field_of_objects(
        self,
        base_pts: 'PointsToSet',
        attr_name: str,
    ) -> 'PointsToSet':
        result = PointsToSet.empty()
        field = attr(attr_name)
        for obj in base_pts:
            result = result.union(self._field_points_to(obj, field))
        return result

    def subscript_of_objects(
        self,
        base_pts: 'PointsToSet',
        index: Optional[Union[int, str]] = None,
    ) -> 'PointsToSet':
        result = PointsToSet.empty()
        for obj in base_pts:
            if index is None:
                field = elem()
            else:
                field = key(index)
            result = result.union(self._field_points_to(obj, field))
        return result

    def call_graph(self) -> 'AbstractCallGraph':
        return self._state.call_graph

    def callees_of(
        self,
        call_stmt: IRCall,
        scope: IRScope,
        context: Optional['AbstractContext'] = None,
    ) -> Set['AbstractObject']:
        callees = set()
        for edge in self._edges_for_call(call_stmt, scope, context):
            callee_scope = edge.get_callee()
            if callee_scope is not None:
                callees.add(callee_scope.obj)
        return callees

    def callees_as_ir(
        self,
        call_stmt: IRCall,
        scope: IRScope,
        context: Optional['AbstractContext'] = None,
    ) -> Set[Union[IRFunc, IRClass]]:
        result: Set[Union[IRFunc, IRClass]] = set()
        for obj in self.callees_of(call_stmt, scope, context):
            if isinstance(obj, (FunctionObject, MethodObject)):
                result.add(obj.ir)
            elif isinstance(obj, ClassObject):
                result.add(obj.ir)
        return result

    def callers_of(
        self,
        callee: Union[IRFunc, IRClass],
    ) -> Set[Tuple[IRCall, IRScope]]:
        callers: Set[Tuple[IRCall, IRScope]] = set()
        for edge in self._state.call_graph.get_edges():
            callee_scope = edge.get_callee()
            if callee_scope.stmt == callee:
                callsite_ctx = edge.get_callsite()
                callers.add((callsite_ctx.content.statement, callsite_ctx.scope.stmt))
        return callers

    def receiver_objects(
        self,
        call_stmt: IRCall,
        scope: IRScope,
        context: Optional['AbstractContext'] = None,
    ) -> 'PointsToSet':
        ir = self._state.scope_manager.get_ir(scope, "ir")
        if not ir:
            return PointsToSet.empty()
        callee_name = call_stmt.get_func_name()
        receiver_name = None
        try:
            idx = ir.index(call_stmt)
            candidates = reversed(ir[:idx])
        except ValueError:
            candidates = reversed(ir)
        for stmt in candidates:
            if isinstance(stmt, IRLoadAttr) and stmt.get_lval().id == callee_name:
                receiver_name = stmt.get_obj().id
                break
        if receiver_name is None:
            return PointsToSet.empty()
        return self.points_to_at(receiver_name, scope, context)

    def may_alias(
        self,
        var1: str,
        var2: str,
        scope: IRScope,
        context: Optional['AbstractContext'] = None,
    ) -> bool:
        pts1 = self.points_to_at(var1, scope, context)
        pts2 = self.points_to_at(var2, scope, context)
        return not pts1.intersection(pts2).is_empty()

    def may_alias_field(
        self,
        var1: str,
        attr1: str,
        var2: str,
        attr2: Optional[str],
        scope: IRScope,
        context: Optional['AbstractContext'] = None,
    ) -> bool:
        pts1 = self.field_of_objects(self.points_to_at(var1, scope, context), attr1)
        if attr2 is None:
            pts2 = self.points_to_at(var2, scope, context)
        else:
            pts2 = self.field_of_objects(self.points_to_at(var2, scope, context), attr2)
        return not pts1.intersection(pts2).is_empty()

    def get_class_mro(
        self,
        cls_obj: 'AbstractObject',
    ) -> List['AbstractObject']:
        if self._class_hierarchy is None:
            return [cls_obj]
        return self._class_hierarchy.get_mro(cls_obj)

    def resolve_method(
        self,
        receiver_pts: 'PointsToSet',
        method_name: str,
    ) -> 'PointsToSet':
        result = PointsToSet.empty()
        field = attr(method_name)
        for obj in receiver_pts:
            result = result.union(self._field_points_to(obj, field))
        return result

    def get_instance_class(
        self,
        instance_obj: 'AbstractObject',
    ) -> Optional['AbstractObject']:
        if isinstance(instance_obj, InstanceObject):
            return instance_obj.class_obj
        if isinstance(instance_obj, BuiltinInstanceObject):
            return ObjectFactory.create_builtin_class(instance_obj.builtin_type, instance_obj.context)
        return None

    def get_bound_receiver(
        self,
        method_obj: 'AbstractObject',
    ) -> Optional['AbstractObject']:
        if isinstance(method_obj, MethodObject):
            return method_obj.instance_obj
        if isinstance(method_obj, BuiltinMethodObject):
            return method_obj.receiver
        return None

    def get_captured_vars(
        self,
        func: IRFunc,
        context: Optional['AbstractContext'] = None,
    ) -> Dict[str, 'PointsToSet']:
        result: Dict[str, PointsToSet] = {}
        for func_obj in self._get_function_objects(func, context):
            cell_vars = self._state.get_cell_vars(func_obj)
            nonlocal_vars = self._state.get_nonlocal_vars(func_obj)
            for var_name, ctx_var in {**cell_vars, **nonlocal_vars}.items():
                pts = self._state.get_points_to(ctx_var)
                if var_name in result:
                    result[var_name] = result[var_name].union(pts)
                else:
                    result[var_name] = pts
        return result

    def may_have_attr(
        self,
        obj: 'AbstractObject',
        attr_name: str,
    ) -> bool:
        scope = self._state.get_internal_scope(obj) or self._state.obj_scope.get(obj)
        if scope is None:
            return True
        field_access = self._state.has_field(scope, scope.context, obj, attr(attr_name))
        if field_access is None:
            return True
        return not self._state.get_points_to(field_access).is_empty()

    def list_elements(
        self,
        list_obj: 'AbstractObject',
    ) -> 'PointsToSet':
        return self._field_points_to(list_obj, elem())

    def dict_keys(
        self,
        dict_obj: 'AbstractObject',
    ) -> 'PointsToSet':
        keys = []
        for (obj, field), _ in self._state._field_accesses.items():
            if obj != dict_obj:
                continue
            if field.kind != FieldKind.KEY or field.name is None:
                continue
            for heap_obj in self._state._heap.objects.values():
                if isinstance(heap_obj, ConstantObject) and str(heap_obj.value) == field.name:
                    keys.append(heap_obj)
        return PointsToSet.from_objects(keys)

    def dict_values(
        self,
        dict_obj: 'AbstractObject',
    ) -> 'PointsToSet':
        return self._field_points_to(dict_obj, value()).union(self._field_points_to(dict_obj, elem()))

    def dict_get(
        self,
        dict_obj: 'AbstractObject',
        key_name: str,
    ) -> 'PointsToSet':
        return self._field_points_to(dict_obj, key(key_name))

    def tuple_element(
        self,
        tuple_obj: 'AbstractObject',
        index: int,
    ) -> 'PointsToSet':
        return self._field_points_to(tuple_obj, key(index))

    def set_elements(
        self,
        set_obj: 'AbstractObject',
    ) -> 'PointsToSet':
        return self._field_points_to(set_obj, elem())

    def get_object_type(
        self,
        obj: 'AbstractObject',
    ) -> Optional['AbstractObject']:
        if isinstance(obj, InstanceObject):
            return obj.class_obj
        if isinstance(obj, BuiltinInstanceObject):
            return ObjectFactory.create_builtin_class(obj.builtin_type, obj.context)
        if isinstance(obj, (FunctionObject, MethodObject, BuiltinFunctionObject, BuiltinMethodObject)):
            return ObjectFactory.create_builtin_class("function", obj.context)
        if isinstance(obj, ClassObject):
            return ObjectFactory.create_builtin_class("type", obj.context)
        if isinstance(obj, BuiltinClassObject):
            return ObjectFactory.create_builtin_class("type", obj.context)
        return None

    def is_instance_of(
        self,
        obj: 'AbstractObject',
        cls: 'AbstractObject',
    ) -> bool:
        obj_type = self.get_object_type(obj)
        if obj_type is None:
            return False
        if obj_type == cls:
            return True
        if isinstance(obj_type, BuiltinClassObject) and isinstance(cls, BuiltinClassObject):
            return obj_type.builtin_name == cls.builtin_name
        if isinstance(obj_type, ClassObject) and isinstance(cls, ClassObject) and self._class_hierarchy:
            return cls in self._class_hierarchy.get_mro(obj_type)
        return False

    def objects_of_type(
        self,
        cls: 'AbstractObject',
    ) -> 'PointsToSet':
        objs = [obj for obj in self.get_all_objects() if self.is_instance_of(obj, cls)]
        return PointsToSet.from_objects(objs)

    def get_reachable_scopes(self) -> Set[IRScope]:
        scopes = {scope.stmt for scope in self._state.call_graph.get_nodes()}
        scopes.update(scope.stmt for scope in self._state._internal_scope.values())
        return scopes

    def get_reachable_functions(self) -> Set[IRFunc]:
        return {scope for scope in self.get_reachable_scopes() if isinstance(scope, IRFunc)}

    def get_reachable_classes(self) -> Set[IRClass]:
        return {scope for scope in self.get_reachable_scopes() if isinstance(scope, IRClass)}

    def is_reachable(
        self,
        scope: IRScope,
    ) -> bool:
        return scope in self.get_reachable_scopes()

    def get_statistics(self) -> Dict[str, Any]:
        state_stats = self._state.get_statistics()
        unknown_stats = self._unknown_tracker.get_summary()
        return {**state_stats, **self._stats, **unknown_stats}

    def get_all_objects(self) -> Iterable['AbstractObject']:
        return self._state._heap.objects.values()

    def get_unknown_summary(self) -> Dict[str, int]:
        return self._unknown_tracker.get_summary()

    def get_unknown_details(self) -> List[Dict[str, Any]]:
        return self._unknown_tracker.get_detailed_report()

    def points_to(self, var: 'Variable') -> 'PointsToSet':
        warnings.warn(
            "points_to(Variable) is deprecated. Use points_to_at(var_name, scope, context).",
            DeprecationWarning,
            stacklevel=2,
        )
        result = PointsToSet.empty()
        for ctx_var in self._iter_ctx_vars_by_name(var):
            result = result.union(self._state.get_points_to(ctx_var))
        return result

    def get_field(self, obj: 'AbstractObject', field: 'Field') -> 'PointsToSet':
        warnings.warn(
            "get_field(AbstractObject, Field) is deprecated. Use field_of_objects() or container-specific methods.",
            DeprecationWarning,
            stacklevel=2,
        )
        return self._field_points_to(obj, field)
