"""Tests for constraints and constraint management."""

import pytest
from pythonstan.analysis.pointer.kcfa import (
    Constraint,
    CopyConstraint,
    LoadConstraint,
    StoreConstraint,
    AllocConstraint,
    CallConstraint,
    ReturnConstraint,
    ConstraintManager,
    AllocKind,
)
from pythonstan.analysis.pointer.kcfa.heap_model import attr, elem


class TestCopyConstraint:
    """Tests for CopyConstraint."""
    
    def test_creation(self, variable_factory):
        """Test creating copy constraint."""
        src = variable_factory("x")
        tgt = variable_factory("y")
        
        constraint = CopyConstraint(src, tgt)
        assert constraint.source == src
        assert constraint.target == tgt
    
    def test_variables_method(self, variable_factory):
        """Test variables() returns both source and target."""
        src = variable_factory("x")
        tgt = variable_factory("y")
        
        constraint = CopyConstraint(src, tgt)
        vars = constraint.variables()
        
        assert len(vars) == 2
        assert src in vars
        assert tgt in vars
    
    def test_string_representation(self, variable_factory):
        """Test __str__ format: target = source."""
        src = variable_factory("x")
        tgt = variable_factory("y")
        
        constraint = CopyConstraint(src, tgt)
        constraint_str = str(constraint)
        
        assert "=" in constraint_str
        assert "x" in constraint_str
        assert "y" in constraint_str
    
    def test_equality(self, variable_factory):
        """Test constraint equality."""
        src = variable_factory("x")
        tgt = variable_factory("y")
        
        c1 = CopyConstraint(src, tgt)
        c2 = CopyConstraint(src, tgt)
        c3 = CopyConstraint(tgt, src)  # Reversed
        
        assert c1 == c2
        assert c1 != c3
    
    def test_frozen(self, variable_factory):
        """Test that CopyConstraint is immutable."""
        src = variable_factory("x")
        tgt = variable_factory("y")
        constraint = CopyConstraint(src, tgt)
        
        with pytest.raises(AttributeError):
            constraint.source = tgt


class TestLoadConstraint:
    """Tests for LoadConstraint."""
    
    def test_creation(self, variable_factory):
        """Test creating load constraint."""
        base = variable_factory("obj")
        field = attr("name")
        target = variable_factory("result")
        
        constraint = LoadConstraint(base, field, target)
        assert constraint.base == base
        assert constraint.field == field
        assert constraint.target == target
    
    def test_variables_method(self, variable_factory):
        """Test variables() returns base and target (not field)."""
        base = variable_factory("obj")
        field = attr("name")
        target = variable_factory("result")
        
        constraint = LoadConstraint(base, field, target)
        vars = constraint.variables()
        
        assert len(vars) == 2
        assert base in vars
        assert target in vars
    
    def test_string_representation(self, variable_factory):
        """Test __str__ format: target = base.field."""
        base = variable_factory("obj")
        field = attr("name")
        target = variable_factory("result")
        
        constraint = LoadConstraint(base, field, target)
        constraint_str = str(constraint)
        
        assert "=" in constraint_str
        assert "obj" in constraint_str
        assert "result" in constraint_str
        assert ".name" in constraint_str
    
    def test_different_field_kinds(self, variable_factory):
        """Test load with different field kinds."""
        base = variable_factory("obj")
        target = variable_factory("result")
        
        load_attr = LoadConstraint(base, attr("name"), target)
        load_elem = LoadConstraint(base, elem(), target)
        
        assert load_attr != load_elem
    
    def test_frozen(self, variable_factory):
        """Test that LoadConstraint is immutable."""
        base = variable_factory("obj")
        field = attr("name")
        target = variable_factory("result")
        constraint = LoadConstraint(base, field, target)
        
        with pytest.raises(AttributeError):
            constraint.field = attr("other")


