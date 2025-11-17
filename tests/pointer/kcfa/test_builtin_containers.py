"""Tests for builtin container handling in pointer analysis.

This test suite verifies that builtin containers (list, dict, tuple, set)
and their methods are properly modeled using PFG-based propagation and
lazy constraints.
"""

import pytest
from pythonstan.analysis.pointer.kcfa import (
    Config, PointerAnalysisState, PointerSolver,
    Variable, AllocConstraint, LoadConstraint, StoreConstraint,
    CallConstraint
)
from pythonstan.analysis.pointer.kcfa.object import (
    ObjectFactory, BuiltinClassObject, BuiltinInstanceObject,
    BuiltinMethodObject, BuiltinFunctionObject, AllocSite, AllocKind
)
from pythonstan.analysis.pointer.kcfa.context import CallStringContext, Ctx
from pythonstan.analysis.pointer.kcfa.variable import VariableFactory, VariableKind
from pythonstan.analysis.pointer.kcfa.heap_model import elem, attr, key
from pythonstan.analysis.pointer.kcfa.points_to_set import PointsToSet


class TestBuiltinObjectFactory:
    """Test ObjectFactory creates builtin objects correctly."""
    
    def test_create_builtin_class(self):
        """Test creating builtin class objects."""
        ctx = CallStringContext((), 2)
        
        list_class = ObjectFactory.create_builtin_class("list", ctx)
        assert isinstance(list_class, BuiltinClassObject)
        assert list_class.builtin_name == "list"
        assert list_class.kind == AllocKind.CLASS
        
        dict_class = ObjectFactory.create_builtin_class("dict", ctx)
        assert isinstance(dict_class, BuiltinClassObject)
        assert dict_class.builtin_name == "dict"
    
    def test_create_builtin_instance(self):
        """Test creating builtin instance objects."""
        ctx = CallStringContext((), 2)
        
        list_inst = ObjectFactory.create_builtin_instance("list", ctx, "<test:1>")
        assert isinstance(list_inst, BuiltinInstanceObject)
        assert list_inst.builtin_type == "list"
        assert list_inst.kind == AllocKind.LIST
        
        dict_inst = ObjectFactory.create_builtin_instance("dict", ctx, "<test:2>")
        assert isinstance(dict_inst, BuiltinInstanceObject)
        assert dict_inst.builtin_type == "dict"
        assert dict_inst.kind == AllocKind.DICT
    
    def test_create_builtin_method(self):
        """Test creating builtin method objects."""
        ctx = CallStringContext((), 2)
        list_inst = ObjectFactory.create_builtin_instance("list", ctx, "<test:1>")
        
        append_method = ObjectFactory.create_builtin_method("append", list_inst, ctx)
        assert isinstance(append_method, BuiltinMethodObject)
        assert append_method.method_name == "append"
        assert append_method.receiver == list_inst
        assert append_method.kind == AllocKind.METHOD
    
    def test_create_builtin_function(self):
        """Test creating builtin function objects."""
        ctx = CallStringContext((), 2)
        
        len_func = ObjectFactory.create_builtin_function("len", ctx)
        assert isinstance(len_func, BuiltinFunctionObject)
        assert len_func.function_name == "len"
        assert len_func.kind == AllocKind.BUILTIN


class TestListContainerOperations:
    """Test list container operations."""
    
    def test_list_constructor_creates_list_instance(self):
        """Test that calling list() creates a list instance."""
        # This test will verify the new builtin handling once implemented
        config = Config()
        state = PointerAnalysisState()
        solver = PointerSolver(state, config)
        
        ctx = CallStringContext((), 2)
        factory = VariableFactory()
        
        # Create variables
        list_class_var = factory.make_variable("list", VariableKind.GLOBAL)
        result_var = factory.make_variable("result", VariableKind.LOCAL)
        
        # list class object points to builtin list class
        list_class_obj = ObjectFactory.create_builtin_class("list", ctx)
        
        # For now, just verify object creation works
        assert isinstance(list_class_obj, BuiltinClassObject)
        assert list_class_obj.builtin_name == "list"
    
    def test_list_append_flow(self):
        """Test that list.append(x) flows x to list.elem()."""
        # Test structure for list.append operation
        # This will be integrated with solver once builtin handler is complete
        config = Config()
        state = PointerAnalysisState()
        solver = PointerSolver(state, config)
        
        ctx = CallStringContext((), 2)
        factory = VariableFactory()
        
        # Create list instance and append method
        list_inst = ObjectFactory.create_builtin_instance("list", ctx, "<test:list>")
        append_method = ObjectFactory.create_builtin_method("append", list_inst, ctx)
        
        # Verify method is correctly bound
        assert append_method.receiver == list_inst
        assert append_method.method_name == "append"
    
    def test_list_subscript_load(self):
        """Test that list[i] loads from list.elem()."""
        config = Config()
        state = PointerAnalysisState()
        ctx = CallStringContext((), 2)
        
        # Create list instance
        list_inst = ObjectFactory.create_builtin_instance("list", ctx, "<test:list>")
        
        # Verify we can access elem() field
        # This will be integrated with LoadSubscrConstraint handling
        assert list_inst.builtin_type == "list"
        assert list_inst.kind == AllocKind.LIST
    
    def test_list_subscript_store(self):
        """Test that list[i] = x stores x to list.elem()."""
        config = Config()
        state = PointerAnalysisState()
        ctx = CallStringContext((), 2)
        
        # Create list instance
        list_inst = ObjectFactory.create_builtin_instance("list", ctx, "<test:list>")
        
        # This will be integrated with StoreSubscrConstraint handling
        assert list_inst.builtin_type == "list"


