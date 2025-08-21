"""Simplified end-to-end tests for k-CFA2 pointer analysis.

This module provides simplified end-to-end tests that demonstrate the complete
analysis functionality without relying on complex mock IR integration.

The tests use direct API calls to exercise the analysis engine components.
"""

import pytest
from pythonstan.analysis.pointer.kcfa2.analysis import KCFA2PointerAnalysis
from pythonstan.analysis.pointer.kcfa2.config import KCFAConfig
from pythonstan.analysis.pointer.kcfa2.context import Context, CallSite
from pythonstan.analysis.pointer.kcfa2.model import PointsToSet
from pythonstan.analysis.pointer.kcfa2.heap_model import attr_key, elem_key, value_key


class TestEndToEndAnalysis:
    """Test complete analysis workflows using direct API calls."""
    
    def test_object_creation_and_assignment(self):
        """Test object creation and variable assignment."""
        analysis = KCFA2PointerAnalysis(KCFAConfig(verbose=False))
        analysis.plan([])
        analysis.initialize()
        
        ctx = Context()
        
        # Create object: x = object()
        obj = analysis._create_object("test.py:1:1:obj", ctx)
        analysis._set_var_pts(ctx, "x", PointsToSet(frozenset([obj])))
        
        # Assignment: y = x
        analysis._constraint_worklist.add_copy_constraint("x", "y")
        analysis.run()
        
        # Verify results
        x_pts = analysis._get_var_pts(ctx, "x")
        y_pts = analysis._get_var_pts(ctx, "y")
        
        assert len(x_pts.objects) == 1
        assert x_pts == y_pts
        
    def test_attribute_operations(self):
        """Test attribute store and load operations."""
        analysis = KCFA2PointerAnalysis(KCFAConfig(verbose=False))
        analysis.plan([])
        analysis.initialize()
        
        ctx = Context()
        
        # Create objects: obj = object(); value = object()
        obj_obj = analysis._create_object("test.py:1:1:obj", ctx)
        value_obj = analysis._create_object("test.py:2:1:obj", ctx)
        
        analysis._set_var_pts(ctx, "obj", PointsToSet(frozenset([obj_obj])))
        analysis._set_var_pts(ctx, "value", PointsToSet(frozenset([value_obj])))
        
        # Store attribute: obj.attr = value
        analysis._constraint_worklist.add_store_constraint("obj", "attr", "value")
        
        # Load attribute: result = obj.attr
        analysis._constraint_worklist.add_load_constraint("obj", "attr", "result")
        
        analysis.run()
        
        # Verify results
        result_pts = analysis._get_var_pts(ctx, "result")
        value_pts = analysis._get_var_pts(ctx, "value")
        
        assert len(result_pts.objects) >= 1
        assert result_pts == value_pts
        
    def test_container_operations(self):
        """Test container element operations."""
        analysis = KCFA2PointerAnalysis(KCFAConfig(verbose=False))
        analysis.plan([])
        analysis.initialize()
        
        ctx = Context()
        
        # Create list and elements
        list_obj = analysis._create_object("test.py:1:1:list", ctx)
        elem1_obj = analysis._create_object("test.py:2:1:obj", ctx)
        elem2_obj = analysis._create_object("test.py:3:1:obj", ctx)
        
        analysis._set_var_pts(ctx, "lst", PointsToSet(frozenset([list_obj])))
        analysis._set_var_pts(ctx, "elem1", PointsToSet(frozenset([elem1_obj])))
        analysis._set_var_pts(ctx, "elem2", PointsToSet(frozenset([elem2_obj])))
        
        # Store elements: lst[0] = elem1; lst[1] = elem2
        elem_field = elem_key()
        combined_elems = PointsToSet(frozenset([elem1_obj, elem2_obj]))
        analysis._set_field_pts(list_obj, elem_field, combined_elems)
        
        # Load element: result = lst[0] 
        analysis._constraint_worklist.add_load_constraint("lst", "elem", "result")
        
        analysis.run()
        
        # Verify results
        result_pts = analysis._get_var_pts(ctx, "result")
        assert len(result_pts.objects) >= 1
        
    def test_function_call_simulation(self):
        """Test function call with context creation."""
        analysis = KCFA2PointerAnalysis(KCFAConfig(k=2, verbose=False))
        analysis.plan([])
        analysis.initialize()
        
        ctx = Context()
        
        # Create argument object
        arg_obj = analysis._create_object("test.py:1:1:obj", ctx)
        analysis._set_var_pts(ctx, "arg", PointsToSet(frozenset([arg_obj])))
        
        # Simulate function call with context
        call_site = CallSite("test.py:2:1:call", "main")
        callee_ctx = analysis._context_selector.push(ctx, call_site)
        
        # Add call graph edge
        analysis._call_graph.add_edge(ctx, call_site, callee_ctx, "identity")
        
        # Parameter passing: param = arg (in callee context)
        analysis._constraint_worklist.add_copy_constraint("arg", "param")
        
        # Return: return_val = param (back to caller)
        analysis._constraint_worklist.add_copy_constraint("param", "return_val")
        
        analysis.run()
        
        # Verify call graph
        call_graph_stats = analysis._call_graph.get_statistics()
        assert call_graph_stats["unique_call_sites"] >= 1
        
        # Verify parameter flow
        arg_pts = analysis._get_var_pts(ctx, "arg")
        return_pts = analysis._get_var_pts(ctx, "return_val")
        assert len(arg_pts.objects) == 1
        assert len(return_pts.objects) >= 1
        
    def test_builtin_function_integration(self):
        """Test integration with builtin functions."""
        analysis = KCFA2PointerAnalysis(KCFAConfig(verbose=False))
        analysis.plan([])
        analysis.initialize()
        
        ctx = Context()
        
        # Create list object
        list_obj = analysis._create_object("test.py:1:1:list", ctx)
        analysis._set_var_pts(ctx, "lst", PointsToSet(frozenset([list_obj])))
        
        # Test len() builtin
        len_summary = analysis._builtin_summaries.get_summary("len")
        if len_summary:
            len_summary.apply("length", ["lst"], ctx, analysis)
        
        # Test iter() builtin
        iter_summary = analysis._builtin_summaries.get_summary("iter")
        if iter_summary:
            iter_summary.apply("iterator", ["lst"], ctx, analysis)
        
        analysis.run()
        
        # Verify builtin results
        length_pts = analysis._get_var_pts(ctx, "length")
        iterator_pts = analysis._get_var_pts(ctx, "iterator")
        
        assert len(length_pts.objects) == 1  # len() returns integer
        assert len(iterator_pts.objects) == 1  # iter() returns iterator
        
    def test_complex_object_graph(self):
        """Test analysis with complex interconnected objects."""
        analysis = KCFA2PointerAnalysis(KCFAConfig(verbose=False))
        analysis.plan([])
        analysis.initialize()
        
        ctx = Context()
        
        # Create interconnected objects: node1 -> node2 -> node3 -> node1
        node1 = analysis._create_object("test.py:1:1:obj", ctx)
        node2 = analysis._create_object("test.py:2:1:obj", ctx)
        node3 = analysis._create_object("test.py:3:1:obj", ctx)
        
        analysis._set_var_pts(ctx, "node1", PointsToSet(frozenset([node1])))
        analysis._set_var_pts(ctx, "node2", PointsToSet(frozenset([node2])))
        analysis._set_var_pts(ctx, "node3", PointsToSet(frozenset([node3])))
        
        # Create circular references
        next_field = attr_key("next")
        analysis._set_field_pts(node1, next_field, PointsToSet(frozenset([node2])))
        analysis._set_field_pts(node2, next_field, PointsToSet(frozenset([node3])))
        analysis._set_field_pts(node3, next_field, PointsToSet(frozenset([node1])))
        
        analysis.run()
        
        # Verify circular structure
        results = analysis.results()
        assert results["statistics"]["objects_created"] == 3
        assert results["heap_size"] == 3  # Three field assignments
        
    def test_context_sensitivity(self):
        """Test k-CFA context sensitivity."""
        analysis = KCFA2PointerAnalysis(KCFAConfig(k=2, verbose=False))
        analysis.plan([])
        analysis.initialize()
        
        ctx = Context()
        
        # Create multiple call sites to same function
        obj1 = analysis._create_object("test.py:1:1:obj", ctx)
        obj2 = analysis._create_object("test.py:2:1:obj", ctx)
        
        analysis._set_var_pts(ctx, "obj1", PointsToSet(frozenset([obj1])))
        analysis._set_var_pts(ctx, "obj2", PointsToSet(frozenset([obj2])))
        
        # Two different call sites
        call_site1 = CallSite("test.py:3:1:call1", "main")
        call_site2 = CallSite("test.py:4:1:call2", "main")
        
        ctx1 = analysis._context_selector.push(ctx, call_site1)
        ctx2 = analysis._context_selector.push(ctx, call_site2)
        
        # Add different call graph edges
        analysis._call_graph.add_edge(ctx, call_site1, ctx1, "func")
        analysis._call_graph.add_edge(ctx, call_site2, ctx2, "func")
        
        analysis.run()
        
        # Verify contexts are different
        assert ctx1 != ctx2
        
        # Verify call graph captured both calls
        call_graph_stats = analysis._call_graph.get_statistics()
        assert call_graph_stats["unique_call_sites"] >= 2
        
    def test_scalability(self):
        """Test analysis scalability with moderate load."""
        analysis = KCFA2PointerAnalysis(KCFAConfig(verbose=False))
        analysis.plan([])
        analysis.initialize()
        
        ctx = Context()
        num_objects = 20
        
        # Create many objects and constraints
        objects = []
        for i in range(num_objects):
            obj = analysis._create_object(f"test.py:{i}:1:obj", ctx)
            objects.append(obj)
            analysis._set_var_pts(ctx, f"var{i}", PointsToSet(frozenset([obj])))
            
            # Add copy constraints
            if i > 0:
                analysis._constraint_worklist.add_copy_constraint(f"var{i-1}", f"alias{i}")
        
        analysis.run()
        
        # Verify scalability
        results = analysis.results()
        assert results["statistics"]["objects_created"] == num_objects
        assert results["statistics"]["constraints_processed"] >= num_objects - 1


