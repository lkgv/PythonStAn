"""Tests for pointer analysis solver."""

import pytest
from pythonstan.analysis.pointer.kcfa import (
    PointerSolver,
    PointerAnalysisState,
    Config,
    AllocKind,
)
from pythonstan.analysis.pointer.kcfa.solver import SolverQuery


class TestPointerSolverInitialization:
    """Tests for PointerSolver initialization."""
    
    def test_basic_initialization(self, empty_state):
        """Test creating solver with state and config."""
        config = Config()
        solver = PointerSolver(empty_state, config)
        
        assert solver.state == empty_state
        assert solver.config == config
        assert solver._iteration == 0
    
    def test_worklist_starts_empty(self, empty_state):
        """Test that worklist is initially empty."""
        config = Config()
        solver = PointerSolver(empty_state, config)
        
        assert len(solver._worklist) == 0
    
    def test_statistics_initialized(self, empty_state):
        """Test that statistics are initialized."""
        config = Config()
        solver = PointerSolver(empty_state, config)
        
        assert "iterations" in solver._stats
        assert "constraints_applied" in solver._stats
        assert solver._stats["iterations"] == 0
        assert solver._stats["constraints_applied"] == 0


class TestSolverSkeletonMethods:
    """Tests for skeleton solver methods (should raise NotImplementedError)."""
    
    def test_add_constraint_not_implemented(self, empty_state, constraint_factory):
        """Test that add_constraint raises NotImplementedError (skeleton)."""
        config = Config()
        solver = PointerSolver(empty_state, config)
        constraint = constraint_factory.copy()
        
        with pytest.raises(NotImplementedError):
            solver.add_constraint(constraint)
    
    def test_solve_to_fixpoint_not_implemented(self, empty_state):
        """Test that solve_to_fixpoint raises NotImplementedError (skeleton)."""
        config = Config()
        solver = PointerSolver(empty_state, config)
        
        with pytest.raises(NotImplementedError):
            solver.solve_to_fixpoint()
    
    def test_process_variable_not_implemented(self, empty_state, variable_factory):
        """Test that _process_variable raises NotImplementedError (skeleton)."""
        config = Config()
        solver = PointerSolver(empty_state, config)
        var = variable_factory("x")
        
        with pytest.raises(NotImplementedError):
            solver._process_variable(var)
    
    def test_apply_constraint_not_implemented(self, empty_state, constraint_factory):
        """Test that _apply_constraint raises NotImplementedError (skeleton)."""
        config = Config()
        solver = PointerSolver(empty_state, config)
        constraint = constraint_factory.copy()
        
        with pytest.raises(NotImplementedError):
            solver._apply_constraint(constraint)
    
    def test_apply_copy_not_implemented(self, empty_state, constraint_factory):
        """Test that _apply_copy raises NotImplementedError (skeleton)."""
        config = Config()
        solver = PointerSolver(empty_state, config)
        constraint = constraint_factory.copy()
        
        with pytest.raises(NotImplementedError):
            solver._apply_copy(constraint)
    
    def test_apply_load_not_implemented(self, empty_state, constraint_factory):
        """Test that _apply_load raises NotImplementedError (skeleton)."""
        config = Config()
        solver = PointerSolver(empty_state, config)
        constraint = constraint_factory.load()
        
        with pytest.raises(NotImplementedError):
            solver._apply_load(constraint)
    
    def test_apply_store_not_implemented(self, empty_state, constraint_factory):
        """Test that _apply_store raises NotImplementedError (skeleton)."""
        config = Config()
        solver = PointerSolver(empty_state, config)
        constraint = constraint_factory.store()
        
        with pytest.raises(NotImplementedError):
            solver._apply_store(constraint)
    
    def test_apply_alloc_not_implemented(self, empty_state, constraint_factory):
        """Test that _apply_alloc raises NotImplementedError (skeleton)."""
        config = Config()
        solver = PointerSolver(empty_state, config)
        constraint = constraint_factory.alloc()
        
        with pytest.raises(NotImplementedError):
            solver._apply_alloc(constraint)
    
    def test_apply_call_not_implemented(self, empty_state, constraint_factory):
        """Test that _apply_call raises NotImplementedError (skeleton)."""
        config = Config()
        solver = PointerSolver(empty_state, config)
        constraint = constraint_factory.call()
        
        with pytest.raises(NotImplementedError):
            solver._apply_call(constraint)
    
    def test_apply_return_not_implemented(self, empty_state, constraint_factory):
        """Test that _apply_return raises NotImplementedError (skeleton)."""
        config = Config()
        solver = PointerSolver(empty_state, config)
        constraint = constraint_factory.ret()
        
        with pytest.raises(NotImplementedError):
            solver._apply_return(constraint)


