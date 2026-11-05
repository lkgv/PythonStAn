"""Pytest fixtures for k-CFA precision tests."""

import pytest
import ast
from typing import List, Optional
from pythonstan.analysis.pointer.kcfa.analysis import PointerAnalysis
from pythonstan.analysis.pointer.kcfa.config import Config


class CallGraphChecker:
    """Helper class for checking call graph results."""
    
    def __init__(self, result):
        """Initialize checker with analysis result.
        
        Args:
            result: PointerAnalysis result object
        """
        self.result = result
        self.call_graph = result.query().call_graph()
        self._module_name = self._infer_module_name()

    def _infer_module_name(self) -> Optional[str]:
        """Infer the entry module name from call graph edges."""
        for edge in self.call_graph.edges:
            caller_name = self._get_function_name(edge.callsite)
            if caller_name and "." not in caller_name:
                return caller_name
        if self.call_graph.edges:
            caller_name = self._get_function_name(self.call_graph.edges[0].callsite)
            if caller_name:
                return caller_name.split(".")[0]
        return None
    
    def get_call_edges(self, caller: Optional[str] = None, callee: Optional[str] = None) -> List:
        """Get call edges matching criteria.
        
        Args:
            caller: Optional caller function name to filter by
            callee: Optional callee function name to filter by
        
        Returns:
            List of matching call edges
        """
        if caller in {"__main__", "module"} and self._module_name:
            caller = self._module_name
        edges = []
        for edge in self.call_graph.edges:
            caller_name = self._get_function_name(edge.callsite)
            callee_name = self._get_function_name(edge.callee)
            
            if caller is not None and caller not in caller_name:
                continue
            if callee is not None and callee not in callee_name:
                continue
            
            edges.append(edge)
        
        return edges
    
    def _get_function_name(self, node) -> str:
        """Extract function name from call graph node."""
        if hasattr(node, 'scope'):
            scope = node.scope
            if hasattr(scope, 'stmt'):
                stmt = scope.stmt
                if hasattr(stmt, 'get_qualname'):
                    return str(stmt.get_qualname())
                if hasattr(stmt, 'name'):
                    return str(stmt.name)
                return str(stmt)
        if hasattr(node, 'stmt'):
            stmt = node.stmt
            if hasattr(stmt, 'get_qualname'):
                return str(stmt.get_qualname())
            if hasattr(stmt, 'name'):
                return str(stmt.name)
            return str(stmt)
        return str(node)
    
    def assert_call_edge(self, caller: str, callee: str):
        """Assert that a call edge exists from caller to callee.
        
        Args:
            caller: Caller function name
            callee: Callee function name
        
        Raises:
            AssertionError: If no matching edge found
        """
        edges = self.get_call_edges(caller=caller, callee=callee)
        
        if not edges:
            # Print debug info
            all_edges = self.get_call_edges()
            print(f"\nExpected call edge: {caller} -> {callee}")
            print(f"Total call edges found: {len(all_edges)}")
            if all_edges:
                print("Available edges:")
                for edge in all_edges[:20]:  # Show first 20
                    caller_name = self._get_function_name(edge.callsite)
                    callee_name = self._get_function_name(edge.callee)
                    print(f"  {caller_name} -> {callee_name}")
                if len(all_edges) > 20:
                    print(f"  ... and {len(all_edges) - 20} more")
            
            raise AssertionError(f"Expected call edge not found: {caller} -> {callee}")
    
    def assert_no_call_edge(self, caller: str, callee: str):
        """Assert that NO call edge exists from caller to callee.
        
        Args:
            caller: Caller function name
            callee: Callee function name
        
        Raises:
            AssertionError: If matching edge is found
        """
        edges = self.get_call_edges(caller=caller, callee=callee)
        
        if edges:
            raise AssertionError(
                f"Unexpected call edge found: {caller} -> {callee} "
                f"({len(edges)} edge(s))"
            )
    
    def assert_call_count(self, min_count: Optional[int] = None, max_count: Optional[int] = None):
        """Assert total call edge count is within bounds.
        
        Args:
            min_count: Minimum expected edges (inclusive)
            max_count: Maximum expected edges (inclusive)
        
        Raises:
            AssertionError: If count is out of bounds
        """
        all_edges = self.get_call_edges()
        count = len(all_edges)
        
        if min_count is not None and count < min_count:
            raise AssertionError(f"Too few call edges: {count} < {min_count}")
        
        if max_count is not None and count > max_count:
            raise AssertionError(f"Too many call edges: {count} > {max_count}")
    
    def print_all_edges(self):
        """Print all call edges for debugging."""
        all_edges = self.get_call_edges()
        print(f"\nTotal call edges: {len(all_edges)}")
        for edge in all_edges:
            caller_name = self._get_function_name(edge.callsite)
            callee_name = self._get_function_name(edge.callee)
            print(f"  {caller_name} -> {callee_name}")


