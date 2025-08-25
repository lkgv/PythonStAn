#!/usr/bin/env python3
"""
AI Benchmarks Runner.

This script runs AI analysis on all benchmark files with configurable pointer and AI settings,
collecting timing and precision metrics for evaluation.
"""

import os
import sys
import json
import time
import argparse
import statistics
from pathlib import Path
from typing import Dict, List, Any, Optional

# Add pythonstan to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from pythonstan.world.pipeline import Pipeline
from pythonstan.world import World


# Get the absolute path to the project root and benchmark directory
PROJECT_ROOT = Path(__file__).parent.parent.absolute()
BENCHMARK_DIR = PROJECT_ROOT / 'benchmark'


def get_library_paths():
    """Get Python library paths for the current environment."""
    import site
    library_paths = []
    
    # Add standard library path
    library_paths.append(os.path.dirname(os.__file__))
    
    # Add site-packages
    for path in site.getsitepackages():
        library_paths.append(path)
    
    # Add user site-packages
    user_site = site.getusersitepackages()
    if user_site:
        library_paths.append(user_site)
    
    return library_paths


def create_benchmark_config(benchmark_file: str, pointer_config: Dict[str, Any], 
                          ai_config: Dict[str, Any]) -> Dict[str, Any]:
    """Create pipeline configuration for benchmark."""
    analyses = []
    
    # Basic transforms
    analyses.extend([
        {
            "name": "ir",
            "id": "IR",
            "description": "IR generation",
            "prev_analysis": [],
            "options": {"type": "transform"}
        },
        {
            "name": "block cfg",
            "id": "BlockCFG",
            "description": "block control flow graph analysis",
            "prev_analysis": ["ir"],
            "options": {"type": "transform"}
        },
        {
            "name": "cfg",
            "id": "CFG",
            "description": "control flow graph analysis", 
            "prev_analysis": ["block cfg"],
            "options": {"type": "transform"}
        }
    ])
    
    # Add pointer analysis
    if not pointer_config.get('skip_pointer', False):
        analyses.append({
            "name": "pointer_analysis",
            "id": "PointerAnalysis",
            "description": "k-CFA pointer analysis",
            "prev_analysis": ["cfg"],
            "options": {
                "type": "pointer analysis",
                **pointer_config
            }
        })
    
    # Add dataflow analysis (liveness as example)
    analyses.append({
        "name": "liveness_analysis", 
        "id": "LivenessAnalysis",
        "description": "liveness analysis",
        "prev_analysis": ["pointer_analysis"] if not pointer_config.get('skip_pointer', False) else ["cfg"],
        "options": {
            "type": "dataflow analysis",
            "ir": "cfg"
        }
    })
    
    return {
        "filename": str(benchmark_file),
        "project_path": str(PROJECT_ROOT),
        "library_paths": get_library_paths(),
        "analysis": analyses
    }


