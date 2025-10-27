"""Tests for program variables: Variable, Scope, VariableKind."""

import pytest
from pythonstan.analysis.pointer.kcfa import (
    Variable,
    VariableKind,
    Scope,
    CallStringContext,
)


class TestScope:
    """Tests for Scope dataclass."""
    
    def test_module_scope_creation(self):
        """Test creating a module scope."""
        scope = Scope("mymodule", "module")
        assert scope.name == "mymodule"
        assert scope.kind == "module"
    
    def test_function_scope_creation(self):
        """Test creating a function scope."""
        scope = Scope("mymodule.my_func", "function")
        assert scope.name == "mymodule.my_func"
        assert scope.kind == "function"
    
    def test_method_scope_creation(self):
        """Test creating a method scope."""
        scope = Scope("mymodule.MyClass.my_method", "method")
        assert scope.name == "mymodule.MyClass.my_method"
        assert scope.kind == "method"
    
    def test_class_scope_creation(self):
        """Test creating a class scope."""
        scope = Scope("mymodule.MyClass", "class")
        assert scope.name == "mymodule.MyClass"
        assert scope.kind == "class"
    
    def test_string_representation(self):
        """Test __str__ returns scope name."""
        scope = Scope("mymodule.func", "function")
        assert str(scope) == "mymodule.func"
    
    def test_equality(self):
        """Test scope equality."""
        scope1 = Scope("test", "function")
        scope2 = Scope("test", "function")
        scope3 = Scope("test", "method")
        assert scope1 == scope2
        assert scope1 != scope3
    
    def test_frozen(self):
        """Test that Scope is immutable."""
        scope = Scope("test", "function")
        with pytest.raises(AttributeError):
            scope.name = "other"


class TestVariableKind:
    """Tests for VariableKind enum."""
    
    def test_all_kinds_exist(self):
        """Test that all expected variable kinds are defined."""
        assert VariableKind.LOCAL
        assert VariableKind.PARAMETER
        assert VariableKind.GLOBAL
        assert VariableKind.TEMPORARY
        assert VariableKind.CONSTANT
    
    def test_kind_values(self):
        """Test kind enum values."""
        assert VariableKind.LOCAL.value == "local"
        assert VariableKind.PARAMETER.value == "parameter"
        assert VariableKind.GLOBAL.value == "global"
        assert VariableKind.TEMPORARY.value == "temporary"
        assert VariableKind.CONSTANT.value == "constant"


class TestVariable:
    """Tests for Variable dataclass."""
    
    def test_basic_creation(self, function_scope, simple_context):
        """Test basic Variable creation."""
        var = Variable("x", function_scope, simple_context)
        assert var.name == "x"
        assert var.scope == function_scope
        assert var.context == simple_context
        assert var.kind == VariableKind.LOCAL  # Default
    
    def test_creation_with_kind(self, function_scope, simple_context):
        """Test Variable creation with explicit kind."""
        var = Variable("arg", function_scope, simple_context, VariableKind.PARAMETER)
        assert var.kind == VariableKind.PARAMETER
    
    def test_local_variable(self, function_scope, simple_context):
        """Test creating a local variable."""
        var = Variable("local_var", function_scope, simple_context, VariableKind.LOCAL)
        assert var.kind == VariableKind.LOCAL
        assert var.is_temporary is False
        assert var.is_global is False
    
    def test_parameter_variable(self, function_scope, simple_context):
        """Test creating a parameter variable."""
        var = Variable("param", function_scope, simple_context, VariableKind.PARAMETER)
        assert var.kind == VariableKind.PARAMETER
        assert var.is_temporary is False
        assert var.is_global is False
    
    def test_global_variable(self, module_scope, simple_context):
        """Test creating a global variable."""
        var = Variable("GLOBAL_VAR", module_scope, simple_context, VariableKind.GLOBAL)
        assert var.kind == VariableKind.GLOBAL
        assert var.is_global is True
        assert var.is_temporary is False
    
    def test_temporary_variable(self, function_scope, simple_context):
        """Test creating a temporary variable."""
        var = Variable("$temp_1", function_scope, simple_context, VariableKind.TEMPORARY)
        assert var.kind == VariableKind.TEMPORARY
        assert var.is_temporary is True
        assert var.is_global is False
    
    def test_constant_variable(self, module_scope, simple_context):
        """Test creating a constant variable."""
        var = Variable("42", module_scope, simple_context, VariableKind.CONSTANT)
        assert var.kind == VariableKind.CONSTANT
    
    def test_string_representation(self, function_scope, simple_context):
        """Test __str__ format: scope::name@context."""
        var = Variable("x", function_scope, simple_context)
        var_str = str(var)
        assert "::" in var_str
        assert "@" in var_str
        assert "x" in var_str
        assert function_scope.name in var_str
    
    def test_equality_same_attributes(self):
        """Test variables with same attributes are equal."""
        scope = Scope("test", "function")
        ctx = CallStringContext((), 2)
        var1 = Variable("x", scope, ctx, VariableKind.LOCAL)
        var2 = Variable("x", scope, ctx, VariableKind.LOCAL)
        assert var1 == var2
    
    def test_equality_different_name(self, function_scope, simple_context):
        """Test variables with different names are not equal."""
        var1 = Variable("x", function_scope, simple_context)
        var2 = Variable("y", function_scope, simple_context)
        assert var1 != var2
    
    def test_equality_different_scope(self, simple_context):
        """Test variables with different scopes are not equal."""
        scope1 = Scope("func1", "function")
        scope2 = Scope("func2", "function")
        var1 = Variable("x", scope1, simple_context)
        var2 = Variable("x", scope2, simple_context)
        assert var1 != var2
    
    def test_equality_different_context(self, function_scope):
        """Test variables with different contexts are not equal."""
        ctx1 = CallStringContext((), 1)
        ctx2 = CallStringContext((), 2)
        var1 = Variable("x", function_scope, ctx1)
        var2 = Variable("x", function_scope, ctx2)
        assert var1 != var2
    
    def test_equality_different_kind(self, function_scope, simple_context):
        """Test variables with different kinds are not equal."""
        var1 = Variable("x", function_scope, simple_context, VariableKind.LOCAL)
        var2 = Variable("x", function_scope, simple_context, VariableKind.PARAMETER)
        assert var1 != var2
    
    def test_hashable(self, function_scope, simple_context):
        """Test that Variable can be used in sets/dicts."""
        var1 = Variable("x", function_scope, simple_context)
        var2 = Variable("x", function_scope, simple_context)  # Same as var1
        var3 = Variable("y", function_scope, simple_context)
        
        vars = {var1, var2, var3}
        assert len(vars) == 2  # var1 and var2 are equal
    
    def test_frozen(self, function_scope, simple_context):
        """Test that Variable is immutable."""
        var = Variable("x", function_scope, simple_context)
        with pytest.raises(AttributeError):
            var.name = "y"
    
    def test_variables_in_dict(self, function_scope, simple_context):
        """Test using Variables as dictionary keys."""
        var1 = Variable("x", function_scope, simple_context)
        var2 = Variable("y", function_scope, simple_context)
        
        var_map = {var1: "value1", var2: "value2"}
        assert var_map[var1] == "value1"
        assert var_map[var2] == "value2"
    
    def test_context_sensitivity(self, function_scope, call_site_factory):
        """Test that context distinguishes same-named variables."""
        ctx1 = CallStringContext((), 2)
        cs = call_site_factory("caller")
        ctx2 = ctx1.append(cs)
        
        var1 = Variable("x", function_scope, ctx1)
        var2 = Variable("x", function_scope, ctx2)
        
        assert var1 != var2
        assert hash(var1) != hash(var2)
    
    def test_scope_distinguishes_variables(self, simple_context):
        """Test that scope distinguishes same-named variables."""
        scope1 = Scope("func1", "function")
        scope2 = Scope("func2", "function")
        
        var1 = Variable("x", scope1, simple_context)
        var2 = Variable("x", scope2, simple_context)
        
        assert var1 != var2
    
    def test_is_temporary_property(self, function_scope, simple_context):
        """Test is_temporary property."""
        temp_var = Variable("$t", function_scope, simple_context, VariableKind.TEMPORARY)
        local_var = Variable("x", function_scope, simple_context, VariableKind.LOCAL)
        
        assert temp_var.is_temporary is True
        assert local_var.is_temporary is False
    
    def test_is_global_property(self, simple_context):
        """Test is_global property."""
        module_scope = Scope("module", "module")
        func_scope = Scope("module.func", "function")
        
        global_var = Variable("G", module_scope, simple_context, VariableKind.GLOBAL)
        local_var = Variable("x", func_scope, simple_context, VariableKind.LOCAL)
        
        assert global_var.is_global is True
        assert local_var.is_global is False


