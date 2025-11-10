"""Constraint-based pointer analysis solver.

This module implements the core solver for pointer analysis using
constraint-based propagation.
"""

import logging
from pyclbr import Function
from typing import Set, Dict, Any, TYPE_CHECKING, Optional, List, Tuple

from pythonstan.ir.ir_statements import IRFunc

from .state import PointerAnalysisState, PointsToSet
from .constraints import (Constraint, CopyConstraint, LoadConstraint, StoreConstraint,
                          AllocConstraint, CallConstraint, ReturnConstraint)
from .variable import Variable, VariableKind, VariableFactory, FieldAccess
from .config import Config
from .heap_model import Field, attr
from pythonstan.graph.call_graph import AbstractCallGraph
from .ir_translator import IRTranslator
from .context_selector import ContextSelector, CallSite, AbstractContext
from .context import Ctx, Scope
from .class_hierarchy import ClassHierarchyManager
from .builtin_api_handler import BuiltinSummaryManager
from .unknown_tracker import UnknownTracker, UnknownKind
from .object import AllocKind, AllocSite, AbstractObject, FunctionObject, ClassObject, ModuleObject, MethodObject
from .solver_interface import ISolverQuery

__all__ = ["PointerSolver", "SolverQuery"]

logger = logging.getLogger(__name__)


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
        self.function_registry = function_registry or {}
        self.class_hierarchy = class_hierarchy
        self.builtin_manager = builtin_manager
        self.variable_factory = variable_factory or VariableFactory()
        self._worklist: Set[Tuple['Scope', Ctx[Variable], PointsToSet]] = set()
        self._static_constraints: Set[Tuple['Scope', 'AbstractContext', 'Constraint']] = set()
        self._iteration = 0
        self._stats: Dict[str, int] = {
            "iterations": 0,
            "constraints_applied": 0
        }
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
    
    def solve_to_fixpoint(self) -> None:
        # self.process_static_constraints()
        
        logger.info("Starting constraint solving")
        max_iter = 1000000 # self.config.max_iterations
        
        while (self._worklist or self._static_constraints) and self._iteration < max_iter:
            self._iteration += 1
            # Log progress periodically
            if self._iteration % 1000 == 0:
                logger.debug(f"Iteration {self._iteration}, worklist size {len(self._worklist)}")
            
            if self._static_constraints:
                scope, ctx, constraint = self._static_constraints.pop()
                self._apply_static(scope, ctx, constraint)
            else:
                scope, var, pts = self._worklist.pop()
                diff = pts - self.state.get_points_to(var)

                if not diff.is_empty():
                    self.state.set_points_to(var, diff)

                    constraints_to_process = list(self.state.constraints.get_by_variable(var))
                    for constraint in constraints_to_process:
                        self._apply_constraint(scope, var, constraint, diff)
                        
                    for target_var in self.state.pointer_flow_graph.get_succs(var):
                        self._worklist.add((target_var.scope, target_var, diff))
        
        if self._iteration >= max_iter:
            logger.warning(f"Reached max iterations {max_iter}")
        
        logger.info(f"Call graph: {self.state._call_graph} node: {len(self.state._call_graph.get_nodes())} edge: {self.state._call_graph.get_number_of_edges()}")
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
        elif isinstance(constraint, CopyConstraint):
            return self._apply_copy(scope, variable, constraint, diff)
        elif isinstance(constraint, ReturnConstraint):
            return self._apply_return(scope, variable, constraint, diff)
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

        if self.state._heap.get_obj(scope, context, c.alloc_site) is not None:
            return

        if c.alloc_site.kind == AllocKind.FUNCTION:
            obj = self._alloc_function(scope, context, c)
            
        elif c.alloc_site.kind == AllocKind.CLASS:
            # complex class translation logic, for processing base classes
            obj = AbstractObject(alloc_site=c.alloc_site, context=context)
        
        elif c.alloc_site.kind == AllocKind.MODULE:
            obj = self._alloc_module(scope, context, c)
        
        elif c.alloc_site.kind == AllocKind.OBJECT:
            # logic for instance allocation is located in _apply_call
            obj = AbstractObject(alloc_site=c.alloc_site, context=context)
            
        else:
            obj = AbstractObject(alloc_site=c.alloc_site, context=context)

        self.state._heap.set_obj(scope, context, c.alloc_site, obj)
        pts = PointsToSet.singleton(obj)
        target = self.state.get_variable(scope, context, c.target)
        self._worklist.add((scope, target, pts))

    def _alloc_function(self, scope: 'Scope', context: 'AbstractContext', c: 'AllocConstraint') -> 'FunctionObject':
        ir_func = c.alloc_site.stmt
        logger.info(f"alloc function {c}")
        assert isinstance(ir_func, IRFunc), f"AllocSite to be allocated as function {c.alloc_site} should be IRFunc, {type(ir_func)} got!"
        
        # process cell vars into the closure
        cell_vars = {}
        for var_name in ir_func.get_cell_vars():
            var = self.variable_factory.make_variable(var_name, VariableKind.CELL)
            cell_vars[var_name] = self.state.get_variable(scope, context, var)
        
        # collect global vars into the closure
        global_vars = {}
        for var_name in ir_func.get_global_vars():
            var = self.variable_factory.make_variable(var_name, VariableKind.GLOBAL)
            global_vars[var_name] = self.state.get_variable(scope, context, var)
            
        # collect nonlocal vars into the closure
        nonlocal_vars = {}
        for var_name in ir_func.get_nonlocal_vars():
            var = self.variable_factory.make_variable(var_name, VariableKind.NONLOCAL)
            nonlocal_vars[var_name] = self.state.get_variable(scope, context, var)
            
        obj = FunctionObject(c.alloc_site, context, scope, c.alloc_site.stmt, cell_vars, global_vars, nonlocal_vars)
        return obj

    def _alloc_module(self, scope: 'Scope', context: 'AbstractContext', c: 'AllocConstraint') -> 'ModuleObject':
        assert c.alloc_site.kind == AllocKind.MODULE, "AllocSite kind in module allocation should be Module"
        assert c.alloc_site.stmt is not None, "AllocSite should have IRImport as stmt"

        logger.info(f"allocating module [{scope.module}] {c.alloc_site.stmt}")
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

        module_obj = ModuleObject(c.alloc_site, context)
        logger.info(f"create obj succeed {module_obj}")

        # resolve the content of module
        stmt = c.alloc_site.stmt

        # TODO add more context policies in new module allocation
        module_ctx = context.append(CallSite(
            site_id=f"{module_ir.get_qualname()}:{stmt.get_ast().lineno}:{stmt.get_ast().col_offset}:import",
            fn=module_ir.get_qualname()))
        
        # module_ctx = context
        ctx_scope = Scope.new(module_obj, None, module_ctx, module_ir, None)

        # translate the IRs in the imported module        
        for constraint in self.ir_translator.translate_module(module_ir, c.alloc_site.stmt):
            self.add_constraint(ctx_scope, module_ctx, constraint)
        
        logger.info(f"create obj succeed {module_obj}")
        
        return module_obj

    def _apply_return(self, c: 'ReturnConstraint'):
        """Apply return constraint: caller_target = callee_return."""
        # TODO should be resolved in the call handling

        self.state.pointer_flow_graph.add_edge(c.callee_return, c.caller_target)
    
    def _apply_load(self, scope: 'Scope', context: 'AbstractContext', c: 'LoadConstraint', pts: 'PointsToSet'):
        """Apply load constraint: target = base.field."""

        for base_obj in pts:
            field_access = self.state.get_field(scope, context, base_obj, c.field)
            target_var = self.state.get_variable(scope, context, c.target)
            self.state.pointer_flow_graph.add_edge(field_access, target_var)
    
    def _apply_store(self, scope: 'Scope', context: 'AbstractContext', c: 'StoreConstraint', pts: 'PointsToSet'):
        """Apply store constraint: base.field = source."""

        for base_obj in pts:
            field_access = self.state.get_field(scope, context, base_obj, c.field)
            source_var = self.state.get_variable(scope, context, c.source)
            self.state.pointer_flow_graph.add_edge(source_var, field_access)
    
    def _apply_call(self, scope: 'Scope', context: 'AbstractContext', c: 'CallConstraint', pts: 'PointsToSet') -> bool:
        """Apply call constraint: target = callee(args...)."""        

        logger.debug(f"Applying call constraint: {c.call_site} -> {pts}")
         
        changed = False
        for callee_obj in pts:
            if isinstance(callee_obj, FunctionObject):
                changed = self._handle_function_call(scope, context, c, callee_obj)
            elif callee_obj.kind == AllocKind.CLASS:
                changed = self._handle_class_instantiation(scope, context, c, callee_obj)
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
        
        return changed
    
    def _handle_function_call(self, scope: 'Scope', context: 'AbstractContext', call: 'CallConstraint', func_obj: 'AbstractObject') -> bool:
        """Handle function call: analyze function body with parameter bindings.
            1. Selects calling context
            2. Translates function body to constraints
            3. Generates parameter passing constraints
            4. Connects return value to caller
            5. Adds call edge to call graph
        """
        # logger.info(f"Handling function call: {call.call_site} -> {func_obj.alloc_site.name}")
        
        if not self.ir_translator or not self.context_selector: 
            self._unknown_tracker.record(
                UnknownKind.MISSING_DEPENDENCIES,
                call.call_site,
                "IR translator or context selector not available"
            )
            logger.debug("[UNKNOWN] Missing dependencies for function call")

            if call.target:
                unknown_alloc = AllocSite(
                    file="<unknown>",
                    line=0,
                    col=0,
                    kind=AllocKind.UNKNOWN,
                    name=f"unknown_missing_deps_{call.call_site}"
                )
                unknown_obj = AbstractObject(unknown_alloc, call.target.context)
                self.state.set_points_to(call.target, PointsToSet.singleton(unknown_obj))
                return True
            return False
        logger.debug(f"Handling function call: {call.call_site} -> {func_obj.alloc_site.name}")

        func_name = func_obj.alloc_site.name
        func_ir: IRFunc = func_obj.alloc_site.stmt        
        if False: # and not func_name or func_name not in self.function_registry:
            from .unknown_tracker import UnknownKind
            from .object import AllocSite, AllocKind, AbstractObject
            from .state import PointsToSet
            
            self._unknown_tracker.record(
                UnknownKind.FUNCTION_NOT_IN_REGISTRY,
                call.call_site,
                f"Function '{func_name}' not found in registry"
            )
            
            if self.config.verbose:
                logger.warning(f"[UNKNOWN] Function not in registry: {func_name}")
            
            if call.target:
                unknown_alloc = AllocSite(
                    file="<unknown>",
                    line=0,
                    col=0,
                    kind=AllocKind.UNKNOWN,
                    scope=call.target.scope,
                    name=f"unknown_func_{func_name or 'unnamed'}"
                )
                unknown_obj = AbstractObject(unknown_alloc, call.target.context)
                self.state.set_points_to(call.target, PointsToSet.singleton(unknown_obj))
                return True
            return False
        
        call_site_obj = CallSite(call.call_site, len(call.args))
        call_context = self.context_selector.select_call_context(
            call_site_obj,
            func_obj.context,
            None,  # No receiver ffor regular functions
            params=call.args
        )
        
        logger.debug(f"Handling function call: {call.call_site} -> {func_obj.alloc_site.name}")
        
        # func_ir = self.function_registry[func_name]

        alloc_site = func_obj.alloc_site
        # assert isinstance(alloc_site.scope, Scope), f"{alloc_site.name} should be scope, {alloc_site.scope} got"
        callee_scope = Scope.new(func_obj, scope.module, call_context, func_ir, scope)
        old_scope = self.ir_translator._current_scope
        # old_context = self.ir_translator._current_context        
        self.ir_translator._current_scope = alloc_site.stmt
        # self.ir_translator._current_context = call_context
        
        # TODO dynamic translating functions cost a lot. [doing, by remove context from context translation]
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
                if i < len(call.args):
                    param = self.variable_factory.make_variable(param_name, VariableKind.LOCAL)
                    param_var = self.state.get_variable(callee_scope, call_context, param)
                    # param_var = self._make_variable(param_name, callee_scope, call_context, VariableKind.PARAMETER)
                    arg = self.variable_factory.make_variable(call.args[i], VariableKind.LOCAL)
                    arg_var = self.state.get_variable(scope, context, arg)
                    self.state.pointer_flow_graph.add_edge(arg_var, param_var)
        '''
        # Handle closure variable restoration
        if hasattr(func_ir, 'cell_vars') and func_ir.cell_vars:
            for freevar_name in func_ir.cell_vars:
                try:
                    # Load cell from function closure
                    cell_var = self._make_variable(
                        name=f"$cell_{freevar_name}",
                        scope=callee_scope,
                        context=call_context,
                        kind=VariableKind.TEMPORARY
                    )
                    
                    func_var = call.callee
                    load_cell_constraint = LoadConstraint(
                        base=func_var,
                        field=attr(f"__closure__{freevar_name}"),
                        target=cell_var
                    )
                    self.add_constraint(load_cell_constraint)
                    inner_var = self._make_variable(
                        name=freevar_name,
                        scope=callee_scope,
                        context=call_context,
                        kind=VariableKind.LOCAL
                    )
                    load_value_constraint = LoadConstraint(
                        base=cell_var,
                        field=attr("contents"),
                        target=inner_var
                    )
                    self.add_constraint(load_value_constraint)
                    changed = True
                except Exception as e:
                    logger.debug(f"Error restoring closure variable {freevar_name}: {e}")
        
        if call.target:
            return_var = Variable(
                name="$return",
                scope=callee_scope,
                context=call_context,
                kind=VariableKind.TEMPORARY
            )
            self.state.pointer_flow_graph.add_edge(return_var, call.target)
        '''
        from pythonstan.graph.call_graph import CallEdge, CallKind
        edge = CallEdge(kind=CallKind.FUNCTION, callsite=call.call_site, callee=func_name)
        self.state.call_graph.add_edge(edge)
        logger.debug(f"Adding call edge: {edge}")

        return changed
    
    def _handle_class_instantiation(self, scope: 'Scope', context: 'AbstractContext', call: 'CallConstraint', class_obj: 'AbstractObject') -> bool:
        """Handle class instantiation: create instance + call __init__."""
        if not call.target:
            return False
        
        return False
        
        instance_alloc = AllocSite(
            file=class_obj.alloc_site.file,
            line=class_obj.alloc_site.line,
            col=class_obj.alloc_site.col,
            kind=AllocKind.OBJECT,
            scope=class_obj.alloc_site.scope,
            name=class_obj.alloc_site.name,
            stmt=None
        )
        
        if self.context_selector:
            call_site = CallSite(call.call_site, len(call.args))
            alloc_context = self.context_selector.select_alloc_context(
                call_site,
                context,
                class_obj
            )
        else:
            alloc_context = context
        
        instance_obj = AbstractObject(instance_alloc, alloc_context)
        
        changed = self.state.set_points_to(call.target, PointsToSet.singleton(instance_obj))
        
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

        builtin_name = builtin_obj.alloc_site.name
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
        return bool(pts1.objects & pts2.objects)
    
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
