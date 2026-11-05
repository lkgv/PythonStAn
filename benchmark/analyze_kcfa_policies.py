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
import os
from pathlib import Path
from typing import List, Dict, Optional, Any

# Add pythonstan to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from benchmark.metrics_collector import AnalysisMetrics, MetricsCollector, save_results
from pythonstan.world.pipeline import Pipeline
from pythonstan.analysis.pointer.kcfa import PointerAnalysis, Config


def _strip_line_suffix(path_str: str) -> str:
    if ":" not in path_str:
        return path_str
    head, tail = path_str.rsplit(":", 1)
    if tail.isdigit():
        return head
    return path_str


def _resolve_test_path(raw_path: str) -> Path:
    cleaned = _strip_line_suffix(raw_path)
    path = Path(cleaned)
    if not path.is_absolute():
        base = Path(__file__).parent.parent
        path = (base / path).resolve()
    return path


def _discover_test_files(test_path: Path) -> List[Path]:
    if test_path.is_file():
        return [test_path]
    if test_path.is_dir():
        files = set(test_path.rglob("test_*.py"))
        files.update(test_path.rglob("*_test.py"))
        return sorted(p for p in files if p.is_file())
    raise FileNotFoundError(f"Test path not found: {test_path}")


def _module_name_for_path(test_file: Path, test_root: Path) -> Optional[str]:
    rel = test_file.relative_to(test_root)
    if rel.name == "__init__.py":
        rel = rel.parent
        if not rel.parts:
            return None
    else:
        rel = rel.with_suffix("")
    return ".".join((test_root.name,) + rel.parts)


def _build_test_entry_file(test_files: List[Path], test_root: Path, entry_dir: Path) -> Path:
    module_names = []
    for test_file in test_files:
        mod_name = _module_name_for_path(test_file, test_root)
        if mod_name:
            module_names.append(mod_name)
    module_names = sorted(set(module_names))
    entry_path = entry_dir / "__pythonstan_test_entry__.py"
    entry_path.write_text("\n".join(f"import {name}" for name in module_names) + "\n", encoding="utf-8")
    return entry_path


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
    
    # Param sensitivity
    "1-param",   # 1-param sensitive
    "2-param",   # 2-param sensitive
    "3-param",   # 3-param sensitive
    
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
    "fastapi": {
        "filename": "/mnt/data_fast/code/PythonStAn/benchmark/projects/fastapi/fastapi/__init__.py",
        "project_path": "/mnt/data_fast/code/PythonStAn/benchmark/projects/fastapi",
        "import_level": 10,
        "time_count": True,
        "max_iterations": 5000000,
        "library_paths": [
            "/mnt/data_fast/code/PythonStAn/benchmark/projects/starlette",
            # "/mnt/data_fast/code/PythonStAn/benchmark/projects/flask/.venv/lib/python3.9/site-packages",
            # "/mnt/data_fast/code/PythonStAn/benchmark/projects/flask/.venv/lib/python3.9",
        ],
        "analysis": []  # No pipeline analyses needed
    },
    "airflow": {
        "filename": "/mnt/data_fast/code/PythonStAn/benchmark/projects/airflow/airflow/__main__.py",
        "project_path": "/mnt/data_fast/code/PythonStAn/benchmark/projects/airflow",
        "import_level": 5,
        "time_count": True,
        "max_iterations": 5000000,
        "library_paths": [
            # "/mnt/data_fast/code/PythonStAn/benchmark/projects/flask/.venv/lib/python3.9/site-packages",
            # "/mnt/data_fast/code/PythonStAn/benchmark/projects/flask/.venv/lib/python3.9",
        ],
        "analysis": []  # No pipeline analyses needed
    },
    
    "flask": {
        "filename": "/mnt/data_fast/code/PythonStAn/benchmark/projects/flask/src/flask/__main__.py",
        "project_path": "/mnt/data_fast/code/PythonStAn/benchmark/projects/flask/src",
        "test_path": "/mnt/data_fast/code/PythonStAn/benchmark/projects/flask/tests",
        "import_level": 10,
        "time_count": True,
        "max_iterations": 5000000,
        "library_paths": [
            # "/mnt/data_fast/code/PythonStAn/benchmark/projects/flask/.venv/lib/python3.9/site-packages",
            # "/mnt/data_fast/code/PythonStAn/benchmark/projects/flask/.venv/lib/python3.9",
        ],
        "analysis": []  # No pipeline analyses needed
    },
    "werkzeug": {
        "filename": "/mnt/data_fast/code/PythonStAn/benchmark/projects/werkzeug/src/werkzeug/__init__.py",
        "project_path": "/mnt/data_fast/code/PythonStAn/benchmark/projects/werkzeug/src",
        "import_level": 20,
        "time_count": True,
        "index_sensitive": True,
        "library_paths": [
            # "/mnt/data_fast/code/PythonStAn/benchmark/projects/werkzeug/.venv/lib/python3.10/site-packages",
            # "/mnt/data_fast/code/PythonStAn/benchmark/projects/werkzeug/.venv/lib/python3.10",
        ],        
        "analysis": []
    }
}

