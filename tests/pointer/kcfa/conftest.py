"""Pytest fixtures for k-CFA pointer analysis tests.

This module provides reusable fixtures and helper functions for testing the
k-CFA pointer analysis implementation.
"""

import pytest
from pythonstan.analysis.pointer.kcfa import (
    # Core types
    AllocKind,
    AllocSite,
    AbstractObject,
    Variable,
    VariableKind,
    Scope,
    Field,
    FieldKind,
    PointsToSet,
    PointerAnalysisState,
    # Context system
    CallSite,
    AbstractContext,
    CallStringContext,
    ObjectContext,
    TypeContext,
    ReceiverContext,
    HybridContext,
    # Constraints
    CopyConstraint,
    LoadConstraint,
    StoreConstraint,
    AllocConstraint,
    CallConstraint,
    ReturnConstraint,
    ConstraintManager,
    # Configuration
    ContextPolicy,
    ContextSelector,
)


# ============================================================================
# Context Fixtures
# ============================================================================

@pytest.fixture
def empty_context() -> AbstractContext:
    """Create an empty context (0-CFA)."""
    return CallStringContext((), 0)


@pytest.fixture
def simple_context() -> AbstractContext:
    """Create a simple 2-CFA context for testing."""
    return CallStringContext((), 2)


@pytest.fixture
def call_site_factory():
    """Factory for creating call sites with incrementing IDs."""
    counter = {"count": 0}
    
    def _make_call_site(fn: str = "test_fn", bb: str = None) -> CallSite:
        counter["count"] += 1
        site_id = f"test.py:10:{counter['count']}:call"
        return CallSite(site_id, fn, bb, counter["count"])
    
    return _make_call_site


@pytest.fixture
def context_with_calls(call_site_factory) -> CallStringContext:
    """Create a 2-CFA context with two call sites."""
    cs1 = call_site_factory("caller1")
    cs2 = call_site_factory("caller2")
    ctx = CallStringContext((), 2)
    ctx = ctx.append(cs1)
    ctx = ctx.append(cs2)
    return ctx


# ============================================================================
# Scope and Variable Fixtures
# ============================================================================

@pytest.fixture
def module_scope() -> Scope:
    """Create a module-level scope."""
    return Scope("test_module", "module")


@pytest.fixture
def function_scope() -> Scope:
    """Create a function scope."""
    return Scope("test_module.test_function", "function")


@pytest.fixture
def method_scope() -> Scope:
    """Create a method scope."""
    return Scope("test_module.TestClass.test_method", "method")


@pytest.fixture
def variable_factory(simple_context):
    """Factory for creating variables."""
    def _make_var(
        name: str,
        scope: Scope = None,
        context: AbstractContext = None,
        kind: VariableKind = VariableKind.LOCAL
    ) -> Variable:
        if scope is None:
            scope = Scope("test_scope", "function")
        if context is None:
            context = simple_context
        return Variable(name, scope, context, kind)
    
    return _make_var


# ============================================================================
# Object and Allocation Site Fixtures
# ============================================================================

@pytest.fixture
def alloc_site_factory():
    """Factory for creating allocation sites with incrementing line numbers."""
    counter = {"line": 0}
    
    def _make_alloc(
        kind: AllocKind = AllocKind.OBJECT,
        name: str = None,
        file: str = "test.py"
    ) -> AllocSite:
        counter["line"] += 1
        return AllocSite(file, counter["line"], 0, kind, name)
    
    return _make_alloc


@pytest.fixture
def object_factory(alloc_site_factory, simple_context):
    """Factory for creating abstract objects."""
    def _make_obj(
        kind: AllocKind = AllocKind.OBJECT,
        name: str = None,
        context: AbstractContext = None
    ) -> AbstractObject:
        site = alloc_site_factory(kind, name)
        if context is None:
            context = simple_context
        return AbstractObject(site, context)
    
    return _make_obj


# ============================================================================
# Field Fixtures
# ============================================================================

@pytest.fixture
def field_factory():
    """Factory for creating fields."""
    def _make_field(kind: FieldKind = FieldKind.ATTRIBUTE, name: str = None) -> Field:
        if kind == FieldKind.ATTRIBUTE:
            if name is None:
                name = "test_attr"
            return Field(kind, name)
        else:
            return Field(kind, None)
    
    return _make_field


# ============================================================================
# Points-To Set Fixtures
# ============================================================================

@pytest.fixture
def pts_factory(object_factory):
    """Factory for creating points-to sets."""
    def _make_pts(*kinds: AllocKind) -> PointsToSet:
        if not kinds:
            return PointsToSet.empty()
        objs = [object_factory(kind) for kind in kinds]
        return PointsToSet(frozenset(objs))
    
    return _make_pts


# ============================================================================
# State Fixtures
# ============================================================================

@pytest.fixture
def empty_state() -> PointerAnalysisState:
    """Create an empty analysis state."""
    return PointerAnalysisState()


@pytest.fixture
def state_with_data(empty_state, variable_factory, object_factory):
    """Create a state with some test data."""
    state = empty_state
    
    # Add some variables with points-to sets
    v1 = variable_factory("x")
    v2 = variable_factory("y")
    obj1 = object_factory(AllocKind.OBJECT, "Obj1")
    obj2 = object_factory(AllocKind.LIST, "List1")
    
    state.set_points_to(v1, PointsToSet.singleton(obj1))
    state.set_points_to(v2, PointsToSet.singleton(obj2))
    
    return state


