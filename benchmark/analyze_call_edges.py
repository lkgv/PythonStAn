#!/usr/bin/env python3
"""Call Edge Quality Analysis for k-CFA Pointer Analysis.

This script provides detailed analysis of call graph construction quality
across different context policies, focusing on:
- Call edge discovery rates
- Polymorphic call site resolution
- Function out-degree distributions
- Cross-module call tracking
- Comparison across different k values

Usage:
    python benchmark/analyze_call_edges.py test_with_calls.py --policies 0-cfa,1-cfa,2-cfa
    python benchmark/analyze_call_edges.py flask --max-modules 5 --policies 0-cfa,2-cfa
"""

import sys
import json
import time
import argparse
from pathlib import Path
from typing import Dict, List, Set, Tuple, Optional, Any
from dataclasses import dataclass, asdict, field
from datetime import datetime
from collections import defaultdict, Counter

# Add project root to path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from pythonstan.world.pipeline import Pipeline
from pythonstan.analysis.pointer.kcfa2 import KCFA2PointerAnalysis, KCFAConfig


@dataclass
class CallSiteInfo:
    """Information about a single call site."""
    call_id: str
    caller_function: str
    caller_context: str
    line_number: Optional[int] = None
    call_type: str = "direct"  # direct, indirect, method
    num_targets: int = 0
    target_functions: List[str] = field(default_factory=list)
    resolved: bool = False


@dataclass
class FunctionCallMetrics:
    """Call metrics for a single function."""
    function_name: str
    
    # Outgoing calls
    total_call_sites: int = 0
    resolved_call_sites: int = 0
    unresolved_call_sites: int = 0
    polymorphic_call_sites: int = 0  # Sites with multiple targets
    
    # Call targets
    total_callees: int = 0
    unique_callees: Set[str] = field(default_factory=set)
    out_degree: int = 0  # Number of unique callees
    
    # Incoming calls
    num_callers: int = 0
    caller_functions: Set[str] = field(default_factory=set)
    in_degree: int = 0  # Number of unique callers
    
    # Context information
    contexts_seen: Set[str] = field(default_factory=set)
    
    def finalize(self):
        """Compute derived metrics."""
        self.out_degree = len(self.unique_callees)
        self.in_degree = len(self.caller_functions)


@dataclass
class CallEdgeQualityReport:
    """Comprehensive call edge quality report for a single policy."""
    policy: str
    timestamp: str
    
    # Overall statistics
    total_functions: int = 0
    total_call_sites: int = 0
    total_call_edges: int = 0
    total_contexts: int = 0
    
    # Call site resolution
    resolved_call_sites: int = 0
    unresolved_call_sites: int = 0
    polymorphic_call_sites: int = 0
    resolution_rate: float = 0.0
    
    # Function coverage
    functions_with_outgoing_calls: int = 0
    functions_with_incoming_calls: int = 0
    unreachable_functions: int = 0
    
    # Degree statistics
    avg_out_degree: float = 0.0
    max_out_degree: int = 0
    avg_in_degree: float = 0.0
    max_in_degree: int = 0
    out_degree_distribution: Dict[str, int] = field(default_factory=dict)
    
    # Polymorphism
    polymorphic_rate: float = 0.0
    avg_targets_per_polymorphic_site: float = 0.0
    max_targets_at_site: int = 0
    
    # Per-function metrics
    function_metrics: Dict[str, FunctionCallMetrics] = field(default_factory=dict)
    
    # Call site details
    call_sites: List[CallSiteInfo] = field(default_factory=list)
    
    # Performance
    analysis_duration: float = 0.0


