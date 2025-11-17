#!/usr/bin/env python3
"""Compare different context-sensitive policies for pointer analysis.

Usage:
    python benchmark/compare_context_policies.py flask --policies all
    python benchmark/compare_context_policies.py werkzeug --policies core
    python benchmark/compare_context_policies.py both --policies 0-cfa,1-cfa,2-cfa,1-obj
"""

import argparse
import sys
import json
import time
from pathlib import Path
from typing import List, Dict, Optional
from dataclasses import dataclass, asdict
from datetime import datetime
import traceback

# Add pythonstan to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from pythonstan.analysis.pointer.kcfa2.config import KCFAConfig
from pythonstan.analysis.pointer.kcfa2.analysis import KCFA2PointerAnalysis
from pythonstan.world.world import World
from pythonstan.world.pipeline import Pipeline
from pythonstan.ir.ir_statements import IRModule

# Policy sets
CORE_POLICIES = [
    "0-cfa",    # Context-insensitive (baseline)
    "1-cfa",    # 1-CFA
    "2-cfa",    # 2-CFA (current default)
    "3-cfa",    # 3-CFA
    "1-obj",    # 1-object sensitive
    "2-obj",    # 2-object sensitive
    "1-type",   # 1-type sensitive
    "2-type",   # 2-type sensitive
    "1-rcv",    # 1-receiver sensitive
]

EXTENDED_POLICIES = CORE_POLICIES + [
    "3-obj",    # 3-object sensitive
    "3-type",   # 3-type sensitive
    "2-rcv",    # 2-receiver sensitive
    "3-rcv",    # 3-receiver sensitive
    "1c1o",     # Hybrid: 1-call + 1-object
    "2c1o",     # Hybrid: 2-call + 1-object
]


@dataclass
class PolicyResult:
    """Results for a single policy."""
    policy: str
    project: str
    
    # Performance
    duration: float
    throughput: float  # LOC/sec
    modules_analyzed: int
    
    # Precision
    total_variables: int
    singleton_sets: int
    singleton_ratio: float
    avg_set_size: float
    max_set_size: int
    
    # Contexts
    total_contexts: int
    avg_vars_per_context: float
    
    # Scalability
    total_functions: int
    total_classes: int
    
    # Status
    success: bool
    error_message: Optional[str] = None


