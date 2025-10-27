"""Tests for object model: AllocSite and AbstractObject."""

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
        site = AllocSite("test.py", 10, 5, AllocKind.OBJECT)
        assert site.file == "test.py"
        assert site.line == 10
        assert site.col == 5
        assert site.kind == AllocKind.OBJECT
        assert site.name is None
    
    def test_named_allocation(self):
        """Test AllocSite with name (for functions/classes)."""
        site = AllocSite("test.py", 20, 0, AllocKind.FUNCTION, "my_func")
        assert site.kind == AllocKind.FUNCTION
        assert site.name == "my_func"
    
    def test_string_representation_unnamed(self):
        """Test __str__ for unnamed allocation."""
        site = AllocSite("test.py", 15, 3, AllocKind.LIST)
        assert str(site) == "test.py:15:3:list"
    
    def test_string_representation_named(self):
        """Test __str__ for named allocation."""
        site = AllocSite("module.py", 100, 4, AllocKind.CLASS, "MyClass")
        assert str(site) == "module.py:100:4:class:MyClass"
    
    def test_equality_same_location(self):
        """Test that AllocSites at same location are equal."""
        site1 = AllocSite("test.py", 10, 5, AllocKind.OBJECT)
        site2 = AllocSite("test.py", 10, 5, AllocKind.OBJECT)
        assert site1 == site2
    
    def test_equality_different_location(self):
        """Test that AllocSites at different locations are not equal."""
        site1 = AllocSite("test.py", 10, 5, AllocKind.OBJECT)
        site2 = AllocSite("test.py", 11, 5, AllocKind.OBJECT)
        assert site1 != site2
    
    def test_equality_different_kind(self):
        """Test that AllocSites with different kinds are not equal."""
        site1 = AllocSite("test.py", 10, 5, AllocKind.OBJECT)
        site2 = AllocSite("test.py", 10, 5, AllocKind.LIST)
        assert site1 != site2
    
    def test_equality_named_vs_unnamed(self):
        """Test that named and unnamed AllocSites differ."""
        site1 = AllocSite("test.py", 10, 5, AllocKind.FUNCTION)
        site2 = AllocSite("test.py", 10, 5, AllocKind.FUNCTION, "func")
        assert site1 != site2
    
    def test_hashable(self):
        """Test that AllocSite can be used in sets/dicts."""
        site1 = AllocSite("test.py", 10, 5, AllocKind.OBJECT)
        site2 = AllocSite("test.py", 10, 5, AllocKind.OBJECT)
        site3 = AllocSite("test.py", 11, 5, AllocKind.OBJECT)
        
        sites = {site1, site2, site3}
        assert len(sites) == 2  # site1 and site2 are equal
    
    def test_frozen(self):
        """Test that AllocSite is immutable."""
        site = AllocSite("test.py", 10, 5, AllocKind.OBJECT)
        with pytest.raises(AttributeError):
            site.line = 20
    
    def test_all_alloc_kinds(self):
        """Test creating AllocSite with all allocation kinds."""
        for kind in AllocKind:
            site = AllocSite("test.py", 1, 0, kind)
            assert site.kind == kind