class CallEdgeAnalyzer:
    """Analyzer for call edge quality across different policies."""
    
    def __init__(self, source_path: Path, project_path: Optional[Path] = None):
        self.source_path = source_path
        self.project_path = project_path or source_path.parent
        
    def analyze_policy(self, policy: str, max_modules: Optional[int] = None, 
                      verbose: bool = False) -> CallEdgeQualityReport:
        """Analyze call edges for a specific policy."""
        print(f"\nAnalyzing policy: {policy}")
        print("=" * 70)
        
        report = CallEdgeQualityReport(
            policy=policy,
            timestamp=datetime.now().isoformat()
        )
        
        start_time = time.time()
        
        # Configure analysis
        config = KCFAConfig(
            context_policy=policy,
            field_sensitivity_mode="attr-name",
            build_class_hierarchy=True,
            use_mro=True,
            verbose=verbose
        )
        
        # Find modules to analyze
        if self.source_path.is_file():
            modules = [self.source_path]
        else:
            modules = self._find_python_modules(self.source_path)
            if max_modules:
                modules = modules[:max_modules]
        
        print(f"Analyzing {len(modules)} module(s)...")
        
        # Analyze all modules
        all_analyses = []
        for i, module_path in enumerate(modules, 1):
            print(f"  [{i}/{len(modules)}] {module_path.name}...", end=' ', flush=True)
            
            try:
                analysis = self._analyze_module(module_path, config)
                if analysis:
                    all_analyses.append(analysis)
                    print("✓")
                else:
                    print("⊘ (no functions)")
            except Exception as e:
                print(f"✗ ({type(e).__name__})")
                if verbose:
                    print(f"    Error: {e}")
        
        report.analysis_duration = time.time() - start_time
        
        # Collect metrics from all analyses
        self._collect_call_edge_metrics(all_analyses, report)
        
        print(f"\nAnalysis complete in {report.analysis_duration:.2f}s")
        return report
    
    def _find_python_modules(self, directory: Path) -> List[Path]:
        """Find all Python modules in a directory."""
        modules = []
        for path in directory.rglob("*.py"):
            if not any(part.startswith('.') or part == '__pycache__' for part in path.parts):
                modules.append(path)
        return sorted(modules)
    
    def _analyze_module(self, module_path: Path, config: KCFAConfig) -> Optional[KCFA2PointerAnalysis]:
        """Analyze a single module."""
        # Create pipeline
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
        
        # Extract functions and methods
        from pythonstan.ir import IRFunc, IRClass
        functions = []
        if hasattr(scope_manager, 'get_subscopes'):
            subscopes = scope_manager.get_subscopes(ir_module)
            functions = [scope for scope in subscopes if isinstance(scope, IRFunc)]
            classes = [scope for scope in subscopes if isinstance(scope, IRClass)]
            
            # Extract methods from classes
            for cls in classes:
                cls_subscopes = scope_manager.get_subscopes(cls)
                methods = [scope for scope in cls_subscopes if isinstance(scope, IRFunc)]
                functions.extend(methods)
        
        if not functions:
            return None
        
        # Attach IR to functions
        for func in functions:
            for fmt in ['cfg', 'block cfg', 'three address', 'ir']:
                func_ir = scope_manager.get_ir(func, fmt)
                if func_ir is not None:
                    func._cfg = func_ir
                    func._ir_format = fmt
                    break
        
        # Run pointer analysis
        analysis = KCFA2PointerAnalysis(config)
        if functions:
            analysis.plan(functions)
        
        analysis.initialize()
        analysis.run()
        
        return analysis
    
    def _collect_call_edge_metrics(self, analyses: List[KCFA2PointerAnalysis], 
                                   report: CallEdgeQualityReport):
        """Collect comprehensive call edge metrics."""
        
        # Aggregate function metrics
        func_metrics: Dict[str, FunctionCallMetrics] = {}
        
        # Track all call sites
        all_call_sites: List[CallSiteInfo] = []
        
        for analysis in analyses:
            # Collect contexts
            if hasattr(analysis, '_contexts'):
                report.total_contexts += len(analysis._contexts)
            
            # Collect functions
            if hasattr(analysis, '_functions'):
                report.total_functions += len(analysis._functions)
                
                # Initialize metrics for each function
                for func_name in analysis._functions.keys():
                    if func_name not in func_metrics:
                        func_metrics[func_name] = FunctionCallMetrics(function_name=func_name)
            
            # Collect call graph edges
            if hasattr(analysis, '_call_graph'):
                cg = analysis._call_graph
                
                # Get statistics
                if hasattr(cg, 'get_statistics'):
                    stats = cg.get_statistics()
                    report.total_call_edges += stats.get('total_cs_edges', 0)
                
                # Analyze context-sensitive call graph
                if hasattr(cg, '_cs_call_graph'):
                    for (caller_ctx, call_site), targets in cg._cs_call_graph.items():
                        report.total_call_sites += 1
                        
                        # Extract caller function name
                        caller_fn = call_site.fn
                        if caller_fn not in func_metrics:
                            func_metrics[caller_fn] = FunctionCallMetrics(function_name=caller_fn)
                        
                        # Update caller metrics
                        caller_metrics = func_metrics[caller_fn]
                        caller_metrics.total_call_sites += 1
                        caller_metrics.contexts_seen.add(str(caller_ctx))
                        
                        # Process targets
                        num_targets = len(targets)
                        resolved = num_targets > 0
                        
                        if resolved:
                            report.resolved_call_sites += 1
                            caller_metrics.resolved_call_sites += 1
                        else:
                            report.unresolved_call_sites += 1
                            caller_metrics.unresolved_call_sites += 1
                        
                        # Check if polymorphic (multiple targets)
                        is_polymorphic = num_targets > 1
                        if is_polymorphic:
                            report.polymorphic_call_sites += 1
                            caller_metrics.polymorphic_call_sites += 1
                            report.max_targets_at_site = max(report.max_targets_at_site, num_targets)
                        
                        # Collect target functions
                        target_fns = []
                        for callee_ctx, callee_fn in targets:
                            target_fns.append(callee_fn)
                            caller_metrics.unique_callees.add(callee_fn)
                            caller_metrics.total_callees += 1
                            
                            # Update callee metrics (incoming calls)
                            if callee_fn not in func_metrics:
                                func_metrics[callee_fn] = FunctionCallMetrics(function_name=callee_fn)
                            
                            callee_metrics = func_metrics[callee_fn]
                            callee_metrics.num_callers += 1
                            callee_metrics.caller_functions.add(caller_fn)
                            callee_metrics.contexts_seen.add(str(callee_ctx))
                        
                        # Create call site info
                        call_site_info = CallSiteInfo(
                            call_id=call_site.site_id,
                            caller_function=caller_fn,
                            caller_context=str(caller_ctx),
                            num_targets=num_targets,
                            target_functions=target_fns,
                            resolved=resolved
                        )
                        all_call_sites.append(call_site_info)
        
        # Finalize function metrics
        for metrics in func_metrics.values():
            metrics.finalize()
        
        report.function_metrics = func_metrics
        report.call_sites = all_call_sites
        
        # Compute aggregate statistics
        self._compute_aggregate_stats(report)
    
    def _compute_aggregate_stats(self, report: CallEdgeQualityReport):
        """Compute aggregate statistics from collected metrics."""
        
        # Resolution rate
        if report.total_call_sites > 0:
            report.resolution_rate = 100.0 * report.resolved_call_sites / report.total_call_sites
        
        # Polymorphic rate
        if report.resolved_call_sites > 0:
            report.polymorphic_rate = 100.0 * report.polymorphic_call_sites / report.resolved_call_sites
        
        # Average targets per polymorphic site
        if report.polymorphic_call_sites > 0:
            poly_sites = [cs for cs in report.call_sites if cs.num_targets > 1]
            total_targets = sum(cs.num_targets for cs in poly_sites)
            report.avg_targets_per_polymorphic_site = total_targets / len(poly_sites)
        
        # Function coverage
        functions_with_out = sum(1 for m in report.function_metrics.values() if m.out_degree > 0)
        functions_with_in = sum(1 for m in report.function_metrics.values() if m.in_degree > 0)
        
        report.functions_with_outgoing_calls = functions_with_out
        report.functions_with_incoming_calls = functions_with_in
        report.unreachable_functions = report.total_functions - functions_with_in
        
        # Degree statistics
        out_degrees = [m.out_degree for m in report.function_metrics.values()]
        in_degrees = [m.in_degree for m in report.function_metrics.values()]
        
        if out_degrees:
            report.avg_out_degree = sum(out_degrees) / len(out_degrees)
            report.max_out_degree = max(out_degrees)
        
        if in_degrees:
            report.avg_in_degree = sum(in_degrees) / len(in_degrees)
            report.max_in_degree = max(in_degrees)
        
        # Out-degree distribution
        out_degree_counts = Counter(out_degrees)
        bins = [(0, 0), (1, 1), (2, 5), (6, 10), (11, 20), (21, 50), (51, float('inf'))]
        
        for low, high in bins:
            count = sum(c for deg, c in out_degree_counts.items() if low <= deg <= high)
            if count > 0:
                label = f"{low}" if low == high else (f"{low}-{high}" if high != float('inf') else f"{low}+")
                report.out_degree_distribution[label] = count