class PolicyBenchmark:
    """Benchmark runner for pointer analysis policies."""
    
    def __init__(self, project_name: str, policies: List[str], debug_config: Optional[Dict[str, Any]] = None,
                 test_path: Optional[str] = None):
        """Initialize benchmark.
        
        Args:
            project_name: Project name (flask, werkzeug)
            policies: List of policy strings to test
            debug_config: Optional debug configuration dictionary
        """
        self.project_name = project_name
        self.policies = policies
        self.config = PROJECT_CONFIGS[project_name]
        self.results: List[AnalysisMetrics] = []
        self.test_path = self.config.get('test_path', None)
        
        # Store debug config
        self._debug_enabled = debug_config.get('enabled', False) if debug_config else False
        self._debug_config = debug_config or {}
    
    def run_all(self) -> List[AnalysisMetrics]:
        """Run benchmark for all policies.
        
        Returns:
            List of AnalysisMetrics for each policy
        """
        print(f"\n{'='*80}")
        print(f"Starting benchmark for {self.project_name.upper()}")
        print(f"Testing {len(self.policies)} policies")
        print(f"{'='*80}\n")
        
        policies = ["0-cfa", "1-cfa", "1-obj", "1-param"]
        # for i, policy in enumerate(self.policies, 1):
        # for i, policy in enumerate(policies, 1):
        for i in [2]:
            policy = "0-cfa"
            
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
                    print(f"  Call edges: {metrics.call_edges}")
                    print(f"  Heap locations: {metrics.num_heap_locations}")
                    print(f"  Unknowns: {metrics.total_unknowns}")
                else:
                    print(f"✗ FAILED: {metrics.error_message}")
                    
            except KeyboardInterrupt:
                print("\n\nBenchmark interrupted by user. Saving partial results...")
                traceback.print_exc()
                break
            except Exception as e:
                raise e
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
            
            # Get debug flags from args if available
            debug_enabled = getattr(self, '_debug_enabled', False)
            debug_config = getattr(self, '_debug_config', {})
            
            local_config = dict(self.config)
            local_config["analysis"] = list(self.config.get("analysis", []))
            local_config["library_paths"] = list(self.config.get("library_paths", []))

            test_root = None
            test_files: List[Path] = []
            entry_filename = local_config["filename"]
            if self.test_path:
                resolved_test_path = _resolve_test_path(self.test_path)
                test_files = _discover_test_files(resolved_test_path)
                test_root = resolved_test_path if resolved_test_path.is_dir() else resolved_test_path.parent
                entry_filename = str(_build_test_entry_file(test_files, test_root, Path(local_config["project_path"])))
                test_root_parent = str(test_root.parent)
                if test_root_parent not in local_config["library_paths"]:
                    local_config["library_paths"].append(test_root_parent)

            pta_config = Config(
                context_policy=policy,
                max_iterations=200000,
                verbose=False,
                log_level="INFO",  # Reduce noise
                track_unknowns=True,
                build_class_hierarchy=True,
                use_mro_resolution=True,
                project_path=local_config['project_path'],
                library_paths=local_config.get('library_paths', []),
                max_import_depth=local_config.get('import_level', 2),
                index_sensitive=False,
                # Debug monitoring options
                enable_debug_monitor=debug_enabled,
                debug_log_interval=debug_config.get('monitor_interval', 1000),
                track_object_flow=debug_config.get('track_object_flow', False),
                track_pfg_activation=debug_config.get('track_pfg', False),
                export_debug_data=debug_config.get('export_debug', False),
                debug_output_dir=debug_config.get('debug_output_dir', 'debug_output')
            )
            
            analyzer_config = {
                "name": f"advanced_pointer_analysis",
                "id": "PointerAnalysis",
                "description": f"Advanced k-CFA pointer analysis",
                "prev_analysis": ["ir"],
                "options": pta_config.to_dict()
            }

            if test_root:
                analyzer_config["options"]["test_root"] = str(test_root)
                analyzer_config["options"]["test_files"] = [str(p) for p in test_files]
                analyzer_config["options"]["test_entry_file"] = entry_filename
            
            local_config['analysis'].append(analyzer_config)
            
            # TODO add the pointer analyzer into ppl
            
            pipeline_time = time.perf_counter() - pipeline_start
            timings['pipeline'] = pipeline_time
            print(f"  Pipeline loaded in {pipeline_time:.2f}s")
            
            # Step 2: Run pointer analysis
            print(f"  Running pointer analysis with {policy}...")
            analysis_start = time.perf_counter()
            
            local_config["filename"] = entry_filename
            ppl = Pipeline(config=local_config)
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
            raise e
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
        choices=["flask", "werkzeug", "pycg", "both", "airflow", "fastapi"],
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
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug monitoring"
    )
    parser.add_argument(
        "--monitor-interval",
        type=int,
        default=1000,
        help="Debug monitoring snapshot interval (default: 1000)"
    )
    parser.add_argument(
        "--track-object-flow",
        action="store_true",
        help="Track object creation and propagation"
    )
    parser.add_argument(
        "--track-pfg",
        action="store_true",
        help="Track PFG edge activation"
    )
    parser.add_argument(
        "--export-debug",
        action="store_true",
        help="Export debug data to files"
    )
    parser.add_argument(
        "--debug-output-dir",
        type=str,
        default="debug_output",
        help="Debug output directory (default: debug_output)"
    )
    parser.add_argument(
        "--test-path",
        type=str,
        default=None,
        help="Test file or directory path (optional :line suffix supported)"
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
    
    # Prepare debug config
    debug_config = None
    if args.debug or args.export_debug or args.track_object_flow or args.track_pfg:
        debug_config = {
            'enabled': args.debug or args.export_debug,
            'monitor_interval': args.monitor_interval,
            'track_object_flow': args.track_object_flow,
            'track_pfg': args.track_pfg,
            'export_debug': args.export_debug,
            'debug_output_dir': args.debug_output_dir
        }
        
        print(f"\nDebug monitoring enabled:")
        print(f"  - Output directory: {args.debug_output_dir}")
        print(f"  - Monitor interval: {args.monitor_interval}")
        print(f"  - Track object flow: {args.track_object_flow}")
        print(f"  - Track PFG: {args.track_pfg}")
        print(f"  - Export debug data: {args.export_debug}")
        print()
    
    for project in projects:
        try:
            benchmark = PolicyBenchmark(project, policies, debug_config, test_path=args.test_path)
            results = benchmark.run_all()
            
            # Save results
            output_file = output_dir / f"{project}_validation.json"
            benchmark.save_results(str(output_file))
            
        except KeyboardInterrupt as e:
            traceback.print_exc()
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

