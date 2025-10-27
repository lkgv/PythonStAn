"""Tests for analysis state: PointsToSet and PointerAnalysisState."""

import pytest
from pythonstan.analysis.pointer.kcfa import (
    PointsToSet,
    PointerAnalysisState,
    AllocKind,
)
from pythonstan.analysis.pointer.kcfa.heap_model import attr, elem, value


class TestPointsToSet:
    """Tests for PointsToSet dataclass."""
    
    def test_empty_creation(self):
        """Test creating an empty points-to set."""
        pts = PointsToSet.empty()
        assert pts.is_empty()
        assert len(pts) == 0
    
    def test_singleton_creation(self, object_factory):
        """Test creating a singleton points-to set."""
        obj = object_factory(AllocKind.OBJECT)
        pts = PointsToSet.singleton(obj)
        
        assert not pts.is_empty()
        assert len(pts) == 1
        assert obj in pts
    
    def test_direct_creation_from_frozenset(self, object_factory):
        """Test creating points-to set from frozenset."""
        obj1 = object_factory(AllocKind.OBJECT)
        obj2 = object_factory(AllocKind.LIST)
        
        pts = PointsToSet(frozenset([obj1, obj2]))
        assert len(pts) == 2
        assert obj1 in pts
        assert obj2 in pts
    
    def test_union_empty_sets(self):
        """Test union of two empty sets."""
        pts1 = PointsToSet.empty()
        pts2 = PointsToSet.empty()
        result = pts1.union(pts2)
        
        assert result.is_empty()
    
    def test_union_empty_with_singleton(self, object_factory):
        """Test union of empty set with singleton."""
        obj = object_factory(AllocKind.OBJECT)
        pts1 = PointsToSet.empty()
        pts2 = PointsToSet.singleton(obj)
        
        result = pts1.union(pts2)
        assert len(result) == 1
        assert obj in result
    
    def test_union_disjoint_sets(self, object_factory):
        """Test union of disjoint sets."""
        obj1 = object_factory(AllocKind.OBJECT)
        obj2 = object_factory(AllocKind.LIST)
        
        pts1 = PointsToSet.singleton(obj1)
        pts2 = PointsToSet.singleton(obj2)
        
        result = pts1.union(pts2)
        assert len(result) == 2
        assert obj1 in result
        assert obj2 in result
    
    def test_union_overlapping_sets(self, object_factory):
        """Test union of overlapping sets."""
        obj1 = object_factory(AllocKind.OBJECT)
        obj2 = object_factory(AllocKind.LIST)
        obj3 = object_factory(AllocKind.DICT)
        
        pts1 = PointsToSet(frozenset([obj1, obj2]))
        pts2 = PointsToSet(frozenset([obj2, obj3]))
        
        result = pts1.union(pts2)
        assert len(result) == 3
        assert obj1 in result
        assert obj2 in result
        assert obj3 in result
    
    def test_union_is_immutable(self, object_factory):
        """Test that union creates new set without modifying originals."""
        obj1 = object_factory(AllocKind.OBJECT)
        obj2 = object_factory(AllocKind.LIST)
        
        pts1 = PointsToSet.singleton(obj1)
        pts2 = PointsToSet.singleton(obj2)
        
        result = pts1.union(pts2)
        
        # Original sets unchanged
        assert len(pts1) == 1
        assert len(pts2) == 1
        # Result has both
        assert len(result) == 2
    
    def test_contains_operator(self, object_factory):
        """Test 'in' operator for membership."""
        obj1 = object_factory(AllocKind.OBJECT)
        obj2 = object_factory(AllocKind.LIST)
        
        pts = PointsToSet.singleton(obj1)
        
        assert obj1 in pts
        assert obj2 not in pts
    
    def test_iteration(self, object_factory):
        """Test iterating over points-to set."""
        obj1 = object_factory(AllocKind.OBJECT)
        obj2 = object_factory(AllocKind.LIST)
        obj3 = object_factory(AllocKind.DICT)
        
        pts = PointsToSet(frozenset([obj1, obj2, obj3]))
        
        objs = list(pts)
        assert len(objs) == 3
        assert obj1 in objs
        assert obj2 in objs
        assert obj3 in objs
    
    def test_len_operator(self, object_factory):
        """Test len() on points-to sets."""
        pts0 = PointsToSet.empty()
        assert len(pts0) == 0
        
        pts1 = PointsToSet.singleton(object_factory())
        assert len(pts1) == 1
        
        pts3 = PointsToSet(frozenset([
            object_factory(),
            object_factory(),
            object_factory()
        ]))
        assert len(pts3) == 3
    
    def test_string_representation_empty(self):
        """Test __str__ for empty set."""
        pts = PointsToSet.empty()
        assert str(pts) == "{}"
    
    def test_string_representation_singleton(self, object_factory):
        """Test __str__ for singleton set."""
        obj = object_factory(AllocKind.OBJECT, "TestObj")
        pts = PointsToSet.singleton(obj)
        pts_str = str(pts)
        
        assert "{" in pts_str
        assert "}" in pts_str
        assert "TestObj" in pts_str
    
    def test_string_representation_multiple(self, object_factory):
        """Test __str__ for set with multiple objects."""
        obj1 = object_factory(AllocKind.OBJECT)
        obj2 = object_factory(AllocKind.LIST)
        
        pts = PointsToSet(frozenset([obj1, obj2]))
        pts_str = str(pts)
        
        assert "{" in pts_str
        assert "}" in pts_str
        assert "," in pts_str
    
    def test_equality(self, object_factory):
        """Test points-to set equality."""
        obj1 = object_factory(AllocKind.OBJECT)
        obj2 = object_factory(AllocKind.LIST)
        
        pts1 = PointsToSet(frozenset([obj1, obj2]))
        pts2 = PointsToSet(frozenset([obj1, obj2]))
        pts3 = PointsToSet(frozenset([obj1]))
        
        assert pts1 == pts2
        assert pts1 != pts3
    
    def test_frozen(self, object_factory):
        """Test that PointsToSet is immutable."""
        pts = PointsToSet.singleton(object_factory())
        with pytest.raises(AttributeError):
            pts.objects = frozenset()


