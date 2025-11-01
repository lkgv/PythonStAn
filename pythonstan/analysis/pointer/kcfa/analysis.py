"""Main k-CFA pointer analysis driver.

This module provides the main entry point for running pointer analysis.
"""

import logging
from typing import Optional, List, Any, TYPE_CHECKING

if TYPE_CHECKING:
    from .config import Config
    from .solver_interface import ISolverQuery

__all__ = ["PointerAnalysis", "AnalysisResult"]


class PointerAnalysis:
    """Main entry point for k-CFA pointer analysis.
    
    Orchestrates all components: IR translation, constraint generation,
    solving, and result querying.
    
    Example:
        >>> config = Config(context_policy="2-cfa")
        >>> analysis = PointerAnalysis(config)
        >>> result = analysis.analyze(ir_module)
        >>> pts = result.query().points_to(var)
    """
    
    def __init__(self, config: Optional['Config'] = None):
        """Initialize pointer analysis.
        
        Args:
            config: Analysis configuration. If None, uses default.
        """
        from .config import Config
        
        self.config = config or Config()
        self._result: Optional['AnalysisResult'] = None
        self._setup_logging()
    
    def analyze(
        self,
        module: Any,
        entry_points: Optional[List[str]] = None
    ) -> 'AnalysisResult':
        """Run pointer analysis on module.
        
        Args:
            module: IR module to analyze
            entry_points: List of entry point function names.
                         If None, analyzes all module-level code.
        
        Returns:
            AnalysisResult containing points-to information and call graph
        """
        import logging
        from .state import PointerAnalysisState
        from .solver import PointerSolver
        from .ir_translator import IRTranslator
        from .context_selector import ContextSelector, parse_policy
        
        logger = logging.getLogger(__name__)
        logger.info("Starting pointer analysis")
        
        # 1. Create analysis state
        state = PointerAnalysisState()
        
        # 2. Create context selector
        policy = parse_policy(self.config.context_policy)
        context_selector = ContextSelector(policy=policy)
        
        # 3. Get empty context for module-level analysis
        empty_context = context_selector.empty_context()
        
        # 4. Create module finder and IR translator
        from .module_finder import ModuleFinder
        module_finder = ModuleFinder(self.config)
        translator = IRTranslator(self.config, module_finder=module_finder)
        
        # 5. Translate ALL scopes to constraints (not just entry module)
        logger.info("Translating all scopes to constraints...")
        constraints = []
        
        # Get all scopes from World's scope manager
        from pythonstan.world import World
        world = World()
        all_scopes = world.scope_manager.get_scopes()
        
        logger.info(f"Found {len(all_scopes)} scopes to analyze")
        
        # Translate each scope
        from pythonstan.ir.ir_statements import IRFunc, IRClass, IRModule
        
        for scope in all_scopes:
            try:
                scope_name = scope.get_qualname()
                
                if isinstance(scope, IRFunc):
                    logger.debug(f"Translating function: {scope_name}")
                    func_constraints = translator.translate_function(scope, empty_context)
                    constraints.extend(func_constraints)
                    
                elif isinstance(scope, IRModule):
                    logger.debug(f"Translating module: {scope_name}")
                    module_constraints = translator.translate_module(scope, empty_context)
                    constraints.extend(module_constraints)
                    
                elif isinstance(scope, IRClass):
                    logger.debug(f"Translating class: {scope_name}")
                    # Classes are handled as part of their containing module/function
                    pass
                    
            except Exception as e:
                raise e
                logger.warning(f"Error translating scope {scope.get_qualname()}: {e}")
                continue
        
        logger.info(f"Total constraints generated: {len(constraints)}")
        
        # 6. Create class hierarchy manager
        from .class_hierarchy import ClassHierarchyManager
        class_hierarchy = ClassHierarchyManager()
        
        # 7. Create builtin summary manager
        from .builtin_api_handler import BuiltinSummaryManager
        builtin_manager = BuiltinSummaryManager(self.config)
        
        # 8. Build function registry (empty for now, will be populated by solver)
        function_registry = {}
        
        # 9. Create solver with full dependencies
        solver = PointerSolver(
            state=state,
            config=self.config,
            ir_translator=translator,
            context_selector=context_selector,
            function_registry=function_registry,
            class_hierarchy=class_hierarchy,
            builtin_manager=builtin_manager
        )
        
        # 10. Add all constraints to solver
        for constraint in constraints:
            solver.add_constraint(constraint)
        
        # 11. Solve to fixpoint
        solver.solve_to_fixpoint()
        
        # 12. Create and return result
        solver_query = solver.query()
        result = AnalysisResult(solver_query)
        self._result = result
        
        logger.info("Analysis complete")
        return result
    
    def query(self) -> 'ISolverQuery':
        """Get query interface for last analysis.
        
        Returns:
            Query interface for retrieving analysis results
        
        Raises:
            RuntimeError: If analyze() hasn't been called yet
        """
        if self._result is None:
            raise RuntimeError("Must call analyze() before query()")
        
        return self._result.query()
    
    def _setup_logging(self):
        """Setup logging configuration."""
        logging.basicConfig(
            level=getattr(logging, self.config.log_level),
            format='%(levelname)s: %(message)s'
        )


class AnalysisResult:
    """Container for analysis results.
    
    Provides access to points-to information, call graph, and statistics.
    """
    
    def __init__(self, solver_query: 'ISolverQuery'):
        """Initialize analysis result.
        
        Args:
            solver_query: Query interface from solver
        """
        self._query = solver_query
    
    def query(self) -> 'ISolverQuery':
        """Get query interface.
        
        Returns:
            Query interface for this result
        """
        return self._query
    
    def get_statistics(self):
        """Get analysis statistics.
        
        Returns:
            Dictionary with analysis statistics
        """
        return self._query.get_statistics()