class TestStoreConstraint:
    """Tests for StoreConstraint."""
    
    def test_creation(self, variable_factory):
        """Test creating store constraint."""
        base = variable_factory("obj")
        field = attr("name")
        source = variable_factory("value")
        
        constraint = StoreConstraint(base, field, source)
        assert constraint.base == base
        assert constraint.field == field
        assert constraint.source == source
    
    def test_variables_method(self, variable_factory):
        """Test variables() returns base and source (not field)."""
        base = variable_factory("obj")
        field = attr("name")
        source = variable_factory("value")
        
        constraint = StoreConstraint(base, field, source)
        vars = constraint.variables()
        
        assert len(vars) == 2
        assert base in vars
        assert source in vars
    
    def test_string_representation(self, variable_factory):
        """Test __str__ format: base.field = source."""
        base = variable_factory("obj")
        field = attr("name")
        source = variable_factory("value")
        
        constraint = StoreConstraint(base, field, source)
        constraint_str = str(constraint)
        
        assert "=" in constraint_str
        assert "obj" in constraint_str
        assert "value" in constraint_str
        assert ".name" in constraint_str
    
    def test_frozen(self, variable_factory):
        """Test that StoreConstraint is immutable."""
        base = variable_factory("obj")
        field = attr("name")
        source = variable_factory("value")
        constraint = StoreConstraint(base, field, source)
        
        with pytest.raises(AttributeError):
            constraint.source = base


class TestAllocConstraint:
    """Tests for AllocConstraint."""
    
    def test_creation(self, variable_factory, alloc_site_factory):
        """Test creating allocation constraint."""
        target = variable_factory("obj")
        site = alloc_site_factory(AllocKind.OBJECT, "MyObj")
        
        constraint = AllocConstraint(target, site)
        assert constraint.target == target
        assert constraint.alloc_site == site
    
    def test_variables_method(self, variable_factory, alloc_site_factory):
        """Test variables() returns only target."""
        target = variable_factory("obj")
        site = alloc_site_factory(AllocKind.OBJECT)
        
        constraint = AllocConstraint(target, site)
        vars = constraint.variables()
        
        assert len(vars) == 1
        assert target in vars
    
    def test_string_representation(self, variable_factory, alloc_site_factory):
        """Test __str__ format: target = new AllocSite."""
        target = variable_factory("obj")
        site = alloc_site_factory(AllocKind.OBJECT, "MyObj")
        
        constraint = AllocConstraint(target, site)
        constraint_str = str(constraint)
        
        assert "=" in constraint_str
        assert "new" in constraint_str
        assert "obj" in constraint_str
    
    def test_different_alloc_kinds(self, variable_factory, alloc_site_factory):
        """Test allocations of different kinds."""
        target = variable_factory("x")
        
        obj_site = alloc_site_factory(AllocKind.OBJECT)
        list_site = alloc_site_factory(AllocKind.LIST)
        
        alloc_obj = AllocConstraint(target, obj_site)
        alloc_list = AllocConstraint(target, list_site)
        
        assert alloc_obj != alloc_list
    
    def test_frozen(self, variable_factory, alloc_site_factory):
        """Test that AllocConstraint is immutable."""
        target = variable_factory("obj")
        site = alloc_site_factory(AllocKind.OBJECT)
        constraint = AllocConstraint(target, site)
        
        with pytest.raises(AttributeError):
            constraint.target = variable_factory("other")