class PolicyComparator:
    def __init__(self, project_path: Path, project_name: str):
        self.project_path = project_path
        self.project_name = project_name
        self.modules = []
    
    def find_python_modules(self, max_modules: Optional[int] = None) -> List[Path]:
        """Find Python modules in the project."""
        modules = sorted(self.project_path.rglob('*.py'))
        
        # Filter out test files and __pycache__
        modules = [
            m for m in modules
            if '__pycache__' not in str(m)
            and 'test' not in m.name.lower()
        ]
        
        if max_modules:
            modules = modules[:max_modules]
        
        self.modules = modules
        return modules
    
    def count_loc(self, module_path: Path) -> int:
        """Count lines of code in a module."""
        try:
            with open(module_path, 'r', encoding='utf-8') as f:
                return len([line for line in f if line.strip() and not line.strip().startswith('#')])
        except Exception:
            return 0
    
    def run_policy(self, policy: str, timeout: int = 300) -> PolicyResult:
        """Run analysis with specific policy."""
        print(f"  Running {policy}...", end=' ', flush=True)
        
        config = KCFAConfig(
            context_policy=policy,
            field_sensitivity_mode="attr-name",
            build_class_hierarchy=True,
            use_mro=True,
            verbose=False
        )
        
        try:
            start_time = time.time()
            
            # Analyze all modules
            analyses = []
            for module_path in self.modules:
                try:
                    # Use Pipeline to properly create IR module
                    pipeline_config = {
                        "filename": str(module_path),
                        "project_path": str(self.project_path),
                        "library_paths": [],
                        "analysis": [],
                        "lazy_ir_construction": True
                    }
                    
                    pipeline = Pipeline(config=pipeline_config)
                    pipeline.run()
                    
                    world = pipeline.get_world()
                    ir_module = world.entry_module
                    scope_manager = world.scope_manager
                    
                    # Extract functions and classes from scope manager (CORRECT WAY)
                    from pythonstan.ir import IRFunc, IRClass
                    functions = []
                    classes = []
                    if hasattr(scope_manager, 'get_subscopes'):
                        subscopes = scope_manager.get_subscopes(ir_module)
                        functions = [scope for scope in subscopes if isinstance(scope, IRFunc)]
                        classes = [scope for scope in subscopes if isinstance(scope, IRClass)]
                        
                        # Extract methods from classes
                        for cls in classes:
                            cls_subscopes = scope_manager.get_subscopes(cls)
                            methods = [scope for scope in cls_subscopes if isinstance(scope, IRFunc)]
                            functions.extend(methods)
                    
                    # Run analysis
                    analysis = KCFA2PointerAnalysis(config)
                    
                    # Plan with functions (if any)
                    if functions:
                        analysis.plan(functions)
                    
                    # Also plan module-level events
                    if hasattr(analysis, 'plan_module'):
                        analysis.plan_module(ir_module)
                    
                    # Process class definitions
                    if classes and hasattr(analysis, 'plan_classes'):
                        analysis.plan_classes(classes)
                    
                    analysis.initialize()
                    analysis.run()
                    
                    analyses.append(analysis)
                    
                    # Check timeout
                    if time.time() - start_time > timeout:
                        print(f"⏱ TIMEOUT", flush=True)
                        break
                        
                except Exception as e:
                    # Skip problematic modules but continue
                    if config.verbose:
                        print(f"\n  Skipped {module_path.name}: {type(e).__name__}: {e}")
                    continue
            
            duration = time.time() - start_time
            
            # Collect metrics
            result = self._collect_metrics(policy, analyses, duration)
            
            print(f"✓ ({duration:.2f}s, {result.singleton_ratio:.1f}% singleton)")
            return result
            
        except Exception as e:
            duration = time.time() - start_time if 'start_time' in locals() else 0.0
            error_msg = f"{type(e).__name__}: {str(e)}"
            print(f"✗ FAILED: {error_msg}")
            
            return PolicyResult(
                policy=policy,
                project=self.project_name,
                duration=duration,
                throughput=0.0,
                modules_analyzed=0,
                total_variables=0,
                singleton_sets=0,
                singleton_ratio=0.0,
                avg_set_size=0.0,
                max_set_size=0,
                total_contexts=0,
                avg_vars_per_context=0.0,
                total_functions=0,
                total_classes=0,
                success=False,
                error_message=error_msg
            )
    
    def _collect_metrics(self, policy: str, analyses: List, duration: float) -> PolicyResult:
        """Collect comprehensive metrics."""
        # Aggregate across all analyses
        total_vars = 0
        singleton_sets = 0
        total_pts_sets = 0
        all_sizes = []
        total_contexts = 0
        total_functions = 0
        total_classes = 0
        
        for analysis in analyses:
            if hasattr(analysis, '_env'):
                env_size = len(analysis._env)
                total_vars += env_size
                for (ctx, var), pts in analysis._env.items():
                    if pts:
                        total_pts_sets += 1
                        size = len(pts)
                        all_sizes.append(size)
                        if size == 0:
                            pass  # empty set
                        elif size == 1:
                            singleton_sets += 1
            
            if hasattr(analysis, '_contexts'):
                ctx_size = len(analysis._contexts)
                total_contexts += ctx_size
            
            if hasattr(analysis, '_functions'):
                func_size = len(analysis._functions)
                total_functions += func_size
            
            if hasattr(analysis, '_class_hierarchy') and analysis._class_hierarchy:
                if hasattr(analysis._class_hierarchy, '_bases'):
                    total_classes += len(analysis._class_hierarchy._bases)
        
        # Calculate statistics
        singleton_ratio = 100.0 * singleton_sets / total_pts_sets if total_pts_sets > 0 else 0.0
        avg_set_size = sum(all_sizes) / len(all_sizes) if all_sizes else 0.0
        max_set_size = max(all_sizes) if all_sizes else 0
        avg_vars_per_ctx = total_vars / total_contexts if total_contexts > 0 else 0.0
        
        # Get LOC for throughput
        total_loc = sum(self.count_loc(m) for m in self.modules)
        throughput = total_loc / duration if duration > 0 else 0.0
        
        return PolicyResult(
            policy=policy,
            project=self.project_name,
            duration=duration,
            throughput=throughput,
            modules_analyzed=len(analyses),
            total_variables=total_vars,
            singleton_sets=singleton_sets,
            singleton_ratio=singleton_ratio,
            avg_set_size=avg_set_size,
            max_set_size=max_set_size,
            total_contexts=total_contexts,
            avg_vars_per_context=avg_vars_per_ctx,
            total_functions=total_functions,
            total_classes=total_classes,
            success=True
        )
    
    def compare_policies(self, policies: List[str]) -> List[PolicyResult]:
        """Run comparison across all policies."""
        results = []
        
        print(f"\n{'='*70}")
        print(f"Comparing {len(policies)} policies on {self.project_name}")
        print(f"Project: {self.project_path}")
        print(f"Modules: {len(self.modules)}")
        print(f"{'='*70}\n")
        
        for i, policy in enumerate(policies, 1):
            print(f"[{i}/{len(policies)}]", end=' ')
            result = self.run_policy(policy)
            results.append(result)
        
        return results
    
    def generate_comparison_report(self, results: List[PolicyResult], output_path: Path):
        """Generate markdown comparison report."""
        lines = []
        
        lines.append(f"# Context Policy Comparison - {self.project_name.upper()}")
        lines.append(f"\n**Generated:** {datetime.now().isoformat()}")
        lines.append(f"**Project Path:** `{self.project_path}`")
        lines.append(f"**Modules Analyzed:** {len(self.modules)}\n")
        
        # Success rate
        successful = [r for r in results if r.success]
        failed = [r for r in results if not r.success]
        lines.append(f"**Success Rate:** {len(successful)}/{len(results)} ({100*len(successful)/len(results):.0f}%)\n")
        
        if failed:
            lines.append("## Failed Policies\n")
            for r in failed:
                lines.append(f"- **{r.policy}:** {r.error_message}")
            lines.append("")
        
        # Only analyze successful results
        if not successful:
            lines.append("\n❌ No successful analyses to compare.\n")
            output_path.write_text('\n'.join(lines))
            return
        
        results = successful
        
        # Performance comparison
        lines.append("## Performance Comparison\n")
        lines.append("| Policy | Duration (s) | Throughput (LOC/s) | Contexts | Vars/Context |")
        lines.append("|--------|--------------|-------------------|----------|--------------|")
        for r in sorted(results, key=lambda x: x.duration):
            lines.append(f"| {r.policy} | {r.duration:.2f} | {r.throughput:.0f} | {r.total_contexts} | {r.avg_vars_per_context:.1f} |")
        
        # Precision comparison
        lines.append("\n## Precision Comparison\n")
        lines.append("| Policy | Singleton % | Avg Size | Max Size | Total Vars |")
        lines.append("|--------|-------------|----------|----------|------------|")
        for r in sorted(results, key=lambda x: -x.singleton_ratio):
            lines.append(f"| {r.policy} | {r.singleton_ratio:.1f}% | {r.avg_set_size:.2f} | {r.max_set_size} | {r.total_variables} |")
        
        # Trade-off analysis
        lines.append("\n## Precision vs Performance Trade-off\n")
        lines.append("| Policy | Precision Rank | Performance Rank | Combined Score |")
        lines.append("|--------|---------------|------------------|----------------|")
        
        # Rank by precision (higher is better)
        precision_ranks = {r.policy: i+1 for i, r in enumerate(sorted(results, key=lambda x: -x.singleton_ratio))}
        # Rank by performance (lower duration is better)
        perf_ranks = {r.policy: i+1 for i, r in enumerate(sorted(results, key=lambda x: x.duration))}
        
        combined_scores = []
        for r in results:
            p_rank = precision_ranks[r.policy]
            perf_rank = perf_ranks[r.policy]
            combined = (p_rank + perf_rank) / 2  # Lower is better
            combined_scores.append((r.policy, p_rank, perf_rank, combined))
        
        for policy, p_rank, perf_rank, combined in sorted(combined_scores, key=lambda x: x[3]):
            lines.append(f"| {policy} | {p_rank} | {perf_rank} | {combined:.1f} |")
        
        # Best performers
        lines.append("\n## Recommendations\n")
        
        best_precision = max(results, key=lambda x: x.singleton_ratio)
        fastest = min(results, key=lambda x: x.duration)
        best_combined = min(combined_scores, key=lambda x: x[3])
        
        lines.append(f"- **Best Precision:** {best_precision.policy} ({best_precision.singleton_ratio:.1f}% singleton)")
        lines.append(f"- **Fastest:** {fastest.policy} ({fastest.duration:.2f}s)")
        lines.append(f"- **Best Balance:** {best_combined[0]} (combined score: {best_combined[3]:.1f})")
        
        # Speedup vs baseline (0-cfa or 2-cfa)
        baseline = next((r for r in results if r.policy == "0-cfa"), None)
        if not baseline:
            baseline = next((r for r in results if r.policy == "2-cfa"), None)
        
        if baseline:
            lines.append(f"\n## Speedup vs {baseline.policy.upper()} (baseline)\n")
            lines.append("| Policy | Speedup | Precision Gain |")
            lines.append("|--------|---------|----------------|")
            for r in results:
                if r.policy != baseline.policy:
                    speedup = baseline.duration / r.duration if r.duration > 0 else 0
                    precision_gain = r.singleton_ratio - baseline.singleton_ratio
                    lines.append(f"| {r.policy} | {speedup:.2f}× | {precision_gain:+.1f}pp |")
        
        # Detailed stats
        lines.append("\n## Detailed Statistics\n")
        lines.append("| Policy | Functions | Classes | Modules | Status |")
        lines.append("|--------|-----------|---------|---------|--------|")
        for r in results:
            status = "✓" if r.success else "✗"
            lines.append(f"| {r.policy} | {r.total_functions} | {r.total_classes} | {r.modules_analyzed} | {status} |")
        
        output_path.write_text('\n'.join(lines))
        print(f"\n✓ Report saved to: {output_path}")