class TestVariableFactory:
    """Tests for VariableFactory helper."""
    
    def test_factory_creation(self):
        """Test that VariableFactory can be created."""
        from pythonstan.analysis.pointer.kcfa.variable import VariableFactory
        factory = VariableFactory()
        assert factory is not None
    
    def test_factory_make_variable(self, simple_context):
        """Test creating variables with factory."""
        from pythonstan.analysis.pointer.kcfa.variable import VariableFactory, Scope, VariableKind
        from pythonstan.analysis.pointer.kcfa import Variable
        
        factory = VariableFactory()
        scope = Scope("test_func", "function")
        
        var = factory.make_variable("x", scope, simple_context, VariableKind.LOCAL)
        
        assert isinstance(var, Variable)
        assert var.name == "x"
        assert var.scope == scope
        assert var.context == simple_context
        assert var.kind == VariableKind.LOCAL
    # - Test variable reuse/caching


class TestVariableUsagePatterns:
    """Test realistic variable usage patterns."""
    
    def test_function_parameters_and_locals(self, simple_context):
        """Test modeling function parameters and local variables."""
        func_scope = Scope("test.my_func", "function")
        
        # Parameters
        param1 = Variable("arg1", func_scope, simple_context, VariableKind.PARAMETER)
        param2 = Variable("arg2", func_scope, simple_context, VariableKind.PARAMETER)
        
        # Locals
        local1 = Variable("result", func_scope, simple_context, VariableKind.LOCAL)
        local2 = Variable("temp", func_scope, simple_context, VariableKind.LOCAL)
        
        # Temporaries
        temp1 = Variable("$t1", func_scope, simple_context, VariableKind.TEMPORARY)
        
        all_vars = {param1, param2, local1, local2, temp1}
        assert len(all_vars) == 5
    
    def test_global_vs_local_same_name(self, simple_context):
        """Test that global and local variables with same name are distinct."""
        module_scope = Scope("mymodule", "module")
        func_scope = Scope("mymodule.func", "function")
        
        global_var = Variable("config", module_scope, simple_context, VariableKind.GLOBAL)
        local_var = Variable("config", func_scope, simple_context, VariableKind.LOCAL)
        
        assert global_var != local_var
    
    def test_method_self_parameter(self, simple_context):
        """Test modeling 'self' parameter in method."""
        method_scope = Scope("MyClass.my_method", "method")
        self_param = Variable("self", method_scope, simple_context, VariableKind.PARAMETER)
        
        assert self_param.kind == VariableKind.PARAMETER
        assert "self" in str(self_param)

