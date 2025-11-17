#!/usr/bin/env python3
"""Final test of k-CFA fixes."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from pythonstan.world.pipeline import Pipeline
from pythonstan.analysis.pointer.kcfa2 import KCFA2PointerAnalysis, KCFAConfig

print("="*70)
print("FINAL TEST: k-CFA Context Sensitivity")
print("="*70)

test_file = Path(__file__).parent / "test_with_calls.py"

for policy in ['0-cfa', '2-cfa']:
    print(f"\n{'='*70}")
    print(f"Policy: {policy}")
    print(f"{'='*70}")
    
    # Create pipeline
    pipeline_config = {
        "filename": str(test_file),
        "project_path": str(Path(__file__).parent),
        "library_paths": [],
        "analysis": [],
        "lazy_ir_construction": True
    }
    
    pipeline = Pipeline(config=pipeline_config)
    pipeline.run()
    
    world = pipeline.get_world()
    ir_module = world.entry_module
    scope_manager = world.scope_manager
    
    # Get functions
    from pythonstan.ir import IRFunc
    subscopes = scope_manager.get_subscopes(ir_module)
    functions = [s for s in subscopes if isinstance(s, IRFunc)]
    
    # Attach IR
    for func in functions:
        for fmt in ['cfg', 'block cfg', 'three address', 'ir']:
            func_ir = scope_manager.get_ir(func, fmt)
            if func_ir is not None:
                func._cfg = func_ir
                func._ir_format = fmt
                break
    
    # Run analysis
    config = KCFAConfig(context_policy=policy, verbose=False)
    analysis = KCFA2PointerAnalysis(config)
    
    analysis.plan(functions)
    analysis.initialize()
    analysis.run()
    
    # Show results
    print(f"\nResults:")
    print(f"  Total Contexts: {len(analysis._contexts)}")
    print(f"  Env Entries: {len(analysis._env)}")
    print(f"  Calls Processed: {analysis._statistics.get('calls_processed', 0)}")
    print(f"  Constraints Processed: {analysis._statistics.get('constraints_processed', 0)}")
    
    # Precision
    if len(analysis._env) > 0:
        singleton_count = sum(1 for pts in analysis._env.values() if len(pts.objects) == 1)
        total_pts = sum(1 for pts in analysis._env.values() if len(pts.objects) > 0)
        precision = (singleton_count / total_pts * 100) if total_pts > 0 else 0
        print(f"  Precision: {singleton_count}/{total_pts} = {precision:.1f}% singleton")
    else:
        print(f"  Precision: N/A (no variables tracked)")

print(f"\n{'='*70}")
print("CONCLUSION:")
if len(analysis._contexts) > 1:
    print("✅ Multiple contexts created (context sensitivity working)")
else:
    print("❌ Only 1 context (context sensitivity broken)")

print("="*70)