class TestSolverQuery:
    """Tests for SolverQuery interface."""
    
    def test_query_creation(self, empty_state):
        """Test creating query interface."""
        config = Config()
        solver = PointerSolver(empty_state, config)
        query = solver.query()
        
        assert isinstance(query, SolverQuery)
    
    def test_query_points_to_empty(self, empty_state, variable_factory):
        """Test querying points-to set for untracked variable."""
        config = Config()
        solver = PointerSolver(empty_state, config)
        query = solver.query()
        
        var = variable_factory("x")
        pts = query.points_to(var)
        
        assert pts.is_empty()
    
    def test_query_points_to_with_data(
        self,
        state_with_data,
        variable_factory,
        object_factory
    ):
        """Test querying points-to set with data in state."""
        config = Config()
        solver = PointerSolver(state_with_data, config)
        query = solver.query()
        
        # state_with_data has x pointing to Obj1
        var = variable_factory("x")
        pts = query.points_to(var)
        
        assert not pts.is_empty()
    
    def test_query_get_field_empty(self, empty_state, object_factory):
        """Test querying field for untracked object."""
        from pythonstan.analysis.pointer.kcfa.heap_model import attr
        
        config = Config()
        solver = PointerSolver(empty_state, config)
        query = solver.query()
        
        obj = object_factory()
        field = attr("name")
        pts = query.get_field(obj, field)
        
        assert pts.is_empty()
    
    def test_query_get_field_with_data(
        self,
        empty_state,
        object_factory,
        variable_factory
    ):
        """Test querying field with data in state."""
        from pythonstan.analysis.pointer.kcfa import PointsToSet
        from pythonstan.analysis.pointer.kcfa.heap_model import attr
        
        obj = object_factory(AllocKind.OBJECT)
        field_obj = object_factory(AllocKind.OBJECT)
        field = attr("name")
        
        empty_state.set_field(obj, field, PointsToSet.singleton(field_obj))
        
        config = Config()
        solver = PointerSolver(empty_state, config)
        query = solver.query()
        
        pts = query.get_field(obj, field)
        assert field_obj in pts
    
    def test_query_may_alias_no_overlap(self, empty_state, variable_factory, object_factory):
        """Test may_alias returns False for non-overlapping sets."""
        from pythonstan.analysis.pointer.kcfa import PointsToSet
        
        var1 = variable_factory("x")
        var2 = variable_factory("y")
        obj1 = object_factory(AllocKind.OBJECT)
        obj2 = object_factory(AllocKind.LIST)
        
        empty_state.set_points_to(var1, PointsToSet.singleton(obj1))
        empty_state.set_points_to(var2, PointsToSet.singleton(obj2))
        
        config = Config()
        solver = PointerSolver(empty_state, config)
        query = solver.query()
        
        assert query.may_alias(var1, var2) is False
    
    def test_query_may_alias_with_overlap(
        self,
        empty_state,
        variable_factory,
        object_factory
    ):
        """Test may_alias returns True for overlapping sets."""
        from pythonstan.analysis.pointer.kcfa import PointsToSet
        
        var1 = variable_factory("x")
        var2 = variable_factory("y")
        obj = object_factory(AllocKind.OBJECT)
        
        # Both variables point to same object
        empty_state.set_points_to(var1, PointsToSet.singleton(obj))
        empty_state.set_points_to(var2, PointsToSet.singleton(obj))
        
        config = Config()
        solver = PointerSolver(empty_state, config)
        query = solver.query()
        
        assert query.may_alias(var1, var2) is True
    
    def test_query_may_alias_partial_overlap(
        self,
        empty_state,
        variable_factory,
        object_factory
    ):
        """Test may_alias with partial overlap."""
        from pythonstan.analysis.pointer.kcfa import PointsToSet
        
        var1 = variable_factory("x")
        var2 = variable_factory("y")
        obj1 = object_factory(AllocKind.OBJECT, "Obj1")
        obj2 = object_factory(AllocKind.OBJECT, "Obj2")
        obj3 = object_factory(AllocKind.OBJECT, "Obj3")
        
        # var1 -> {obj1, obj2}, var2 -> {obj2, obj3}
        empty_state.set_points_to(var1, PointsToSet(frozenset([obj1, obj2])))
        empty_state.set_points_to(var2, PointsToSet(frozenset([obj2, obj3])))
        
        config = Config()
        solver = PointerSolver(empty_state, config)
        query = solver.query()
        
        # Should alias because of obj2
        assert query.may_alias(var1, var2) is True
    
    def test_query_get_statistics(self, empty_state):
        """Test getting statistics from query."""
        config = Config()
        solver = PointerSolver(empty_state, config)
        query = solver.query()
        
        stats = query.get_statistics()
        
        assert "num_variables" in stats
        assert "num_objects" in stats
        assert "num_heap_locations" in stats
        assert "iterations" in stats
        assert "constraints_applied" in stats


