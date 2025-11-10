"""Main k-CFA pointer analysis driver.

This module provides the main entry point for running pointer analysis.
"""

import logging
from typing import Optional, List, Any, TYPE_CHECKING, Dict
from pythonstan.analysis import AnalysisDriver, AnalysisConfig
from pythonstan.analysis.pointer.kcfa.object import AllocKind, AllocSite
from pythonstan.ir import IRScope

if TYPE_CHECKING:
    from .config import Config
    from .solver_interface import ISolverQuery

__all__ = ["PointerAnalysis", "AnalysisResult"]

logger = logging.getLogger(__name__)


class PointerAnalysis(AnalysisDriver):
    """Main entry point for k-CFA pointer analysis.
    """
    
    def __init__(self, analysis_config: AnalysisConfig):
        """Initialize pointer analysis.
        
        Args:
            config: Analysis configuration. If None, uses default.
        """
        from .config import Config        
        from .state import PointerAnalysisState
        from .solver import PointerSolver
        from .ir_translator import IRTranslator
        from .context_selector import ContextSelector, parse_policy
        from .class_hierarchy import ClassHierarchyManager
        from .builtin_api_handler import BuiltinSummaryManager        
        from pythonstan.world import World
        
        self.config = analysis_config
        if not hasattr(self.config, 'options'):
            print(f"Analysis config {self.config} has no options")
        self.kcfa_config = Config.from_dict(self.config.options)        
        self._setup_logging()
        self._result: Optional['AnalysisResult'] = None
        self.world = World()
        self.state = PointerAnalysisState()
        policy = parse_policy(self.kcfa_config.context_policy)
        self.context_selector = ContextSelector(policy=policy)
        self.translator = IRTranslator(self.kcfa_config)
        self.class_hierarchy = ClassHierarchyManager()
        self.builtin_manager = BuiltinSummaryManager(self.kcfa_config)
        self.function_registry = {}
        
        self.solver = PointerSolver(
            state=self.state,
            config=self.kcfa_config,
            ir_translator=self.translator,
            context_selector=self.context_selector,
            function_registry=self.function_registry,
            class_hierarchy=self.class_hierarchy,
            builtin_manager=self.builtin_manager
        )

    def analyze(
        self,
        entry_scope: IRScope,
        prev_results: Dict[str, Any]
    ) -> 'AnalysisResult':
        """Run pointer analysis on module.
        
        Args:
            entry_scope: Scope to analyze
            prev_results: Results of previous analyses
        
        Returns:
            AnalysisResult containing points-to information and call graph
        """        
        logger.info("Starting pointer analysis")

        # Get empty context for module-level analysis
        empty_context = self.context_selector.empty_context()
        
        from .object import ModuleObject
        from .context import Scope

        # Translate ALL scopes to constraints (not just entry module)
        logger.info("Translating all scopes to constraints...")
        
        constraints = []
        
        scope = self.world.get_entry_module()
        
        # Make scope with context
        alloc_site = AllocSite(scope.filename, line=0, col=0,
                               kind=AllocKind.MODULE,
                               scope=None,
                               name=scope.get_qualname(),
                               stmt=None)
        module_obj = ModuleObject(alloc_site, empty_context)
        ctx_scope = Scope(scope, module_obj, empty_context, None, None)
        
        # Generate constraints
        try:
            scope_name = scope.get_qualname()
            logger.debug(f"Translating module: {scope_name}")                    
            c = self.translator.translate_module(scope)
            constraints.extend(c)
        except Exception as e:
            import traceback
            print(traceback.format_exc())
            logger.warning(f"Error translating scope {scope.get_qualname()}: {e}")        
        logger.info(f"Total constraints generated: {len(constraints)}")
        
        # Add all constraints to solver
        for constraint in constraints:
            self.solver.add_constraint(ctx_scope, empty_context, constraint)
        
        # Solve to fixpoint
        self.solver.solve_to_fixpoint()
        
        # Create and return result
        solver_query = self.solver.query()
        result = AnalysisResult(solver_query)
        self.results = result
        
        logger.info("Analysis complete")
        return result
    
    def query(self) -> 'ISolverQuery':
        """Get query interface for last analysis.
        
        Returns:
            Query interface for retrieving analysis results
        
        Raises:
            RuntimeError: If analyze() hasn't been called yet
        """
        if self.results is None:
            raise RuntimeError("Must call analyze() before query()")
        
        return self.results.query()
    
    def _setup_logging(self):
        """Setup logging configuration."""
        logging.basicConfig(
            level=getattr(logging, self.kcfa_config.log_level),
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