class TestPointerAnalysisState:
    """Tests for PointerAnalysisState."""
    
    def test_initialization(self):
        """Test creating empty state."""
        state = PointerAnalysisState()
        assert state is not None
    
    def test_get_points_to_untracked_variable(self, variable_factory):
        """Test getting points-to set for untracked variable returns empty."""
        state = PointerAnalysisState()
        var = variable_factory("x")
        
        pts = state.get_points_to(var)
        assert pts.is_empty()
    
    def test_set_points_to_new_variable(self, variable_factory, object_factory):
        """Test setting points-to set for new variable."""
        state = PointerAnalysisState()
        var = variable_factory("x")
        obj = object_factory(AllocKind.OBJECT)
        pts = PointsToSet.singleton(obj)
        
        changed = state.set_points_to(var, pts)
        
        assert changed is True
        assert state.get_points_to(var) == pts
    
    def test_set_points_to_existing_variable_same_set(self, variable_factory, object_factory):
        """Test setting same points-to set returns False (no change)."""
        state = PointerAnalysisState()
        var = variable_factory("x")
        obj = object_factory(AllocKind.OBJECT)
        pts = PointsToSet.singleton(obj)
        
        state.set_points_to(var, pts)
        changed = state.set_points_to(var, pts)
        
        assert changed is False
    
    def test_set_points_to_performs_union(self, variable_factory, object_factory):
        """Test that set_points_to performs union with existing set."""
        state = PointerAnalysisState()
        var = variable_factory("x")
        
        obj1 = object_factory(AllocKind.OBJECT)
        obj2 = object_factory(AllocKind.LIST)
        
        pts1 = PointsToSet.singleton(obj1)
        pts2 = PointsToSet.singleton(obj2)
        
        state.set_points_to(var, pts1)
        changed = state.set_points_to(var, pts2)
        
        assert changed is True
        result = state.get_points_to(var)
        assert len(result) == 2
        assert obj1 in result
        assert obj2 in result
    
    def test_multiple_variables(self, variable_factory, object_factory):
        """Test tracking multiple variables independently."""
        state = PointerAnalysisState()
        
        var1 = variable_factory("x")
        var2 = variable_factory("y")
        
        obj1 = object_factory(AllocKind.OBJECT)
        obj2 = object_factory(AllocKind.LIST)
        
        state.set_points_to(var1, PointsToSet.singleton(obj1))
        state.set_points_to(var2, PointsToSet.singleton(obj2))
        
        pts1 = state.get_points_to(var1)
        pts2 = state.get_points_to(var2)
        
        assert obj1 in pts1
        assert obj1 not in pts2
        assert obj2 in pts2
        assert obj2 not in pts1
    
    def test_get_field_untracked_location(self, object_factory):
        """Test getting field for untracked location returns empty."""
        state = PointerAnalysisState()
        obj = object_factory(AllocKind.OBJECT)
        field = attr("name")
        
        pts = state.get_field(obj, field)
        assert pts.is_empty()
    
    def test_set_field_new_location(self, object_factory):
        """Test setting field for new heap location."""
        state = PointerAnalysisState()
        
        base_obj = object_factory(AllocKind.OBJECT)
        field_obj = object_factory(AllocKind.OBJECT)
        field = attr("name")
        
        pts = PointsToSet.singleton(field_obj)
        changed = state.set_field(base_obj, field, pts)
        
        assert changed is True
        assert state.get_field(base_obj, field) == pts
    
    def test_set_field_performs_union(self, object_factory):
        """Test that set_field performs union."""
        state = PointerAnalysisState()
        
        base_obj = object_factory(AllocKind.OBJECT)
        field = attr("name")
        
        obj1 = object_factory(AllocKind.OBJECT, "Obj1")
        obj2 = object_factory(AllocKind.OBJECT, "Obj2")
        
        state.set_field(base_obj, field, PointsToSet.singleton(obj1))
        changed = state.set_field(base_obj, field, PointsToSet.singleton(obj2))
        
        assert changed is True
        result = state.get_field(base_obj, field)
        assert len(result) == 2
        assert obj1 in result
        assert obj2 in result
    
    def test_different_fields_on_same_object(self, object_factory):
        """Test tracking different fields on same object."""
        state = PointerAnalysisState()
        
        obj = object_factory(AllocKind.OBJECT)
        name_obj = object_factory(AllocKind.OBJECT, "NameObj")
        age_obj = object_factory(AllocKind.OBJECT, "AgeObj")
        
        state.set_field(obj, attr("name"), PointsToSet.singleton(name_obj))
        state.set_field(obj, attr("age"), PointsToSet.singleton(age_obj))
        
        name_pts = state.get_field(obj, attr("name"))
        age_pts = state.get_field(obj, attr("age"))
        
        assert name_obj in name_pts
        assert age_obj in age_pts
        assert name_obj not in age_pts
        assert age_obj not in name_pts
    
    def test_same_field_on_different_objects(self, object_factory):
        """Test tracking same field on different objects."""
        state = PointerAnalysisState()
        
        obj1 = object_factory(AllocKind.OBJECT, "Obj1")
        obj2 = object_factory(AllocKind.OBJECT, "Obj2")
        
        val1 = object_factory(AllocKind.OBJECT, "Val1")
        val2 = object_factory(AllocKind.OBJECT, "Val2")
        
        field = attr("name")
        
        state.set_field(obj1, field, PointsToSet.singleton(val1))
        state.set_field(obj2, field, PointsToSet.singleton(val2))
        
        pts1 = state.get_field(obj1, field)
        pts2 = state.get_field(obj2, field)
        
        assert val1 in pts1
        assert val2 in pts2
        assert val1 not in pts2
        assert val2 not in pts1
    
    def test_container_element_field(self, object_factory):
        """Test tracking container element field."""
        state = PointerAnalysisState()
        
        list_obj = object_factory(AllocKind.LIST)
        elem1 = object_factory(AllocKind.OBJECT, "Elem1")
        elem2 = object_factory(AllocKind.OBJECT, "Elem2")
        
        state.set_field(list_obj, elem(), PointsToSet.singleton(elem1))
        state.set_field(list_obj, elem(), PointsToSet.singleton(elem2))
        
        elem_pts = state.get_field(list_obj, elem())
        assert len(elem_pts) == 2
        assert elem1 in elem_pts
        assert elem2 in elem_pts
    
    def test_dict_value_field(self, object_factory):
        """Test tracking dictionary value field."""
        state = PointerAnalysisState()
        
        dict_obj = object_factory(AllocKind.DICT)
        val1 = object_factory(AllocKind.OBJECT, "Val1")
        val2 = object_factory(AllocKind.OBJECT, "Val2")
        
        state.set_field(dict_obj, value(), PointsToSet.singleton(val1))
        state.set_field(dict_obj, value(), PointsToSet.singleton(val2))
        
        val_pts = state.get_field(dict_obj, value())
        assert len(val_pts) == 2
        assert val1 in val_pts
        assert val2 in val_pts
    
    def test_get_statistics_empty_state(self):
        """Test statistics for empty state."""
        state = PointerAnalysisState()
        stats = state.get_statistics()
        
        assert stats["num_variables"] == 0
        assert stats["num_objects"] == 0
        assert stats["num_heap_locations"] == 0
    
    def test_get_statistics_with_data(self, variable_factory, object_factory):
        """Test statistics with data."""
        state = PointerAnalysisState()
        
        var1 = variable_factory("x")
        var2 = variable_factory("y")
        
        obj1 = object_factory(AllocKind.OBJECT)
        obj2 = object_factory(AllocKind.LIST)
        obj3 = object_factory(AllocKind.DICT)
        
        state.set_points_to(var1, PointsToSet.singleton(obj1))
        state.set_points_to(var2, PointsToSet.singleton(obj2))
        state.set_field(obj1, attr("field"), PointsToSet.singleton(obj3))
        
        stats = state.get_statistics()
        
        assert stats["num_variables"] == 2
        assert stats["num_objects"] == 3
        assert stats["num_heap_locations"] == 1
    
    def test_constraints_property_initialized(self):
        """Test that constraints are automatically initialized."""
        state = PointerAnalysisState()
        assert state.constraints is not None
        assert len(state.constraints) == 0
    
    def test_call_graph_property_initialized(self):
        """Test that call_graph is automatically initialized."""
        state = PointerAnalysisState()
        assert state.call_graph is not None


