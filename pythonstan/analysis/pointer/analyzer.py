"""Pointer Analysis integration with PythonStAn analysis framework.

This module provides integration between the k-CFA pointer analysis
implementation and PythonStAn's general analysis framework.
"""

from typing import Any, Dict, List, Optional, Set
from pythonstan.ir import IRScope
from ..analysis import Analysis, AnalysisDriver, AnalysisConfig
from .kcfa2.analysis import KCFA2PointerAnalysis
from .kcfa2.config import KCFAConfig
from .kcfa2.ir_adapter import iter_function_events


class PointerAnalysis(Analysis):
    """Pointer analysis that integrates with PythonStAn's analysis framework."""
    
    def __init__(self, config: AnalysisConfig):
        """Initialize pointer analysis with configuration."""
        super().__init__(config)
        
        # Create k-CFA configuration from analysis config
        kcfa_config = self._create_kcfa_config(config)
        
        # Initialize the core pointer analysis engine
        self.analysis_engine = KCFA2PointerAnalysis(kcfa_config)
        
        # Store scope and other analysis state
        self.scope: Optional[IRScope] = None
        self.inputs: Dict[str, Any] = {}
        self.results: Dict[str, Any] = {}
    
    def _create_kcfa_config(self, config: AnalysisConfig) -> KCFAConfig:
        """Create KCFAConfig from AnalysisConfig."""
        options = config.options or {}
        
        # Map field_sensitivity values to field_sensitivity_mode
        field_sens = options.get("field_sensitivity", "attr")
        if field_sens == "attr":
            field_sensitivity_mode = "attr-name"
        else:
            field_sensitivity_mode = "field-insensitive"
        
        return KCFAConfig(
            k=options.get("k", 2),
            obj_depth=options.get("obj_depth", 2),
            field_sensitivity_mode=field_sensitivity_mode,
            max_heap_widening=options.get("max_heap_widening", 1000),
            verbose=options.get("verbose", False)
        )
    
    def set_scope(self, scope: IRScope):
        """Add a scope for inter-procedural analysis."""
        if not hasattr(self, 'scopes'):
            self.scopes = []
        self.scopes.append(scope)
        self.scope = scope  # Keep for backward compatibility
    
    def set_input(self, key: str, value: Any):
        """Set input for the analysis."""
        self.inputs[key] = value
    
    def get_input(self, key: str) -> Any:
        """Get input for the analysis."""
        return self.inputs.get(key)
    
    def run_analysis(self) -> Dict[str, Any]:
        """Run the pointer analysis on all set scopes (inter-procedural)."""
        if not hasattr(self, 'scopes') or not self.scopes:
            if self.scope:
                # Fallback to single scope
                self.scopes = [self.scope]
            else:
                raise ValueError("No scopes set for pointer analysis")
        
        # Get functions from all scopes for inter-procedural analysis
        all_functions = []
        for scope in self.scopes:
            functions = self._extract_functions_from_scope(scope)
            all_functions.extend(functions)
        
        # Run unified inter-procedural analysis
        self.analysis_engine.plan(all_functions)
        self.analysis_engine.initialize()
        self.analysis_engine.run()
        
        # Get results
        results = self.analysis_engine.results()
        self.results = results
        return results
    
    def _extract_functions_from_scope(self, scope: IRScope) -> List[Any]:
        """Extract analyzable functions from an IR scope."""
        
        class MockFunction:
            def __init__(self, name: str, scope: IRScope):
                self.name = name
                self.scope = scope
                self._ir_statements = None
                self._cached_statements = None
                self._ir_func = None
            
            def get_name(self) -> str:
                return self.name
            
            def _get_ir_func(self):
                """Get the underlying IRFunc object if this scope is a function."""
                if self._ir_func is None:
                    # Check if the scope itself is an IRFunc
                    if hasattr(self.scope, 'args') and hasattr(self.scope, 'get_arg_names'):
                        self._ir_func = self.scope
                    else:
                        # Try to find IRFunc in the scope's statements
                        statements = self._get_ir_statements()
                        for stmt in statements:
                            if hasattr(stmt, 'args') and hasattr(stmt, 'get_arg_names'):
                                self._ir_func = stmt
                                break
                return self._ir_func
            
            def _get_ir_statements(self):
                """Get processed IR statements from scope manager."""
                if self._ir_statements is None:
                    from pythonstan.world import World
                    
                    # Get processed IR statements from scope manager
                    ir_statements = World().scope_manager.get_ir(self.scope, "ir")
                    
                    if ir_statements:
                        self._ir_statements = ir_statements
                    else:
                        self._ir_statements = []
                
                return self._ir_statements
            
            # Parameter extraction methods for KCFA2PointerAnalysis
            def get_arg_names(self):
                """Get function argument names from underlying IRFunc."""
                ir_func = self._get_ir_func()
                if ir_func and hasattr(ir_func, 'get_arg_names'):
                    return ir_func.get_arg_names()
                return None
            
            @property
            def args(self):
                """Get function arguments from underlying IRFunc."""
                ir_func = self._get_ir_func()
                if ir_func and hasattr(ir_func, 'args'):
                    return ir_func.args
                return None
            
            # Implement interface for iter_function_events
            # Don't implement get_blocks() so it falls through to __iter__ path
            
            def __iter__(self):
                """Make the function iterable over its IR statements."""
                statements = self._get_ir_statements()
                return iter(statements) if statements else iter([])
            
            @property 
            def body(self):
                """Provide body attribute for iter_function_events."""
                return self._get_ir_statements()
            
            @property
            def stmts(self):
                """Provide stmts attribute for iter_function_events.""" 
                return self._get_ir_statements()
        
        # Create a mock function for the scope
        return [MockFunction(scope.get_qualname(), scope)]
    
    def get_points_to(self, var_name: str, context=None) -> Set[Any]:
        """Get points-to set for a variable."""
        if not self.results:
            return set()
        
        from .kcfa2.context import Context
        from .kcfa2.model import PointsToSet
        
        ctx = context or Context()
        pts = self.analysis_engine._get_var_pts(ctx, var_name)
        return pts.objects if isinstance(pts, PointsToSet) else set()
    
    def get_call_graph(self) -> Dict[str, Any]:
        """Get the computed call graph."""
        if not self.results:
            return {}
        return self.results.get("call_graph", {})
    
    def get_statistics(self) -> Dict[str, Any]:
        """Get analysis statistics."""
        if not self.results:
            return {}
        return self.results.get("statistics", {})