class TestCallConstraint:
    """Tests for CallConstraint."""
    
    def test_creation_with_return(self, variable_factory):
        """Test creating call constraint with return value."""
        callee = variable_factory("func")
        arg1 = variable_factory("arg1")
        arg2 = variable_factory("arg2")
        target = variable_factory("result")
        
        constraint = CallConstraint(
            callee,
            (arg1, arg2),
            target,
            "test.py:10:0:call"
        )
        
        assert constraint.callee == callee
        assert constraint.args == (arg1, arg2)
        assert constraint.target == target
        assert constraint.call_site == "test.py:10:0:call"
    
    def test_creation_without_return(self, variable_factory):
        """Test creating call constraint without return value."""
        callee = variable_factory("func")
        arg = variable_factory("arg")
        
        constraint = CallConstraint(callee, (arg,), None, "test.py:10:0:call")
        
        assert constraint.target is None
    
    def test_variables_method_with_return(self, variable_factory):
        """Test variables() includes callee, args, and target."""
        callee = variable_factory("func")
        arg1 = variable_factory("arg1")
        arg2 = variable_factory("arg2")
        target = variable_factory("result")
        
        constraint = CallConstraint(callee, (arg1, arg2), target, "site")
        vars = constraint.variables()
        
        assert len(vars) == 4
        assert callee in vars
        assert arg1 in vars
        assert arg2 in vars
        assert target in vars
    
    def test_variables_method_without_return(self, variable_factory):
        """Test variables() without return value."""
        callee = variable_factory("func")
        arg = variable_factory("arg")
        
        constraint = CallConstraint(callee, (arg,), None, "site")
        vars = constraint.variables()
        
        assert len(vars) == 2
        assert callee in vars
        assert arg in vars
    
    def test_string_representation_with_return(self, variable_factory):
        """Test __str__ with return: result = func(arg1, arg2)."""
        callee = variable_factory("func")
        arg1 = variable_factory("arg1")
        arg2 = variable_factory("arg2")
        target = variable_factory("result")
        
        constraint = CallConstraint(callee, (arg1, arg2), target, "site")
        constraint_str = str(constraint)
        
        assert "=" in constraint_str
        assert "(" in constraint_str
        assert ")" in constraint_str
        assert "," in constraint_str
    
    def test_string_representation_without_return(self, variable_factory):
        """Test __str__ without return: func(arg)."""
        callee = variable_factory("func")
        arg = variable_factory("arg")
        
        constraint = CallConstraint(callee, (arg,), None, "site")
        constraint_str = str(constraint)
        
        assert "=" not in constraint_str
        assert "(" in constraint_str
        assert ")" in constraint_str
    
    def test_no_arguments(self, variable_factory):
        """Test call with no arguments."""
        callee = variable_factory("func")
        target = variable_factory("result")
        
        constraint = CallConstraint(callee, (), target, "site")
        vars = constraint.variables()
        
        assert len(vars) == 2
        assert callee in vars
        assert target in vars
    
    def test_frozen(self, variable_factory):
        """Test that CallConstraint is immutable."""
        callee = variable_factory("func")
        constraint = CallConstraint(callee, (), None, "site")
        
        with pytest.raises(AttributeError):
            constraint.callee = variable_factory("other")


class TestReturnConstraint:
    """Tests for ReturnConstraint."""
    
    def test_creation(self, variable_factory):
        """Test creating return constraint."""
        callee_return = variable_factory("$return")
        caller_target = variable_factory("result")
        
        constraint = ReturnConstraint(callee_return, caller_target)
        assert constraint.callee_return == callee_return
        assert constraint.caller_target == caller_target
    
    def test_variables_method(self, variable_factory):
        """Test variables() returns both variables."""
        callee_return = variable_factory("$return")
        caller_target = variable_factory("result")
        
        constraint = ReturnConstraint(callee_return, caller_target)
        vars = constraint.variables()
        
        assert len(vars) == 2
        assert callee_return in vars
        assert caller_target in vars
    
    def test_string_representation(self, variable_factory):
        """Test __str__ format."""
        callee_return = variable_factory("$return")
        caller_target = variable_factory("result")
        
        constraint = ReturnConstraint(callee_return, caller_target)
        constraint_str = str(constraint)
        
        assert "=" in constraint_str
        assert "return" in constraint_str
    
    def test_frozen(self, variable_factory):
        """Test that ReturnConstraint is immutable."""
        callee_return = variable_factory("$return")
        caller_target = variable_factory("result")
        constraint = ReturnConstraint(callee_return, caller_target)
        
        with pytest.raises(AttributeError):
            constraint.callee_return = variable_factory("other")


