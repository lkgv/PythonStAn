"""Demonstration of modular pointer analysis.

Shows how the new module summary architecture enables scalable
multi-module analysis.
"""

import ast
from pythonstan.analysis.pointer.kcfa.analysis import PointerAnalysis
from pythonstan.analysis.pointer.kcfa.config import Config


def create_simple_module(name: str, code: str):
    """Create a simple module representation from source code."""
    tree = ast.parse(code)
    tree.name = name
    tree.module_name = name
    return tree


def demo_single_module():
    """Demo: Single module analysis (monolithic)."""
    print("=" * 60)
    print("DEMO 1: Single Module Analysis (Monolithic)")
    print("=" * 60)
    
    code = """
def greet(name):
    return "Hello, " + name

result = greet("World")
"""
    
    module = create_simple_module("main", code)
    
    config = Config(
        context_policy="1-cfa",
        enable_modular_analysis=True,
        verbose=False
    )
    
    analysis = PointerAnalysis(config)
    
    try:
        result = analysis.analyze(module)
        print(f"✓ Analysis complete")
        print(f"  Variables tracked: {result.get_statistics()['num_variables']}")
        print(f"  Objects created: {result.get_statistics()['num_objects']}")
    except Exception as e:
        print(f"✗ Analysis failed: {e}")
    
    print()


def demo_multi_module():
    """Demo: Multi-module analysis with summaries."""
    print("=" * 60)
    print("DEMO 2: Multi-Module Analysis (Modular with Summaries)")
    print("=" * 60)
    
    base_code = """
class Base:
    def __init__(self):
        self.value = 42
"""
    
    utils_code = """
from base import Base

def create_object():
    return Base()
"""
    
    main_code = """
from utils import create_object

obj = create_object()
result = obj.value
"""
    
    base_module = create_simple_module("base", base_code)
    utils_module = create_simple_module("utils", utils_code)
    main_module = create_simple_module("main", main_code)
    
    config = Config(
        context_policy="1-cfa",
        enable_modular_analysis=True,
        verbose=False
    )
    
    analysis = PointerAnalysis(config)
    
    try:
        result = analysis.analyze([base_module, utils_module, main_module])
        print(f"✓ Multi-module analysis complete")
        print(f"  Modules analyzed: 3 (base → utils → main)")
        print(f"  Variables tracked: {result.get_statistics()['num_variables']}")
        print(f"  Objects created: {result.get_statistics()['num_objects']}")
        print(f"\n  Analysis order: base (no deps) → utils (imports base) → main (imports utils)")
    except Exception as e:
        print(f"✗ Analysis failed: {e}")
        import traceback
        traceback.print_exc()
    
    print()


def demo_circular_imports():
    """Demo: Handling circular imports."""
    print("=" * 60)
    print("DEMO 3: Circular Import Detection")
    print("=" * 60)
    
    mod_a_code = """
from b import func_b

def func_a():
    return func_b() + 1
"""
    
    mod_b_code = """
from a import func_a

def func_b():
    return 42
"""
    
    mod_a = create_simple_module("a", mod_a_code)
    mod_b = create_simple_module("b", mod_b_code)
    
    config = Config(
        context_policy="1-cfa",
        enable_modular_analysis=True,
        verbose=False
    )
    
    analysis = PointerAnalysis(config)
    
    try:
        result = analysis.analyze([mod_a, mod_b])
        print(f"✓ Circular import handled gracefully")
        print(f"  Modules: a ⇄ b (circular)")
        print(f"  Analysis proceeds in arbitrary order with warning")
    except Exception as e:
        print(f"✗ Analysis failed: {e}")
    
    print()


def demo_relative_imports():
    """Demo: Relative import resolution."""
    print("=" * 60)
    print("DEMO 4: Relative Import Resolution")
    print("=" * 60)
    
    from pythonstan.analysis.pointer.kcfa.dependency_graph import ModuleDependencyGraph
    
    graph = ModuleDependencyGraph()
    
    examples = [
        ("pkg.sub.mod", "sibling", 1, "pkg.sub.sibling"),
        ("pkg.sub.mod", "foo", 2, "pkg.foo"),
        ("pkg.sub.mod", "", 2, "pkg"),
        ("pkg.sub.mod", "bar", 3, "bar"),
    ]
    
    print("Relative import resolution examples:")
    print()
    
    for current, import_name, level, expected in examples:
        result = graph.resolve_relative_import(current, import_name, level)
        dots = "." * level
        import_str = f"{dots}{import_name}" if import_name else dots
        status = "✓" if result == expected else "✗"
        print(f"  {status} {current}: from {import_str} import ...")
        print(f"     → {result}")
        print()


def demo_performance_comparison():
    """Demo: Performance comparison (conceptual)."""
    print("=" * 60)
    print("DEMO 5: Scalability Benefits (Conceptual)")
    print("=" * 60)
    
    print("Monolithic Analysis:")
    print("  • Analyzes entire project as one unit")
    print("  • Re-analyzes imported modules on every import")
    print("  • Complexity: O(N × M) where N=modules, M=avg imports")
    print("  • Exponential blowup on deep import chains")
    print()
    
    print("Modular Analysis (with summaries):")
    print("  • Analyzes each module once")
    print("  • Reuses summaries across importers")
    print("  • Complexity: O(N + E) where E=import edges")
    print("  • Linear scaling with project size")
    print()
    
    print("Example project: 100 modules, avg 5 imports each")
    print("  Monolithic: ~500 module analyses")
    print("  Modular:    ~100 module analyses (5x speedup)")
    print()


if __name__ == "__main__":
    print("\n")
    print("╔" + "═" * 58 + "╗")
    print("║" + " " * 58 + "║")
    print("║" + "  MODULE SUMMARY ARCHITECTURE DEMONSTRATION".center(58) + "║")
    print("║" + " " * 58 + "║")
    print("╚" + "═" * 58 + "╝")
    print()
    
    demo_single_module()
    demo_multi_module()
    demo_circular_imports()
    demo_relative_imports()
    demo_performance_comparison()
    
    print("=" * 60)
    print("All demonstrations complete!")
    print("=" * 60)
    print()
    print("Key Features Demonstrated:")
    print("  ✓ Single module analysis (backward compatible)")
    print("  ✓ Multi-module modular analysis")
    print("  ✓ Circular import detection")
    print("  ✓ Relative import resolution")
    print("  ✓ Scalability benefits")
    print()

