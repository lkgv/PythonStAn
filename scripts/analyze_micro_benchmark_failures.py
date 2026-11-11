#!/usr/bin/env python3
"""Analyze micro-benchmark test failures and categorize root causes.

This script analyzes the failure patterns from precision micro-benchmarks
and identifies the root causes affecting Flask analysis.
"""

import sys
from pathlib import Path

# Test results analysis
RESULTS = {
    "PASSING": {
        "basic_functions": [
            "test_direct_call",
            "test_nested_functions", 
            "test_multiple_calls_chain",
        ],
        "methods": [
            "test_instance_method_call",
            "test_class_method_call",
            "test_static_method_call",
            "test_constructor_call",
            "test_multiple_instance_methods",
            "test_method_via_variable",
            "test_method_call_chain",
        ],
        "decorators": [
            "test_simple_decorator",
            "test_decorator_with_args",
            "test_decorator_chain",
            "test_method_decorator",
        ],
        "inheritance": [
            "test_override_method",
            "test_super_call",
        ],
        "function_as_values": [
            "test_multiple_callbacks",
        ],
    },
    "FAILING": {
        "containers_ALL": [
            "test_function_in_list",
            "test_function_list_iteration",
            "test_function_in_dict",
            "test_function_dict_dynamic_key",
            "test_function_in_tuple",
            "test_nested_container_functions",
            "test_function_list_append",
            "test_function_dict_setitem",
            "test_mixed_container_types",
        ],
        "flask_patterns_ALL": [
            "test_flask_route_pattern",
            "test_callback_registry",
            "test_handler_dict_pattern",
            "test_method_decorator_with_registry",
        ],
        "closures_and_callbacks": [
            "test_closure_capture",
            "test_function_as_argument",
            "test_function_returned_from_function",
            "test_function_factory",
            "test_callback_pattern",
            "test_function_chain_return",
            "test_conditional_function_return",
            "test_function_composition",
        ],
        "function_references": [
            "test_function_reference",
        ],
        "recursion": [
            "test_recursive_call",
            "test_mutually_recursive",
        ],
        "conditional_calls": [
            "test_conditional_call",
        ],
        "higher_order": [
            "test_higher_order_function",
        ],
        "inheritance_gaps": [
            "test_inherit_method",
            "test_multilevel_inheritance", 
            "test_inherited_and_own_methods",
        ],
        "method_to_method": [
            "test_method_calling_method",
        ],
        "decorator_factory": [
            "test_decorator_factory",
        ],
    }
}


def print_header(text):
    print(f"\n{'='*80}")
    print(f"  {text}")
    print('='*80)


def print_section(text):
    print(f"\n{'-'*80}")
    print(f"  {text}")
    print('-'*80)


