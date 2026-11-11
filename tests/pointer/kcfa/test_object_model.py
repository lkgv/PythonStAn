"""Tests for object model: AllocSite and AbstractObject."""

from tests.pointer.kcfa.builders import make_site
from pythonstan.ir.ir_statements import IRAssign

import pytest
from pythonstan.analysis.pointer.kcfa import (
    AllocKind,
    AllocSite,
    AbstractObject,
    CallStringContext,
)


class TestAllocSite:
    """Tests for AllocSite dataclass."""
    
    def test_basic_creation(self):
        """Test basic AllocSite creation."""
        site = make_site("test.py", 10, 5, AllocKind.OBJECT)
        assert isinstance(site.stmt, IRAssign)
        assert site.line == 10
        assert site.col == 5
        assert site.kind == AllocKind.OBJECT
    
    def test_named_allocation(self):
        """Test AllocSite with name (for functions/classes)."""
        site = make_site("test.py", 20, 0, AllocKind.FUNCTION, "my_func")
        assert site.kind == AllocKind.FUNCTION
        assert site.stmt.name == "my_func"
    
    def test_string_representation_unnamed(self):
        """Test __str__ for unnamed allocation."""
        site = make_site("test.py", 15, 3, AllocKind.LIST)
        assert str(site.stmt) in str(site)
        assert "LIST" in str(site)
    
    def test_string_representation_named(self):
        """Test __str__ for named allocation."""
        site = make_site("module.py", 100, 4, AllocKind.CLASS, "MyClass")
        assert "MyClass" in str(site)
        assert "CLASS" in str(site)
    
    def test_equality_same_location(self):
        """Test that allocations of the identical IR statement are equal."""
        site1 = make_site("test.py", 10, 5, AllocKind.OBJECT)
        site2 = AllocSite(site1.stmt, AllocKind.OBJECT)
        assert site1 == site2
    
    def test_equality_different_location(self):
        """Test that AllocSites at different locations are not equal."""
        site1 = make_site("test.py", 10, 5, AllocKind.OBJECT)
        site2 = make_site("test.py", 11, 5, AllocKind.OBJECT)
        assert site1 != site2
    
    def test_equality_different_kind(self):
        """Test that AllocSites with different kinds are not equal."""
        site1 = make_site("test.py", 10, 5, AllocKind.OBJECT)
        site2 = make_site("test.py", 10, 5, AllocKind.LIST)
        assert site1 != site2
    
    def test_equality_named_vs_unnamed(self):
        """Test that named and unnamed AllocSites differ."""
        site1 = make_site("test.py", 10, 5, AllocKind.FUNCTION)
        site2 = make_site("test.py", 10, 5, AllocKind.FUNCTION, "func")
        assert site1 != site2
    
    def test_hashable(self):
        """Test that AllocSite can be used in sets/dicts."""
        site1 = make_site("test.py", 10, 5, AllocKind.OBJECT)
        site2 = AllocSite(site1.stmt, AllocKind.OBJECT)
        site3 = make_site("test.py", 11, 5, AllocKind.OBJECT)
        
        sites = {site1, site2, site3}
        assert len(sites) == 2  # site1 and site2 are equal
    
    def test_frozen(self):
        """Test that AllocSite is immutable."""
        site = make_site("test.py", 10, 5, AllocKind.OBJECT)
        with pytest.raises(AttributeError):
            site.line = 20
    
    def test_all_alloc_kinds(self):
        """Test creating AllocSite with all allocation kinds."""
        for kind in AllocKind:
            site = make_site("test.py", 1, 0, kind)
            assert site.kind == kind