def compare_policies(source_path: Path, policies: List[str], 
                     max_modules: Optional[int] = None, 
                     output_dir: Optional[Path] = None,
                     verbose: bool = False) -> Dict[str, CallEdgeQualityReport]:
    """Compare call edge quality across multiple policies."""
    
    print(f"\n{'='*70}")
    print(f"Call Edge Quality Analysis")
    print(f"{'='*70}")
    print(f"Source: {source_path}")
    print(f"Policies: {', '.join(policies)}")
    if max_modules:
        print(f"Max modules: {max_modules}")
    print(f"{'='*70}")
    
    analyzer = CallEdgeAnalyzer(source_path)
    reports = {}
    
    for policy in policies:
        report = analyzer.analyze_policy(policy, max_modules=max_modules, verbose=verbose)
        reports[policy] = report
    
    # Generate comparison report
    if output_dir:
        output_dir.mkdir(parents=True, exist_ok=True)
        
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        md_path = output_dir / f"call_edge_comparison_{timestamp}.md"
        json_path = output_dir / f"call_edge_comparison_{timestamp}.json"
        
        generate_markdown_report(reports, md_path, source_path)
        generate_json_report(reports, json_path)
        
        print(f"\n{'='*70}")
        print(f"Reports saved:")
        print(f"  Markdown: {md_path}")
        print(f"  JSON: {json_path}")
        print(f"{'='*70}")
    
    return reports