class PointerAnalysisDriver(AnalysisDriver):
    """Driver for pointer analysis that integrates with PythonStAn pipeline."""
    
    def __init__(self, config: AnalysisConfig):
        """Initialize pointer analysis driver."""
        super().__init__(config)
        self.analysis_class = PointerAnalysis
        self.results: Dict[str, Any] = {}
    
    def analyze(self, module: 'IRModule', prev_results: Dict[str, Any]) -> Dict[str, Any]:
        """Run pointer analysis on the given module.
        
        Note: The interface expects IRModule but we handle it as IRScope since
        IRModule is a subclass of IRScope.
        """
        from pythonstan.ir import IRModule
        from pythonstan.world import World
        
        # Get all scopes in the module (including functions and classes)
        scope_manager = World().scope_manager
        module_scopes = [module]  # Start with the module itself
        
        # Add all subscopes (functions and classes)
        subscopes = scope_manager.get_subscopes(module)
        if subscopes:
            module_scopes.extend(subscopes)
            
            # Recursively add nested scopes
            for subscope in subscopes:
                nested_scopes = scope_manager.get_subscopes(subscope)
                if nested_scopes:
                    module_scopes.extend(nested_scopes)
        
        print(f"Analyzing {len(module_scopes)} scopes in module {module.get_qualname()}")
        
        try:
            # Create unified inter-procedural analyzer with all functions
            analyzer = self.analysis_class(self.config)
            
            # Set ALL scopes in the analyzer for complete inter-procedural analysis
            print(f"Setting up unified analysis for {len(module_scopes)} scopes")
            for scope in module_scopes:
                analyzer.set_scope(scope)
            
            # Set inputs from previous analyses
            for prev_analysis_name in self.config.prev_analysis:
                if prev_analysis_name in prev_results:
                    analyzer.set_input(prev_analysis_name, prev_results[prev_analysis_name])
            
            # Run unified inter-procedural analysis
            analysis_results = analyzer.run_analysis()
            
            # Extract scope-specific results from unified analysis
            for scope in module_scopes:
                # Get scope-specific environment and heap entries
                scope_points_to = {}
                all_points_to = analysis_results.get("points_to", {})
                
                # Filter points-to entries for this scope based on context and variable names
                scope_qualname = scope.get_qualname()
                for var_ctx, objs in all_points_to.items():
                    var_name = var_ctx.split('@')[0] if '@' in var_ctx else var_ctx
                    
                    # Include if it's a global variable or relates to this scope
                    if (not '@' in var_ctx or  # Global variables
                        scope_qualname in var_ctx or  # Context contains scope name
                        var_name in ['self', 'param_0', 'param_1'] or  # Function parameters
                        var_name.startswith('$const_')):  # Constants
                        scope_points_to[var_ctx] = objs
                
                # Create scope-specific results while sharing the analyzer for call graph access
                self.results[scope] = {
                    "points_to": scope_points_to,
                    "call_graph": analysis_results.get("call_graph", {}),
                    "contexts": analysis_results.get("contexts", set()),
                    "statistics": analysis_results.get("statistics", {}),
                    "analyzer": analyzer  # Shared analyzer for unified call graph
                }
                
                print(f"Successfully analyzed scope: {scope.get_qualname()}")
                
        except Exception as e:
            print(f"Pointer analysis failed for module {module.get_qualname()}: {e}")
            import traceback
            traceback.print_exc()
            
            # Return empty results for all scopes on failure
            for scope in module_scopes:
                self.results[scope] = {
                    "points_to": {},
                    "call_graph": {},
                    "contexts": set(),
                    "statistics": {},
                    "analyzer": None,
                    "error": str(e)
                }
        
        return self.results
    
    def get_results(self) -> Dict[str, Any]:
        """Get analysis results."""
        return self.results
    
    def get_result_for_scope(self, scope: IRScope) -> Dict[str, Any]:
        """Get results for a specific scope."""
        return self.results.get(scope, {})
