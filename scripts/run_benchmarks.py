#!/usr/bin/env python3
"""
Script to run all benchmarks through the PythonStAn pipeline.
"""
import os
import sys
import ast
import argparse
from pathlib import Path

from pythonstan.world.pipeline import Pipeline
from pythonstan.world import World

# Get the absolute path to the project root
PROJECT_ROOT = Path(__file__).parent.parent.absolute()
BENCHMARK_DIR = PROJECT_ROOT / 'benchmark'

# List of available analyses
AVAILABLE_ANALYSES = [
    {
        "name": "cfg",
        "id": "CFGAnalysis",
        "description": "control flow graph analysis",
        "prev_analysis": [],
        "options": {
            "type": "transform"
        }
    },
    {
        "name": "liveness",
        "id": "LivenessAnalysis",
        "description": "liveness analysis",
        "prev_analysis": ["cfg"],
        "options": {
            "type": "dataflow analysis",
            "ir": "ssa"
        }
    },
    {
        "name": "reaching_definition",
        "id": "ReachingDefinitionAnalysis",
        "description": "reaching definition analysis",
        "prev_analysis": ["cfg"],
        "options": {
            "type": "dataflow analysis",
            "solver": "WorklistSolver"
        }
    },
    {
        "name": "callgraph",
        "id": "CallGraphAnalysis",
        "description": "call graph analysis",
        "prev_analysis": ["cfg"],
        "options": {
            "type": "transform"
        }
    }
]

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

def run_benchmark(benchmark_file, analyses=None, output_dir=None):
    """Run a single benchmark file through the PythonStAn pipeline."""
    benchmark_path = os.path.abspath(benchmark_file)
    
    if not os.path.exists(benchmark_path):
        print(f"Benchmark file not found: {benchmark_path}")
        return False
    
    # Set up the analysis configuration
    if analyses is None:
        # Default to all analyses
        selected_analyses = AVAILABLE_ANALYSES
    else:
        # Filter to selected analyses
        selected_analyses = [a for a in AVAILABLE_ANALYSES if a["name"] in analyses]
    
    # Create the pipeline configuration
    config = {
        "filename": benchmark_path,
        "project_path": str(PROJECT_ROOT),
        "library_paths": get_library_paths(),
        "analysis": selected_analyses
    }
    
    print(f"\n===== Running PythonStAn on {os.path.basename(benchmark_path)} =====")
    
    # Create and run the pipeline
    pipeline = Pipeline(config=config)
    pipeline.run()
    
    # Print results
    print(f"\n----- Three Address Form -----")
    print('\n'.join([ast.unparse(x) for x in pipeline.get_world().scope_manager.get_ir(
        pipeline.get_world().entry_module, 'three address form').body]))
    
    print(f"\n----- Scopes -----")
    for scope in pipeline.get_world().scope_manager.get_scopes():
        print(f'<Scope: {scope.get_qualname()}>')
        print('\n'.join([str(x) for x in pipeline.get_world().scope_manager.get_ir(scope, 'ir')]))
        print()
    
    # Save results to output directory if specified
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)
        base_name = os.path.splitext(os.path.basename(benchmark_path))[0]
        output_path = os.path.join(output_dir, f"{base_name}_results.txt")
        
        with open(output_path, 'w') as f:
            f.write(f"===== PythonStAn Results for {os.path.basename(benchmark_path)} =====\n\n")
            
            f.write("----- Three Address Form -----\n")
            f.write('\n'.join([ast.unparse(x) for x in pipeline.get_world().scope_manager.get_ir(
                pipeline.get_world().entry_module, 'three address form').body]))
            f.write("\n\n")
            
            f.write("----- Scopes -----\n")
            for scope in pipeline.get_world().scope_manager.get_scopes():
                f.write(f'<Scope: {scope.get_qualname()}>\n')
                f.write('\n'.join([str(x) for x in pipeline.get_world().scope_manager.get_ir(scope, 'ir')]))
                f.write("\n\n")
        
        print(f"Results saved to {output_path}")
    
    return True

def run_all_benchmarks(analyses=None, output_dir=None):
    """Run all benchmark files in the benchmark directory."""
    success_count = 0
    failure_count = 0
    
    if not os.path.isdir(BENCHMARK_DIR):
        print(f"Benchmark directory not found: {BENCHMARK_DIR}")
        return
    
    # Get all Python files in the benchmark directory
    benchmark_files = list(BENCHMARK_DIR.glob("*.py"))
    
    print(f"Found {len(benchmark_files)} benchmark files")
    
    for benchmark_file in benchmark_files:
        try:
            success = run_benchmark(benchmark_file, analyses, output_dir)
            if success:
                success_count += 1
            else:
                failure_count += 1
        except Exception as e:
            print(f"Error running benchmark {benchmark_file}: {e}")
            failure_count += 1
    
    print(f"\n===== Benchmark Summary =====")
    print(f"Total: {len(benchmark_files)}")
    print(f"Successful: {success_count}")
    print(f"Failed: {failure_count}")

def main():
    parser = argparse.ArgumentParser(description="Run PythonStAn on benchmark programs")
    parser.add_argument(
        "benchmark", nargs="?",
        help="Specific benchmark file to run (if not specified, runs all benchmarks)"
    )
    parser.add_argument(
        "--analyses", "-a", nargs="+", choices=[a["name"] for a in AVAILABLE_ANALYSES],
        help="Specific analyses to run (default: all)"
    )
    parser.add_argument(
        "--output-dir", "-o",
        help="Directory to save analysis results"
    )
    parser.add_argument(
        "--list", "-l", action="store_true",
        help="List available benchmarks and exit"
    )
    
    args = parser.parse_args()
    
    if args.list:
        print("Available benchmarks:")
        if os.path.isdir(BENCHMARK_DIR):
            for benchmark_file in sorted(BENCHMARK_DIR.glob("*.py")):
                print(f"  {benchmark_file.name}")
        else:
            print(f"  Benchmark directory not found: {BENCHMARK_DIR}")
        return
    
    if args.benchmark:
        # Run a specific benchmark
        benchmark_path = args.benchmark
        if not os.path.isabs(benchmark_path):
            # If a relative path is given, check in the benchmark directory
            potential_path = BENCHMARK_DIR / benchmark_path
            if potential_path.exists():
                benchmark_path = potential_path
        
        run_benchmark(benchmark_path, args.analyses, args.output_dir)
    else:
        # Run all benchmarks
        run_all_benchmarks(args.analyses, args.output_dir)

if __name__ == "__main__":
    main() 