def generate_markdown_report(reports: Dict[str, CallEdgeQualityReport], 
                             output_path: Path, source_path: Path):
    """Generate markdown comparison report."""
    lines = []
    
    lines.append("# Call Edge Quality Analysis Report")
    lines.append(f"\n**Generated:** {datetime.now().isoformat()}")
    lines.append(f"**Source:** `{source_path}`")
    lines.append(f"**Policies Compared:** {', '.join(reports.keys())}\n")
    
    # Summary comparison
    lines.append("## Summary Comparison\n")
    lines.append("| Policy | Call Sites | Call Edges | Resolution | Polymorphic | Contexts | Functions |")
    lines.append("|--------|------------|------------|------------|-------------|----------|-----------|")
    
    for policy, report in sorted(reports.items()):
        lines.append(
            f"| {policy} | {report.total_call_sites} | {report.total_call_edges} | "
            f"{report.resolution_rate:.1f}% | {report.polymorphic_rate:.1f}% | "
            f"{report.total_contexts} | {report.total_functions} |"
        )
    
    # Call site resolution comparison
    lines.append("\n## Call Site Resolution\n")
    lines.append("| Policy | Total Sites | Resolved | Unresolved | Resolution Rate |")
    lines.append("|--------|-------------|----------|------------|-----------------|")
    
    for policy, report in sorted(reports.items()):
        lines.append(
            f"| {policy} | {report.total_call_sites} | {report.resolved_call_sites} | "
            f"{report.unresolved_call_sites} | {report.resolution_rate:.1f}% |"
        )
    
    # Polymorphism analysis
    lines.append("\n## Polymorphism Analysis\n")
    lines.append("| Policy | Polymorphic Sites | Rate | Avg Targets | Max Targets |")
    lines.append("|--------|------------------|------|-------------|-------------|")
    
    for policy, report in sorted(reports.items()):
        lines.append(
            f"| {policy} | {report.polymorphic_call_sites} | {report.polymorphic_rate:.1f}% | "
            f"{report.avg_targets_per_polymorphic_site:.2f} | {report.max_targets_at_site} |"
        )
    
    # Function coverage
    lines.append("\n## Function Coverage\n")
    lines.append("| Policy | Total Funcs | With Outgoing | With Incoming | Unreachable |")
    lines.append("|--------|-------------|---------------|---------------|-------------|")
    
    for policy, report in sorted(reports.items()):
        out_pct = 100.0 * report.functions_with_outgoing_calls / report.total_functions if report.total_functions > 0 else 0
        in_pct = 100.0 * report.functions_with_incoming_calls / report.total_functions if report.total_functions > 0 else 0
        lines.append(
            f"| {policy} | {report.total_functions} | {report.functions_with_outgoing_calls} ({out_pct:.0f}%) | "
            f"{report.functions_with_incoming_calls} ({in_pct:.0f}%) | {report.unreachable_functions} |"
        )
    
    # Degree statistics
    lines.append("\n## Degree Statistics\n")
    lines.append("| Policy | Avg Out-Degree | Max Out-Degree | Avg In-Degree | Max In-Degree |")
    lines.append("|--------|----------------|----------------|---------------|---------------|")
    
    for policy, report in sorted(reports.items()):
        lines.append(
            f"| {policy} | {report.avg_out_degree:.2f} | {report.max_out_degree} | "
            f"{report.avg_in_degree:.2f} | {report.max_in_degree} |"
        )
    
    # Out-degree distribution
    lines.append("\n## Out-Degree Distribution\n")
    
    # Get all bin labels
    all_bins = set()
    for report in reports.values():
        all_bins.update(report.out_degree_distribution.keys())
    
    if all_bins:
        sorted_bins = sorted(all_bins, key=lambda x: int(x.split('-')[0].replace('+', '')))
        
        header = "| Policy | " + " | ".join(sorted_bins) + " |"
        lines.append(header)
        lines.append("|--------|" + "|".join("---" for _ in sorted_bins) + "|")
        
        for policy, report in sorted(reports.items()):
            row = f"| {policy} | "
            row += " | ".join(str(report.out_degree_distribution.get(bin_label, 0)) for bin_label in sorted_bins)
            row += " |"
            lines.append(row)
    
    # Performance
    lines.append("\n## Performance\n")
    lines.append("| Policy | Analysis Time | Call Sites/sec |")
    lines.append("|--------|---------------|----------------|")
    
    for policy, report in sorted(reports.items()):
        throughput = report.total_call_sites / report.analysis_duration if report.analysis_duration > 0 else 0
        lines.append(f"| {policy} | {report.analysis_duration:.2f}s | {throughput:.1f} |")
    
    # Key findings
    lines.append("\n## Key Findings\n")
    
    # Compare 0-cfa vs highest k
    if '0-cfa' in reports:
        baseline = reports['0-cfa']
        
        # Find highest k policy
        k_policies = [p for p in reports.keys() if 'cfa' in p and p != '0-cfa']
        if k_policies:
            # Sort by k value
            k_policies.sort(key=lambda p: int(p.split('-')[0]))
            highest = reports[k_policies[-1]]
            
            lines.append(f"### Context Sensitivity Impact (0-cfa vs {k_policies[-1]})\n")
            
            # Contexts
            ctx_increase = highest.total_contexts - baseline.total_contexts
            lines.append(f"- **Contexts:** {baseline.total_contexts} → {highest.total_contexts} "
                        f"(**{ctx_increase}** more contexts)")
            
            # Call edges
            edge_increase = highest.total_call_edges - baseline.total_call_edges
            edge_pct = 100.0 * edge_increase / baseline.total_call_edges if baseline.total_call_edges > 0 else 0
            lines.append(f"- **Call Edges:** {baseline.total_call_edges} → {highest.total_call_edges} "
                        f"(**+{edge_pct:.1f}%**)")
            
            # Resolution rate
            res_diff = highest.resolution_rate - baseline.resolution_rate
            lines.append(f"- **Resolution Rate:** {baseline.resolution_rate:.1f}% → {highest.resolution_rate:.1f}% "
                        f"(**{res_diff:+.1f}pp**)")
            
            # Polymorphism
            poly_diff = highest.polymorphic_rate - baseline.polymorphic_rate
            lines.append(f"- **Polymorphic Rate:** {baseline.polymorphic_rate:.1f}% → {highest.polymorphic_rate:.1f}% "
                        f"(**{poly_diff:+.1f}pp**)")
    
    output_path.write_text('\n'.join(lines))
    print(f"\n✓ Markdown report saved to: {output_path}")