class TestStateUsagePatterns:
    """Test realistic state usage patterns."""
    
    def test_assignment_chain(self, variable_factory, object_factory):
        """Test modeling assignment chain: x = y = z = obj."""
        state = PointerAnalysisState()
        
        x = variable_factory("x")
        y = variable_factory("y")
        z = variable_factory("z")
        obj = object_factory(AllocKind.OBJECT)
        
        pts = PointsToSet.singleton(obj)
        state.set_points_to(z, pts)
        state.set_points_to(y, state.get_points_to(z))
        state.set_points_to(x, state.get_points_to(y))
        
        assert obj in state.get_points_to(x)
        assert obj in state.get_points_to(y)
        assert obj in state.get_points_to(z)
    
    def test_field_load_store(self, variable_factory, object_factory):
        """Test modeling field load/store: y = x.field; x.field = z."""
        state = PointerAnalysisState()
        
        x = variable_factory("x")
        y = variable_factory("y")
        z = variable_factory("z")
        
        obj = object_factory(AllocKind.OBJECT)
        val = object_factory(AllocKind.OBJECT, "Val")
        
        # x points to obj
        state.set_points_to(x, PointsToSet.singleton(obj))
        
        # z points to val
        state.set_points_to(z, PointsToSet.singleton(val))
        
        # Store: obj.field = val (via x.field = z)
        state.set_field(obj, attr("field"), state.get_points_to(z))
        
        # Load: y = obj.field (via y = x.field)
        state.set_points_to(y, state.get_field(obj, attr("field")))
        
        # y should now point to val
        assert val in state.get_points_to(y)