class TestAnalysisRobustness:
    """Test analysis robustness and edge cases."""
    
    def test_empty_analysis(self):
        """Test analysis with no objects or constraints."""
        analysis = KCFA2PointerAnalysis(KCFAConfig(verbose=False))
        analysis.plan([])
        analysis.initialize()
        analysis.run()
        
        results = analysis.results()
        assert results["statistics"]["objects_created"] == 0
        assert results["statistics"]["constraints_processed"] == 0
        
    def test_single_object(self):
        """Test analysis with single object."""
        analysis = KCFA2PointerAnalysis(KCFAConfig(verbose=False))
        analysis.plan([])
        analysis.initialize()
        
        ctx = Context()
        obj = analysis._create_object("test.py:1:1:obj", ctx)
        analysis._set_var_pts(ctx, "x", PointsToSet(frozenset([obj])))
        
        analysis.run()
        
        results = analysis.results()
        assert results["statistics"]["objects_created"] == 1
        
        x_pts = analysis._get_var_pts(ctx, "x")
        assert len(x_pts.objects) == 1
        
    def test_constraint_convergence(self):
        """Test that constraints converge to fixpoint."""
        analysis = KCFA2PointerAnalysis(KCFAConfig(verbose=False))
        analysis.plan([])
        analysis.initialize()
        
        ctx = Context()
        
        # Create constraint cycle: x = y, y = z, z = x
        obj = analysis._create_object("test.py:1:1:obj", ctx)
        analysis._set_var_pts(ctx, "x", PointsToSet(frozenset([obj])))
        
        analysis._constraint_worklist.add_copy_constraint("x", "y")
        analysis._constraint_worklist.add_copy_constraint("y", "z")
        analysis._constraint_worklist.add_copy_constraint("z", "x")
        
        analysis.run()
        
        # All variables should have same points-to set
        x_pts = analysis._get_var_pts(ctx, "x")
        y_pts = analysis._get_var_pts(ctx, "y")
        z_pts = analysis._get_var_pts(ctx, "z")
        
        assert x_pts == y_pts == z_pts
        assert len(x_pts.objects) == 1


# Test fixtures
@pytest.fixture
def empty_analysis():
    """Provide an empty analysis instance."""
    analysis = KCFA2PointerAnalysis(KCFAConfig(verbose=False))
    analysis.plan([])
    analysis.initialize()
    return analysis


@pytest.fixture
def context_sensitive_analysis():
    """Provide a context-sensitive analysis instance."""
    analysis = KCFA2PointerAnalysis(KCFAConfig(k=2, obj_depth=2, verbose=False))
    analysis.plan([])
    analysis.initialize()
    return analysis


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