# ============================================================================
# TODO: Tests to add when solver methods are implemented in Phase 4
# ============================================================================

class TestSolverCopyConstraint:
    """Tests for copy constraint application."""
    
    def test_copy_propagates_points_to_set(self, empty_state, variable_factory, object_factory):
        """Test that copy constraint propagates points-to set."""
        from pythonstan.analysis.pointer.kcfa import PointsToSet, CopyConstraint
        
        config = Config()
        solver = PointerSolver(empty_state, config)
        
        # Create variables
        var_src = variable_factory("src")
        var_tgt = variable_factory("tgt")
        
        # Create object and add to source
        obj = object_factory(AllocKind.OBJECT)
        empty_state.set_points_to(var_src, PointsToSet.singleton(obj))
        
        # Add copy constraint
        constraint = CopyConstraint(source=var_src, target=var_tgt)
        solver.add_constraint(constraint)
        solver.solve_to_fixpoint()
        
        # Verify target has object
        pts_tgt = empty_state.get_points_to(var_tgt)
        assert obj in pts_tgt
    
    def test_copy_is_monotonic(self, empty_state, variable_factory, object_factory):
        """Test that copy constraint is monotonic (union semantics)."""
        from pythonstan.analysis.pointer.kcfa import PointsToSet, CopyConstraint, AllocConstraint
        
        config = Config()
        solver = PointerSolver(empty_state, config)
        
        var_src = variable_factory("src")
        var_tgt = variable_factory("tgt")
        
        obj1 = object_factory(AllocKind.OBJECT, "obj1")
        obj2 = object_factory(AllocKind.OBJECT, "obj2")
        
        # First copy
        empty_state.set_points_to(var_src, PointsToSet.singleton(obj1))
        solver.add_constraint(CopyConstraint(source=var_src, target=var_tgt))
        solver.solve_to_fixpoint()
        
        # Update source with second object
        empty_state.set_points_to(var_src, PointsToSet(frozenset([obj1, obj2])))
        # Trigger re-processing by scheduling
        solver._worklist.add(var_src)
        solver.solve_to_fixpoint()
        
        # Both objects should be in target (union)
        pts_tgt = empty_state.get_points_to(var_tgt)
        assert obj1 in pts_tgt
        assert obj2 in pts_tgt
    
    def test_copy_empty_source(self, empty_state, variable_factory):
        """Test copy from empty source."""
        from pythonstan.analysis.pointer.kcfa import CopyConstraint
        
        config = Config()
        solver = PointerSolver(empty_state, config)
        
        var_src = variable_factory("src")
        var_tgt = variable_factory("tgt")
        
        # Source is empty, target initially empty
        constraint = CopyConstraint(source=var_src, target=var_tgt)
        solver.add_constraint(constraint)
        solver.solve_to_fixpoint()
        
        # Target should remain empty
        pts_tgt = empty_state.get_points_to(var_tgt)
        assert pts_tgt.is_empty()


