#!/usr/bin/env python3
"""Benchmark k-CFA pointer analysis across all context sensitivity policies.

This script validates the refactored k-CFA pointer analysis on Flask and Werkzeug
codebases, testing all 16 context policies with comprehensive metrics collection.

Usage:
    python benchmark/analyze_kcfa_policies.py flask
    python benchmark/analyze_kcfa_policies.py werkzeug
    python benchmark/analyze_kcfa_policies.py both
"""

import argparse
import sys
import time
import tracemalloc
import traceback
from pathlib import Path
from typing import List, Dict, Optional

# Add pythonstan to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from benchmark.metrics_collector import AnalysisMetrics, MetricsCollector, save_results
from pythonstan.world.pipeline import Pipeline
from pythonstan.analysis.pointer.kcfa import PointerAnalysis, Config


# All 16 context sensitivity policies
ALL_POLICIES = [
    # Call-string sensitivity (k-CFA)
    "0-cfa",    # Context-insensitive baseline
    "1-cfa",    # 1-CFA
    "2-cfa",    # 2-CFA (default)
    "3-cfa",    # 3-CFA
    
    # Object sensitivity
    "1-obj",    # 1-object sensitive
    "2-obj",    # 2-object sensitive
    "3-obj",    # 3-object sensitive
    
    # Type sensitivity
    "1-type",   # 1-type sensitive
    "2-type",   # 2-type sensitive
    "3-type",   # 3-type sensitive
    
    # Receiver sensitivity
    "1-rcv",    # 1-receiver sensitive
    "2-rcv",    # 2-receiver sensitive
    "3-rcv",    # 3-receiver sensitive
    
    # Hybrid policies
    "1c1o",     # 1-call + 1-object
    "2c1o",     # 2-call + 1-object
    "1c2o",     # 1-call + 2-object
]


# Project configurations
PROJECT_CONFIGS = {
    "flask": {
        "filename": "/mnt/data_fast/code/PythonStAn/benchmark/projects/flask/src/flask/__init__.py",
        "project_path": "/mnt/data_fast/code/PythonStAn/benchmark/projects/flask/src",
        "library_paths": [
            "/mnt/data_fast/code/PythonStAn/benchmark/projects/flask/.venv/lib/python3.10/site-packages",
            "/mnt/data_fast/code/PythonStAn/benchmark/projects/flask/.venv/lib/python3.10",
        ],
        "analysis": []  # No pipeline analyses needed
    },
    "werkzeug": {
        "filename": "/mnt/data_fast/code/PythonStAn/benchmark/projects/werkzeug/src/werkzeug/__init__.py",
        "project_path": "/mnt/data_fast/code/PythonStAn/benchmark/projects/werkzeug/src",
        "library_paths": [
            "/mnt/data_fast/code/PythonStAn/benchmark/projects/werkzeug/.venv/lib/python3.10/site-packages",
            "/mnt/data_fast/code/PythonStAn/benchmark/projects/werkzeug/.venv/lib/python3.10",
        ],        
        "analysis": []
    }
}