@pytest.fixture
def run_analysis():
    """Fixture for running pointer analysis on code snippets.
    
    Returns a factory function that takes code and returns a CallGraphChecker.
    
    Usage:
        def test_something(run_analysis):
            code = '''
            def foo(): pass
            def bar(): foo()
            bar()
            '''
            checker = run_analysis(code)
            checker.assert_call_edge("bar", "foo")
    """
    def _run_analysis(
        code: str,
        context_policy: str = "2-cfa",
        enable_debug_monitor: bool = False,
        **config_kwargs
    ) -> CallGraphChecker:
        """Run pointer analysis on code snippet.
        
        Args:
            code: Python source code to analyze
            context_policy: Context sensitivity policy (default: 2-cfa)
            enable_debug_monitor: Enable debug monitoring
            **config_kwargs: Additional config options
        
        Returns:
            CallGraphChecker for asserting on results
        """
        from pythonstan.analysis import AnalysisConfig
        from pythonstan.ir import IRModule
        from pythonstan.world import World
        
        # Setup World singleton if not already done
        try:
            _ = World().scope_manager
        except AttributeError:
            World().setup()
        
        # Build scope manager if needed
        if not hasattr(World().scope_manager, 'scopes'):
            World().scope_manager.build()
        
        # Parse code to AST
        tree = ast.parse(code)
        
        # Convert to IRModule
        ir_module = IRModule(qualname="test", module=tree, name="test", filename="test.py")
        
        # Set as entry module in World
        World().set_entry_module(ir_module)
        
        # Preprocess the module: Three-Address Form -> IR
        from pythonstan.analysis.transform.three_address import ThreeAddressTransformer
        from pythonstan.analysis.transform.ir import IRTransformer
        
        # Apply three-address transformation
        tac_transformer = ThreeAddressTransformer()
        tac_module = tac_transformer.visit(tree)
        World().scope_manager.set_ir(ir_module, "three address form", tac_module)
        
        # Apply IR transformation
        ir_transformer = IRTransformer(ir_module)
        ir_transformer.process_stmts(tac_module.body)
        ir_stmts = ir_transformer.get_stmts()
        World().scope_manager.set_ir(ir_module, "ir", ir_stmts)
        
        # Create k-CFA config
        kcfa_config = Config(
            context_policy=context_policy,
            max_iterations=50000,
            verbose=False,
            log_level="WARNING",  # Reduce noise in tests
            track_unknowns=True,
            build_class_hierarchy=True,
            use_mro_resolution=True,
            index_sensitive=True,
            enable_debug_monitor=enable_debug_monitor,
            **config_kwargs
        )
        
        # Wrap in AnalysisConfig
        analysis_config = AnalysisConfig(
            name="test_pointer_analysis",
            id="PointerAnalysis",
            description="Test pointer analysis",
            prev_analysis=["ir"],
            options=kcfa_config.to_dict()
        )
        
        # Run analysis
        analysis = PointerAnalysis(analysis_config)
        result = analysis.analyze(ir_module, {})
        
        return CallGraphChecker(result)
    
    return _run_analysis