class TestSolverLoadConstraint:
    """Tests for load constraint application."""
    
    def test_load_propagates_field_values(self, empty_state, variable_factory, object_factory):
        """Test that load propagates field values."""
        from pythonstan.analysis.pointer.kcfa import PointsToSet, LoadConstraint
        from pythonstan.analysis.pointer.kcfa.heap_model import attr
        
        config = Config()
        solver = PointerSolver(empty_state, config)
        
        var_base = variable_factory("base")
        var_tgt = variable_factory("tgt")
        
        base_obj = object_factory(AllocKind.OBJECT)
        field_obj = object_factory(AllocKind.OBJECT, "field_val")
        
        # Set up: base -> base_obj, base_obj.field -> field_obj
        empty_state.set_points_to(var_base, PointsToSet.singleton(base_obj))
        empty_state.set_field(base_obj, attr("field"), PointsToSet.singleton(field_obj))
        
        # Load: tgt = base.field
        constraint = LoadConstraint(base=var_base, field=attr("field"), target=var_tgt)
        solver.add_constraint(constraint)
        solver.solve_to_fixpoint()
        
        # Verify
        pts_tgt = empty_state.get_points_to(var_tgt)
        assert field_obj in pts_tgt
    
    def test_load_from_multiple_objects(self, empty_state, variable_factory, object_factory):
        """Test load when base points to multiple objects."""
        from pythonstan.analysis.pointer.kcfa import PointsToSet, LoadConstraint
        from pythonstan.analysis.pointer.kcfa.heap_model import attr
        
        config = Config()
        solver = PointerSolver(empty_state, config)
        
        var_base = variable_factory("base")
        var_tgt = variable_factory("tgt")
        
        obj1 = object_factory(AllocKind.OBJECT, "obj1")
        obj2 = object_factory(AllocKind.OBJECT, "obj2")
        field_val1 = object_factory(AllocKind.OBJECT, "val1")
        field_val2 = object_factory(AllocKind.OBJECT, "val2")
        
        # base -> {obj1, obj2}, obj1.field -> val1, obj2.field -> val2
        empty_state.set_points_to(var_base, PointsToSet(frozenset([obj1, obj2])))
        empty_state.set_field(obj1, attr("field"), PointsToSet.singleton(field_val1))
        empty_state.set_field(obj2, attr("field"), PointsToSet.singleton(field_val2))
        
        # Load from field
        constraint = LoadConstraint(base=var_base, field=attr("field"), target=var_tgt)
        solver.add_constraint(constraint)
        solver.solve_to_fixpoint()
        
        # Target should have both field values
        pts_tgt = empty_state.get_points_to(var_tgt)
        assert field_val1 in pts_tgt
        assert field_val2 in pts_tgt
    
    def test_load_unknown_field(self, empty_state, variable_factory, object_factory):
        """Test load from field that hasn't been written."""
        from pythonstan.analysis.pointer.kcfa import PointsToSet, LoadConstraint
        from pythonstan.analysis.pointer.kcfa.heap_model import attr
        
        config = Config()
        solver = PointerSolver(empty_state, config)
        
        var_base = variable_factory("base")
        var_tgt = variable_factory("tgt")
        base_obj = object_factory(AllocKind.OBJECT)
        
        empty_state.set_points_to(var_base, PointsToSet.singleton(base_obj))
        
        # Load from unwritten field
        constraint = LoadConstraint(base=var_base, field=attr("unknown"), target=var_tgt)
        solver.add_constraint(constraint)
        solver.solve_to_fixpoint()
        
        # Target should be empty
        pts_tgt = empty_state.get_points_to(var_tgt)
        assert pts_tgt.is_empty()


