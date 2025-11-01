"""Constraint-based pointer analysis solver.

This module implements the core solver for pointer analysis using
constraint-based propagation.
"""

import logging
from typing import Set, Dict, Any, TYPE_CHECKING, Optional, List, Tuple

from .state import PointerAnalysisState, PointsToSet
from .constraints import (Constraint, CopyConstraint, LoadConstraint, StoreConstraint,
                          AllocConstraint, CallConstraint, ReturnConstraint)
from .variable import Variable, Scope, VariableKind, VariableFactory, FieldAccess
from .config import Config
from .heap_model import Field, attr
from pythonstan.graph.call_graph import AbstractCallGraph
from .ir_translator import IRTranslator
from .context_selector import ContextSelector, CallSite, AbstractContext
from .class_hierarchy import ClassHierarchyManager
from .builtin_api_handler import BuiltinSummaryManager
from .unknown_tracker import UnknownTracker, UnknownKind
from .object import AllocKind, AllocSite, AbstractObject
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
        self._worklist: Set[Tuple[Variable, PointsToSet]] = set()
        self._iteration = 0
        self._stats: Dict[str, int] = {
            "iterations": 0,
            "constraints_applied": 0
        }
        self._unknown_tracker = UnknownTracker()
    
    def add_constraint(self, constraint: 'Constraint') -> None:
        self.state.constraints.add(constraint)
    
    def solve_to_fixpoint(self) -> None:
        self.process_static_constraints()
        
        logger.info("Starting constraint solving")
        
        max_iter = self.config.max_iterations
        
        while self._worklist and self._iteration < max_iter:
            self._iteration += 1
            # Log progress periodically
            if self._iteration % 1000 == 0:
                logger.debug(f"Iteration {self._iteration}, worklist size {len(self._worklist)}")
            
            var, pts = self._worklist.pop()
            
            logger.info(f"Processing variable {var} with points-to set {pts}")

            diff = pts - self.state.get_points_to(var)
            if not diff.is_empty():
                self.state.set_points_to(var, diff)
                for target in self.state.pointer_flow_graph.get_succs(var):
                    self._worklist.add((target, diff))
                    
                constraints_to_process = list(self.state.constraints.get_by_variable(var))
                for constraint in constraints_to_process:
                    logger.info(f"Processing constraint {constraint}")
                    self._apply_constraint(constraint, diff)
        
        if self._iteration >= max_iter:
            logger.warning(f"Reached max iterations {max_iter}")
        
        self._stats["iterations"] = self._iteration
        logger.info(f"Converged after {self._iteration} iterations")
    
    def process_static_constraints(self):
        for constraint in self.state.constraints.get_by_type(CopyConstraint):
            self._apply_copy(constraint)
        for constraint in self.state.constraints.get_by_type(AllocConstraint):
            self._apply_alloc(constraint)
        for constraint in self.state.constraints.get_by_type(ReturnConstraint):
            self._apply_return(constraint)
    
    def _apply_constraint(self, constraint: 'Constraint', diff: 'PointsToSet') -> bool:
        if isinstance(constraint, LoadConstraint):
            return self._apply_load(constraint, diff)
        elif isinstance(constraint, StoreConstraint):
            return self._apply_store(constraint, diff)
        elif isinstance(constraint, CallConstraint):
            return self._apply_call(constraint, diff)
        elif isinstance(constraint, (CopyConstraint, AllocConstraint, ReturnConstraint)):
            # These constraints are static, so we don't need to apply them
            return False
        else:
            logger.warning(f"Unknown constraint type: {type(constraint)}")
            return False
    
    def _apply_copy(self, c: 'CopyConstraint'):
        """Apply copy constraint: target = source."""
        self.state.pointer_flow_graph.add_edge(c.source, c.target)    
        
    def _apply_alloc(self, c: 'AllocConstraint'):
        """Apply allocation constraint: target = new Object."""
        obj = AbstractObject(alloc_site=c.alloc_site, context=c.target.context)
        pts = PointsToSet.singleton(obj)
        self._worklist.add((c.target, pts))    
        
    def _apply_return(self, c: 'ReturnConstraint'):
        """Apply return constraint: caller_target = callee_return."""
        self.state.pointer_flow_graph.add_edge(c.callee_return, c.caller_target)
    
    def _apply_load(self, c: 'LoadConstraint', pts: 'PointsToSet'):
        """Apply load constraint: target = base.field."""
        
        # TODO For a setattr, set all object in the same module with the same type with that value
        for base_obj in pts:
            field_access = self.state.get_field(base_obj, c.field)
            self.state.pointer_flow_graph.add_edge(field_access, c.target)
        '''
        ** For future reference:
        
        for base_obj in pts:
            # Check if we're loading function objects - create bound methods
            for field_obj in field_pts:
                if field_obj.kind == AllocKind.FUNCTON:
                    bm_alloc = AllocSite(
                        file=c.target.scope.name,
                        line=0,
                        col=0,
                        kind=AllocKind.BOUND_METHOD,
                        name=f"{base_obj.alloc_site.name if base_obj.alloc_site.name else 'obj'}.{c.field}"
                    )
                    bm_obj = AbstractObject(alloc_site=bm_alloc, context=c.target.context)
                    
                    self.state.set_field(bm_obj, attr("__self__"), PointsToSet.singleton(base_obj))
                    self.state.set_field(bm_obj, attr("__func__"), PointsToSet.singleton(field_obj))
                    
                    changed = self.state.set_points_to(c.target, PointsToSet.singleton(bm_obj))
                else:
                    changed = self.state.set_points_to(c.target, PointsToSet.singleton(field_obj))
        '''
    
    def _apply_store(self, c: 'StoreConstraint', pts: 'PointsToSet'):
        """Apply store constraint: base.field = source."""
        for base_obj in pts:
            field_access = self.state.get_field(base_obj, c.field)
            self.state.pointer_flow_graph.add_edge(field_access, c.source)
    
    def _apply_call(self, c: 'CallConstraint', pts: 'PointsToSet') -> bool:
        """Apply call constraint: target = callee(args...)."""        
        '''
        Unknown tracking is not implemented yet.
        
        if callee_pts.is_empty():
            self._unknown_tracker.record(
                UnknownKind.CALLEE_EMPTY,
                c.call_site,
                f"Call with empty callee points-to set: {c.callee}"
            )
            
            if self.config.verbose:
                logger.warning(f"[UNKNOWN] Empty callee at {c.call_site}: {c.callee}")
                
            if c.target:
                unknown_alloc = AllocSite(
                    file="<unknown>",
                    line=0,
                    col=0,
                    kind=AllocKind.UNKNOWN,
                    name=f"unknown_call_{c.call_site}"
                )
                unknown_obj = AbstractObject(unknown_alloc, c.target.context)
                self.state.set_points_to(c.target, PointsToSet.singleton(unknown_obj))
                return True
            
            return False
        '''
        
        changed = False
        for callee_obj in pts:
            if callee_obj.kind == AllocKind.FUNCTION:
                changed = self._handle_function_call(c, callee_obj)                    
            elif callee_obj.kind == AllocKind.CLASS:
                changed = self._handle_class_instantiation(c, callee_obj)                    
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
                        file="<unknown>",
                        line=0,
                        col=0,
                        kind=AllocKind.UNKNOWN,
                        name=f"unknown_noncallable_{c.call_site}"
                    )
                    unknown_obj = AbstractObject(unknown_alloc, c.target.context)
                    self.state.set_points_to(c.target, PointsToSet.singleton(unknown_obj))
                    changed = True
        
        return changed
    
    def _handle_function_call(self, call: 'CallConstraint', func_obj: 'AbstractObject') -> bool:
        """Handle function call: analyze function body with parameter bindings.
            1. Selects calling context
            2. Translates function body to constraints
            3. Generates parameter passing constraints
            4. Connects return value to caller
            5. Adds call edge to call graph
        """
        
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
        
        func_name = func_obj.alloc_site.name
        if not func_name or func_name not in self.function_registry:
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
                    name=f"unknown_func_{func_name or 'unnamed'}"
                )
                unknown_obj = AbstractObject(unknown_alloc, call.target.context)
                self.state.set_points_to(call.target, PointsToSet.singleton(unknown_obj))
                return True
            return False
        
        call_site_obj = CallSite(call.call_site, len(call.args))
        call_context = self.context_selector.select_call_context(
            call_site_obj,
            call.callee.context,
            None  # No receiver for regular functions
        )
        
        func_ir = self.function_registry[func_name]
        
        callee_scope = Scope(name=func_name, stmt=func_ir, context=call_context, kind="function")
        old_scope = self.ir_translator._current_scope
        old_context = self.ir_translator._current_context
        
        self.ir_translator._current_scope = callee_scope
        self.ir_translator._current_context = call_context
        
        
        
        # TODO dynamic translating functions cost a lot.
        try:
            body_constraints = self.ir_translator.translate_function(func_ir, call_context)
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
            self.ir_translator._current_context = old_context
        
        changed = False
        for constraint in body_constraints:
            self.add_constraint(constraint)
            changed = True
        
        if hasattr(func_ir, 'args') and hasattr(func_ir.args, 'args'):
            param_names = [arg.arg for arg in func_ir.args.args]
            for i, param_name in enumerate(param_names):
                if i < len(call.args):
                    param_var = self._make_variable(param_name, callee_scope, call_context, VariableKind.PARAMETER)
                    self.state.pointer_flow_graph.add_edge(call.args[i], param_var)
        
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
        
        if hasattr(self.state, '_call_edges'):
            from pythonstan.graph.call_graph import CallEdge, CallKind
            edge = CallEdge(
                kind=CallKind.FUNCTION,
                callsite=call.call_site,
                callee=func_name
            )
            self.state._call_edges.append(edge)
        
        return changed
    
    def _handle_class_instantiation(self, call: 'CallConstraint', class_obj: 'AbstractObject') -> bool:
        """Handle class instantiation: create instance + call __init__."""
        if not call.target:
            return False
        
        instance_alloc = AllocSite(
            file=class_obj.alloc_site.file,
            line=class_obj.alloc_site.line,
            col=class_obj.alloc_site.col,
            kind=AllocKind.OBJECT,
            name=class_obj.alloc_site.name
        )
        
        if self.context_selector:
            call_site = CallSite(call.call_site, len(call.args))
            alloc_context = self.context_selector.select_alloc_context(
                call_site,
                call.target.context,
                class_obj
            )
        else:
            alloc_context = call.target.context
        
        instance_obj = AbstractObject(instance_alloc, alloc_context)
        
        changed = self.state.set_points_to(call.target, PointsToSet.singleton(instance_obj))
        
        if self.class_hierarchy and self.class_hierarchy.has_class(class_obj):
            init_pts = self.state.get_field(class_obj, attr("__init__"))
            
            if not init_pts.is_empty():
                instance_var = Variable(
                    name="$instance",
                    scope=call.target.scope,
                    context=call.target.context,
                    kind=VariableKind.TEMPORARY
                )
                
                self.state.set_points_to(instance_var, PointsToSet.singleton(instance_obj))

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
    
    def _make_variable(self, name: str, scope: Scope, context: 'AbstractContext', kind: VariableKind = VariableKind.LOCAL) -> 'Variable':
        return self.variable_factory.make_variable(name, scope, context, kind)
    
    def _make_field_access(self, obj: 'AbstractObject', field: 'Field') -> 'FieldAccess':
        return self.variable_factory.make_field_access(obj, field)
    
    def _get_field_access(self, obj: 'AbstractObject', field: 'Field') -> 'FieldAccess':
        field_access = self.state.get_field(obj, field)
        if field_access is None:
            field_access = self._make_field_access(obj, field)
            self.state.set_field(obj, field, field_access)
        return field_access
    
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
