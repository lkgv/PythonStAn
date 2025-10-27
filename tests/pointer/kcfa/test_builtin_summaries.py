"""Tests for builtin function summaries."""

import pytest
from pythonstan.analysis.pointer.kcfa.builtin_api_handler import (
    BuiltinSummaryManager, ContainerSummary, ConstantSummary,
    IteratorSummary, NextSummary, FilterSummary, SortedSummary,
    EnumerateSummary, GetAttrSummary, SetAttrSummary, VoidSummary
)
from pythonstan.analysis.pointer.kcfa import Config, Variable, Scope, AllocKind, AllocConstraint, LoadConstraint
from pythonstan.analysis.pointer.kcfa.context import CallStringContext


class TestBuiltinSummaryManagerInitialization:
    """Tests for BuiltinSummaryManager initialization."""
    
    def test_basic_initialization(self):
        """Test creating builtin summary manager."""
        config = Config()
        manager = BuiltinSummaryManager(config)
        
        assert manager.config == config
        assert len(manager._summaries) > 0
    
    def test_comprehensive_builtin_coverage(self):
        """Test that all major builtins are registered."""
        config = Config()
        manager = BuiltinSummaryManager(config)
        
        # Container constructors
        assert manager.has_summary('list')
        assert manager.has_summary('dict')
        assert manager.has_summary('tuple')
        assert manager.has_summary('set')
        
        # Iterator functions
        assert manager.has_summary('iter')
        assert manager.has_summary('next')
        assert manager.has_summary('enumerate')
        assert manager.has_summary('zip')
        assert manager.has_summary('reversed')
        
        # Functional programming
        assert manager.has_summary('map')
        assert manager.has_summary('filter')
        assert manager.has_summary('sorted')
        
        # Type functions
        assert manager.has_summary('isinstance')
        assert manager.has_summary('type')
        assert manager.has_summary('len')
        
        # Conversion functions
        assert manager.has_summary('str')
        assert manager.has_summary('int')
        assert manager.has_summary('float')
        assert manager.has_summary('bool')
        
        # Introspection
        assert manager.has_summary('getattr')
        assert manager.has_summary('setattr')
        assert manager.has_summary('hasattr')
        
        # I/O
        assert manager.has_summary('print')
        assert manager.has_summary('open')
        
        # Should have ~30 builtins
        assert len(manager._summaries) >= 25


class TestContainerSummaries:
    """Tests for container constructor summaries."""
    
    def test_list_summary_creates_list_allocation(self):
        """Test that list() summary creates LIST allocation constraint."""
        summary = ContainerSummary("list", "LIST")
        ctx = CallStringContext((), 2)
        scope = Scope("test", "function")
        target = Variable("x", scope, ctx)
        
        constraints = summary.apply(target, [], ctx)
        
        assert len(constraints) == 1
        assert isinstance(constraints[0], AllocConstraint)
        assert constraints[0].target == target
        assert constraints[0].alloc_site.kind == AllocKind.LIST
        assert constraints[0].alloc_site.name == "builtin_list"
    
    def test_dict_summary_creates_dict_allocation(self):
        """Test that dict() summary creates DICT allocation constraint."""
        summary = ContainerSummary("dict", "DICT")
        ctx = CallStringContext((), 2)
        scope = Scope("test", "function")
        target = Variable("x", scope, ctx)
        
        constraints = summary.apply(target, [], ctx)
        
        assert len(constraints) == 1
        assert isinstance(constraints[0], AllocConstraint)
        assert constraints[0].alloc_site.kind == AllocKind.DICT


class TestIteratorSummaries:
    """Tests for iterator function summaries."""
    
    def test_iter_summary_creates_iterator_with_element_link(self):
        """Test that iter() creates iterator linked to container elements."""
        summary = IteratorSummary("iter")
        ctx = CallStringContext((), 2)
        scope = Scope("test", "function")
        target = Variable("iterator", scope, ctx)
        container = Variable("container", scope, ctx)
        
        constraints = summary.apply(target, [container], ctx)
        
        # Should create iterator + link to container elements
        assert len(constraints) == 2
        
        # First constraint: allocate iterator
        alloc_constraint = constraints[0]
        assert isinstance(alloc_constraint, AllocConstraint)
        assert alloc_constraint.target == target
        assert alloc_constraint.alloc_site.name == "builtin_iter"
        
        # Second constraint: link to container elements
        load_constraint = constraints[1]
        assert isinstance(load_constraint, LoadConstraint)
        assert load_constraint.base == container
        assert load_constraint.target == target
        # Field should be elem()
        from pythonstan.analysis.pointer.kcfa.heap_model import elem
        assert load_constraint.field == elem()
    
    def test_next_summary_loads_from_iterator(self):
        """Test that next() loads elements from iterator."""
        summary = NextSummary("next")
        ctx = CallStringContext((), 2)
        scope = Scope("test", "function")
        target = Variable("result", scope, ctx)
        iterator = Variable("iter", scope, ctx)
        
        constraints = summary.apply(target, [iterator], ctx)
        
        # Should load from iterator.elem
        assert len(constraints) == 1
        load_constraint = constraints[0]
        assert isinstance(load_constraint, LoadConstraint)
        assert load_constraint.base == iterator
        assert load_constraint.target == target
    
    def test_enumerate_creates_iterator(self):
        """Test that enumerate() creates iterator object."""
        summary = EnumerateSummary("enumerate")
        ctx = CallStringContext((), 2)
        scope = Scope("test", "function")
        target = Variable("enum", scope, ctx)
        iterable = Variable("lst", scope, ctx)
        
        constraints = summary.apply(target, [iterable], ctx)
        
        assert len(constraints) == 1
        assert isinstance(constraints[0], AllocConstraint)
        assert constraints[0].alloc_site.name == "builtin_enumerate"