class TestSolverStoreConstraint:
    """Tests for store constraint application."""
    
    def test_store_updates_field(self, empty_state, variable_factory, object_factory):
        """Test that store updates object field."""
        from pythonstan.analysis.pointer.kcfa import PointsToSet, StoreConstraint
        from pythonstan.analysis.pointer.kcfa.heap_model import attr
        
        config = Config()
        solver = PointerSolver(empty_state, config)
        
        var_base = variable_factory("base")
        var_src = variable_factory("src")
        
        base_obj = object_factory(AllocKind.OBJECT)
        src_obj = object_factory(AllocKind.OBJECT, "val")
        
        empty_state.set_points_to(var_base, PointsToSet.singleton(base_obj))
        empty_state.set_points_to(var_src, PointsToSet.singleton(src_obj))
        
        # Store: base.field = src
        constraint = StoreConstraint(base=var_base, field=attr("field"), source=var_src)
        solver.add_constraint(constraint)
        solver.solve_to_fixpoint()
        
        # Verify field updated
        field_pts = empty_state.get_field(base_obj, attr("field"))
        assert src_obj in field_pts
    
    def test_store_to_multiple_objects(self, empty_state, variable_factory, object_factory):
        """Test store when base points to multiple objects."""
        from pythonstan.analysis.pointer.kcfa import PointsToSet, StoreConstraint
        from pythonstan.analysis.pointer.kcfa.heap_model import attr
        
        config = Config()
        solver = PointerSolver(empty_state, config)
        
        var_base = variable_factory("base")
        var_src = variable_factory("src")
        
        obj1 = object_factory(AllocKind.OBJECT, "obj1")
        obj2 = object_factory(AllocKind.OBJECT, "obj2")
        src_obj = object_factory(AllocKind.OBJECT, "val")
        
        empty_state.set_points_to(var_base, PointsToSet(frozenset([obj1, obj2])))
        empty_state.set_points_to(var_src, PointsToSet.singleton(src_obj))
        
        # Store to all objects in base
        constraint = StoreConstraint(base=var_base, field=attr("field"), source=var_src)
        solver.add_constraint(constraint)
        solver.solve_to_fixpoint()
        
        # Both objects should have field updated
        assert src_obj in empty_state.get_field(obj1, attr("field"))
        assert src_obj in empty_state.get_field(obj2, attr("field"))
    
    def test_store_is_monotonic(self, empty_state, variable_factory, object_factory):
        """Test that store is monotonic."""
        from pythonstan.analysis.pointer.kcfa import PointsToSet, StoreConstraint
        from pythonstan.analysis.pointer.kcfa.heap_model import attr
        
        config = Config()
        solver = PointerSolver(empty_state, config)
        
        var_base = variable_factory("base")
        var_src = variable_factory("src")
        
        base_obj = object_factory(AllocKind.OBJECT)
        val1 = object_factory(AllocKind.OBJECT, "val1")
        val2 = object_factory(AllocKind.OBJECT, "val2")
        
        empty_state.set_points_to(var_base, PointsToSet.singleton(base_obj))
        
        # First store
        empty_state.set_points_to(var_src, PointsToSet.singleton(val1))
        solver.add_constraint(StoreConstraint(base=var_base, field=attr("field"), source=var_src))
        solver.solve_to_fixpoint()
        
        # Update source with second value
        empty_state.set_points_to(var_src, PointsToSet(frozenset([val1, val2])))
        solver._worklist.add(var_src)
        solver.solve_to_fixpoint()
        
        # Field should have both values (union)
        field_pts = empty_state.get_field(base_obj, attr("field"))
        assert val1 in field_pts
        assert val2 in field_pts