def extract_metrics(world: World, analysis_manager, 
                   pointer_config: Dict[str, Any], ai_config: Dict[str, Any],
                   start_time: float, end_time: float) -> Dict[str, Any]:
    """Extract metrics from analysis results."""
    metrics = {
        "timing": {
            "total_time": end_time - start_time,
            "setup_time": 0.0,  # Could be tracked separately
            "analysis_time": end_time - start_time
        },
        "scopes": {
            "total_scopes": len(world.scope_manager.get_scopes()),
            "scope_names": [scope.get_qualname() for scope in world.scope_manager.get_scopes()]
        },
        "configuration": {
            "pointer": pointer_config,
            "ai": ai_config
        }
    }
    
    # Extract pointer analysis metrics
    try:
        if not pointer_config.get('skip_pointer', False):
            pointer_results = analysis_manager.get_results("pointer_analysis")
            if pointer_results:
                pointer_metrics = {
                    "available": True,
                    "scope_count": len(pointer_results),
                    "total_points_to_entries": 0,
                    "total_call_graph_edges": 0,
                    "avg_points_to_size": 0.0
                }
                
                points_to_sizes = []
                for scope, scope_results in pointer_results.items():
                    if "points_to" in scope_results:
                        pts = scope_results["points_to"]
                        pointer_metrics["total_points_to_entries"] += len(pts)
                        for var, pt_set in pts.items():
                            if hasattr(pt_set, '__len__'):
                                points_to_sizes.append(len(pt_set))
                    
                    if "call_graph" in scope_results:
                        cg = scope_results["call_graph"]
                        pointer_metrics["total_call_graph_edges"] += cg.get("total_cs_edges", 0)
                
                if points_to_sizes:
                    pointer_metrics["avg_points_to_size"] = statistics.mean(points_to_sizes)
                    pointer_metrics["max_points_to_size"] = max(points_to_sizes)
                
                metrics["pointer_analysis"] = pointer_metrics
            else:
                metrics["pointer_analysis"] = {"available": False, "error": "No results"}
        else:
            metrics["pointer_analysis"] = {"available": False, "skipped": True}
    except Exception as e:
        metrics["pointer_analysis"] = {"available": False, "error": str(e)}
    
    # Extract dataflow analysis metrics
    try:
        liveness_results = analysis_manager.get_results("liveness_analysis")
        if liveness_results:
            df_metrics = {
                "available": True,
                "scope_count": len(liveness_results) if isinstance(liveness_results, dict) else 1,
                "result_type": type(liveness_results).__name__
            }
            
            if hasattr(liveness_results, '__len__'):
                df_metrics["result_size"] = len(liveness_results)
            
            metrics["dataflow_analysis"] = df_metrics
        else:
            metrics["dataflow_analysis"] = {"available": False, "error": "No results"}
    except Exception as e:
        metrics["dataflow_analysis"] = {"available": False, "error": str(e)}
    
    # Extract CFG metrics
    try:
        cfg_metrics = {
            "total_basic_blocks": 0,
            "total_edges": 0
        }
        
        for scope in world.scope_manager.get_scopes():
            try:
                cfg = world.scope_manager.get_cfg(scope)
                if cfg:
                    cfg_metrics["total_basic_blocks"] += len(cfg.basic_blocks)
                    cfg_metrics["total_edges"] += len(cfg.edges)
            except Exception:
                pass
        
        metrics["cfg"] = cfg_metrics
    except Exception as e:
        metrics["cfg"] = {"error": str(e)}
    
    return metrics


def run_single_benchmark(benchmark_file: Path, pointer_config: Dict[str, Any], 
                        ai_config: Dict[str, Any], verbose: bool = False) -> Dict[str, Any]:
    """Run AI analysis on a single benchmark file."""
    result = {
        "benchmark": benchmark_file.name,
        "success": False,
        "error": None,
        "metrics": {}
    }
    
    if verbose:
        print(f"\n--- Running benchmark: {benchmark_file.name} ---")
    
    try:
        # Create configuration
        config = create_benchmark_config(benchmark_file, pointer_config, ai_config)
        
        # Run pipeline
        start_time = time.time()
        pipeline = Pipeline(config=config)
        pipeline.run()
        end_time = time.time()
        
        # Extract metrics
        world = pipeline.get_world()
        analysis_manager = pipeline.analysis_manager
        
        metrics = extract_metrics(world, analysis_manager, pointer_config, ai_config, 
                                start_time, end_time)
        
        result["success"] = True
        result["metrics"] = metrics
        
        if verbose:
            print(f"  Success! Time: {metrics['timing']['total_time']:.2f}s, "
                  f"Scopes: {metrics['scopes']['total_scopes']}")
        
    except Exception as e:
        result["error"] = str(e)
        if verbose:
            print(f"  Error: {e}")
    
    return result