class TestFunctionalProgrammingSummaries:
    """Tests for functional programming summaries."""
    
    def test_filter_summary_links_to_iterable_elements(self):
        """Test that filter() creates iterator linked to iterable elements."""
        summary = FilterSummary("filter")
        ctx = CallStringContext((), 2)
        scope = Scope("test", "function")
        target = Variable("filtered", scope, ctx)
        func = Variable("predicate", scope, ctx)
        iterable = Variable("items", scope, ctx)
        
        constraints = summary.apply(target, [func, iterable], ctx)
        
        # Should create filter iterator + link to iterable elements
        assert len(constraints) == 2
        
        # Allocation constraint
        alloc_constraint = constraints[0]
        assert isinstance(alloc_constraint, AllocConstraint)
        assert alloc_constraint.alloc_site.name == "builtin_filter"
        
        # Load constraint from iterable
        load_constraint = constraints[1]
        assert isinstance(load_constraint, LoadConstraint)
        assert load_constraint.base == iterable
        assert load_constraint.target == target
    
    def test_sorted_summary_creates_list_with_elements(self):
        """Test that sorted() creates list with elements from iterable."""
        summary = SortedSummary("sorted")
        ctx = CallStringContext((), 2)
        scope = Scope("test", "function")
        target = Variable("sorted_list", scope, ctx)
        iterable = Variable("unsorted", scope, ctx)
        
        constraints = summary.apply(target, [iterable], ctx)
        
        # Should create list + copy elements
        assert len(constraints) == 2
        
        # List allocation
        alloc_constraint = constraints[0]
        assert isinstance(alloc_constraint, AllocConstraint)
        assert alloc_constraint.alloc_site.kind == AllocKind.LIST
        assert alloc_constraint.alloc_site.name == "builtin_sorted"
        
        # Element copying
        load_constraint = constraints[1]
        assert isinstance(load_constraint, LoadConstraint)
        assert load_constraint.base == iterable
        assert load_constraint.target == target


class TestIteratorChainScenario:
    """Test iterator constraint generation."""
    
    def test_iter_generates_correct_constraints(self):
        """Test that iter() generates correct constraint structure."""
        summary = IteratorSummary("iter")
        ctx = CallStringContext((), 2)
        scope = Scope("test", "function")
        target = Variable("iterator", scope, ctx)
        container = Variable("container", scope, ctx)
        
        constraints = summary.apply(target, [container], ctx)
        
        # Should generate allocation + load constraints
        assert len(constraints) == 2
        assert isinstance(constraints[0], AllocConstraint)
        assert isinstance(constraints[1], LoadConstraint)
        
        # Load constraint should link container elements to iterator
        load_constraint = constraints[1]
        assert load_constraint.base == container
        assert load_constraint.target == target
    
    def test_next_generates_correct_constraints(self):
        """Test that next() generates correct constraint structure."""
        summary = NextSummary("next")
        ctx = CallStringContext((), 2)
        scope = Scope("test", "function")
        target = Variable("result", scope, ctx)
        iterator = Variable("iter", scope, ctx)
        
        constraints = summary.apply(target, [iterator], ctx)
        
        # Should generate load from iterator elements
        assert len(constraints) == 1
        assert isinstance(constraints[0], LoadConstraint)
        
        load_constraint = constraints[0]
        assert load_constraint.base == iterator
        assert load_constraint.target == target