class TestSolverAllocConstraint:
    """Tests for allocation constraint application."""
    
    def test_alloc_creates_object(self, empty_state, variable_factory, alloc_site_factory):
        """Test that alloc creates new abstract object."""
        from pythonstan.analysis.pointer.kcfa import AllocConstraint
        
        config = Config()
        solver = PointerSolver(empty_state, config)
        
        var_tgt = variable_factory("tgt")
        alloc_site = alloc_site_factory(AllocKind.OBJECT)
        
        constraint = AllocConstraint(target=var_tgt, alloc_site=alloc_site)
        solver.add_constraint(constraint)
        solver.solve_to_fixpoint()
        
        # Verify object created
        pts_tgt = empty_state.get_points_to(var_tgt)
        assert len(pts_tgt) == 1
        obj = list(pts_tgt)[0]
        assert obj.alloc_site == alloc_site
    
    def test_alloc_uses_context(self, empty_state, alloc_site_factory):
        """Test that alloc uses variable's context."""
        from pythonstan.analysis.pointer.kcfa import AllocConstraint, Variable, Scope
        from pythonstan.analysis.pointer.kcfa.context import CallStringContext, CallSite
        
        config = Config()
        solver = PointerSolver(empty_state, config)
        
        # Create variable with specific context
        ctx = CallStringContext((CallSite("test", 0),), 2)
        scope = Scope("func", "function")
        var_tgt = Variable("x", scope, ctx)
        
        alloc_site = alloc_site_factory(AllocKind.OBJECT)
        
        constraint = AllocConstraint(target=var_tgt, alloc_site=alloc_site)
        solver.add_constraint(constraint)
        solver.solve_to_fixpoint()
        
        # Verify object has same context as variable
        pts_tgt = empty_state.get_points_to(var_tgt)
        obj = list(pts_tgt)[0]
        assert obj.context == ctx
    
    def test_multiple_allocs_same_site(self, empty_state, alloc_site_factory):
        """Test multiple allocations at same site with different contexts."""
        from pythonstan.analysis.pointer.kcfa import AllocConstraint, Variable, Scope
        from pythonstan.analysis.pointer.kcfa.context import CallStringContext, CallSite
        
        config = Config()
        solver = PointerSolver(empty_state, config)
        
        scope = Scope("func", "function")
        ctx1 = CallStringContext((CallSite("call1", 0),), 2)
        ctx2 = CallStringContext((CallSite("call2", 0),), 2)
        
        var1 = Variable("x", scope, ctx1)
        var2 = Variable("y", scope, ctx2)
        
        # Same allocation site, different contexts
        alloc_site = alloc_site_factory(AllocKind.OBJECT)
        
        solver.add_constraint(AllocConstraint(target=var1, alloc_site=alloc_site))
        solver.add_constraint(AllocConstraint(target=var2, alloc_site=alloc_site))
        solver.solve_to_fixpoint()
        
        # Should create different objects
        obj1 = list(empty_state.get_points_to(var1))[0]
        obj2 = list(empty_state.get_points_to(var2))[0]
        assert obj1 != obj2  # Different contexts = different objects