def generate_config_combinations(base_pointer_config: Dict[str, Any], 
                               base_ai_config: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Generate different configuration combinations for testing."""
    combinations = []
    
    # Base configuration
    combinations.append({
        "name": "base",
        "pointer": base_pointer_config.copy(),
        "ai": base_ai_config.copy()
    })
    
    # Different k values
    for k in [1, 3]:
        if k != base_pointer_config.get('k', 2):
            config = {
                "name": f"k_{k}",
                "pointer": {**base_pointer_config, "k": k},
                "ai": base_ai_config.copy()
            }
            combinations.append(config)
    
    # Without pointer analysis
    combinations.append({
        "name": "no_pointer",
        "pointer": {**base_pointer_config, "skip_pointer": True},
        "ai": base_ai_config.copy()
    })
    
    # With widening
    if not base_ai_config.get('widening', False):
        combinations.append({
            "name": "widening",
            "pointer": base_pointer_config.copy(),
            "ai": {**base_ai_config, "widening": True}
        })
    
    return combinations


def print_summary(results: List[Dict[str, Any]], output_file: Optional[str] = None):
    """Print and optionally save summary of benchmark results."""
    total_benchmarks = len(results)
    successful_benchmarks = sum(1 for r in results if r["success"])
    
    # Calculate timing statistics
    successful_results = [r for r in results if r["success"]]
    if successful_results:
        times = [r["metrics"]["timing"]["total_time"] for r in successful_results]
        avg_time = statistics.mean(times)
        total_time = sum(times)
        
        # Calculate scope statistics
        scope_counts = [r["metrics"]["scopes"]["total_scopes"] for r in successful_results]
        avg_scopes = statistics.mean(scope_counts)
        
        # Calculate AI iteration statistics
        ai_iterations = []
        for r in successful_results:
            if r["metrics"].get("ai_analysis", {}).get("available", False):
                iterations = r["metrics"]["ai_analysis"].get("avg_iterations", 0)
                if iterations > 0:
                    ai_iterations.append(iterations)
        
        summary = {
            "overview": {
                "total_benchmarks": total_benchmarks,
                "successful": successful_benchmarks,
                "failed": total_benchmarks - successful_benchmarks,
                "success_rate": successful_benchmarks / total_benchmarks * 100
            },
            "timing": {
                "total_time": total_time,
                "average_time": avg_time,
                "min_time": min(times),
                "max_time": max(times)
            },
            "scopes": {
                "average_scopes": avg_scopes,
                "min_scopes": min(scope_counts),
                "max_scopes": max(scope_counts)
            },
            "ai_analysis": {
                "benchmarks_with_ai": len(ai_iterations),
                "avg_iterations": statistics.mean(ai_iterations) if ai_iterations else 0,
                "max_iterations": max(ai_iterations) if ai_iterations else 0
            },
            "detailed_results": results
        }
    else:
        summary = {
            "overview": {
                "total_benchmarks": total_benchmarks,
                "successful": 0,
                "failed": total_benchmarks,
                "success_rate": 0.0
            },
            "detailed_results": results
        }
    
    # Print summary
    print(f"\n{'='*60}")
    print("AI BENCHMARK SUMMARY")
    print(f"{'='*60}")
    print(f"Total benchmarks: {summary['overview']['total_benchmarks']}")
    print(f"Successful: {summary['overview']['successful']}")
    print(f"Failed: {summary['overview']['failed']}")
    print(f"Success rate: {summary['overview']['success_rate']:.1f}%")
    
    if "timing" in summary:
        print(f"\nTiming:")
        print(f"  Total time: {summary['timing']['total_time']:.2f}s")
        print(f"  Average time: {summary['timing']['average_time']:.2f}s")
        print(f"  Range: {summary['timing']['min_time']:.2f}s - {summary['timing']['max_time']:.2f}s")
    
    if "scopes" in summary:
        print(f"\nScopes:")
        print(f"  Average scopes: {summary['scopes']['average_scopes']:.1f}")
        print(f"  Range: {summary['scopes']['min_scopes']} - {summary['scopes']['max_scopes']}")
    
    if "ai_analysis" in summary and summary["ai_analysis"]["benchmarks_with_ai"] > 0:
        print(f"\nAI Analysis:")
        print(f"  Benchmarks with AI results: {summary['ai_analysis']['benchmarks_with_ai']}")
        print(f"  Average iterations: {summary['ai_analysis']['avg_iterations']:.1f}")
        print(f"  Max iterations: {summary['ai_analysis']['max_iterations']}")
    
    # Show failed benchmarks
    failed_results = [r for r in results if not r["success"]]
    if failed_results:
        print(f"\nFailed benchmarks:")
        for result in failed_results:
            print(f"  {result['benchmark']}: {result['error']}")
    
    # Save to file if requested
    if output_file:
        with open(output_file, 'w') as f:
            json.dump(summary, f, indent=2)
        print(f"\nDetailed results saved to {output_file}")


def main():
    """Main function."""
    parser = argparse.ArgumentParser(
        description="Run AI analysis on benchmark files with configurable settings",
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    
    # Benchmark selection
    parser.add_argument("--benchmark", help="Specific benchmark file to run")
    parser.add_argument("--exclude", nargs="+", help="Benchmark files to exclude")
    
    # Pointer analysis configuration  
    parser.add_argument("--k", type=int, default=2, help="Context sensitivity depth (default: 2)")
    parser.add_argument("--obj-depth", type=int, default=2, help="Object sensitivity depth (default: 2)")
    parser.add_argument("--field-sensitivity", choices=["attr", "elem", "value", "full"],
                       default="attr", help="Field sensitivity mode (default: attr)")
    parser.add_argument("--containers-mode", default="precise", help="Container handling mode")
    parser.add_argument("--skip-pointer", action="store_true", help="Skip pointer analysis")
    
    # AI analysis configuration
    parser.add_argument("--timeout", type=int, default=300, help="Analysis timeout (default: 300s)")
    parser.add_argument("--widening", action="store_true", help="Enable widening")
    
    # Execution options
    parser.add_argument("--config-sweep", action="store_true", 
                       help="Test multiple configurations on each benchmark")
    parser.add_argument("--verbose", action="store_true", help="Enable verbose output")
    parser.add_argument("--output", help="Save detailed results to JSON file")
    parser.add_argument("--parallel", type=int, help="Number of parallel processes (not implemented)")
    
    args = parser.parse_args()
    
    # Check benchmark directory
    if not BENCHMARK_DIR.exists():
        print(f"Error: Benchmark directory not found: {BENCHMARK_DIR}")
        return 1
    
    # Get benchmark files
    if args.benchmark:
        # Run specific benchmark
        benchmark_path = Path(args.benchmark)
        if not benchmark_path.is_absolute():
            benchmark_path = BENCHMARK_DIR / benchmark_path
        
        if not benchmark_path.exists():
            print(f"Error: Benchmark file not found: {benchmark_path}")
            return 1
        
        benchmark_files = [benchmark_path]
    else:
        # Run all benchmarks
        benchmark_files = list(BENCHMARK_DIR.glob("*.py"))
        
        # Exclude specified files
        if args.exclude:
            exclude_names = set(args.exclude)
            benchmark_files = [f for f in benchmark_files if f.name not in exclude_names]
    
    if not benchmark_files:
        print("No benchmark files found")
        return 1
    
    print(f"Found {len(benchmark_files)} benchmark files")
    if args.verbose:
        for bf in benchmark_files:
            print(f"  - {bf.name}")
    
    # Create base configurations
    base_pointer_config = {
        "k": args.k,
        "obj_depth": args.obj_depth,
        "field_sensitivity": args.field_sensitivity,
        "containers_mode": args.containers_mode,
        "skip_pointer": args.skip_pointer,
        "verbose": args.verbose
    }
    
    base_ai_config = {
        "timeout": args.timeout,
        "widening": args.widening,
        "verbose": args.verbose
    }
    
    # Generate configurations to test
    if args.config_sweep:
        configs = generate_config_combinations(base_pointer_config, base_ai_config)
        print(f"Testing {len(configs)} different configurations")
    else:
        configs = [{
            "name": "single",
            "pointer": base_pointer_config,
            "ai": base_ai_config
        }]
    
    # Run benchmarks
    all_results = []
    total_combinations = len(benchmark_files) * len(configs)
    current_combination = 0
    
    for config in configs:
        if len(configs) > 1:
            print(f"\n{'='*40}")
            print(f"Configuration: {config['name']}")
            print(f"{'='*40}")
        
        config_results = []
        for benchmark_file in benchmark_files:
            current_combination += 1
            if not args.verbose:
                print(f"Progress: {current_combination}/{total_combinations} "
                      f"({current_combination/total_combinations*100:.1f}%)", end='\r')
            
            result = run_single_benchmark(benchmark_file, config["pointer"], 
                                        config["ai"], args.verbose)
            result["config_name"] = config["name"]
            config_results.append(result)
            all_results.append(result)
        
        # Print config summary if doing sweep
        if args.config_sweep:
            successful = sum(1 for r in config_results if r["success"])
            print(f"Configuration '{config['name']}': {successful}/{len(config_results)} successful")
    
    if not args.verbose:
        print()  # New line after progress
    
    # Print final summary
    if args.config_sweep:
        # Group results by configuration
        by_config = {}
        for result in all_results:
            config_name = result["config_name"]
            if config_name not in by_config:
                by_config[config_name] = []
            by_config[config_name].append(result)
        
        print(f"\n{'='*60}")
        print("CONFIGURATION COMPARISON")
        print(f"{'='*60}")
        
        for config_name, config_results in by_config.items():
            successful = sum(1 for r in config_results if r["success"])
            success_rate = successful / len(config_results) * 100
            
            times = [r["metrics"]["timing"]["total_time"] for r in config_results if r["success"]]
            avg_time = statistics.mean(times) if times else 0
            
            print(f"{config_name:15s}: {successful:2d}/{len(config_results):2d} "
                  f"({success_rate:5.1f}%) avg_time={avg_time:6.2f}s")
    
    print_summary(all_results, args.output)
    
    # Return appropriate exit code
    success_count = sum(1 for r in all_results if r["success"])
    return 0 if success_count > 0 else 1


if __name__ == "__main__":
    sys.exit(main())