def main():
    parser = argparse.ArgumentParser(description="Compare context-sensitive policies")
    parser.add_argument("project", choices=["flask", "werkzeug", "both"])
    parser.add_argument("--policies", default="core", 
                       help="Policy set: 'core', 'all', or comma-separated list")
    parser.add_argument("--max-modules", type=int, default=None,
                       help="Maximum number of modules to analyze (for testing)")
    parser.add_argument("--output-dir", type=Path, 
                       default=Path(__file__).parent / "reports" / "context_comparison")
    parser.add_argument("--timeout", type=int, default=300,
                       help="Timeout per policy in seconds")
    
    args = parser.parse_args()
    
    # Parse policies
    if args.policies == "core":
        policies = CORE_POLICIES
    elif args.policies == "all":
        policies = EXTENDED_POLICIES
    else:
        policies = args.policies.split(',')
    
    args.output_dir.mkdir(parents=True, exist_ok=True)
    
    # Run comparison
    projects = []
    if args.project in ["flask", "both"]:
        flask_path = Path(__file__).parent / "projects" / "flask" / "src" / "flask"
        if flask_path.exists():
            projects.append(("flask", flask_path))
        else:
            print(f"Warning: Flask path not found: {flask_path}")
    
    if args.project in ["werkzeug", "both"]:
        werkzeug_path = Path(__file__).parent / "projects" / "werkzeug" / "src" / "werkzeug"
        if werkzeug_path.exists():
            projects.append(("werkzeug", werkzeug_path))
        else:
            print(f"Warning: Werkzeug path not found: {werkzeug_path}")
    
    if not projects:
        print("Error: No valid project paths found!")
        sys.exit(1)
    
    for project_name, project_path in projects:
        comparator = PolicyComparator(project_path, project_name)
        comparator.find_python_modules(max_modules=args.max_modules)
        
        results = comparator.compare_policies(policies)
        
        # Save results
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        md_path = args.output_dir / f"{project_name}_policy_comparison_{timestamp}.md"
        json_path = args.output_dir / f"{project_name}_policy_comparison_{timestamp}.json"
        
        comparator.generate_comparison_report(results, md_path)
        
        # Save JSON
        json_data = [asdict(r) for r in results]
        with open(json_path, 'w') as f:
            json.dump(json_data, f, indent=2)
        print(f"✓ JSON data saved to: {json_path}")
    
    print(f"\n{'='*70}")
    print("Context policy comparison complete!")
    print(f"{'='*70}")


if __name__ == "__main__":
    main()