class TestConstraintManager:
    """Tests for ConstraintManager."""
    
    def test_initialization(self):
        """Test creating empty constraint manager."""
        manager = ConstraintManager()
        assert len(manager) == 0
    
    def test_add_constraint(self, constraint_factory):
        """Test adding constraint returns True for new constraint."""
        manager = ConstraintManager()
        constraint = constraint_factory.copy()
        
        added = manager.add(constraint)
        assert added is True
        assert len(manager) == 1
    
    def test_add_duplicate_constraint(self, constraint_factory):
        """Test adding duplicate constraint returns False."""
        manager = ConstraintManager()
        constraint = constraint_factory.copy()
        
        manager.add(constraint)
        added = manager.add(constraint)
        
        assert added is False
        assert len(manager) == 1
    
    def test_remove_constraint(self, constraint_factory):
        """Test removing constraint returns True if existed."""
        manager = ConstraintManager()
        constraint = constraint_factory.copy()
        
        manager.add(constraint)
        removed = manager.remove(constraint)
        
        assert removed is True
        assert len(manager) == 0
    
    def test_remove_nonexistent_constraint(self, constraint_factory):
        """Test removing nonexistent constraint returns False."""
        manager = ConstraintManager()
        constraint = constraint_factory.copy()
        
        removed = manager.remove(constraint)
        assert removed is False
    
    def test_get_by_variable(self, constraint_factory, variable_factory):
        """Test getting constraints by variable."""
        manager = ConstraintManager()
        
        v1 = variable_factory("x")
        v2 = variable_factory("y")
        v3 = variable_factory("z")
        
        c1 = CopyConstraint(v1, v2)
        c2 = CopyConstraint(v2, v3)
        c3 = CopyConstraint(v1, v3)
        
        manager.add(c1)
        manager.add(c2)
        manager.add(c3)
        
        # v1 appears in c1 and c3
        v1_constraints = manager.get_by_variable(v1)
        assert len(v1_constraints) == 2
        assert c1 in v1_constraints
        assert c3 in v1_constraints
        
        # v2 appears in c1 and c2
        v2_constraints = manager.get_by_variable(v2)
        assert len(v2_constraints) == 2
        assert c1 in v2_constraints
        assert c2 in v2_constraints
        
        # v3 appears in c2 and c3
        v3_constraints = manager.get_by_variable(v3)
        assert len(v3_constraints) == 2
        assert c2 in v3_constraints
        assert c3 in v3_constraints
    
    def test_get_by_variable_empty(self, variable_factory):
        """Test getting constraints for untracked variable."""
        manager = ConstraintManager()
        var = variable_factory("x")
        
        constraints = manager.get_by_variable(var)
        assert len(constraints) == 0
    
    def test_get_by_type(self, constraint_factory):
        """Test getting constraints by type."""
        manager = ConstraintManager()
        
        c1 = constraint_factory.copy()
        c2 = constraint_factory.copy("x2", "y2")
        c3 = constraint_factory.load()
        c4 = constraint_factory.alloc()
        
        manager.add(c1)
        manager.add(c2)
        manager.add(c3)
        manager.add(c4)
        
        # Get all copy constraints
        copy_constraints = manager.get_by_type(CopyConstraint)
        assert len(copy_constraints) == 2
        assert c1 in copy_constraints
        assert c2 in copy_constraints
        
        # Get all load constraints
        load_constraints = manager.get_by_type(LoadConstraint)
        assert len(load_constraints) == 1
        assert c3 in load_constraints
        
        # Get all alloc constraints
        alloc_constraints = manager.get_by_type(AllocConstraint)
        assert len(alloc_constraints) == 1
        assert c4 in alloc_constraints
    
    def test_get_by_type_empty(self):
        """Test getting constraints of type that doesn't exist."""
        manager = ConstraintManager()
        
        constraints = manager.get_by_type(CopyConstraint)
        assert len(constraints) == 0
    
    def test_all(self, constraint_factory):
        """Test getting all constraints."""
        manager = ConstraintManager()
        
        c1 = constraint_factory.copy()
        c2 = constraint_factory.load()
        c3 = constraint_factory.store()
        
        manager.add(c1)
        manager.add(c2)
        manager.add(c3)
        
        all_constraints = manager.all()
        assert len(all_constraints) == 3
        assert c1 in all_constraints
        assert c2 in all_constraints
        assert c3 in all_constraints
    
    def test_remove_updates_variable_index(self, constraint_factory, variable_factory):
        """Test that removing constraint updates variable index."""
        manager = ConstraintManager()
        
        v1 = variable_factory("x")
        v2 = variable_factory("y")
        c = CopyConstraint(v1, v2)
        
        manager.add(c)
        assert len(manager.get_by_variable(v1)) == 1
        
        manager.remove(c)
        assert len(manager.get_by_variable(v1)) == 0
        assert len(manager.get_by_variable(v2)) == 0
    
    def test_remove_updates_type_index(self, constraint_factory):
        """Test that removing constraint updates type index."""
        manager = ConstraintManager()
        
        c1 = constraint_factory.copy()
        c2 = constraint_factory.copy("x2", "y2")
        
        manager.add(c1)
        manager.add(c2)
        assert len(manager.get_by_type(CopyConstraint)) == 2
        
        manager.remove(c1)
        assert len(manager.get_by_type(CopyConstraint)) == 1
    
    def test_multiple_constraint_types(self, constraint_factory):
        """Test manager with multiple constraint types."""
        manager = ConstraintManager()
        
        copy = constraint_factory.copy()
        load = constraint_factory.load()
        store = constraint_factory.store()
        alloc = constraint_factory.alloc()
        call = constraint_factory.call()
        ret = constraint_factory.ret()
        
        manager.add(copy)
        manager.add(load)
        manager.add(store)
        manager.add(alloc)
        manager.add(call)
        manager.add(ret)
        
        assert len(manager) == 6
        assert len(manager.get_by_type(CopyConstraint)) == 1
        assert len(manager.get_by_type(LoadConstraint)) == 1
        assert len(manager.get_by_type(StoreConstraint)) == 1
        assert len(manager.get_by_type(AllocConstraint)) == 1
        assert len(manager.get_by_type(CallConstraint)) == 1
        assert len(manager.get_by_type(ReturnConstraint)) == 1


