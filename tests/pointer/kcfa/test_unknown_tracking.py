"""Tests for unknown resolution tracking in pointer analysis.

Tests verify that unknown/unresolved calls and allocations are:
- Tracked with appropriate categories
- Create conservative unknown objects
- Provide statistics via query interface
- Support configurable logging
"""

import pytest
from pythonstan.analysis.pointer.kcfa.config import Config
from pythonstan.analysis.pointer.kcfa.state import PointerAnalysisState
from pythonstan.analysis.pointer.kcfa.solver import PointerSolver
from pythonstan.analysis.pointer.kcfa.object import AllocSite, AllocKind, AbstractObject
from pythonstan.analysis.pointer.kcfa.variable import Variable, Scope, VariableKind
from pythonstan.analysis.pointer.kcfa.context import CallStringContext
from pythonstan.analysis.pointer.kcfa.constraints import CallConstraint, AllocConstraint
from pythonstan.analysis.pointer.kcfa.unknown_tracker import UnknownKind, UnknownTracker


class TestUnknownTracker:
    """Test UnknownTracker class."""
    
    def test_record_unknown(self):
        """Test recording unknown resolution failure."""
        tracker = UnknownTracker()
        
        tracker.record(
            UnknownKind.CALLEE_EMPTY,
            "test.py:42",
            "Empty callee points-to set"
        )
        
        assert len(tracker.records) == 1
        assert tracker.records[0].kind == UnknownKind.CALLEE_EMPTY
        assert tracker.records[0].location == "test.py:42"
        assert "Empty callee" in tracker.records[0].message
    
    def test_get_summary(self):
        """Test summary statistics."""
        tracker = UnknownTracker()
        
        tracker.record(UnknownKind.CALLEE_EMPTY, "loc1", "msg1")
        tracker.record(UnknownKind.CALLEE_EMPTY, "loc2", "msg2")
        tracker.record(UnknownKind.CALLEE_NON_CALLABLE, "loc3", "msg3")
        
        summary = tracker.get_summary()
        
        assert summary["total_unknowns"] == 3
        assert summary["unknown_callee_empty"] == 2
        assert summary["unknown_callee_non_callable"] == 1
    
    def test_get_detailed_report(self):
        """Test detailed report generation."""
        tracker = UnknownTracker()
        
        tracker.record(
            UnknownKind.FUNCTION_NOT_IN_REGISTRY,
            "test.py:100",
            "Function 'foo' not found",
            context="foo"
        )
        
        report = tracker.get_detailed_report()
        
        assert len(report) == 1
        assert report[0]["kind"] == "function_not_in_registry"
        assert report[0]["location"] == "test.py:100"
        assert "foo" in report[0]["message"]
        assert report[0]["context"] == "foo"


class TestEmptyCalleeTracking:
    """Test tracking of calls with empty callee points-to set."""
    
    def test_empty_callee_creates_unknown_object(self):
        """Test that calling empty callee creates unknown object."""
        config = Config(verbose=True)
        state = PointerAnalysisState()
        solver = PointerSolver(state, config)
        
        # Create call with empty callee
        scope = Scope(name="test", kind="function")
        context = CallStringContext(call_sites=(), k=2)
        
        callee_var = Variable("func", scope, context, VariableKind.LOCAL)
        target_var = Variable("result", scope, context, VariableKind.LOCAL)
        
        call = CallConstraint(
            callee=callee_var,
            args=(),
            target=target_var,
            call_site="test:42"
        )
        
        solver.add_constraint(call)
        solver.solve_to_fixpoint()
        
        # Check unknown was tracked
        query = solver.query()
        unknown_summary = query.get_unknown_summary()
        
        assert unknown_summary["total_unknowns"] >= 1
        assert "unknown_callee_empty" in unknown_summary
        
        # Check conservative object was created
        pts = state.get_points_to(target_var)
        assert not pts.is_empty()
        
        # Verify it's an UNKNOWN object
        for obj in pts:
            assert obj.kind == AllocKind.UNKNOWN


class TestNonCallableTracking:
    """Test tracking of calls to non-callable objects."""
    
    def test_non_callable_creates_unknown_object(self):
        """Test that calling non-callable creates unknown object."""
        config = Config(verbose=True)
        state = PointerAnalysisState()
        solver = PointerSolver(state, config)
        
        # Create non-callable object (LIST)
        scope = Scope(name="test", kind="function")
        context = CallStringContext(call_sites=(), k=2)
        
        callee_var = Variable("list_obj", scope, context, VariableKind.LOCAL)
        target_var = Variable("result", scope, context, VariableKind.LOCAL)
        
        # Allocate list object
        list_alloc = AllocSite(
            file="test.py",
            line=10,
            col=0,
            kind=AllocKind.LIST,
            name="my_list"
        )
        alloc_constraint = AllocConstraint(target=callee_var, alloc_site=list_alloc)
        
        # Call the list object
        call = CallConstraint(
            callee=callee_var,
            args=(),
            target=target_var,
            call_site="test:50"
        )
        
        solver.add_constraint(alloc_constraint)
        solver.add_constraint(call)
        solver.solve_to_fixpoint()
        
        # Check unknown was tracked
        query = solver.query()
        unknown_summary = query.get_unknown_summary()
        
        assert unknown_summary["total_unknowns"] >= 1
        assert "unknown_callee_non_callable" in unknown_summary
        
        # Check conservative object was created
        pts = state.get_points_to(target_var)
        assert not pts.is_empty()


