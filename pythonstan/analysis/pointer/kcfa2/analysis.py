"""Main k-CFA pointer analysis implementation.

This module implements the KCFA2PointerAnalysis class, which is the main
entry point for running k-CFA pointer analysis with 2-object sensitivity.
"""

from typing import Any, Dict, List, Optional, Set, Union, Tuple
from .config import KCFAConfig
from .context import CallSite, AbstractContext
from .context_selector import ContextSelector, parse_policy
from .model import AbstractLocation, AbstractObject, PointsToSet, Env, Store, Heap, FieldKey
from .worklist import CallItem, Worklist, ConstraintWorklist, CallWorklist
from .errors import AnalysisTimeout, ConfigurationError
from .heap_model import make_object, attr_key, elem_key, value_key, unknown_attr_key
from .callgraph_adapter import CallGraphAdapter
from .ir_adapter import Event, iter_function_events, site_id_of
from .summaries import BuiltinSummaryManager
from .mro import ClassHierarchyManager
from .name_resolution import NameResolver

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
        self._env: Dict[Tuple[AbstractContext, str], PointsToSet] = {}  # (ctx, var) -> pts
        self._heap: Dict[Tuple[AbstractObject, FieldKey], PointsToSet] = {}  # (obj, field) -> pts
        
        # Worklists
        self._constraint_worklist = ConstraintWorklist()
        self._call_worklist = CallWorklist()
        
        # Context management
        policy = parse_policy(self.config.context_policy)
        self._context_selector = ContextSelector(policy=policy, k=self.config.k)
        self._contexts: Set[AbstractContext] = set()
        
        # Call graph
        self._call_graph = CallGraphAdapter(self.config)
        
        # Builtin summaries
        self._builtin_summaries = BuiltinSummaryManager(self.config)
        
        # Class hierarchy and MRO (if enabled)
        if self.config.build_class_hierarchy:
            self._class_hierarchy = ClassHierarchyManager()
        else:
            self._class_hierarchy = None
        
        # Name resolution for temporary variables
        self._name_resolver = NameResolver()
        
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
    
    def plan_module(self, ir_module: Any) -> None:
        """Plan module-level analysis (extract class definitions, etc.)."""
        from .ir_adapter import iter_module_events
        
        # Store module for later processing
        if not hasattr(self, '_modules'):
            self._modules = []
        self._modules.append(ir_module)
        
        if self.config.verbose:
            print(f"Planned module-level analysis")
    
    def plan_classes(self, classes: List[Any]) -> None:
        """Plan class definitions for analysis."""
        # Store classes for later processing
        if not hasattr(self, '_classes'):
            self._classes = []
        self._classes.extend(classes)
        
        if self.config.verbose:
            print(f"Planned {len(classes)} classes for analysis")
            
    def initialize(self) -> None:
        """Initialize analysis state and worklists."""
        from .ir_adapter import iter_module_events, site_id_of, AllocEvent
        import ast
        
        empty_context = self._context_selector.empty_context()
        self._contexts.add(empty_context)
        
        # Process class definitions first
        if hasattr(self, '_classes'):
            for cls in self._classes:
                # Create allocation event for class
                site_id = site_id_of(cls, 'class')
                name = cls.name if hasattr(cls, 'name') else 'unknown'
                
                event_data = {
                    "kind": "alloc",
                    "alloc_id": site_id,
                    "target": name,
                    "type": "class",
                    "bb": "module",
                    "idx": 0
                }
                
                # Extract base classes
                if hasattr(cls, 'bases') and cls.bases:
                    bases = []
                    for base_expr in cls.bases:
                        if isinstance(base_expr, ast.Name):
                            bases.append(base_expr.id)
                        elif isinstance(base_expr, ast.Attribute):
                            try:
                                bases.append(ast.unparse(base_expr))
                            except AttributeError:
                                if hasattr(base_expr, 'attr'):
                                    bases.append(base_expr.attr)
                    if bases:
                        event_data["bases"] = bases
                
                class_event = AllocEvent(**event_data)
                self._add_event_to_worklist(class_event, empty_context)
            
            if self.config.verbose:
                print(f"Added {len(self._classes)} class allocation events")
        
        # Process module-level events (class definitions, etc.)
        if hasattr(self, '_modules'):
            for module in self._modules:
                module_events = list(iter_module_events(module))
                for event in module_events:
                    self._add_event_to_worklist(event, empty_context)
                if self.config.verbose and module_events:
                    print(f"Added {len(module_events)} module-level events")
        
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
            
            # Process call worklist first (includes parameter passing)
            while not self._call_worklist.empty():
                call = self._call_worklist.pop()
                if self._process_call(call):
                    changed = True
                self._statistics["calls_processed"] += 1
                    
            # Process constraint worklist after calls (variables should be available)
            while not self._constraint_worklist.empty():
                constraint = self._constraint_worklist.pop()
                if self._process_constraint(constraint):
                    changed = True
                self._statistics["constraints_processed"] += 1
                    
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
        
        # Get resolved names for temporary variables
        resolved_names = self._name_resolver.get_all_resolved()
        
        # Add resolved names as annotations to points_to results
        points_to_annotated = {}
        for key, objs in points_to.items():
            points_to_annotated[key] = {
                "objects": objs,
                "resolved_name": resolved_names.get(key)
            }
            
        return {
            "points_to": points_to,
            "points_to_annotated": points_to_annotated,
            "resolved_names": resolved_names,
            "call_graph": self._call_graph.get_statistics(),
            "contexts": {str(ctx): len(ctx) for ctx in self._contexts},
            "statistics": self._statistics.copy(),
            "heap_size": len(self._heap),
            "env_size": len(self._env),
            "class_hierarchy_size": len(self._class_hierarchy._bases) if self._class_hierarchy else 0
        }
    
    # Helper methods for environment and heap access
    
    def _get_var_pts(self, ctx: AbstractContext, var: str) -> PointsToSet:
        """Get points-to set for a variable in context."""
        return self._env.get((ctx, var), PointsToSet())
    
    def _set_var_pts(self, ctx: AbstractContext, var: str, pts: PointsToSet) -> bool:
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
    
    def _create_object(self, alloc_id: str, ctx: AbstractContext, recv_objs: Optional[List[AbstractObject]] = None) -> AbstractObject:
        """Create a new abstract object."""
        recv_ctx = tuple(recv_objs) if recv_objs else None
        obj = make_object(alloc_id, ctx, recv_ctx, depth=self.config.obj_depth)
        self._statistics["objects_created"] += 1
        return obj
    
    def _resolve_attribute_with_mro(self, obj: AbstractObject, field: FieldKey, ctx: AbstractContext) -> PointsToSet:
        """Resolve attribute following MRO chain if enabled.
        
        Args:
            obj: Object to load attribute from
            field: Field key for the attribute
            ctx: Current context
            
        Returns:
            Points-to set for the attribute
        """
        if not self.config.use_mro or not self._class_hierarchy:
            # MRO disabled - use direct field access
            return self._get_field_pts(obj, field)
        
        # Check if this is a class object (has MRO)
        # Class objects are identified by allocation type 'class' in their ID
        if ':class' not in obj.alloc_id:
            # Not a class object - use direct field access
            return self._get_field_pts(obj, field)
        
        # Get class ID from object
        class_id = obj.alloc_id
        
        # Check if class is in hierarchy
        if not self._class_hierarchy.has_class(class_id):
            # Class not in hierarchy - fall back to direct access
            return self._get_field_pts(obj, field)
        
        try:
            # Compute MRO for this class
            mro = self._class_hierarchy.get_mro(class_id)
            
            # Search for attribute through MRO chain
            result = PointsToSet()
            for ancestor_id in mro:
                # Find the class object for this ancestor
                # We need to look it up in our environment/heap
                # For now, check if we have a direct match
                if ancestor_id == class_id:
                    # Same class - check directly
                    field_pts = self._get_field_pts(obj, field)
                    if field_pts.objects:
                        # Found attribute - return immediately (first match wins)
                        return field_pts
                else:
                    # Different class in MRO - need to find its object
                    # Search for class object with matching allocation ID
                    for (ctx_key, var), pts in self._env.items():
                        for cls_obj in pts.objects:
                            if cls_obj.alloc_id == ancestor_id:
                                field_pts = self._get_field_pts(cls_obj, field)
                                if field_pts.objects:
                                    # Found attribute in ancestor - return immediately
                                    return field_pts
            
            # No attribute found in MRO chain - return empty
            return result
            
        except Exception as e:
            # MRO computation failed - fall back to direct access
            if self.config.verbose:
                print(f"Warning: MRO resolution failed for {class_id}: {e}")
            return self._get_field_pts(obj, field)
    
    # Event processing methods
    
    def _add_event_to_worklist(self, event: Event, ctx: AbstractContext) -> None:
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
        
    def _handle_allocation(self, event: Event, ctx: AbstractContext) -> None:
        """Handle object allocation events.
        
        Creates an abstract object for the allocation site and initializes
        its fields based on the allocation type.
        
        Allocation types:
        - const: Constant values (numbers, strings, booleans, None)
        - list/tuple/set: Container literals with element field
        - dict: Dictionary literals with value field
        - obj: Generic object allocations (class instances)
        - func: Function definitions with closure capture
        - class: Class definitions
        - exc: Exception objects
        - method: Bound method objects
        - genframe: Generator frame objects
        """
        alloc_id = event["alloc_id"]
        target = event["target"]
        alloc_type = event["type"]
        
        # Create abstract object
        obj = self._create_object(alloc_id, ctx)
        
        # Add to target variable's points-to set
        pts = PointsToSet(frozenset([obj]))
        self._set_var_pts(ctx, target, pts)
        
        # Track allocation for name resolution
        self._name_resolver.record_allocation(
            target=target,
            alloc_site=alloc_id,
            alloc_type=alloc_type,
            context_str=str(ctx)
        )
        
        # Initialize object fields based on type
        if alloc_type == "const":
            # Constants are immutable singleton-like objects
            # No field initialization needed - they represent values, not mutable objects
            # Their identity is captured by the allocation site
            pass
        
        elif alloc_type in ("list", "tuple", "set"):
            # Initialize element field for containers
            # All elements are tracked through a single abstract "elem" field
            elem_field = elem_key()
            if "elements" in event:
                # Initialize with provided elements
                elem_pts = PointsToSet()
                for elem_var in event["elements"]:
                    elem_pts = elem_pts.join(self._get_var_pts(ctx, elem_var))
                self._set_field_pts(obj, elem_field, elem_pts)
            else:
                # Empty container - initialize with empty points-to set
                # This establishes the field in the heap model
                self._set_field_pts(obj, elem_field, PointsToSet())
        
        elif alloc_type == "dict":
            # Initialize value field for dictionaries
            # All values are tracked through a single abstract "value" field (key-insensitive)
            value_field = value_key()
            if "values" in event:
                # Initialize with provided values
                value_pts = PointsToSet()
                for value_var in event["values"]:
                    value_pts = value_pts.join(self._get_var_pts(ctx, value_var))
                self._set_field_pts(obj, value_field, value_pts)
            else:
                # Empty dict - initialize with empty points-to set
                self._set_field_pts(obj, value_field, PointsToSet())
        
        elif alloc_type == "obj":
            # Generic object allocation (class instance)
            # Initialize __dict__ field to track instance attributes
            # This allows attribute stores/loads to propagate points-to information
            dict_field = attr_key("__dict__")
            self._set_field_pts(obj, dict_field, PointsToSet())
            
            # Additional instance attributes will be added through attr_store events
            # when the constructor (__init__) runs or when attributes are set
        
        elif alloc_type == "func":
            # Function object allocation (function definition)
            # Functions are first-class objects that can have attributes
            
            # Initialize __dict__ for function attributes (e.g., func.custom_attr = value)
            dict_field = attr_key("__dict__")
            self._set_field_pts(obj, dict_field, PointsToSet())
            
            # Initialize __closure__ field for closure variables
            # Actual closure bindings would be populated by the IR adapter
            # if closure information is available in the event
            closure_field = attr_key("__closure__")
            if "closure_vars" in event and event["closure_vars"]:
                # Capture closure variables
                closure_pts = PointsToSet()
                for closure_var in event["closure_vars"]:
                    closure_pts = closure_pts.join(self._get_var_pts(ctx, closure_var))
                self._set_field_pts(obj, closure_field, closure_pts)
            else:
                # No closure or empty closure
                self._set_field_pts(obj, closure_field, PointsToSet())
            
            # __name__, __code__, __globals__ are typically constant metadata
            # and don't need points-to tracking
        
        elif alloc_type == "class":
            # Class object allocation (class definition)
            # Classes are objects that can have class attributes and methods
            
            # Initialize __dict__ for class attributes and methods
            dict_field = attr_key("__dict__")
            self._set_field_pts(obj, dict_field, PointsToSet())
            
            # Initialize __bases__ field for inheritance tracking
            # If base class information is available, populate it
            bases_field = attr_key("__bases__")
            base_ids = []
            if "bases" in event and event["bases"]:
                bases_pts = PointsToSet()
                for base_var in event["bases"]:
                    # Track base class names for hierarchy
                    base_ids.append(base_var)
                    bases_pts = bases_pts.join(self._get_var_pts(ctx, base_var))
                self._set_field_pts(obj, bases_field, bases_pts)
            else:
                # No explicit bases (defaults to object)
                self._set_field_pts(obj, bases_field, PointsToSet())
            
            # Build class hierarchy (if enabled)
            if self.config.build_class_hierarchy and self._class_hierarchy:
                # Register this class in the hierarchy
                # Use allocation ID as unique class identifier
                class_id = alloc_id
                
                # Resolve base class names to allocation IDs via points-to analysis
                resolved_base_ids = []
                for base_name in base_ids:
                    # Try to resolve base_name to its allocation site
                    base_pts = self._get_var_pts(ctx, base_name)
                    if base_pts.objects:
                        # Use the allocation ID of the base class object
                        for base_obj in base_pts.objects:
                            resolved_base_ids.append(base_obj.alloc_id)
                    else:
                        # Base not yet resolved - use the name as placeholder
                        # This will be resolved in later iterations
                        resolved_base_ids.append(base_name)
                
                # Add to hierarchy
                self._class_hierarchy.add_class(class_id, resolved_base_ids if resolved_base_ids else None)
                
                if self.config.verbose:
                    print(f"Added class {class_id} with bases {resolved_base_ids}")
            
            # __name__, __module__ are constant metadata
        
        elif alloc_type == "exc":
            # Exception object allocation
            # Exceptions are objects with special fields for error information
            
            # Initialize __dict__ for exception attributes
            dict_field = attr_key("__dict__")
            self._set_field_pts(obj, dict_field, PointsToSet())
            
            # Initialize args field for exception arguments
            # Exception(*args) -> args tuple
            args_field = attr_key("args")
            if "args" in event and event["args"]:
                args_pts = PointsToSet()
                for arg_var in event["args"]:
                    args_pts = args_pts.join(self._get_var_pts(ctx, arg_var))
                self._set_field_pts(obj, args_field, args_pts)
            else:
                self._set_field_pts(obj, args_field, PointsToSet())
            
            # __traceback__, __cause__, __context__ could be tracked but are often
            # runtime-specific and less relevant for static pointer analysis
        
        elif alloc_type == "method":
            # Bound method object - created when accessing a method on an instance
            # bound_method = instance.method_name
            # bound_method has two key attributes: __self__ (receiver) and __func__ (function)
            
            # Initialize __self__ field to point to the receiver object
            self_field = attr_key("__self__")
            if "recv_binding" in event and event["recv_binding"]:
                recv_pts = self._get_var_pts(ctx, event["recv_binding"])
                if recv_pts.objects:
                    self._set_field_pts(obj, self_field, recv_pts)
            else:
                # Method without receiver binding - shouldn't happen in normal code
                self._set_field_pts(obj, self_field, PointsToSet())
            
            # Initialize __func__ field to point to the underlying function
            func_field = attr_key("__func__")
            if "func_binding" in event and event["func_binding"]:
                func_pts = self._get_var_pts(ctx, event["func_binding"])
                if func_pts.objects:
                    self._set_field_pts(obj, func_field, func_pts)
            else:
                # Function binding may not always be available at allocation time
                self._set_field_pts(obj, func_field, PointsToSet())
        
        elif alloc_type == "genframe":
            # Generator frame object - created when a generator function is called
            # Generators maintain internal state and yield values
            
            # Initialize __dict__ for generator attributes (if any)
            dict_field = attr_key("__dict__")
            self._set_field_pts(obj, dict_field, PointsToSet())
            
            # Track yielded values through a special field
            # This abstracts all yield expressions in the generator
            yield_field = attr_key("__yield_value__")
            if "yield_binding" in event and event["yield_binding"]:
                yield_pts = self._get_var_pts(ctx, event["yield_binding"])
                self._set_field_pts(obj, yield_field, yield_pts)
            else:
                self._set_field_pts(obj, yield_field, PointsToSet())
            
            # gi_code, gi_frame, gi_running are runtime-specific and not tracked
        
        else:
            # Unknown allocation type - log warning and treat as generic object
            if self.config.verbose:
                print(f"Warning: Unknown allocation type '{alloc_type}' for {alloc_id}")
            # Fallback: initialize as generic object with __dict__
            dict_field = attr_key("__dict__")
            self._set_field_pts(obj, dict_field, PointsToSet())
    
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
            # Try to find source in the specified context first, then search all contexts
            source_pts = self._get_var_pts(ctx, constraint.source)
            
            # If source not found in the specified context, search all contexts
            if not source_pts.objects:
                for search_ctx in self._contexts:
                    source_pts_search = self._get_var_pts(search_ctx, constraint.source)
                    if source_pts_search.objects:
                        source_pts = source_pts_search
                        break
            
            if self._set_var_pts(ctx, constraint.target, source_pts):
                changed = True
                
            # Track assignment for name resolution
            self._name_resolver.record_assignment(
                target=constraint.target,
                source=constraint.source,
                context_str=str(ctx)
            )
                    
        elif constraint.constraint_type == "load":
            # Load constraint: target = source.field
            # Try to find source in the specified context first, then search all contexts
            source_pts = self._get_var_pts(ctx, constraint.source)
            
            # If source not found in the specified context, search all contexts
            if not source_pts.objects:
                for search_ctx in self._contexts:
                    source_pts_search = self._get_var_pts(search_ctx, constraint.source)
                    if source_pts_search.objects:
                        source_pts = source_pts_search
                        break
            
            target_pts = PointsToSet()
            
            # Hybrid __dict__ model for attribute access
            if constraint.field == "unknown":
                # Dynamic/unknown attribute access - go through __dict__ indirection
                # This handles: getattr(obj, dynamic_name), obj.__dict__[key], etc.
                dict_field = attr_key("__dict__")
                for obj in source_pts.objects:
                    # Get the __dict__ object(s) for this object
                    dict_pts = self._get_field_pts(obj, dict_field)
                    # Load all values from the dictionary object(s)
                    for dict_obj in dict_pts.objects:
                        value_field = value_key()
                        value_pts = self._get_field_pts(dict_obj, value_field)
                        target_pts = target_pts.join(value_pts)
            else:
                # Known attribute name - direct field access for precision
                # This handles: obj.attr_name where attr_name is statically known
                if constraint.field == "elem":
                    field = elem_key()
                elif constraint.field == "value":
                    field = value_key()
                else:
                    field = attr_key(constraint.field)
                
                # Load from all objects in source
                for obj in source_pts.objects:
                    # Use MRO-based resolution for class objects if enabled
                    field_pts = self._resolve_attribute_with_mro(obj, field, ctx)
                    target_pts = target_pts.join(field_pts)
            
            if self._set_var_pts(ctx, constraint.target, target_pts):
                changed = True
                    
        elif constraint.constraint_type == "store":
            # Store constraint: target.field = source
            # Try to find variables in the specified context first, then search all contexts
            target_pts = self._get_var_pts(ctx, constraint.target)
            source_pts = self._get_var_pts(ctx, constraint.source)
            
            # If target or source not found in the specified context, search all contexts
            if not target_pts.objects:
                for search_ctx in self._contexts:
                    target_pts_search = self._get_var_pts(search_ctx, constraint.target)
                    if target_pts_search.objects:
                        target_pts = target_pts_search
                        break
            
            if not source_pts.objects:
                for search_ctx in self._contexts:
                    source_pts_search = self._get_var_pts(search_ctx, constraint.source)
                    if source_pts_search.objects:
                        source_pts = source_pts_search
                        break
            
            # Hybrid __dict__ model for attribute access
            if constraint.field == "unknown":
                # Dynamic/unknown attribute store - go through __dict__ indirection
                # This handles: setattr(obj, dynamic_name, value), obj.__dict__[key] = value, etc.
                dict_field = attr_key("__dict__")
                for obj in target_pts.objects:
                    # Get the __dict__ object(s) for this object
                    dict_pts = self._get_field_pts(obj, dict_field)
                    # Store to all values in the dictionary object(s)
                    for dict_obj in dict_pts.objects:
                        value_field = value_key()
                        if self._set_field_pts(dict_obj, value_field, source_pts):
                            changed = True
            else:
                # Known attribute name - direct field access for precision
                # This handles: obj.attr_name = value where attr_name is statically known
                if constraint.field == "elem":
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
    
    def _handle_parameter_passing(self, caller_ctx: AbstractContext, callee_ctx: AbstractContext, call, callee_func) -> bool:
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
    
    def _handle_return_value(self, caller_ctx: AbstractContext, callee_ctx: AbstractContext, call, callee_func) -> bool:
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
    
    def _get_receiver_alloc_site(self, call: CallItem, caller_ctx: AbstractContext) -> Optional[str]:
        """Get allocation site of receiver object for method calls.
        
        Args:
            call: Call item being processed
            caller_ctx: Context of the caller
            
        Returns:
            Allocation site ID of receiver, or None if not a method call
        """
        # Check if this is a method call (has receiver attribute)
        if not hasattr(call, 'receiver') or not call.receiver:
            # For indirect calls through variables, check first argument
            if call.call_type == "indirect" and call.args:
                receiver_var = call.args[0]
                receiver_pts = self._get_var_pts(caller_ctx, receiver_var)
                if receiver_pts.objects:
                    # Use first object's allocation site
                    return receiver_pts.objects[0].alloc_id
            return None
        
        # Look up receiver in points-to set
        receiver_pts = self._get_var_pts(caller_ctx, call.receiver)
        if receiver_pts.objects:
            # Use first object's allocation site (could be improved with flow analysis)
            return receiver_pts.objects[0].alloc_id
        
        return None
    
    def _get_receiver_type(self, call: CallItem, caller_ctx: AbstractContext) -> Optional[str]:
        """Get type of receiver object for method calls.
        
        Args:
            call: Call item being processed
            caller_ctx: Context of the caller
            
        Returns:
            Type name of receiver, or None if not available
        """
        # Check if this is a method call
        if not hasattr(call, 'receiver') or not call.receiver:
            # For indirect calls, check first argument
            if call.call_type == "indirect" and call.args:
                receiver_var = call.args[0]
                receiver_pts = self._get_var_pts(caller_ctx, receiver_var)
                if receiver_pts.objects:
                    obj = receiver_pts.objects[0]
                    return self._extract_type_from_object(obj)
            return None
        
        receiver_pts = self._get_var_pts(caller_ctx, call.receiver)
        if receiver_pts.objects:
            obj = receiver_pts.objects[0]
            return self._extract_type_from_object(obj)
        
        return None
    
    def _extract_type_from_object(self, obj: AbstractObject) -> Optional[str]:
        """Extract type name from an abstract object.
        
        Args:
            obj: Abstract object
            
        Returns:
            Type name, or None if not available
        """
        # Check if object has explicit type metadata
        if hasattr(obj, 'alloc_type') and obj.alloc_type:
            return obj.alloc_type
        
        # Parse from allocation ID (format: file:line:col:type or similar)
        alloc_id = obj.alloc_id
        parts = alloc_id.split(':')
        
        # Try to extract type from various formats
        if len(parts) >= 4:
            # Format: file:line:col:type
            return parts[-1]
        elif 'class:' in alloc_id:
            # Format: class:ClassName
            return alloc_id.split('class:')[-1].split(':')[0]
        elif '/' in alloc_id:
            # Format: path/ClassName or similar
            return alloc_id.split('/')[-1].split(':')[0]
        
        # Try to extract from allocation ID if it contains a class name
        for part in parts:
            if part and part[0].isupper():  # Class names typically start with uppercase
                return part
        
        return None
    
    def _process_call(self, call: CallItem) -> bool:
        """Process a function call from the worklist."""
        changed = False
        
        # Parse context from string representation
        # For now, use simplified context handling
        caller_ctx = self._context_selector.empty_context()  # Use empty context for module-level
        
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
                    
                    # Get receiver information for context selection
                    receiver_alloc = self._get_receiver_alloc_site(call, caller_ctx)
                    receiver_type = self._get_receiver_type(call, caller_ctx)
                    
                    # Select callee context based on policy
                    callee_ctx = self._context_selector.select_call_context(
                        caller_ctx=caller_ctx,
                        call_site=call_site,
                        callee=resolved_callee,
                        receiver_alloc=receiver_alloc,
                        receiver_type=receiver_type
                    )
                    
                    # Add call graph edge
                    self._call_graph.add_edge(caller_ctx, call_site, callee_ctx, resolved_callee)
                    
                    # Handle parameter passing
                    self._handle_parameter_passing(caller_ctx, callee_ctx, call, callee_func)
                    
                    # Add function events to the callee context
                    events = list(iter_function_events(callee_func))
                    for event in events:
                        # Skip allocation events (already processed in empty context)
                        if event["kind"] != "alloc":
                            self._add_event_to_worklist(event, callee_ctx)
                    
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
                        
                        # Get receiver information for context selection
                        receiver_alloc = self._get_receiver_alloc_site(call, caller_ctx)
                        receiver_type = self._get_receiver_type(call, caller_ctx)
                        
                        # Select callee context based on policy
                        callee_ctx = self._context_selector.select_call_context(
                            caller_ctx=caller_ctx,
                            call_site=call_site,
                            callee=callee_fn,
                            receiver_alloc=receiver_alloc,
                            receiver_type=receiver_type
                        )
                        
                        # Add call graph edge
                        self._call_graph.add_edge(caller_ctx, call_site, callee_ctx, callee_fn)
                        
                        # Handle parameter passing
                        self._handle_parameter_passing(caller_ctx, callee_ctx, call, callee_func)
                        
                        # Add function events to the callee context
                        events = list(iter_function_events(callee_func))
                        for event in events:
                            # Skip allocation events (already processed in empty context)
                            if event["kind"] != "alloc":
                                self._add_event_to_worklist(event, callee_ctx)
                        
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
                                
                                # Get receiver information for context selection
                                receiver_alloc = self._get_receiver_alloc_site(call, caller_ctx)
                                receiver_type = self._get_receiver_type(call, caller_ctx)
                                
                                # Select callee context based on policy
                                callee_ctx = self._context_selector.select_call_context(
                                    caller_ctx=caller_ctx,
                                    call_site=call_site,
                                    callee=callee_fn,
                                    receiver_alloc=receiver_alloc,
                                    receiver_type=receiver_type
                                )
                                
                                # Add call graph edge
                                self._call_graph.add_edge(caller_ctx, call_site, callee_ctx, callee_fn)
                                
                                # Handle parameter passing
                                self._handle_parameter_passing(caller_ctx, callee_ctx, call, callee_func)
                                
                                # Add function events to the callee context
                                events = list(iter_function_events(callee_func))
                                for event in events:
                                    # Skip allocation events (already processed in empty context)
                                    if event["kind"] != "alloc":
                                        self._add_event_to_worklist(event, callee_ctx)
                                
                                # Handle return value
                                self._handle_return_value(caller_ctx, callee_ctx, call, callee_func)
                                
                                self._contexts.add(callee_ctx)
                                changed = True
        
        return changed