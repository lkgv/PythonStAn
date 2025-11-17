"""
Basic integration test for new k-CFA implementation.

Tests that the analysis can run end-to-end on a simple program.
"""

def test_basic_copy_propagation():
    """Test simple copy propagation: a = []; b = a"""
    from pythonstan.analysis.pointer.kcfa import (
        PointerAnalysis, Config,
        PointerAnalysisState, Variable, Scope, AllocSite, AllocKind,
        AbstractObject, CopyConstraint, AllocConstraint
    )
    from pythonstan.analysis.pointer.kcfa.context import CallStringContext
    from pythonstan.analysis.pointer.kcfa.solver import PointerSolver
    
    print("\n=== Test: Basic Copy Propagation ===")
    
    # Create state and solver
    config = Config()
    state = PointerAnalysisState()
    solver = PointerSolver(state, config)
    
    # Create context
    ctx = CallStringContext(call_sites=(), k=2)
    
    # Create scope
    scope = Scope(name="test_func", kind="function")
    
    # Variables: a, b
    var_a = Variable(name="a", scope=scope, context=ctx)
    var_b = Variable(name="b", scope=scope, context=ctx)
    
    # Allocation: a = []
    list_alloc = AllocSite(file="<test>", line=1, col=0, kind=AllocKind.LIST, name="list1")
    alloc_constraint = AllocConstraint(target=var_a, alloc_site=list_alloc)
    
    # Copy: b = a
    copy_constraint = CopyConstraint(source=var_a, target=var_b)
    
    # Add constraints to solver
    solver.add_constraint(alloc_constraint)
    solver.add_constraint(copy_constraint)
    
    print(f"Added constraints:")
    print(f"  {alloc_constraint}")
    print(f"  {copy_constraint}")
    
    # Solve to fixpoint
    print("\nSolving to fixpoint...")
    solver.solve_to_fixpoint()
    
    # Query results
    query = solver.query()
    pts_a = query.points_to(var_a)
    pts_b = query.points_to(var_b)
    
    print(f"\nResults:")
    print(f"  pts(a) = {pts_a}")
    print(f"  pts(b) = {pts_b}")
    
    # Verify
    assert len(pts_a) == 1, f"Expected 1 object in pts(a), got {len(pts_a)}"
    assert len(pts_b) == 1, f"Expected 1 object in pts(b), got {len(pts_b)}"
    
    obj_a = list(pts_a)[0]
    obj_b = list(pts_b)[0]
    
    assert obj_a == obj_b, "a and b should point to the same object"
    assert obj_a.kind == AllocKind.LIST, "Object should be a list"
    
    print("✅ Test passed!")
    return True


def test_field_operations():
    """Test field store and load: obj.field = val; x = obj.field"""
    from pythonstan.analysis.pointer.kcfa import (
        Config,
        PointerAnalysisState, Variable, Scope, AllocSite, AllocKind,
        AbstractObject, AllocConstraint, StoreConstraint, LoadConstraint
    )
    from pythonstan.analysis.pointer.kcfa.heap_model import attr
    from pythonstan.analysis.pointer.kcfa.context import CallStringContext
    from pythonstan.analysis.pointer.kcfa.solver import PointerSolver
    
    print("\n=== Test: Field Operations ===")
    
    # Create state and solver
    config = Config()
    state = PointerAnalysisState()
    solver = PointerSolver(state, config)
    
    # Create context and scope
    ctx = CallStringContext(call_sites=(), k=2)
    scope = Scope(name="test_func", kind="function")
    
    # Variables: obj, val, x
    var_obj = Variable(name="obj", scope=scope, context=ctx)
    var_val = Variable(name="val", scope=scope, context=ctx)
    var_x = Variable(name="x", scope=scope, context=ctx)
    
    # Allocations: obj = Object(), val = []
    obj_alloc = AllocSite(file="<test>", line=1, col=0, kind=AllocKind.OBJECT, name="Obj1")
    val_alloc = AllocSite(file="<test>", line=2, col=0, kind=AllocKind.LIST, name="list1")
    
    solver.add_constraint(AllocConstraint(target=var_obj, alloc_site=obj_alloc))
    solver.add_constraint(AllocConstraint(target=var_val, alloc_site=val_alloc))
    
    # Store: obj.field = val
    store_constraint = StoreConstraint(base=var_obj, field=attr("data"), source=var_val)
    solver.add_constraint(store_constraint)
    
    # Load: x = obj.field
    load_constraint = LoadConstraint(base=var_obj, field=attr("data"), target=var_x)
    solver.add_constraint(load_constraint)
    
    print("Added constraints:")
    print(f"  {AllocConstraint(target=var_obj, alloc_site=obj_alloc)}")
    print(f"  {AllocConstraint(target=var_val, alloc_site=val_alloc)}")
    print(f"  {store_constraint}")
    print(f"  {load_constraint}")
    
    # Solve
    print("\nSolving to fixpoint...")
    solver.solve_to_fixpoint()
    
    # Query results
    query = solver.query()
    pts_val = query.points_to(var_val)
    pts_x = query.points_to(var_x)
    
    print(f"\nResults:")
    print(f"  pts(val) = {pts_val}")
    print(f"  pts(x) = {pts_x}")
    
    # Verify
    assert len(pts_val) == 1, f"Expected 1 object in pts(val), got {len(pts_val)}"
    assert len(pts_x) == 1, f"Expected 1 object in pts(x), got {len(pts_x)}"
    
    obj_val = list(pts_val)[0]
    obj_x = list(pts_x)[0]
    
    assert obj_val == obj_x, "val and x should point to the same list object"
    assert obj_x.kind == AllocKind.LIST, "Object should be a list"
    
    print("✅ Test passed!")
    return True


if __name__ == "__main__":
    try:
        test_basic_copy_propagation()
        test_field_operations()
        print("\n" + "="*50)
        print("✅ ALL TESTS PASSED!")
        print("="*50)
    except Exception as e:
        print(f"\n❌ Test failed: {e}")
        import traceback
        traceback.print_exc()
        exit(1)