class TestFunctionNotInRegistryTracking:
    """Test tracking of function calls where function not in registry."""
    
    def test_function_not_in_registry_creates_unknown(self):
        """Test function not in registry creates unknown object."""
        config = Config(verbose=True)
        state = PointerAnalysisState()
        
        # Empty function registry
        solver = PointerSolver(
            state,
            config,
            ir_translator=None,
            context_selector=None,
            function_registry={}
        )
        
        # Create function object
        scope = Scope(name="test", kind="function")
        context = CallStringContext(call_sites=(), k=2)
        
        callee_var = Variable("some_func", scope, context, VariableKind.LOCAL)
        target_var = Variable("result", scope, context, VariableKind.LOCAL)
        
        # Allocate function object
        func_alloc = AllocSite(
            file="test.py",
            line=20,
            col=0,
            kind=AllocKind.FUNCTION,
            name="missing_func"
        )
        alloc_constraint = AllocConstraint(target=callee_var, alloc_site=func_alloc)
        
        # Call the function
        call = CallConstraint(
            callee=callee_var,
            args=(),
            target=target_var,
            call_site="test:60"
        )
        
        solver.add_constraint(alloc_constraint)
        solver.add_constraint(call)
        solver.solve_to_fixpoint()
        
        # Check unknown was tracked
        query = solver.query()
        unknown_details = query.get_unknown_details()
        
        # Should have tracked either empty callee or function not in registry
        # (depending on solver iteration order)
        assert len(unknown_details) >= 1
        
        # Verify at least one relevant unknown type
        unknown_types = {u["kind"] for u in unknown_details}
        assert ("callee_empty" in unknown_types or 
                "function_not_in_registry" in unknown_types or
                "missing_dependencies" in unknown_types)


class TestQueryIntegration:
    """Test integration with query interface."""
    
    def test_get_statistics_includes_unknowns(self):
        """Test that get_statistics includes unknown counts."""
        config = Config()
        state = PointerAnalysisState()
        solver = PointerSolver(state, config)
        
        # Create some unknowns
        scope = Scope(name="test", kind="function")
        context = CallStringContext(call_sites=(), k=2)
        
        callee_var = Variable("func", scope, context, VariableKind.LOCAL)
        target_var = Variable("result", scope, context, VariableKind.LOCAL)
        
        call = CallConstraint(
            callee=callee_var,
            args=(),
            target=target_var,
            call_site="test:70"
        )
        
        solver.add_constraint(call)
        solver.solve_to_fixpoint()
        
        # Get statistics
        query = solver.query()
        stats = query.get_statistics()
        
        # Should include unknown statistics
        assert "total_unknowns" in stats
        assert stats["total_unknowns"] >= 0
    
    def test_get_unknown_summary_works(self):
        """Test get_unknown_summary method."""
        config = Config()
        state = PointerAnalysisState()
        solver = PointerSolver(state, config)
        
        solver.solve_to_fixpoint()
        
        query = solver.query()
        summary = query.get_unknown_summary()
        
        assert isinstance(summary, dict)
        assert "total_unknowns" in summary
    
    def test_get_unknown_details_works(self):
        """Test get_unknown_details method."""
        config = Config()
        state = PointerAnalysisState()
        solver = PointerSolver(state, config)
        
        solver.solve_to_fixpoint()
        
        query = solver.query()
        details = query.get_unknown_details()
        
        assert isinstance(details, list)


class TestConfigOptions:
    """Test configuration options for unknown tracking."""
    
    def test_track_unknowns_default_true(self):
        """Test track_unknowns defaults to True."""
        config = Config()
        assert config.track_unknowns is True
    
    def test_log_unknown_details_default_false(self):
        """Test log_unknown_details defaults to False."""
        config = Config()
        assert config.log_unknown_details is False
    
    def test_config_accepts_unknown_options(self):
        """Test config accepts unknown tracking options."""
        config = Config(
            track_unknowns=False,
            log_unknown_details=True
        )
        
        assert config.track_unknowns is False
        assert config.log_unknown_details is True