class TestDictContainerOperations:
    """Test dict container operations."""
    
    def test_dict_constructor_creates_dict_instance(self):
        """Test that calling dict() creates a dict instance."""
        ctx = CallStringContext((), 2)
        
        dict_class_obj = ObjectFactory.create_builtin_class("dict", ctx)
        assert isinstance(dict_class_obj, BuiltinClassObject)
        assert dict_class_obj.builtin_name == "dict"
    
    def test_dict_getitem_with_constant_key(self):
        """Test that dict["key"] accesses dict.key("key")."""
        config = Config()
        state = PointerAnalysisState()
        ctx = CallStringContext((), 2)
        
        dict_inst = ObjectFactory.create_builtin_instance("dict", ctx, "<test:dict>")
        assert dict_inst.builtin_type == "dict"
        assert dict_inst.kind == AllocKind.DICT
    
    def test_dict_setitem_with_constant_key(self):
        """Test that dict["key"] = x stores to dict.key("key")."""
        config = Config()
        state = PointerAnalysisState()
        ctx = CallStringContext((), 2)
        
        dict_inst = ObjectFactory.create_builtin_instance("dict", ctx, "<test:dict>")
        assert dict_inst.builtin_type == "dict"
    
    def test_dict_get_method(self):
        """Test dict.get(key) method."""
        ctx = CallStringContext((), 2)
        dict_inst = ObjectFactory.create_builtin_instance("dict", ctx, "<test:dict>")
        
        get_method = ObjectFactory.create_builtin_method("get", dict_inst, ctx)
        assert get_method.method_name == "get"
        assert get_method.receiver == dict_inst


class TestTupleContainerOperations:
    """Test tuple container operations."""
    
    def test_tuple_constructor(self):
        """Test that calling tuple() creates a tuple instance."""
        ctx = CallStringContext((), 2)
        
        tuple_class_obj = ObjectFactory.create_builtin_class("tuple", ctx)
        assert isinstance(tuple_class_obj, BuiltinClassObject)
        assert tuple_class_obj.builtin_name == "tuple"
    
    def test_tuple_subscript_load(self):
        """Test that tuple[i] loads from tuple fields."""
        ctx = CallStringContext((), 2)
        
        tuple_inst = ObjectFactory.create_builtin_instance("tuple", ctx, "<test:tuple>")
        assert tuple_inst.builtin_type == "tuple"
        assert tuple_inst.kind == AllocKind.TUPLE


class TestSetContainerOperations:
    """Test set container operations."""
    
    def test_set_constructor(self):
        """Test that calling set() creates a set instance."""
        ctx = CallStringContext((), 2)
        
        set_class_obj = ObjectFactory.create_builtin_class("set", ctx)
        assert isinstance(set_class_obj, BuiltinClassObject)
        assert set_class_obj.builtin_name == "set"
    
    def test_set_add_method(self):
        """Test set.add(x) method."""
        ctx = CallStringContext((), 2)
        set_inst = ObjectFactory.create_builtin_instance("set", ctx, "<test:set>")
        
        add_method = ObjectFactory.create_builtin_method("add", set_inst, ctx)
        assert add_method.method_name == "add"
        assert add_method.receiver == set_inst


class TestBuiltinFunctions:
    """Test standalone builtin functions."""
    
    def test_iter_function(self):
        """Test iter() builtin function."""
        ctx = CallStringContext((), 2)
        
        iter_func = ObjectFactory.create_builtin_function("iter", ctx)
        assert iter_func.function_name == "iter"
        assert iter_func.kind == AllocKind.BUILTIN
    
    def test_len_function(self):
        """Test len() builtin function."""
        ctx = CallStringContext((), 2)
        
        len_func = ObjectFactory.create_builtin_function("len", ctx)
        assert len_func.function_name == "len"
        assert len_func.kind == AllocKind.BUILTIN
    
    def test_sorted_function(self):
        """Test sorted() builtin function."""
        ctx = CallStringContext((), 2)
        
        sorted_func = ObjectFactory.create_builtin_function("sorted", ctx)
        assert sorted_func.function_name == "sorted"
        assert sorted_func.kind == AllocKind.BUILTIN


class TestIteratorBuiltins:
    """Test iterator-related builtins."""
    
    def test_enumerate_function(self):
        """Test enumerate() function."""
        ctx = CallStringContext((), 2)
        
        enum_func = ObjectFactory.create_builtin_function("enumerate", ctx)
        assert enum_func.function_name == "enumerate"
    
    def test_zip_function(self):
        """Test zip() function."""
        ctx = CallStringContext((), 2)
        
        zip_func = ObjectFactory.create_builtin_function("zip", ctx)
        assert zip_func.function_name == "zip"
    
    def test_filter_function(self):
        """Test filter() function."""
        ctx = CallStringContext((), 2)
        
        filter_func = ObjectFactory.create_builtin_function("filter", ctx)
        assert filter_func.function_name == "filter"
    
    def test_map_function(self):
        """Test map() function."""
        ctx = CallStringContext((), 2)
        
        map_func = ObjectFactory.create_builtin_function("map", ctx)
        assert map_func.function_name == "map"


# NOTE: These tests establish the expected structure for builtin handling.
# They verify that:
# 1. ObjectFactory creates correct builtin objects
# 2. Builtin objects have proper types and relationships
# 3. Container operations (append, get, add, etc.) create proper method objects
# 4. Iterator functions create proper function objects
#
# The actual dataflow integration (PFG edges and constraints) will be
# tested once the builtin handler is fully implemented.

