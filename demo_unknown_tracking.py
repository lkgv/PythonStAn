#!/usr/bin/env python3
"""Demonstration of unknown resolution tracking.

This script demonstrates how the unknown tracking system works
and how to access unknown statistics and details.
"""

from pythonstan.analysis.pointer.kcfa.config import Config
from pythonstan.analysis.pointer.kcfa.state import PointerAnalysisState
from pythonstan.analysis.pointer.kcfa.solver import PointerSolver
from pythonstan.analysis.pointer.kcfa.object import AllocSite, AllocKind
from pythonstan.analysis.pointer.kcfa.variable import Variable, Scope, VariableKind
from pythonstan.analysis.pointer.kcfa.context import CallStringContext
from pythonstan.analysis.pointer.kcfa.constraints import CallConstraint, AllocConstraint


def demonstrate_unknown_tracking():
    """Demonstrate unknown tracking features."""
    
    print("=" * 70)
    print("Unknown Resolution Tracking - Demonstration")
    print("=" * 70)
    
    # Create solver with verbose mode
    config = Config(verbose=True, max_iterations=100)
    state = PointerAnalysisState()
    solver = PointerSolver(state, config)
    
    print("\n[1] Creating scenarios with unknowns...")
    print("-" * 70)
    
    # Scenario 1: Empty callee
    print("\nScenario 1: Call with empty callee points-to set")
    scope = Scope(name="test", kind="function")
    context = CallStringContext(call_sites=(), k=2)
    
    empty_callee = Variable("unknown_func", scope, context, VariableKind.LOCAL)
    result1 = Variable("result1", scope, context, VariableKind.LOCAL)
    
    call1 = CallConstraint(
        callee=empty_callee,
        args=(),
        target=result1,
        call_site="demo:10"
    )
    solver.add_constraint(call1)
    print(f"  Created call: {result1.name} = {empty_callee.name}()")
    
    # Scenario 2: Non-callable object
    print("\nScenario 2: Call to non-callable object (LIST)")
    list_var = Variable("my_list", scope, context, VariableKind.LOCAL)
    result2 = Variable("result2", scope, context, VariableKind.LOCAL)
    
    list_alloc = AllocSite(
        file="demo.py",
        line=20,
        col=0,
        kind=AllocKind.LIST,
        name="my_list"
    )
    alloc_list = AllocConstraint(target=list_var, alloc_site=list_alloc)
    
    call2 = CallConstraint(
        callee=list_var,
        args=(),
        target=result2,
        call_site="demo:25"
    )
    solver.add_constraint(alloc_list)
    solver.add_constraint(call2)
    print(f"  Created call: {result2.name} = {list_var.name}()")
    
    # Scenario 3: Function not in registry
    print("\nScenario 3: Function not in registry")
    func_var = Variable("missing_func", scope, context, VariableKind.LOCAL)
    result3 = Variable("result3", scope, context, VariableKind.LOCAL)
    
    func_alloc = AllocSite(
        file="demo.py",
        line=30,
        col=0,
        kind=AllocKind.FUNCTION,
        name="missing_func"
    )
    alloc_func = AllocConstraint(target=func_var, alloc_site=func_alloc)
    
    call3 = CallConstraint(
        callee=func_var,
        args=(),
        target=result3,
        call_site="demo:35"
    )
    solver.add_constraint(alloc_func)
    solver.add_constraint(call3)
    print(f"  Created call: {result3.name} = {func_var.name}()")
    
    # Solve to fixpoint
    print("\n[2] Solving constraints...")
    print("-" * 70)
    solver.solve_to_fixpoint()
    print(f"  Converged after {solver._iteration} iterations")
    
    # Query results
    print("\n[3] Querying unknown statistics...")
    print("-" * 70)
    query = solver.query()
    
    # Get summary
    unknown_summary = query.get_unknown_summary()
    print(f"\nUnknown Summary:")
    print(f"  Total unknowns: {unknown_summary['total_unknowns']}")
    for key, count in sorted(unknown_summary.items()):
        if key != 'total_unknowns' and count > 0:
            print(f"  {key}: {count}")
    
    # Get detailed report
    print("\n[4] Detailed unknown records...")
    print("-" * 70)
    details = query.get_unknown_details()
    
    for i, unknown in enumerate(details[:5], 1):  # Show first 5
        print(f"\nUnknown #{i}:")
        print(f"  Kind: {unknown['kind']}")
        print(f"  Location: {unknown['location']}")
        print(f"  Message: {unknown['message']}")
        if unknown['context']:
            print(f"  Context: {unknown['context']}")
    
    if len(details) > 5:
        print(f"\n... and {len(details) - 5} more unknown records")
    
    # Verify conservative objects were created
    print("\n[5] Verifying conservative objects...")
    print("-" * 70)
    
    pts1 = state.get_points_to(result1)
    pts2 = state.get_points_to(result2)
    pts3 = state.get_points_to(result3)
    
    print(f"\nResult variables points-to sets:")
    print(f"  result1: {len(pts1)} objects")
    for obj in pts1:
        print(f"    - {obj.kind.value}: {obj.alloc_site.name}")
    
    print(f"  result2: {len(pts2)} objects")
    for obj in pts2:
        print(f"    - {obj.kind.value}: {obj.alloc_site.name}")
    
    print(f"  result3: {len(pts3)} objects")
    for obj in pts3:
        print(f"    - {obj.kind.value}: {obj.alloc_site.name}")
    
    # Integrated statistics
    print("\n[6] Integrated statistics...")
    print("-" * 70)
    stats = query.get_statistics()
    
    print(f"\nComplete Analysis Statistics:")
    for key, value in sorted(stats.items()):
        print(f"  {key}: {value}")
    
    print("\n" + "=" * 70)
    print("✓ Unknown tracking demonstration complete!")
    print("=" * 70)
    
    return 0


if __name__ == '__main__':
    import sys
    try:
        sys.exit(demonstrate_unknown_tracking())
    except Exception as e:
        print(f"\n✗ Error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