def generate_json_report(reports: Dict[str, CallEdgeQualityReport], output_path: Path):
    """Generate JSON report for programmatic access."""
    
    def convert_report(report: CallEdgeQualityReport) -> Dict:
        """Convert report to JSON-serializable format."""
        data = asdict(report)
        
        # Convert sets to lists
        for func_name, metrics in data['function_metrics'].items():
            metrics['unique_callees'] = list(metrics['unique_callees'])
            metrics['caller_functions'] = list(metrics['caller_functions'])
            metrics['contexts_seen'] = list(metrics['contexts_seen'])
        
        return data
    
    json_data = {
        policy: convert_report(report)
        for policy, report in reports.items()
    }
    
    output_path.write_text(json.dumps(json_data, indent=2))
    print(f"✓ JSON report saved to: {output_path}")


def main():
    parser = argparse.ArgumentParser(
        description="Analyze call edge quality across k-CFA policies"
    )
    parser.add_argument(
        "source",
        type=Path,
        help="Source file or directory to analyze"
    )
    parser.add_argument(
        "--policies",
        default="0-cfa,1-cfa,2-cfa",
        help="Comma-separated list of policies to compare"
    )
    parser.add_argument(
        "--max-modules",
        type=int,
        default=None,
        help="Maximum number of modules to analyze (default: analyze all)"
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(__file__).parent / "reports" / "call_edge_analysis",
        help="Output directory for reports"
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable verbose output"
    )
    
    args = parser.parse_args()
    
    # Parse policies
    policies = args.policies.split(',')
    
    # Check source exists
    if not args.source.exists():
        print(f"Error: Source not found: {args.source}")
        sys.exit(1)
    
    # Run comparison
    reports = compare_policies(
        source_path=args.source,
        policies=policies,
        max_modules=args.max_modules,
        output_dir=args.output_dir,
        verbose=args.verbose
    )
    
    # Print summary
    print(f"\n{'='*70}")
    print("Analysis Summary:")
    print(f"{'='*70}")
    
    for policy in policies:
        report = reports[policy]
        print(f"\n{policy}:")
        print(f"  Call Sites: {report.total_call_sites}")
        print(f"  Call Edges: {report.total_call_edges}")
        print(f"  Resolution: {report.resolution_rate:.1f}%")
        print(f"  Contexts: {report.total_contexts}")
        print(f"  Functions: {report.total_functions}")
    
    print(f"\n{'='*70}")


if __name__ == "__main__":
    main()