def main():
    print_header("MICRO-BENCHMARK FAILURE ANALYSIS")
    
    passing = sum(len(tests) for tests in RESULTS["PASSING"].values())
    failing = sum(len(tests) for tests in RESULTS["FAILING"].values())
    total = passing + failing
    
    print(f"\nOverall Results:")
    print(f"  PASSING: {passing}/{total} ({passing*100//total}%)")
    print(f"  FAILING: {failing}/{total} ({failing*100//total}%)")
    
    print_section("ROOT CAUSE ANALYSIS")
    
    print("\n1. **CONTAINER OPERATIONS - CRITICAL** (9/9 failures)")
    print("   - Functions stored in lists/dicts/tuples cannot be called")
    print("   - ALL container tests fail - 100% failure rate")
    print("   - Root cause: LoadSubscr/StoreSubscr constraints don't flow functions")
    print("   - Impact: HIGH - Flask stores handlers in dicts")
    print("\n   Failed tests:")
    for test in RESULTS["FAILING"]["containers_ALL"]:
        print(f"     - {test}")
    
    print("\n2. **FLASK PATTERNS - CRITICAL** (4/4 failures)")
    print("   - @app.route() decorator factory + dict storage fails")
    print("   - Callback registry pattern doesn't work")
    print("   - Root cause: Combination of decorator factory + container issues")
    print("   - Impact: CRITICAL - This is Flask's core pattern")
    print("\n   Failed tests:")
    for test in RESULTS["FAILING"]["flask_patterns_ALL"]:
        print(f"     - {test}")
    
    print("\n3. **CLOSURES AND CALLBACKS** (8/9 failures)")
    print("   - Closures returned from functions not callable")
    print("   - Functions passed as arguments sometimes don't propagate")
    print("   - Root cause: Function object flow through assignments")
    print("   - Impact: HIGH - Flask uses many higher-order patterns")
    print("\n   Failed tests:")
    for test in RESULTS["FAILING"]["closures_and_callbacks"]:
        print(f"     - {test}")
    
    print("\n4. **INHERITANCE GAPS** (3/5 failures)")
    print("   - Simple inheritance methods not resolved")
    print("   - Multi-level inheritance fails")
    print("   - Root cause: INSTANCE/INHERIT edges not activating")
    print("   - Impact: MEDIUM - Flask uses inheritance")
    print("\n   Failed tests:")
    for test in RESULTS["FAILING"]["inheritance_gaps"]:
        print(f"     - {test}")
    
    print("\n5. **DECORATOR FACTORIES** (1 failure)")
    print("   - @decorator(args) pattern fails")
    print("   - Root cause: Decorator call chain not fully resolved")
    print("   - Impact: CRITICAL - Flask uses @app.route('/path')")
    print(f"\n   Failed test: {RESULTS['FAILING']['decorator_factory'][0]}")
    
    print("\n6. **OTHER ISSUES** (smaller impact)")
    print(f"   - Function references: {len(RESULTS['FAILING']['function_references'])} failures")
    print(f"   - Recursion: {len(RESULTS['FAILING']['recursion'])} failures")
    print(f"   - Conditional calls: {len(RESULTS['FAILING']['conditional_calls'])} failure")
    print(f"   - Method-to-method: {len(RESULTS['FAILING']['method_to_method'])} failure")
    
    print_section("WHAT'S WORKING")
    print("\n✓ Direct function calls")
    print("✓ Instance/class/static methods")
    print("✓ Simple decorators (non-factory)")
    print("✓ Method override and super()")
    print("✓ Basic higher-order functions (1 test passing)")
    
    print_section("RECOMMENDED FIX PRIORITY")
    
    print("\n**PRIORITY 1 - Container Operations**")
    print("  Fix: pythonstan/analysis/pointer/kcfa/builtin_api_handler.py")
    print("  - Implement list.append(), list.pop(), dict.__getitem__(), dict.__setitem__()")
    print("  - Ensure LoadSubscrConstraint/StoreSubscrConstraint flow functions")
    print("  - Verify flow through elem() for lists, key() for dicts")
    print("  Expected impact: +9 tests, enables Flask patterns")
    
    print("\n**PRIORITY 2 - Decorator Factories**")
    print("  Fix: pythonstan/analysis/pointer/kcfa/ir_translator.py")
    print("  - Complete @decorator(args) call chain translation")
    print("  - Ensure factory call returns decorator, decorator returns wrapper")
    print("  Expected impact: +5 tests, critical for Flask")
    
    print("\n**PRIORITY 3 - Closure/Callback Propagation**")
    print("  Fix: pythonstan/analysis/pointer/kcfa/solver.py")
    print("  - Fix function object flow through CopyConstraint")
    print("  - Ensure return values propagate correctly")
    print("  Expected impact: +8 tests")
    
    print("\n**PRIORITY 4 - Inheritance Resolution**")
    print("  Fix: pythonstan/analysis/pointer/kcfa/state.py")
    print("  - Fix INSTANCE edge activation (class field -> instance field)")
    print("  - Fix INHERIT edge for multi-level inheritance")
    print("  Expected impact: +4 tests")
    
    print_section("EXPECTED FLASK IMPACT")
    
    print("\nCurrent Flask edges: ~130")
    print("\nAfter Priority 1 (containers): ~400-600 edges")
    print("After Priority 2 (decorator factories): ~800-1200 edges")
    print("After Priority 3 (closures): ~1000-1500 edges")
    print("After Priority 4 (inheritance): ~1200-1800 edges")
    print("\nTarget: 1500-2000 edges")
    
    print_header("NEXT STEPS")
    print("\n1. Fix container operations (Priority 1)")
    print("2. Run micro-benchmarks to verify fix")
    print("3. Fix decorator factories (Priority 2)")
    print("4. Run Flask analysis, measure edge count")
    print("5. Continue with Priority 3 and 4")
    print("\n")


if __name__ == "__main__":
    main()

