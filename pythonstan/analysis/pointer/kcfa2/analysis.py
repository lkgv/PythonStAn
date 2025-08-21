"""Main k-CFA pointer analysis implementation.

This module implements the KCFA2PointerAnalysis class, which is the main
entry point for running k-CFA pointer analysis with 2-object sensitivity.
"""

from typing import Any, Dict, List, Optional, Set, Union, Tuple
from .config import KCFAConfig
from .context import Context, ContextManager, CallSite, ContextSelector
from .model import AbstractLocation, AbstractObject, PointsToSet, Env, Store, Heap, FieldKey
from .worklist import Worklist, ConstraintWorklist, CallWorklist
from .errors import AnalysisTimeout, ConfigurationError
from .heap_model import make_object, attr_key, elem_key, value_key, unknown_attr_key
from .callgraph_adapter import CallGraphAdapter
from .ir_adapter import Event, iter_function_events, site_id_of
from .summaries import BuiltinSummaryManager

__all__ = ["KCFA2PointerAnalysis"]


class KCFA2PointerAnalysis:
    """k-CFA pointer analysis with 2-object sensitivity.
    
    This class implements a context-sensitive pointer analysis that tracks
    object allocations through calling contexts (k-CFA) and receiver object
    contexts (2-object sensitivity).
    """
    
    def __init__(self, config: Optional[KCFAConfig] = None):
        """Initialize k-CFA pointer analysis."""
        self.config = config or KCFAConfig()
        
        # Analysis state
        self._functions: Dict[str, Any] = {}
        self._env: Dict[Tuple[Context, str], PointsToSet] = {}  # (ctx, var) -> pts
        self._heap: Dict[Tuple[AbstractObject, FieldKey], PointsToSet] = {}  # (obj, field) -> pts
        
        # Worklists
        self._constraint_worklist = ConstraintWorklist()
        self._call_worklist = CallWorklist()
        
        # Context management
        self._context_selector = ContextSelector(k=self.config.k)
        self._contexts: Set[Context] = set()
        
        # Call graph
        self._call_graph = CallGraphAdapter(self.config)
        
        # Builtin summaries
        self._builtin_summaries = BuiltinSummaryManager(self.config)
        
        # Analysis state
        self._analysis_complete = False
        self._statistics = {"objects_created": 0, "constraints_processed": 0, "calls_processed": 0}
        
    def plan(self, ir_module_or_functions: Any) -> None:
        """Plan the analysis by identifying functions to analyze."""
        # Extract functions from IR module or function list
        if hasattr(ir_module_or_functions, '__iter__'):
            # Assume it's an iterable of functions
            for func in ir_module_or_functions:
                if hasattr(func, 'get_name'):
                    self._functions[func.get_name()] = func
                    self._call_graph.register_function(func.get_name(), func)
        else:
            # Assume it's a single module/function
            if hasattr(ir_module_or_functions, 'get_functions'):
                functions = ir_module_or_functions.get_functions()
                for func in functions:
                    if hasattr(func, 'get_name'):
                        self._functions[func.get_name()] = func
                        self._call_graph.register_function(func.get_name(), func)
            elif hasattr(ir_module_or_functions, 'get_name'):
                # Single function
                func = ir_module_or_functions
                self._functions[func.get_name()] = func
                self._call_graph.register_function(func.get_name(), func)
                
        if self.config.verbose:
            print(f"Planned analysis for {len(self._functions)} functions")
            
    def initialize(self) -> None:
        """Initialize analysis state and worklists."""
        empty_context = Context()
        self._contexts.add(empty_context)
        
        # Initialize entry points
        for func_name, func in self._functions.items():
            # Add entry point context
            self._contexts.add(empty_context)
            
            # Extract events from function and add to worklist
            events = list(iter_function_events(func))
            for event in events:
                self._add_event_to_worklist(event, empty_context)
                
        if self.config.verbose:
            print(f"Initialized analysis with {len(self._contexts)} contexts")
            print(f"Initial worklist size: constraints={self._constraint_worklist.size()}, calls={self._call_worklist.size()}")
            
    def run(self) -> None:
        """Run the pointer analysis to fixpoint."""
        iteration = 0
        max_iterations = self.config.max_iterations if hasattr(self.config, 'max_iterations') else 10000
        
        while iteration < max_iterations:
            changed = False
            
            # Process constraint worklist
            while not self._constraint_worklist.empty():
                constraint = self._constraint_worklist.pop()
                if self._process_constraint(constraint):
                    changed = True
                self._statistics["constraints_processed"] += 1
                    
            # Process call worklist
            while not self._call_worklist.empty():
                call = self._call_worklist.pop()
                if self._process_call(call):
                    changed = True
                self._statistics["calls_processed"] += 1
                    
            iteration += 1
            
            if not changed:
                break
                
            if iteration >= max_iterations:
                if self.config.verbose:
                    print(f"Analysis reached maximum iterations: {max_iterations}")
                break
                
        self._analysis_complete = True
        if self.config.verbose:
            print(f"Analysis converged after {iteration} iterations")
            print(f"Statistics: {self._statistics}")
            
    def results(self) -> Dict[str, Any]:
        """Get analysis results."""
        if not self._analysis_complete:
            raise RuntimeError("Analysis has not been completed yet")
            
        # Convert internal state to external format
        points_to = {}
        for (ctx, var), pts in self._env.items():
            key = f"{var}@{ctx}"
            points_to[key] = [str(obj) for obj in pts.objects]
            
        return {
            "points_to": points_to,
            "call_graph": self._call_graph.get_statistics(),
            "contexts": {str(ctx): len(ctx) for ctx in self._contexts},
            "statistics": self._statistics.copy(),
            "heap_size": len(self._heap),
            "env_size": len(self._env)
        }
    
    # Helper methods for environment and heap access
    
    def _get_var_pts(self, ctx: Context, var: str) -> PointsToSet:
        """Get points-to set for a variable in context."""
        return self._env.get((ctx, var), PointsToSet())
    
    def _set_var_pts(self, ctx: Context, var: str, pts: PointsToSet) -> bool:
        """Set points-to set for a variable in context. Returns True if changed."""
        key = (ctx, var)
        old_pts = self._env.get(key, PointsToSet())
        new_pts = old_pts.join(pts)
        
        if new_pts != old_pts:
            self._env[key] = new_pts
            return True
        return False
    
    def _get_field_pts(self, obj: AbstractObject, field: FieldKey) -> PointsToSet:
        """Get points-to set for an object field."""
        return self._heap.get((obj, field), PointsToSet())
    
    def _set_field_pts(self, obj: AbstractObject, field: FieldKey, pts: PointsToSet) -> bool:
        """Set points-to set for an object field. Returns True if changed."""
        key = (obj, field)
        old_pts = self._heap.get(key, PointsToSet())
        new_pts = old_pts.join(pts)
        
        if new_pts != old_pts:
            self._heap[key] = new_pts
            return True
        return False
    
    def _create_object(self, alloc_id: str, ctx: Context, recv_objs: Optional[List[AbstractObject]] = None) -> AbstractObject:
        """Create a new abstract object."""
        recv_ctx = tuple(recv_objs) if recv_objs else None
        obj = make_object(alloc_id, ctx, recv_ctx, depth=self.config.obj_depth)
        self._statistics["objects_created"] += 1
        return obj
    
    # Event processing methods
    
    def _add_event_to_worklist(self, event: Event, ctx: Context) -> None:
        """Add an event to the appropriate worklist."""
        if event["kind"] == "alloc":
            # Handle allocation immediately
            self._handle_allocation(event, ctx)
        elif event["kind"] == "call":
            # Add to call worklist
            self._call_worklist.add_call(
                call_type="direct" if event.get("callee_symbol") else "indirect",
                call_id=event["call_id"],
                caller_ctx=str(ctx),
                callee=event.get("callee_symbol") or event.get("callee_expr", "unknown"),
                args=tuple(event.get("args", [])),
                receiver=event.get("receiver"),
                target=event.get("target")
            )
        elif event["kind"] in ("attr_load", "attr_store"):
            # Add to constraint worklist
            if event["kind"] == "attr_load":
                self._constraint_worklist.add_load_constraint(
                    source=event["obj"],
                    field=event.get("attr", "unknown"),
                    target=event["target"],
                    context=str(ctx),
                    site_id=f"attr_load_{event.get('attr', 'unknown')}"
                )
            else:  # attr_store
                self._constraint_worklist.add_store_constraint(
                    target=event["obj"],
                    field=event.get("attr", "unknown"),
                    source=event["value"],
                    context=str(ctx),
                    site_id=f"attr_store_{event.get('attr', 'unknown')}"
                )
        elif event["kind"] in ("elem_load", "elem_store"):
            # Handle container operations
            if event["kind"] == "elem_load":
                field_name = "value" if event.get("container_kind") == "dict" else "elem"
                self._constraint_worklist.add_load_constraint(
                    source=event["container"],
                    field=field_name,
                    target=event["target"],
                    context=str(ctx),
                    site_id=f"elem_load_{field_name}"
                )
            else:  # elem_store
                field_name = "value" if event.get("container_kind") == "dict" else "elem"
                self._constraint_worklist.add_store_constraint(
                    target=event["container"],
                    field=field_name,
                    source=event["value"],
                    context=str(ctx),
                    site_id=f"elem_store_{field_name}"
                )
        elif event["kind"] == "copy":
            # Handle copy/assignment: target = source
            self._constraint_worklist.add_copy_constraint(
                source=event["source"],
                target=event["target"],
                context=str(ctx),
                site_id=f"copy_{event['source']}_to_{event['target']}"
            )
        
    def _handle_allocation(self, event: Event, ctx: Context) -> None:
        """Handle object allocation events."""
        alloc_id = event["alloc_id"]
        target = event["target"]
        alloc_type = event["type"]
        
        # Create abstract object
        obj = self._create_object(alloc_id, ctx)
        
        # Add to target variable's points-to set
        pts = PointsToSet(frozenset([obj]))
        self._set_var_pts(ctx, target, pts)
        
        # Initialize object fields based on type
        if alloc_type in ("list", "tuple", "set"):
            # Initialize element field
            elem_field = elem_key()
            if "elements" in event:
                # Initialize with provided elements
                elem_pts = PointsToSet()
                for elem_var in event["elements"]:
                    elem_pts = elem_pts.join(self._get_var_pts(ctx, elem_var))
                self._set_field_pts(obj, elem_field, elem_pts)
        elif alloc_type == "dict":
            # Initialize value field  
            value_field = value_key()
            if "values" in event:
                # Initialize with provided values
                value_pts = PointsToSet()
                for value_var in event["values"]:
                    value_pts = value_pts.join(self._get_var_pts(ctx, value_var))
                self._set_field_pts(obj, value_field, value_pts)
    
    def _process_constraint(self, constraint) -> bool:
        """Process a constraint from the worklist."""
        changed = False
        
        # Parse constraint context
        from .context import Context
        if constraint.context == "[]":
            ctx = Context()  # Empty context
        else:
            # For now, treat context as a string identifier
            # TODO: Parse complex context strings if needed
            ctx = Context()  # Default to empty context for simplicity
        
        if constraint.constraint_type == "copy":
            # Copy constraint: target = source
            source_pts = self._get_var_pts(ctx, constraint.source)
            if self._set_var_pts(ctx, constraint.target, source_pts):
                changed = True
                    
        elif constraint.constraint_type == "load":
            # Load constraint: target = source.field
            source_pts = self._get_var_pts(ctx, constraint.source)
            target_pts = PointsToSet()
            
            # Get field key
            if constraint.field == "unknown":
                field = unknown_attr_key()
            elif constraint.field == "elem":
                field = elem_key()
            elif constraint.field == "value":
                field = value_key()
            else:
                field = attr_key(constraint.field)
            
            # Load from all objects in source
            for obj in source_pts.objects:
                field_pts = self._get_field_pts(obj, field)
                target_pts = target_pts.join(field_pts)
            
            if self._set_var_pts(ctx, constraint.target, target_pts):
                changed = True
                    
        elif constraint.constraint_type == "store":
            # Store constraint: target.field = source
            target_pts = self._get_var_pts(ctx, constraint.target)
            source_pts = self._get_var_pts(ctx, constraint.source)
            
            # Get field key
            if constraint.field == "unknown":
                field = unknown_attr_key()
            elif constraint.field == "elem":
                field = elem_key()
            elif constraint.field == "value":
                field = value_key()
            else:
                field = attr_key(constraint.field)
            
            # Store to all objects in target
            for obj in target_pts.objects:
                if self._set_field_pts(obj, field, source_pts):
                    changed = True
        
        return changed
    
    def _handle_parameter_passing(self, caller_ctx: Context, callee_ctx: Context, call, callee_func) -> bool:
        """Handle parameter passing from caller to callee."""
        changed = False
        
        # Extract real parameter names from function signature
        param_names = []
        
        # Try to get parameter names from IR function if available
        if hasattr(callee_func, 'get_arg_names') or hasattr(callee_func, 'args'):
            try:
                # Get AST arguments from IRFunc
                if hasattr(callee_func, 'get_arg_names'):
                    ast_args = callee_func.get_arg_names()
                else:
                    ast_args = callee_func.args
                
                # Extract parameter names from ast.arguments
                if hasattr(ast_args, 'args'):
                    param_names = [arg.arg for arg in ast_args.args]
                else:
                    # Fallback for unexpected argument structure
                    param_names = [f'param_{i}' for i in range(len(call.args))]
                    
            except (AttributeError, TypeError):
                # Fallback if extraction fails
                param_names = [f'param_{i}' for i in range(len(call.args))]
        else:
            # For method calls, handle 'self' parameter specially
            if hasattr(callee_func, 'get_name'):
                func_name = callee_func.get_name()
                if '.' in func_name and '__init__' in func_name:
                    # Constructor call: obj = Class(args) -> Class.__init__(obj, args)
                    param_names = ['self'] + [f'param_{i}' for i in range(len(call.args))]
                elif '.' in func_name:
                    # Method call: obj.method(args) -> method(obj, args)  
                    param_names = ['self'] + [f'param_{i}' for i in range(len(call.args))]
                else:
                    # Regular function call
                    param_names = [f'param_{i}' for i in range(len(call.args))]
            else:
                # Fallback: generate parameter names
                param_names = [f'param_{i}' for i in range(len(call.args))]
        
        # Handle 'self' parameter for method/constructor calls
        if param_names and param_names[0] == 'self':
            if call.receiver:
                # Method call: receiver becomes 'self'
                receiver_pts = self._get_var_pts(caller_ctx, call.receiver)
                if self._set_var_pts(callee_ctx, 'self', receiver_pts):
                    changed = True
            elif call.target:
                # Constructor call: target object becomes 'self'
                # For constructor calls, we need to create the object first
                # This is a bit tricky - the object should be allocated in caller context
                # but passed as 'self' to the callee
                
                # Check if target already has an object (from allocation)
                target_pts = self._get_var_pts(caller_ctx, call.target)
                if target_pts.objects:
                    # Object already exists, pass it as 'self'
                    if self._set_var_pts(callee_ctx, 'self', target_pts):
                        changed = True
                else:
                    # Create object for constructor call
                    # This should typically be handled by allocation events, but as fallback:
                    alloc_id = f"obj_{call.call_id}"
                    obj = self._create_object(alloc_id, caller_ctx)
                    obj_pts = PointsToSet(frozenset([obj]))
                    
                    # Set in both caller (target) and callee (self)
                    if self._set_var_pts(caller_ctx, call.target, obj_pts):
                        changed = True
                    if self._set_var_pts(callee_ctx, 'self', obj_pts):
                        changed = True
        
        # Handle regular arguments
        start_idx = 1 if param_names and param_names[0] == 'self' else 0
        for i, arg_name in enumerate(call.args):
            if start_idx + i < len(param_names):
                param_name = param_names[start_idx + i]
                arg_pts = self._get_var_pts(caller_ctx, arg_name)
                if self._set_var_pts(callee_ctx, param_name, arg_pts):
                    changed = True
        
        return changed
    
    def _handle_return_value(self, caller_ctx: Context, callee_ctx: Context, call, callee_func) -> bool:
        """Handle return value from callee to caller."""
        changed = False
        
        if call.target:
            # Look for return value in callee context
            # This is simplified - in practice we'd track return statements
            return_pts = self._get_var_pts(callee_ctx, 'return')
            if return_pts.objects:
                if self._set_var_pts(caller_ctx, call.target, return_pts):
                    changed = True
        
        return changed
    
    def _process_call(self, call) -> bool:
        """Process a function call from the worklist."""
        changed = False
        
        # Parse context from string representation
        # For now, use simplified context handling
        caller_ctx = Context()  # TODO: Parse from call.caller_ctx
        
        if call.call_type == "direct":
            # Direct call - resolve callee directly
            callee_fn = call.callee
            
            # Check if it's a builtin
            if self._builtin_summaries.has_summary(callee_fn):
                summary = self._builtin_summaries.get_summary(callee_fn)
                if summary:
                    try:
                        summary.apply(call.target, list(call.args), caller_ctx, self)
                    except NotImplementedError:
                        # Conservative handling
                        if call.target:
                            # Create a conservative object for the return value
                            ret_obj = self._create_object(f"builtin_{callee_fn}_ret", caller_ctx)
                            ret_pts = PointsToSet(frozenset([ret_obj]))
                            self._set_var_pts(caller_ctx, call.target, ret_pts)
                            changed = True
            else:
                # User-defined function call - try exact match first
                resolved_callee = None
                if callee_fn in self._functions:
                    resolved_callee = callee_fn
                else:
                    # Try to find function by name suffix (handle qualified names)
                    for full_name in self._functions.keys():
                        if full_name.endswith('.' + callee_fn) or full_name == callee_fn:
                            resolved_callee = full_name
                            break
                
                if resolved_callee:
                    callee_func = self._functions[resolved_callee]
                    call_site = CallSite(call.call_id, resolved_callee)
                    callee_ctx = self._context_selector.push(caller_ctx, call_site)
                    
                    # Add call graph edge
                    self._call_graph.add_edge(caller_ctx, call_site, callee_ctx, resolved_callee)
                    
                    # Handle parameter passing
                    self._handle_parameter_passing(caller_ctx, callee_ctx, call, callee_func)
                    
                    # Handle return value
                    self._handle_return_value(caller_ctx, callee_ctx, call, callee_func)
                    
                    self._contexts.add(callee_ctx)
                    changed = True
                    
        elif call.call_type == "indirect":
            # Indirect call - resolve through variable
            callee_var = call.callee
            callee_pts = self._get_var_pts(caller_ctx, callee_var)
            
            # For each possible callee object
            for callee_obj in callee_pts.objects:
                # TODO: Extract function name from object
                # For now, assume object allocation ID contains function info
                if "func" in callee_obj.alloc_id:
                    # Extract function name
                    callee_fn = callee_obj.alloc_id.split(":")[-2] if ":" in callee_obj.alloc_id else "unknown"
                    
                    if callee_fn in self._functions:
                        callee_func = self._functions[callee_fn]
                        call_site = CallSite(call.call_id, callee_fn)
                        callee_ctx = self._context_selector.push(caller_ctx, call_site)
                        
                        # Add call graph edge
                        self._call_graph.add_edge(caller_ctx, call_site, callee_ctx, callee_fn)
                        
                        # Handle parameter passing
                        self._handle_parameter_passing(caller_ctx, callee_ctx, call, callee_func)
                        
                        # Handle return value
                        self._handle_return_value(caller_ctx, callee_ctx, call, callee_func)
                        
                        self._contexts.add(callee_ctx)
                        changed = True
                        
        elif call.call_type == "method":
            # Method call - resolve through receiver
            if call.receiver:
                receiver_pts = self._get_var_pts(caller_ctx, call.receiver)
                
                # For each possible receiver object
                for recv_obj in receiver_pts.objects:
                    # Look up method in receiver object
                    method_field = attr_key(call.callee)
                    method_pts = self._get_field_pts(recv_obj, method_field)
                    
                    # For each possible method object
                    for method_obj in method_pts.objects:
                        # TODO: Extract function name from method object
                        if "func" in method_obj.alloc_id:
                            callee_fn = method_obj.alloc_id.split(":")[-2] if ":" in method_obj.alloc_id else "unknown"
                            
                            if callee_fn in self._functions:
                                callee_func = self._functions[callee_fn]
                                call_site = CallSite(call.call_id, callee_fn)
                                callee_ctx = self._context_selector.push(caller_ctx, call_site)
                                
                                # Add call graph edge
                                self._call_graph.add_edge(caller_ctx, call_site, callee_ctx, callee_fn)
                                
                                # Handle parameter passing
                                self._handle_parameter_passing(caller_ctx, callee_ctx, call, callee_func)
                                
                                # Handle return value
                                self._handle_return_value(caller_ctx, callee_ctx, call, callee_func)
                                
                                self._contexts.add(callee_ctx)
                                changed = True
        
        return changed