# ============================================================================
# Constraint Fixtures
# ============================================================================

@pytest.fixture
def constraint_manager() -> ConstraintManager:
    """Create an empty constraint manager."""
    return ConstraintManager()


@pytest.fixture
def constraint_factory(variable_factory, alloc_site_factory, field_factory):
    """Factory for creating various constraint types."""
    class ConstraintFactory:
        def copy(self, source: str = "src", target: str = "tgt") -> CopyConstraint:
            return CopyConstraint(
                variable_factory(source),
                variable_factory(target)
            )
        
        def load(
            self,
            base: str = "base",
            field_name: str = "attr",
            target: str = "tgt"
        ) -> LoadConstraint:
            return LoadConstraint(
                variable_factory(base),
                field_factory(FieldKind.ATTRIBUTE, field_name),
                variable_factory(target)
            )
        
        def store(
            self,
            base: str = "base",
            field_name: str = "attr",
            source: str = "src"
        ) -> StoreConstraint:
            return StoreConstraint(
                variable_factory(base),
                field_factory(FieldKind.ATTRIBUTE, field_name),
                variable_factory(source)
            )
        
        def alloc(
            self,
            target: str = "tgt",
            kind: AllocKind = AllocKind.OBJECT
        ) -> AllocConstraint:
            return AllocConstraint(
                variable_factory(target),
                alloc_site_factory(kind)
            )
        
        def call(
            self,
            callee: str = "fn",
            args: list = None,
            target: str = "result",
            call_site: str = "test.py:10:0:call"
        ) -> CallConstraint:
            if args is None:
                args = []
            arg_vars = tuple(variable_factory(arg) for arg in args)
            return CallConstraint(
                variable_factory(callee),
                arg_vars,
                variable_factory(target) if target else None,
                call_site
            )
        
        def ret(
            self,
            callee_return: str = "$return",
            caller_target: str = "result"
        ) -> ReturnConstraint:
            return ReturnConstraint(
                variable_factory(callee_return),
                variable_factory(caller_target)
            )
    
    return ConstraintFactory()


# ============================================================================
# Context Selector Fixtures
# ============================================================================

@pytest.fixture
def context_selector() -> ContextSelector:
    """Create a context selector with default 2-CFA policy."""
    return ContextSelector(ContextPolicy.CALL_2)


@pytest.fixture
def insensitive_selector() -> ContextSelector:
    """Create an insensitive (0-CFA) context selector."""
    return ContextSelector(ContextPolicy.INSENSITIVE)


@pytest.fixture
def object_sensitive_selector() -> ContextSelector:
    """Create a 1-object-sensitive context selector."""
    return ContextSelector(ContextPolicy.OBJ_1)


# ============================================================================
# Helper Functions
# ============================================================================

def make_var(
    name: str,
    scope_name: str = "test",
    context: AbstractContext = None
) -> Variable:
    """Helper to create a variable quickly.
    
    Args:
        name: Variable name
        scope_name: Scope name (default: "test")
        context: Context (default: empty 0-CFA)
    
    Returns:
        Created variable
    """
    if context is None:
        context = CallStringContext((), 0)
    scope = Scope(scope_name, "function")
    return Variable(name, scope, context, VariableKind.LOCAL)


def make_obj(
    kind: AllocKind = AllocKind.OBJECT,
    name: str = None,
    line: int = 1,
    context: AbstractContext = None
) -> AbstractObject:
    """Helper to create an abstract object quickly.
    
    Args:
        kind: Allocation kind
        name: Optional name
        line: Line number (default: 1)
        context: Context (default: empty 0-CFA)
    
    Returns:
        Created abstract object
    """
    if context is None:
        context = CallStringContext((), 0)
    site = AllocSite("test.py", line, 0, kind, name)
    return AbstractObject(site, context)


def make_pts(*objects: AbstractObject) -> PointsToSet:
    """Helper to create a points-to set from objects.
    
    Args:
        *objects: Objects to include in set
    
    Returns:
        Points-to set containing the objects
    """
    if not objects:
        return PointsToSet.empty()
    return PointsToSet(frozenset(objects))


def make_call_site(
    site_id: str,
    fn: str = "test",
    bb: str = None,
    idx: int = 0
) -> CallSite:
    """Helper to create a call site quickly.
    
    Args:
        site_id: Call site identifier
        fn: Function name
        bb: Optional basic block
        idx: Index in block
    
    Returns:
        Created call site
    """
    return CallSite(site_id, fn, bb, idx)


# ============================================================================
# Assertion Helpers
# ============================================================================

def assert_pts_equal(pts1: PointsToSet, pts2: PointsToSet):
    """Assert two points-to sets are equal."""
    assert pts1.objects == pts2.objects, (
        f"Points-to sets differ:\n"
        f"  Expected: {pts1}\n"
        f"  Got:      {pts2}"
    )


def assert_pts_contains(pts: PointsToSet, obj: AbstractObject):
    """Assert points-to set contains object."""
    assert obj in pts, f"Object {obj} not in points-to set {pts}"


def assert_pts_empty(pts: PointsToSet):
    """Assert points-to set is empty."""
    assert pts.is_empty(), f"Expected empty points-to set, got {pts}"