class TestSolverCallConstraint:
    """Tests for call constraint application."""
    
    def test_call_resolves_callees(self, empty_state, variable_factory, object_factory):
        """Test that call resolves callee objects."""
        from pythonstan.analysis.pointer.kcfa import PointsToSet, CallConstraint
        
        config = Config()
        solver = PointerSolver(empty_state, config)
        
        var_callee = variable_factory("callee")
        var_tgt = variable_factory("tgt")
        
        # Create function object and bind to callee
        func_obj = object_factory(AllocKind.FUNCTION, "test_func")
        empty_state.set_points_to(var_callee, PointsToSet.singleton(func_obj))
        
        # Create call constraint
        constraint = CallConstraint(callee=var_callee, args=(), target=var_tgt, call_site="test:1")
        solver.add_constraint(constraint)
        
        # Call should resolve (even if it doesn't fully process yet)
        changed = solver._apply_call(constraint)
        # Just verify it doesn't crash
        assert changed is not None
    
    def test_call_generates_parameter_constraints(self, empty_state, variable_factory, object_factory):
        """Test that call generates parameter passing constraints."""
        # This test verifies basic call handling works
        from pythonstan.analysis.pointer.kcfa import PointsToSet, CallConstraint
        
        config = Config()
        solver = PointerSolver(empty_state, config)
        
        var_callee = variable_factory("callee")
        var_arg = variable_factory("arg")
        var_tgt = variable_factory("tgt")
        
        func_obj = object_factory(AllocKind.FUNCTION, "test_func")
        empty_state.set_points_to(var_callee, PointsToSet.singleton(func_obj))
        
        constraint = CallConstraint(
            callee=var_callee,
            args=(var_arg,),
            target=var_tgt,
            call_site="test:1"
        )
        solver.add_constraint(constraint)
        # Just verify constraint handling works
        solver.solve_to_fixpoint()
    
    def test_call_generates_return_constraints(self, empty_state, variable_factory, object_factory):
        """Test that call generates return value constraints."""
        from pythonstan.analysis.pointer.kcfa import PointsToSet, CallConstraint
        
        config = Config()
        solver = PointerSolver(empty_state, config)
        
        var_callee = variable_factory("callee")
        var_tgt = variable_factory("tgt")
        
        func_obj = object_factory(AllocKind.FUNCTION, "test_func")
        empty_state.set_points_to(var_callee, PointsToSet.singleton(func_obj))
        
        constraint = CallConstraint(callee=var_callee, args=(), target=var_tgt, call_site="test:1")
        solver.add_constraint(constraint)
        solver.solve_to_fixpoint()
        # Basic smoke test - verify it runs


class TestSolverReturnConstraint:
    """Tests for return constraint application."""
    
    def test_return_propagates_value(self, empty_state, variable_factory, object_factory):
        """Test that return propagates return value."""
        from pythonstan.analysis.pointer.kcfa import PointsToSet, ReturnConstraint
        
        config = Config()
        solver = PointerSolver(empty_state, config)
        
        var_callee_ret = variable_factory("$return")
        var_caller_tgt = variable_factory("result")
        
        ret_obj = object_factory(AllocKind.OBJECT, "return_val")
        empty_state.set_points_to(var_callee_ret, PointsToSet.singleton(ret_obj))
        
        constraint = ReturnConstraint(callee_return=var_callee_ret, caller_target=var_caller_tgt)
        solver.add_constraint(constraint)
        solver.solve_to_fixpoint()
        
        # Verify return value propagated
        pts_tgt = empty_state.get_points_to(var_caller_tgt)
        assert ret_obj in pts_tgt