class TestConstantSummaries:
    """Tests for constant-returning summaries."""
    
    def test_len_summary_creates_object(self):
        """Test that len() creates allocation."""
        summary = ConstantSummary("len", "OBJECT")
        ctx = CallStringContext((), 2)
        scope = Scope("test", "function")
        target = Variable("length", scope, ctx)
        container = Variable("container", scope, ctx)
        
        constraints = summary.apply(target, [container], ctx)
        
        assert len(constraints) == 1
        assert isinstance(constraints[0], AllocConstraint)
        assert constraints[0].alloc_site.name == "builtin_len_result"
    
    def test_type_functions_create_objects(self):
        """Test that type functions create result objects."""
        for builtin_name in ["isinstance", "issubclass", "type"]:
            summary = ConstantSummary(builtin_name, "OBJECT")
            ctx = CallStringContext((), 2)
            scope = Scope("test", "function")
            target = Variable("result", scope, ctx)
            
            constraints = summary.apply(target, [Variable("arg", scope, ctx)], ctx)
            
            assert len(constraints) == 1
            assert isinstance(constraints[0], AllocConstraint)
            assert builtin_name in constraints[0].alloc_site.name


class TestIntrospectionSummaries:
    """Tests for introspection function summaries."""
    
    def test_getattr_creates_conservative_result(self):
        """Test that getattr() creates conservative result object."""
        from pythonstan.analysis.pointer.kcfa.builtin_api_handler import GetAttrSummary
        
        summary = GetAttrSummary("getattr")
        ctx = CallStringContext((), 2)
        scope = Scope("test", "function")
        target = Variable("attr_val", scope, ctx)
        obj_var = Variable("obj", scope, ctx)
        name_var = Variable("name", scope, ctx)
        
        constraints = summary.apply(target, [obj_var, name_var], ctx)
        
        assert len(constraints) == 1
        assert isinstance(constraints[0], AllocConstraint)
        assert "getattr" in constraints[0].alloc_site.name
    
    def test_setattr_no_return_value(self):
        """Test that setattr() produces no constraints (side effect only)."""
        from pythonstan.analysis.pointer.kcfa.builtin_api_handler import SetAttrSummary
        
        summary = SetAttrSummary("setattr")
        ctx = CallStringContext((), 2)
        scope = Scope("test", "function")
        obj_var = Variable("obj", scope, ctx)
        name_var = Variable("name", scope, ctx)
        val_var = Variable("value", scope, ctx)
        
        # setattr() has no return value
        constraints = summary.apply(None, [obj_var, name_var, val_var], ctx)
        
        assert len(constraints) == 0


class TestVoidFunctions:
    """Tests for void function summaries."""
    
    def test_print_no_constraints(self):
        """Test that print() produces no constraints."""
        from pythonstan.analysis.pointer.kcfa.builtin_api_handler import VoidSummary
        
        summary = VoidSummary("print")
        ctx = CallStringContext((), 2)
        scope = Scope("test", "function")
        arg_var = Variable("message", scope, ctx)
        
        constraints = summary.apply(None, [arg_var], ctx)
        
        assert len(constraints) == 0


class TestBuiltinSummaryIntegration:
    """Integration tests for builtin summaries with solver."""
    
    def test_builtin_manager_integration_with_solver(self):
        """Test that builtin manager integrates correctly with solver."""
        from pythonstan.analysis.pointer.kcfa import (
            PointerAnalysisState, PointerSolver, Config,
            CallConstraint, Variable, Scope, AbstractObject,
            AllocSite, AllocKind
        )
        from pythonstan.analysis.pointer.kcfa.context import CallStringContext
        from pythonstan.analysis.pointer.kcfa.state import PointsToSet
        
        config = Config()
        state = PointerAnalysisState()
        
        # Create builtin manager
        builtin_manager = BuiltinSummaryManager(config)
        
        # Create solver with builtin manager
        solver = PointerSolver(
            state=state,
            config=config,
            builtin_manager=builtin_manager
        )
        
        # Setup: builtin_var points to list builtin
        ctx = CallStringContext((), 2)
        scope = Scope("test", "function")
        builtin_var = Variable("list", scope, ctx)
        target_var = Variable("result", scope, ctx)
        
        # Create builtin object
        builtin_obj = AbstractObject(
            AllocSite("<builtin>", 0, 0, AllocKind.BUILTIN, "list"),
            ctx
        )
        state.set_points_to(builtin_var, PointsToSet.singleton(builtin_obj))
        
        # Create call constraint
        call_constraint = CallConstraint(
            callee=builtin_var,
            args=(),
            target=target_var,
            call_site="test:1"
        )
        
        # Add constraint and solve
        solver.add_constraint(call_constraint)
        solver.solve_to_fixpoint()
        
        # Verify that builtin summary was applied
        result_pts = state.get_points_to(target_var)
        # Should have created list object
        assert len(result_pts) > 0
        result_obj = list(result_pts)[0]
        assert result_obj.kind == AllocKind.LIST


# NOTE: This comprehensive test suite verifies:
# 1. All major Python builtins are registered
# 2. Container constructors work correctly
# 3. Iterator functions create proper field links
# 4. Functional programming functions handle element flow
# 5. Introspection functions are conservative
# 6. Integration with solver works correctly
#
# This matches the comprehensiveness of kcfa2/summaries.py
# but uses the new constraint-based architecture.