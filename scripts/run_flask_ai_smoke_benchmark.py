#!/usr/bin/env python3
"""Smoke benchmark for Flask with AI-integrated pointer analysis.

This script runs a quick validation of the kcfa pointer analysis with
AI integration for getattr/setattr/hasattr on the Flask codebase.

Runs with 2-cfa policy (and optionally 1-param) and writes results to
benchmark/results/flask_validation.json.

Usage:
    python scripts/run_flask_ai_smoke_benchmark.py
    python scripts/run_flask_ai_smoke_benchmark.py --include-1param
"""

import argparse
import sys
import time
import tracemalloc
import traceback
from pathlib import Path
from typing import List, Dict, Any

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from benchmark.metrics_collector import AnalysisMetrics, MetricsCollector, save_results
from pythonstan.world.pipeline import Pipeline
from pythonstan.analysis.pointer.kcfa import PointerAnalysis, Config


# Flask configuration
FLASK_CONFIG = {
    "filename": "/mnt/data_fast/code/PythonStAn/benchmark/projects/flask/src/flask/__main__.py",
    "project_path": "/mnt/data_fast/code/PythonStAn/benchmark/projects/flask/src",
    "import_level": 10,
    "time_count": True,
    "max_iterations": 5000000,
    "library_paths": [],
    "analysis": []
}


def run_single_policy(project_config: Dict, policy: str) -> AnalysisMetrics:
    """Run pointer analysis for a single policy.
    
    Args:
        project_config: Project configuration dictionary
        policy: Context policy string (e.g., "2-cfa")
        
    Returns:
        AnalysisMetrics for this run
    """
    timings = {}
    memory = {}
    
    try:
        # Start memory tracking
        tracemalloc.start()
        total_start = time.perf_counter()
        
        # Step 1: Configure pipeline
        print(f"  Configuring analysis with {policy}...")
        pipeline_start = time.perf_counter()
        
        pta_config = Config(
            context_policy=policy,
            max_iterations=5000000,
            verbose=False,
            log_level="INFO",
            track_unknowns=True,
            build_class_hierarchy=True,
            use_mro_resolution=True,
            project_path=project_config['project_path'],
            library_paths=project_config.get('library_paths', []),
            max_import_depth=project_config.get('import_level', 2),
            index_sensitive=True,
            enable_debug_monitor=False,
        )
        
        analyzer_config = {
            "name": "advanced_pointer_analysis",
            "id": "PointerAnalysis",
            "description": f"k-CFA pointer analysis with AI integration ({policy})",
            "prev_analysis": ["ir"],
            "options": pta_config.to_dict()
        }
        
        # Make a copy of config to avoid mutation
        config = dict(project_config)
        config['analysis'] = [analyzer_config]
        
        pipeline_time = time.perf_counter() - pipeline_start
        timings['pipeline'] = pipeline_time
        print(f"  Configuration ready in {pipeline_time:.2f}s")
        
        # Step 2: Run analysis
        print(f"  Running pointer analysis with {policy}...")
        analysis_start = time.perf_counter()
        
        ppl = Pipeline(config=config)
        ppl.run()
        
        result = ppl.analysis_manager.get_results(analyzer_config['name'])
        
        analysis_time = time.perf_counter() - analysis_start
        timings['analysis'] = analysis_time
        print(f"  Analysis completed in {analysis_time:.2f}s")
        
        # Step 3: Collect statistics
        print(f"  Collecting statistics...")
        stats = result.get_statistics()
        
        # Get memory stats
        current_mem, peak_mem = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        memory['peak'] = peak_mem
        memory['final'] = current_mem
        
        # Total time
        total_time = time.perf_counter() - total_start
        timings['total'] = total_time
        
        # Collect metrics
        metrics = MetricsCollector.collect_from_statistics(
            stats=stats,
            policy=policy,
            project="flask",
            timings=timings,
            memory=memory
        )
        
        return metrics
        
    except Exception as e:
        print(traceback.format_exc())
        
        # Stop memory tracking if still running
        if tracemalloc.is_tracing():
            current_mem, peak_mem = tracemalloc.get_traced_memory()
            tracemalloc.stop()
            memory['peak'] = peak_mem
        else:
            memory['peak'] = 0
        
        # Get total time if available
        if 'total' not in timings:
            timings['total'] = time.perf_counter() - total_start if 'total_start' in locals() else 0.0
        
        # Create failed metrics
        error_msg = f"{type(e).__name__}: {str(e)}"
        print(f"  Error: {error_msg}")
        
        metrics = MetricsCollector.create_failed_metrics(
            policy=policy,
            project="flask",
            error_message=error_msg,
            timings=timings,
            memory=memory
        )
        
        return metrics


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Smoke benchmark for Flask with AI-integrated pointer analysis"
    )
    parser.add_argument(
        "--include-1param",
        action="store_true",
        help="Also run 1-param policy in addition to 2-cfa"
    )
    parser.add_argument(
        "--output",
        type=str,
        default="benchmark/results/flask_validation.json",
        help="Output file for results (default: benchmark/results/flask_validation.json)"
    )
    
    args = parser.parse_args()
    
    # Policies to test
    policies = ["2-cfa"]
    if args.include_1param:
        policies.append("1-param")
    
    print("=" * 80)
    print("Flask AI Smoke Benchmark")
    print("=" * 80)
    print(f"Testing policies: {policies}")
    print()
    
    results: List[AnalysisMetrics] = []
    
    for i, policy in enumerate(policies, 1):
        print(f"\n[{i}/{len(policies)}] Testing policy: {policy}")
        print("-" * 80)
        
        try:
            metrics = run_single_policy(FLASK_CONFIG, policy)
            results.append(metrics)
            
            if metrics.success:
                print(f"✓ SUCCESS")
                print(f"  Time: {metrics.total_time:.2f}s")
                print(f"  Memory: {metrics.peak_memory:.1f} MB")
                print(f"  Iterations: {metrics.solver_iterations}")
                print(f"  Variables: {metrics.num_variables}")
                print(f"  Objects: {metrics.num_objects}")
                print(f"  Call edges: {metrics.call_edges}")
            else:
                print(f"✗ FAILED: {metrics.error_message}")
                
        except KeyboardInterrupt:
            print("\n\nBenchmark interrupted by user.")
            break
        except Exception as e:
            print(f"✗ UNEXPECTED ERROR: {e}")
            traceback.print_exc()
    
    # Ensure output directory exists
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    
    # Save results
    save_results(results, str(output_path))
    print(f"\nResults saved to: {output_path}")
    
    # Summary
    print("\n" + "=" * 80)
    print("BENCHMARK COMPLETE")
    print(f"Successful: {sum(1 for r in results if r.success)}/{len(results)}")
    print("=" * 80)
    
    return 0 if all(r.success for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())