class TestConstraintUsagePatterns:
    """Test realistic constraint usage patterns."""
    
    def test_simple_assignment_sequence(self, variable_factory):
        """Test modeling: x = obj; y = x; z = y."""
        manager = ConstraintManager()
        
        obj = variable_factory("obj")
        x = variable_factory("x")
        y = variable_factory("y")
        z = variable_factory("z")
        
        # Each assignment creates a copy constraint
        manager.add(CopyConstraint(obj, x))
        manager.add(CopyConstraint(x, y))
        manager.add(CopyConstraint(y, z))
        
        assert len(manager) == 3
        
        # z is involved in 1 constraint
        assert len(manager.get_by_variable(z)) == 1
        
        # y is involved in 2 constraints
        assert len(manager.get_by_variable(y)) == 2
    
    def test_field_access_pattern(self, variable_factory):
        """Test modeling: obj.field = value; result = obj.field."""
        manager = ConstraintManager()
        
        obj = variable_factory("obj")
        value = variable_factory("value")
        result = variable_factory("result")
        field = attr("name")
        
        # Store
        manager.add(StoreConstraint(obj, field, value))
        
        # Load
        manager.add(LoadConstraint(obj, field, result))
        
        assert len(manager) == 2
        assert len(manager.get_by_type(StoreConstraint)) == 1
        assert len(manager.get_by_type(LoadConstraint)) == 1