class PolicyBenchmark:
    """Benchmark runner for pointer analysis policies."""
    
    def __init__(self, project_name: str, policies: List[str]):
        """Initialize benchmark.
        
        Args:
            project_name: Project name (flask, werkzeug)
            policies: List of policy strings to test
        """
        self.project_name = project_name
        self.policies = policies
        self.config = PROJECT_CONFIGS[project_name]
        self.results: List[AnalysisMetrics] = []
    
    def run_all(self) -> List[AnalysisMetrics]:
        """Run benchmark for all policies.
        
        Returns:
            List of AnalysisMetrics for each policy
        """
        print(f"\n{'='*80}")
        print(f"Starting benchmark for {self.project_name.upper()}")
        print(f"Testing {len(self.policies)} policies")
        print(f"{'='*80}\n")
        
        # for i, policy in enumerate(self.policies, 1):
        for i in [2]:
            policy = "2-cfa"
            
            print(f"[{i}/{len(self.policies)}] Testing policy: {policy}")
            print("-" * 80)
            
            try:
                metrics = self.run_single_policy(policy)
                self.results.append(metrics)
                
                if metrics.success:
                    print(f"✓ SUCCESS")
                    print(f"  Time: {metrics.total_time:.2f}s")
                    print(f"  Memory: {metrics.peak_memory:.1f} MB")
                    print(f"  Iterations: {metrics.solver_iterations}")
                    print(f"  Variables: {metrics.num_variables}")
                    print(f"  Objects: {metrics.num_objects}")
                    print(f"  Unknowns: {metrics.total_unknowns}")
                else:
                    print(f"✗ FAILED: {metrics.error_message}")
                    
            except KeyboardInterrupt:
                print("\n\nBenchmark interrupted by user. Saving partial results...")
                break
            except Exception as e:
                print(f"✗ UNEXPECTED ERROR: {e}")
                traceback.print_exc()
                # Create failed metrics
                metrics = MetricsCollector.create_failed_metrics(
                    policy=policy,
                    project=self.project_name,
                    error_message=f"Unexpected error: {e}",
                    timings={'total': 0.0},
                    memory={'peak': 0}
                )
                self.results.append(metrics)
            
            print()
        
        print(f"\n{'='*80}")
        print(f"Benchmark complete for {self.project_name.upper()}")
        print(f"Successful: {sum(1 for r in self.results if r.success)}/{len(self.results)}")
        print(f"{'='*80}\n")
        
        return self.results
    
    def run_single_policy(self, policy: str) -> AnalysisMetrics:
        """Run analysis for a single policy.
        
        Args:
            policy: Policy string
        
        Returns:
            AnalysisMetrics for this policy
        """
        timings = {}
        memory = {}
        
        try:
            # Start memory tracking
            tracemalloc.start()
            total_start = time.perf_counter()
            
            # Step 1: Load project with Pipeline
            print(f"  Loading {self.project_name} with Pipeline...")
            pipeline_start = time.perf_counter()
            
            pta_config = Config(
                context_policy=policy,
                max_iterations=10000,
                verbose=False,
                log_level="INFO",  # Reduce noise
                track_unknowns=True,
                build_class_hierarchy=True,
                use_mro_resolution=True,
                project_path=self.config['project_path'],
                library_paths=self.config.get('library_paths', []),
                max_import_depth=2
            )
            
            analyzer_config = {
                "name": f"advanced_pointer_analysis",
                "id": "PointerAnalysis",
                "description": f"Advanced k-CFA pointer analysis",
                "prev_analysis": ["ir"],
                "options": pta_config.to_dict()
            }
            
            self.config['analysis'].append(analyzer_config)
            
            # TODO add the pointer analyzer into ppl
            
            pipeline_time = time.perf_counter() - pipeline_start
            timings['pipeline'] = pipeline_time
            print(f"  Pipeline loaded in {pipeline_time:.2f}s")
            
            # Step 2: Run pointer analysis
            print(f"  Running pointer analysis with {policy}...")
            analysis_start = time.perf_counter()
            
            ppl = Pipeline(config=self.config)
            ppl.run()
                        
            # Run analysis
            # analysis = PointerAnalysis(analysis_config)
            # result = analysis.analyze(module)
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
                project=self.project_name,
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
                project=self.project_name,
                error_message=error_msg,
                timings=timings,
                memory=memory
            )
            
            return metrics
    
    def save_results(self, output_path: str) -> None:
        """Save results to JSON file.
        
        Args:
            output_path: Output file path
        """
        save_results(self.results, output_path)
        print(f"Results saved to: {output_path}")


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Benchmark k-CFA pointer analysis policies"
    )
    parser.add_argument(
        "project",
        choices=["flask", "werkzeug", "both"],
        help="Project to benchmark"
    )
    parser.add_argument(
        "--policies",
        nargs="+",
        default=ALL_POLICIES,
        help="Specific policies to test (default: all)"
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default="benchmark/results",
        help="Output directory for results"
    )
    
    args = parser.parse_args()
    
    # Ensure output directory exists
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # Validate policies
    policies = args.policies if isinstance(args.policies, list) else ALL_POLICIES
    invalid = [p for p in policies if p not in ALL_POLICIES]
    if invalid:
        print(f"Error: Invalid policies: {invalid}")
        print(f"Valid policies: {ALL_POLICIES}")
        return 1
    
    # Run benchmarks
    projects = ["flask", "werkzeug"] if args.project == "both" else [args.project]
    
    for project in projects:
        try:
            benchmark = PolicyBenchmark(project, policies)
            results = benchmark.run_all()
            
            # Save results
            output_file = output_dir / f"{project}_validation.json"
            benchmark.save_results(str(output_file))
            
        except KeyboardInterrupt:
            print("\nBenchmark interrupted. Exiting...")
            return 130
        except Exception as e:
        
            print(f"\nFatal error benchmarking {project}: {e}")
            traceback.print_exc()
            return 1
    
    print("\n" + "="*80)
    print("ALL BENCHMARKS COMPLETE")
    print("="*80)
    return 0


if __name__ == "__main__":
    sys.exit(main())

