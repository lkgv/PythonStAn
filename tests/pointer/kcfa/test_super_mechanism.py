"""Tests for super() mechanism and MRO-based method resolution.

This test suite verifies that super() works correctly with:
- SuperObject creation via builtin handler
- SuperResolveConstraint for argument resolution
- Field access via InheritanceConstraint and PFG edges
- Method binding to instances
"""

import pytest
from pythonstan.analysis.pointer.kcfa import (
    Config, PointerAnalysisState, PointerSolver,
    Variable, AllocConstraint, CallConstraint,
    SuperResolveConstraint
)
from pythonstan.analysis.pointer.kcfa.object import (
    ObjectFactory, SuperObject, ClassObject, InstanceObject,
    AllocSite, AllocKind
)
from pythonstan.analysis.pointer.kcfa.context import CallStringContext, Ctx
from pythonstan.analysis.pointer.kcfa.variable import VariableFactory, VariableKind
from pythonstan.analysis.pointer.kcfa.heap_model import attr
from pythonstan.analysis.pointer.kcfa.points_to_set import PointsToSet
from pythonstan.analysis.pointer.kcfa.builtin_api_handler import BuiltinAPIHandler


class TestSuperObjectFactory:
    """Test ObjectFactory creates SuperObject correctly."""
    
    def test_create_super_without_class(self):
        """Test creating unresolved SuperObject."""
        ctx = CallStringContext((), 2)
        
        super_obj = ObjectFactory.create_super(ctx, "<test:super>")
        assert isinstance(super_obj, SuperObject)
        assert super_obj.current_class is None
        assert super_obj.instance_obj is None
        assert super_obj.kind == AllocKind.OBJECT
    
    def test_create_super_with_class(self):
        """Test creating SuperObject with current_class."""
        ctx = CallStringContext((), 2)
        
        # Create a class object
        class_alloc = AllocSite(stmt="<test:class>", kind=AllocKind.CLASS)
        class_obj = ClassObject(context=ctx, alloc_site=class_alloc, container_scope=None, ir=None)
        
        super_obj = ObjectFactory.create_super(ctx, "<test:super>", current_class=class_obj)
        assert isinstance(super_obj, SuperObject)
        assert super_obj.current_class == class_obj
        assert super_obj.instance_obj is None
    
    def test_create_super_with_instance(self):
        """Test creating SuperObject with instance_obj."""
        ctx = CallStringContext((), 2)
        
        # Create class and instance
        class_alloc = AllocSite(stmt="<test:class>", kind=AllocKind.CLASS)
        class_obj = ClassObject(context=ctx, alloc_site=class_alloc, container_scope=None, ir=None)
        
        inst_alloc = AllocSite(stmt="<test:inst>", kind=AllocKind.INSTANCE)
        inst_obj = InstanceObject(context=ctx, alloc_site=inst_alloc, class_obj=class_obj)
        
        super_obj = ObjectFactory.create_super(ctx, "<test:super>", current_class=class_obj, instance_obj=inst_obj)
        assert isinstance(super_obj, SuperObject)
        assert super_obj.current_class == class_obj
        assert super_obj.instance_obj == inst_obj


class TestSuperResolveConstraint:
    """Test SuperResolveConstraint structure."""
    
    def test_explicit_super_constraint(self):
        """Test SuperResolveConstraint for super(Class, obj)."""
        factory = VariableFactory()
        target = factory.make_variable("super_result", VariableKind.LOCAL)
        class_var = factory.make_variable("MyClass", VariableKind.LOCAL)
        instance_var = factory.make_variable("self", VariableKind.LOCAL)
        
        constraint = SuperResolveConstraint(
            target=target,
            class_var=class_var,
            instance_var=instance_var,
            implicit=False
        )
        
        assert constraint.target == target
        assert constraint.class_var == class_var
        assert constraint.instance_var == instance_var
        assert not constraint.implicit
        assert target in constraint.variables()
        assert class_var in constraint.variables()
        assert instance_var in constraint.variables()
    
    def test_implicit_super_constraint(self):
        """Test SuperResolveConstraint for super()."""
        factory = VariableFactory()
        target = factory.make_variable("super_result", VariableKind.LOCAL)
        
        constraint = SuperResolveConstraint(
            target=target,
            class_var=None,
            instance_var=None,
            implicit=True
        )
        
        assert constraint.target == target
        assert constraint.class_var is None
        assert constraint.instance_var is None
        assert constraint.implicit
        assert target in constraint.variables()