class TestAbstractObject:
    """Tests for AbstractObject dataclass."""
    
    def test_basic_creation(self, simple_context):
        """Test basic AbstractObject creation."""
        site = make_site("test.py", 10, 5, AllocKind.OBJECT)
        obj = AbstractObject(simple_context, site)
        assert obj.alloc_site == site
        assert obj.context == simple_context
    
    def test_kind_property(self, simple_context):
        """Test that kind property returns alloc_site.kind."""
        site = make_site("test.py", 10, 5, AllocKind.LIST)
        obj = AbstractObject(simple_context, site)
        assert obj.kind == AllocKind.LIST
    
    def test_is_callable_function(self, simple_context):
        """Test is_callable for function objects."""
        site = make_site("test.py", 10, 0, AllocKind.FUNCTION, "func")
        obj = AbstractObject(simple_context, site)
        assert obj.is_callable is True
    
    def test_is_callable_class(self, simple_context):
        """Test is_callable for class objects."""
        site = make_site("test.py", 10, 0, AllocKind.CLASS, "MyClass")
        obj = AbstractObject(simple_context, site)
        assert obj.is_callable is True
    
    def test_is_callable_bound_method(self, simple_context):
        """Test is_callable for bound method objects."""
        site = make_site("test.py", 10, 0, AllocKind.BOUND_METHOD)
        obj = AbstractObject(simple_context, site)
        assert obj.is_callable is True
    
    def test_is_callable_builtin(self, simple_context):
        """Test is_callable for builtin objects."""
        site = make_site("<builtin>", 0, 0, AllocKind.BUILTIN, "len")
        obj = AbstractObject(simple_context, site)
        assert obj.is_callable is True
    
    def test_is_callable_non_callable(self, simple_context):
        """Test is_callable for non-callable objects."""
        for kind in [AllocKind.OBJECT, AllocKind.LIST, AllocKind.DICT,
                     AllocKind.TUPLE, AllocKind.SET, AllocKind.MODULE]:
            site = make_site("test.py", 1, 0, kind)
            obj = AbstractObject(simple_context, site)
            assert obj.is_callable is False
    
    def test_string_representation(self, simple_context):
        """Test __str__ combines site and context."""
        site = make_site("test.py", 10, 5, AllocKind.OBJECT, "obj")
        obj = AbstractObject(simple_context, site)
        obj_str = str(obj)
        assert str(site) in obj_str
        assert "@" in obj_str  # Context separator
    
    def test_equality_same_site_and_context(self):
        """Test objects with same site and context are equal."""
        site = make_site("test.py", 10, 5, AllocKind.OBJECT)
        ctx = CallStringContext((), 2)
        obj1 = AbstractObject(ctx, site)
        obj2 = AbstractObject(ctx, site)
        assert obj1 == obj2
    
    def test_equality_same_site_different_context(self):
        """Test objects with same site but different context are not equal."""
        site = make_site("test.py", 10, 5, AllocKind.OBJECT)
        ctx1 = CallStringContext((), 1)
        ctx2 = CallStringContext((), 2)
        obj1 = AbstractObject(ctx1, site)
        obj2 = AbstractObject(ctx2, site)
        assert obj1 != obj2
    
    def test_equality_different_site_same_context(self, simple_context):
        """Test objects with different site but same context are not equal."""
        site1 = make_site("test.py", 10, 5, AllocKind.OBJECT)
        site2 = make_site("test.py", 11, 5, AllocKind.OBJECT)
        obj1 = AbstractObject(simple_context, site1)
        obj2 = AbstractObject(simple_context, site2)
        assert obj1 != obj2
    
    def test_hashable(self, simple_context):
        """Test that AbstractObject can be used in sets/dicts."""
        site1 = make_site("test.py", 10, 5, AllocKind.OBJECT)
        site2 = make_site("test.py", 11, 5, AllocKind.OBJECT)
        
        obj1 = AbstractObject(simple_context, site1)
        obj2 = AbstractObject(simple_context, site1)  # Same as obj1
        obj3 = AbstractObject(simple_context, site2)
        
        objs = {obj1, obj2, obj3}
        assert len(objs) == 2  # obj1 and obj2 are equal
    
    def test_frozen(self, simple_context):
        """Test that AbstractObject is immutable."""
        site = make_site("test.py", 10, 5, AllocKind.OBJECT)
        obj = AbstractObject(simple_context, site)
        with pytest.raises(AttributeError):
            obj.alloc_site = site
    
    def test_different_contexts_create_different_objects(self, call_site_factory):
        """Test context sensitivity: same site, different contexts."""
        site = make_site("test.py", 10, 0, AllocKind.OBJECT)
        
        # Create different contexts
        ctx1 = CallStringContext((), 2)
        cs = call_site_factory("caller")
        ctx2 = ctx1.append(cs)
        
        obj1 = AbstractObject(ctx1, site)
        obj2 = AbstractObject(ctx2, site)
        
        assert obj1 != obj2
        assert hash(obj1) != hash(obj2)
    
    def test_objects_in_dict(self, simple_context):
        """Test using AbstractObjects as dictionary keys."""
        site1 = make_site("test.py", 10, 0, AllocKind.OBJECT)
        site2 = make_site("test.py", 20, 0, AllocKind.LIST)
        
        obj1 = AbstractObject(simple_context, site1)
        obj2 = AbstractObject(simple_context, site2)
        
        obj_map = {obj1: "value1", obj2: "value2"}
        assert obj_map[obj1] == "value1"
        assert obj_map[obj2] == "value2"


class TestAllocSiteFromIRNode:
    """Tests for AllocSite.from_ir_node factory method."""
    
    def test_from_ir_node_with_ast_node(self):
        """Test creating allocation site from AST node."""
        import ast
        node = IRAssign(ast.parse("x = [1, 2, 3]").body[0])
        site = AllocSite.from_ir_node(node, AllocKind.LIST)
        
        assert site.kind == AllocKind.LIST
        assert site.stmt is node
        assert site.line > 0
        assert site.col >= 0
    
    def test_from_ir_node_without_name(self):
        """Test creating allocation site without name."""
        import ast
        node = IRAssign(ast.parse("x = {}").body[0])
        site = AllocSite.from_ir_node(node, AllocKind.DICT)
        
        assert site.kind == AllocKind.DICT
        assert site.stmt is not None
    
    def test_from_ir_node_different_kinds(self):
        """Test creating allocation sites for different kinds."""
        import ast
        node = IRAssign(ast.parse("x = 1").body[0])
        
        list_site = AllocSite.from_ir_node(node, AllocKind.LIST)
        dict_site = AllocSite.from_ir_node(node, AllocKind.DICT)
        obj_site = AllocSite.from_ir_node(node, AllocKind.OBJECT)
        
        assert list_site.kind == AllocKind.LIST
        assert dict_site.kind == AllocKind.DICT
        assert obj_site.kind == AllocKind.OBJECT