class TestAbstractObject:
    """Tests for AbstractObject dataclass."""
    
    def test_basic_creation(self, simple_context):
        """Test basic AbstractObject creation."""
        site = AllocSite("test.py", 10, 5, AllocKind.OBJECT)
        obj = AbstractObject(site, simple_context)
        assert obj.alloc_site == site
        assert obj.context == simple_context
    
    def test_kind_property(self, simple_context):
        """Test that kind property returns alloc_site.kind."""
        site = AllocSite("test.py", 10, 5, AllocKind.LIST)
        obj = AbstractObject(site, simple_context)
        assert obj.kind == AllocKind.LIST
    
    def test_is_callable_function(self, simple_context):
        """Test is_callable for function objects."""
        site = AllocSite("test.py", 10, 0, AllocKind.FUNCTION, "func")
        obj = AbstractObject(site, simple_context)
        assert obj.is_callable is True
    
    def test_is_callable_class(self, simple_context):
        """Test is_callable for class objects."""
        site = AllocSite("test.py", 10, 0, AllocKind.CLASS, "MyClass")
        obj = AbstractObject(site, simple_context)
        assert obj.is_callable is True
    
    def test_is_callable_bound_method(self, simple_context):
        """Test is_callable for bound method objects."""
        site = AllocSite("test.py", 10, 0, AllocKind.BOUND_METHOD)
        obj = AbstractObject(site, simple_context)
        assert obj.is_callable is True
    
    def test_is_callable_builtin(self, simple_context):
        """Test is_callable for builtin objects."""
        site = AllocSite("<builtin>", 0, 0, AllocKind.BUILTIN, "len")
        obj = AbstractObject(site, simple_context)
        assert obj.is_callable is True
    
    def test_is_callable_non_callable(self, simple_context):
        """Test is_callable for non-callable objects."""
        for kind in [AllocKind.OBJECT, AllocKind.LIST, AllocKind.DICT,
                     AllocKind.TUPLE, AllocKind.SET, AllocKind.MODULE]:
            site = AllocSite("test.py", 1, 0, kind)
            obj = AbstractObject(site, simple_context)
            assert obj.is_callable is False
    
    def test_string_representation(self, simple_context):
        """Test __str__ combines site and context."""
        site = AllocSite("test.py", 10, 5, AllocKind.OBJECT, "obj")
        obj = AbstractObject(site, simple_context)
        obj_str = str(obj)
        assert "test.py:10:5" in obj_str
        assert "@" in obj_str  # Context separator
    
    def test_equality_same_site_and_context(self):
        """Test objects with same site and context are equal."""
        site = AllocSite("test.py", 10, 5, AllocKind.OBJECT)
        ctx = CallStringContext((), 2)
        obj1 = AbstractObject(site, ctx)
        obj2 = AbstractObject(site, ctx)
        assert obj1 == obj2
    
    def test_equality_same_site_different_context(self):
        """Test objects with same site but different context are not equal."""
        site = AllocSite("test.py", 10, 5, AllocKind.OBJECT)
        ctx1 = CallStringContext((), 1)
        ctx2 = CallStringContext((), 2)
        obj1 = AbstractObject(site, ctx1)
        obj2 = AbstractObject(site, ctx2)
        assert obj1 != obj2
    
    def test_equality_different_site_same_context(self, simple_context):
        """Test objects with different site but same context are not equal."""
        site1 = AllocSite("test.py", 10, 5, AllocKind.OBJECT)
        site2 = AllocSite("test.py", 11, 5, AllocKind.OBJECT)
        obj1 = AbstractObject(site1, simple_context)
        obj2 = AbstractObject(site2, simple_context)
        assert obj1 != obj2
    
    def test_hashable(self, simple_context):
        """Test that AbstractObject can be used in sets/dicts."""
        site1 = AllocSite("test.py", 10, 5, AllocKind.OBJECT)
        site2 = AllocSite("test.py", 11, 5, AllocKind.OBJECT)
        
        obj1 = AbstractObject(site1, simple_context)
        obj2 = AbstractObject(site1, simple_context)  # Same as obj1
        obj3 = AbstractObject(site2, simple_context)
        
        objs = {obj1, obj2, obj3}
        assert len(objs) == 2  # obj1 and obj2 are equal
    
    def test_frozen(self, simple_context):
        """Test that AbstractObject is immutable."""
        site = AllocSite("test.py", 10, 5, AllocKind.OBJECT)
        obj = AbstractObject(site, simple_context)
        with pytest.raises(AttributeError):
            obj.alloc_site = site
    
    def test_different_contexts_create_different_objects(self, call_site_factory):
        """Test context sensitivity: same site, different contexts."""
        site = AllocSite("test.py", 10, 0, AllocKind.OBJECT)
        
        # Create different contexts
        ctx1 = CallStringContext((), 2)
        cs = call_site_factory("caller")
        ctx2 = ctx1.append(cs)
        
        obj1 = AbstractObject(site, ctx1)
        obj2 = AbstractObject(site, ctx2)
        
        assert obj1 != obj2
        assert hash(obj1) != hash(obj2)
    
    def test_objects_in_dict(self, simple_context):
        """Test using AbstractObjects as dictionary keys."""
        site1 = AllocSite("test.py", 10, 0, AllocKind.OBJECT)
        site2 = AllocSite("test.py", 20, 0, AllocKind.LIST)
        
        obj1 = AbstractObject(site1, simple_context)
        obj2 = AbstractObject(site2, simple_context)
        
        obj_map = {obj1: "value1", obj2: "value2"}
        assert obj_map[obj1] == "value1"
        assert obj_map[obj2] == "value2"


class TestAllocSiteFromIRNode:
    """Tests for AllocSite.from_ir_node factory method."""
    
    def test_from_ir_node_with_ast_node(self):
        """Test creating allocation site from AST node."""
        import ast
        node = ast.parse("x = [1, 2, 3]").body[0]
        site = AllocSite.from_ir_node(node, AllocKind.LIST, "mylist")
        
        assert site.kind == AllocKind.LIST
        assert site.name == "mylist"
        assert site.line > 0
        assert site.col >= 0
    
    def test_from_ir_node_without_name(self):
        """Test creating allocation site without name."""
        import ast
        node = ast.parse("x = {}").body[0]
        site = AllocSite.from_ir_node(node, AllocKind.DICT)
        
        assert site.kind == AllocKind.DICT
        assert site.name is None
    
    def test_from_ir_node_different_kinds(self):
        """Test creating allocation sites for different kinds."""
        import ast
        node = ast.parse("x = 1").body[0]
        
        list_site = AllocSite.from_ir_node(node, AllocKind.LIST)
        dict_site = AllocSite.from_ir_node(node, AllocKind.DICT)
        obj_site = AllocSite.from_ir_node(node, AllocKind.OBJECT)
        
        assert list_site.kind == AllocKind.LIST
        assert dict_site.kind == AllocKind.DICT
        assert obj_site.kind == AllocKind.OBJECT