class TestBuiltinSuperHandler:
    """Test builtin super() handler generates correct constraints."""
    
    def test_super_handler_explicit_form(self):
        """Test super(Class, obj) generates allocation + resolve constraints."""
        config = Config()
        state = PointerAnalysisState()
        handler = BuiltinAPIHandler(state, config)
        
        ctx = CallStringContext((), 2)
        factory = VariableFactory()
        
        # Create call constraint for super(Class, obj)
        target = factory.make_variable("s", VariableKind.LOCAL)
        class_var = factory.make_variable("MyClass", VariableKind.LOCAL)
        instance_var = factory.make_variable("self", VariableKind.LOCAL)
        
        call = CallConstraint(
            callee=factory.make_variable("super", VariableKind.GLOBAL),
            args=(class_var, instance_var),
            kwargs=frozenset(),
            target=target,
            call_site="test:1"
        )
        
        # Handle super() call
        from pythonstan.analysis.pointer.kcfa.context import Scope
        # Note: Scope needs proper initialization, using None for test
        constraints = handler._handle_super(None, ctx, call)
        
        # Should generate AllocConstraint + SuperResolveConstraint
        assert len(constraints) == 2
        
        # First: AllocConstraint for SuperObject
        alloc_constraint = constraints[0]
        assert isinstance(alloc_constraint, AllocConstraint)
        assert alloc_constraint.target == target
        
        # Second: SuperResolveConstraint to populate class/instance
        resolve_constraint = constraints[1]
        assert isinstance(resolve_constraint, SuperResolveConstraint)
        assert resolve_constraint.target == target
        assert resolve_constraint.class_var == class_var
        assert resolve_constraint.instance_var == instance_var
        assert not resolve_constraint.implicit
    
    def test_super_handler_implicit_form(self):
        """Test super() generates allocation + resolve constraints."""
        config = Config()
        state = PointerAnalysisState()
        handler = BuiltinAPIHandler(state, config)
        
        ctx = CallStringContext((), 2)
        factory = VariableFactory()
        
        # Create call constraint for super()
        target = factory.make_variable("s", VariableKind.LOCAL)
        
        call = CallConstraint(
            callee=factory.make_variable("super", VariableKind.GLOBAL),
            args=(),
            kwargs=frozenset(),
            target=target,
            call_site="test:1"
        )
        
        # Handle super() call
        constraints = handler._handle_super(None, ctx, call)
        
        # Should generate AllocConstraint + SuperResolveConstraint
        assert len(constraints) == 2
        
        # First: AllocConstraint
        alloc_constraint = constraints[0]
        assert isinstance(alloc_constraint, AllocConstraint)
        
        # Second: SuperResolveConstraint with implicit flag
        resolve_constraint = constraints[1]
        assert isinstance(resolve_constraint, SuperResolveConstraint)
        assert resolve_constraint.implicit


class TestSuperObjectFieldResolution:
    """Test SuperObject field resolution via state.get_field().
    
    Note: These tests verify the structure is set up correctly.
    Full integration testing requires complete IR and class hierarchy.
    """
    
    def test_super_object_has_field_method(self):
        """Test that SuperObject can be used with state.get_field()."""
        ctx = CallStringContext((), 2)
        
        super_obj = ObjectFactory.create_super(ctx, "<test:super>")
        
        # SuperObject is an AbstractObject, should work with get_field
        assert isinstance(super_obj, SuperObject)
        assert hasattr(super_obj, 'context')
        assert hasattr(super_obj, 'alloc_site')
        assert hasattr(super_obj, 'current_class')
        assert hasattr(super_obj, 'instance_obj')


# NOTE: Full integration tests for super() field resolution require:
# 1. Complete IR class hierarchy with base classes
# 2. Function objects with methods
# 3. Instance objects for method binding
# 4. Full solver execution with constraint propagation
#
# These tests verify the component structure is correct. Integration
# testing should be done with real Python code examples using super().

