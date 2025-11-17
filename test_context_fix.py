#!/usr/bin/env python3
"""Test that the context sensitivity fix works with a file that has actual calls."""

import sys
import os
import site
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent
sys.path.insert(0, str(PROJECT_ROOT))

from pythonstan.world.pipeline import Pipeline
from pythonstan.analysis.pointer.kcfa2 import KCFA2PointerAnalysis, KCFAConfig
from pythonstan.ir import IRFunc

def analyze_file(filepath: Path, k: int):
    """Analyze a file with k-CFA."""
    library_paths = [os.path.dirname(os.__file__)]
    library_paths.extend(site.getsitepackages())
    
    pipeline_config = {
        "filename": str(filepath),
        "project_path": str(filepath.parent),
        "library_paths": library_paths,
        "analysis": [],
        "lazy_ir_construction": True
    }
    
    pipeline = Pipeline(config=pipeline_config)
    pipeline.run()
    
    world = pipeline.get_world()
    ir_module = world.entry_module
    scope_manager = world.scope_manager
    
    subscopes = scope_manager.get_subscopes(ir_module)
    functions = [scope for scope in subscopes if isinstance(scope, IRFunc)]
    
    print(f"\nFound {len(functions)} functions:")
    for func in functions:
        print(f"  - {func.name} ({func.qualname})")
    
    for func in functions:
        for fmt in ['cfg', 'block cfg', 'three address', 'ir']:
            func_ir = scope_manager.get_ir(func, fmt)
            if func_ir is not None:
                func._cfg = func_ir
                func._ir_format = fmt
                break
    
    config = KCFAConfig(context_policy=f'{k}-cfa', verbose=False)
    analysis = KCFA2PointerAnalysis(config)
    
    if functions:
        analysis.plan(functions)
    
    analysis.initialize()
    analysis.run()
    
    # Collect metrics
    metrics = {
        'k': k,
        'contexts': len(analysis._contexts),
        'env_entries': len(analysis._env),
        'heap_entries': len(analysis._heap),
        'unique_contexts_in_env': len(set(ctx for ctx, var in analysis._env.keys())),
    }
    
    # Call graph metrics
    cg_stats = analysis._call_graph.get_statistics()
    metrics['call_edges'] = cg_stats.get('total_cs_edges', 0)
    metrics['call_sites'] = cg_stats.get('unique_call_sites', 0)
    
    # Precision metrics
    singleton_count = 0
    total_non_empty = 0
    
    for (ctx, var), pts in analysis._env.items():
        if pts and len(pts.objects) > 0:
            total_non_empty += 1
            if len(pts.objects) == 1:
                singleton_count += 1
    
    metrics['singleton_ratio'] = singleton_count / total_non_empty if total_non_empty > 0 else 0.0
    metrics['total_non_empty_pts'] = total_non_empty
    
    return metrics, analysis

# Test file
test_file = PROJECT_ROOT / "test_with_calls.py"

print("="*70)
print("TESTING CONTEXT SENSITIVITY FIX")
print("="*70)
print(f"Test file: {test_file}")

print("\n" + "="*70)
print("ANALYZING WITH 0-CFA (context-insensitive)")
print("="*70)
metrics_0, analysis_0 = analyze_file(test_file, 0)

print(f"\nResults:")
print(f"  Total contexts: {metrics_0['contexts']}")
print(f"  Unique contexts in env: {metrics_0['unique_contexts_in_env']}")
print(f"  Env entries: {metrics_0['env_entries']}")
print(f"  Call edges: {metrics_0['call_edges']}")
print(f"  Call sites: {metrics_0['call_sites']}")
print(f"  Non-empty pts: {metrics_0['total_non_empty_pts']}")
print(f"  Singleton ratio: {metrics_0['singleton_ratio']:.1%}")

print("\n" + "="*70)
print("ANALYZING WITH 2-CFA (context-sensitive)")
print("="*70)
metrics_2, analysis_2 = analyze_file(test_file, 2)

print(f"\nResults:")
print(f"  Total contexts: {metrics_2['contexts']}")
print(f"  Unique contexts in env: {metrics_2['unique_contexts_in_env']}")
print(f"  Env entries: {metrics_2['env_entries']}")
print(f"  Call edges: {metrics_2['call_edges']}")
print(f"  Call sites: {metrics_2['call_sites']}")
print(f"  Non-empty pts: {metrics_2['total_non_empty_pts']}")
print(f"  Singleton ratio: {metrics_2['singleton_ratio']:.1%}")

print("\n" + "="*70)
print("COMPARISON")
print("="*70)

if metrics_0['contexts'] == metrics_2['contexts']:
    print(f"⚠️  Context counts are identical: {metrics_0['contexts']}")
else:
    print(f"✓ Context counts differ: 0-cfa={metrics_0['contexts']}, 2-cfa={metrics_2['contexts']}")

if metrics_0['call_edges'] == 0:
    print(f"⚠️  No call edges discovered - functions may not be calling each other")
else:
    print(f"✓ {metrics_0['call_edges']} call edges discovered")

if metrics_0['singleton_ratio'] == metrics_2['singleton_ratio']:
    print(f"⚠️  Precision is identical: {metrics_0['singleton_ratio']:.1%}")
else:
    print(f"✓ Precision differs: 0-cfa={metrics_0['singleton_ratio']:.1%}, 2-cfa={metrics_2['singleton_ratio']:.1%}")

print()

# Final verdict
if (metrics_0['contexts'] != metrics_2['contexts'] or 
    metrics_0['singleton_ratio'] != metrics_2['singleton_ratio']):
    print("✅ SUCCESS: Different policies produce different results!")
    print("   The context sensitivity fix is working!")
elif metrics_0['call_edges'] == 0:
    print("⚠️  INCONCLUSIVE: No inter-procedural calls discovered")
    print("   The test may be too simple or call resolution isn't working")
else:
    print("❌ FAILURE: Results are still identical")
    print("   The bug may not be fully fixed")