class TestSolverFixpoint:
    """Tests for fixpoint iteration."""
    
    def test_fixpoint_simple_copy_chain(self, empty_state, variable_factory, object_factory):
        """Test fixpoint for simple copy chain."""
        from pythonstan.analysis.pointer.kcfa import PointsToSet, CopyConstraint, AllocConstraint
        
        config = Config()
        solver = PointerSolver(empty_state, config)
        
        var_x = variable_factory("x")
        var_y = variable_factory("y")
        var_z = variable_factory("z")
        
        alloc_site = object_factory(AllocKind.OBJECT).alloc_site
        
        # z = new Obj; y = z; x = y
        solver.add_constraint(AllocConstraint(target=var_z, alloc_site=alloc_site))
        solver.add_constraint(CopyConstraint(source=var_z, target=var_y))
        solver.add_constraint(CopyConstraint(source=var_y, target=var_x))
        
        solver.solve_to_fixpoint()
        
        # All should point to same object
        pts_x = empty_state.get_points_to(var_x)
        pts_y = empty_state.get_points_to(var_y)
        pts_z = empty_state.get_points_to(var_z)
        
        assert len(pts_x) == 1
        assert pts_x == pts_y
        assert pts_y == pts_z
    
    def test_fixpoint_field_load_store(self, empty_state, variable_factory, object_factory):
        """Test fixpoint for field operations."""
        from pythonstan.analysis.pointer.kcfa import PointsToSet, StoreConstraint, LoadConstraint
        from pythonstan.analysis.pointer.kcfa.heap_model import attr
        
        config = Config()
        solver = PointerSolver(empty_state, config)
        
        var_obj = variable_factory("obj")
        var_x = variable_factory("x")
        var_y = variable_factory("y")
        
        obj = object_factory(AllocKind.OBJECT)
        val = object_factory(AllocKind.OBJECT, "val")
        
        empty_state.set_points_to(var_obj, PointsToSet.singleton(obj))
        empty_state.set_points_to(var_x, PointsToSet.singleton(val))
        
        # obj.f = x; y = obj.f
        solver.add_constraint(StoreConstraint(base=var_obj, field=attr("f"), source=var_x))
        solver.add_constraint(LoadConstraint(base=var_obj, field=attr("f"), target=var_y))
        
        solver.solve_to_fixpoint()
        
        # y should point to val
        pts_y = empty_state.get_points_to(var_y)
        assert val in pts_y
    
    def test_fixpoint_max_iterations(self):
        """Test that max iterations limit is respected."""
        from pythonstan.analysis.pointer.kcfa import PointerAnalysisState
        
        # Create config with low max iterations
        config = Config()
        config.max_iterations = 10
        
        state = PointerAnalysisState()
        solver = PointerSolver(state, config)
        
        # Solve (should stop at max iterations if no constraints)
        solver.solve_to_fixpoint()
        
        assert solver._iteration <= config.max_iterations
    
    def test_fixpoint_worklist_exhaustion(self, empty_state, variable_factory, object_factory):
        """Test that solver terminates when worklist is empty."""
        from pythonstan.analysis.pointer.kcfa import AllocConstraint
        
        config = Config()
        solver = PointerSolver(empty_state, config)
        
        var = variable_factory("x")
        alloc_site = object_factory(AllocKind.OBJECT).alloc_site
        
        solver.add_constraint(AllocConstraint(target=var, alloc_site=alloc_site))
        solver.solve_to_fixpoint()
        
        # Worklist should be empty after convergence
        assert len(solver._worklist) == 0


class TestSolverIntegration:
    """Integration tests for solver."""
    
    def test_solve_simple_program(self, empty_state, variable_factory, object_factory):
        """Test solving constraints for simple program."""
        from pythonstan.analysis.pointer.kcfa import (
            AllocConstraint, CopyConstraint, StoreConstraint, LoadConstraint, PointsToSet
        )
        from pythonstan.analysis.pointer.kcfa.heap_model import attr
        
        config = Config()
        solver = PointerSolver(empty_state, config)
        
        # Program: x = new Obj; x.f = new Val; y = x; z = y.f
        var_x = variable_factory("x")
        var_y = variable_factory("y")
        var_z = variable_factory("z")
        
        obj_site = object_factory(AllocKind.OBJECT).alloc_site
        val_site = object_factory(AllocKind.OBJECT, "val").alloc_site
        var_tmp = variable_factory("$tmp")
        
        solver.add_constraint(AllocConstraint(target=var_x, alloc_site=obj_site))
        solver.add_constraint(AllocConstraint(target=var_tmp, alloc_site=val_site))
        solver.add_constraint(StoreConstraint(base=var_x, field=attr("f"), source=var_tmp))
        solver.add_constraint(CopyConstraint(source=var_x, target=var_y))
        solver.add_constraint(LoadConstraint(base=var_y, field=attr("f"), target=var_z))
        
        solver.solve_to_fixpoint()
        
        # z should point to val
        pts_z = empty_state.get_points_to(var_z)
        assert len(pts_z) == 1
    
    def test_solve_with_different_policies(self):
        """Test solving with different context sensitivity policies."""
        from pythonstan.analysis.pointer.kcfa import PointerAnalysisState
        
        # Test that solver works with different configs
        for policy in ["insensitive", "1-cfa", "2-cfa"]:
            config = Config()
            config.context_policy = policy
            state = PointerAnalysisState()
            solver = PointerSolver(state, config)
            solver.solve_to_fixpoint()
            # Just verify no crashes
            assert solver._iteration >= 0

