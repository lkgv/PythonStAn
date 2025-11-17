#!/usr/bin/env python3
"""Generate comparison tables from k-CFA policy benchmark results.

This script loads JSON results from Flask and Werkzeug benchmarks and generates
comprehensive comparison tables in Markdown format.

Usage:
    python benchmark/generate_comparison_tables.py --flask results/flask_validation.json --werkzeug results/werkzeug_validation.json
"""

import argparse
import sys
from pathlib import Path
from typing import List, Dict, Any, Optional
from datetime import datetime

# Add pythonstan to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from benchmark.metrics_collector import load_results, AnalysisMetrics


def format_time(seconds: float) -> str:
    """Format time in seconds."""
    if seconds < 1:
        return f"{seconds*1000:.1f}ms"
    elif seconds < 60:
        return f"{seconds:.2f}s"
    else:
        mins = int(seconds // 60)
        secs = seconds % 60
        return f"{mins}m {secs:.1f}s"


def format_memory(mb: float) -> str:
    """Format memory in MB."""
    if mb < 1024:
        return f"{mb:.1f} MB"
    else:
        return f"{mb/1024:.2f} GB"


def format_number(num: int) -> str:
    """Format number with thousands separator."""
    return f"{num:,}"


def calculate_precision_score(metrics: AnalysisMetrics) -> float:
    """Calculate precision score (0-100, higher is better).
    
    Combines multiple precision indicators:
    - Singleton ratio (40%)
    - Inverse avg points-to size (30%)
    - Low unknowns (30%)
    """
    # Singleton ratio contribution (0-40)
    total_vars = metrics.num_variables
    singleton_score = 0
    if total_vars > 0:
        singleton_ratio = metrics.singleton_points_to / total_vars
        singleton_score = singleton_ratio * 40
    
    # Points-to size contribution (0-30, smaller is better)
    pts_score = 0
    if metrics.avg_points_to_size > 0:
        # Normalize: 1.0 = perfect (30 pts), 10.0 = poor (3 pts)
        pts_score = max(0, 30 - (metrics.avg_points_to_size - 1) * 3)
    
    # Unknown contribution (0-30, fewer is better)
    unknown_score = 30
    if metrics.num_variables > 0:
        unknown_ratio = metrics.total_unknowns / max(metrics.num_variables, 1)
        unknown_score = max(0, 30 - unknown_ratio * 100)
    
    return singleton_score + pts_score + unknown_score


def calculate_cost_score(metrics: AnalysisMetrics) -> float:
    """Calculate cost score (0-100, lower is better).
    
    Combines time and memory costs.
    """
    # Normalize time: 10s = 0 pts, 100s = 50 pts
    time_score = min(50, (metrics.total_time / 10) * 5)
    
    # Normalize memory: 100MB = 0 pts, 1000MB = 50 pts
    memory_score = min(50, (metrics.peak_memory / 100) * 5)
    
    return time_score + memory_score


def generate_performance_table(results: List[AnalysisMetrics], project: str) -> str:
    """Generate performance comparison table.
    
    Args:
        results: List of AnalysisMetrics
        project: Project name
    
    Returns:
        Markdown table string
    """
    lines = [
        f"### Performance Comparison - {project.title()}",
        "",
        "| Policy | Status | Time | Memory (Peak) | Iterations | Vars | Objects | Contexts |",
        "|--------|--------|------|---------------|------------|------|---------|----------|"
    ]
    
    for r in results:
        status = "✓" if r.success else "✗"
        time_str = format_time(r.total_time) if r.success else "N/A"
        memory_str = format_memory(r.peak_memory) if r.success else "N/A"
        iters = format_number(r.solver_iterations) if r.success else "N/A"
        vars_str = format_number(r.num_variables) if r.success else "N/A"
        objs = format_number(r.num_objects) if r.success else "N/A"
        ctxs = format_number(r.num_contexts) if r.success else "N/A"
        
        lines.append(
            f"| {r.policy} | {status} | {time_str} | {memory_str} | {iters} | {vars_str} | {objs} | {ctxs} |"
        )
    
    return "\n".join(lines)


def generate_precision_table(results: List[AnalysisMetrics], project: str) -> str:
    """Generate precision comparison table.
    
    Args:
        results: List of AnalysisMetrics
        project: Project name
    
    Returns:
        Markdown table string
    """
    lines = [
        f"### Precision Comparison - {project.title()}",
        "",
        "| Policy | Avg PTS | Singletons | Empty | Large (>10) | Call Edges | Unknowns |",
        "|--------|---------|------------|-------|-------------|------------|----------|"
    ]
    
    for r in results:
        if not r.success:
            lines.append(f"| {r.policy} | N/A | N/A | N/A | N/A | N/A | N/A |")
            continue
        
        avg_pts = f"{r.avg_points_to_size:.2f}" if r.avg_points_to_size > 0 else "0.00"
        singletons = format_number(r.singleton_points_to)
        empty = format_number(r.empty_variables)
        large = format_number(r.large_points_to_sets)
        edges = format_number(r.call_edges)
        unknowns = format_number(r.total_unknowns)
        
        lines.append(
            f"| {r.policy} | {avg_pts} | {singletons} | {empty} | {large} | {edges} | {unknowns} |"
        )
    
    return "\n".join(lines)


def generate_tradeoff_table(results: List[AnalysisMetrics], project: str) -> str:
    """Generate precision-cost tradeoff table.
    
    Args:
        results: List of AnalysisMetrics
        project: Project name
    
    Returns:
        Markdown table string
    """
    lines = [
        f"### Precision-Cost Tradeoff - {project.title()}",
        "",
        "| Rank | Policy | Precision | Cost | Tradeoff | Time | Memory |",
        "|------|--------|-----------|------|----------|------|--------|"
    ]
    
    # Calculate scores for successful runs
    scored_results = []
    for r in results:
        if r.success:
            precision = calculate_precision_score(r)
            cost = calculate_cost_score(r)
            # Tradeoff: precision - cost (higher is better)
            tradeoff = precision - cost
            scored_results.append((r, precision, cost, tradeoff))
    
    # Sort by tradeoff (best first)
    scored_results.sort(key=lambda x: x[3], reverse=True)
    
    # Generate table
    for rank, (r, precision, cost, tradeoff) in enumerate(scored_results, 1):
        time_str = format_time(r.total_time)
        memory_str = format_memory(r.peak_memory)
        
        lines.append(
            f"| {rank} | {r.policy} | {precision:.1f} | {cost:.1f} | {tradeoff:.1f} | {time_str} | {memory_str} |"
        )
    
    # Add failed policies at the end
    for r in results:
        if not r.success:
            lines.append(f"| - | {r.policy} | N/A | N/A | N/A | N/A | N/A |")
    
    return "\n".join(lines)


def generate_unknown_breakdown(results: List[AnalysisMetrics], project: str) -> str:
    """Generate unknown tracking breakdown.
    
    Args:
        results: List of AnalysisMetrics
        project: Project name
    
    Returns:
        Markdown section string
    """
    lines = [
        f"### Unknown Tracking Breakdown - {project.title()}",
        "",
        "| Policy | Total | Empty Callee | Non-Callable | Missing Func | Dynamic Attr | Import Failed |",
        "|--------|-------|--------------|--------------|--------------|--------------|---------------|"
    ]
    
    for r in results:
        if not r.success:
            lines.append(f"| {r.policy} | N/A | N/A | N/A | N/A | N/A | N/A |")
            continue
        
        lines.append(
            f"| {r.policy} | {r.total_unknowns} | {r.unknown_callee_empty} | "
            f"{r.unknown_callee_non_callable} | {r.unknown_function_not_in_registry} | "
            f"{r.unknown_dynamic_attribute} | {r.unknown_import_not_found} |"
        )
    
    return "\n".join(lines)


def generate_summary(flask_results: List[AnalysisMetrics], 
                     werkzeug_results: Optional[List[AnalysisMetrics]] = None) -> str:
    """Generate executive summary.
    
    Args:
        flask_results: Flask benchmark results
        werkzeug_results: Werkzeug benchmark results (optional)
    
    Returns:
        Markdown summary string
    """
    lines = [
        "# Phase 6: k-CFA Validation Results",
        "",
        f"**Generated:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        "## Executive Summary",
        ""
    ]
    
    # Overall statistics
    total_runs = len(flask_results)
    if werkzeug_results:
        total_runs += len(werkzeug_results)
    
    flask_success = sum(1 for r in flask_results if r.success)
    werkzeug_success = sum(1 for r in werkzeug_results if r.success) if werkzeug_results else 0
    total_success = flask_success + werkzeug_success
    
    lines.extend([
        f"- **Total Policy Tests:** {total_runs}",
        f"- **Successful Runs:** {total_success}/{total_runs} ({100*total_success/total_runs:.1f}%)",
        f"- **Flask Success Rate:** {flask_success}/{len(flask_results)} ({100*flask_success/len(flask_results):.1f}%)",
    ])
    
    if werkzeug_results:
        lines.append(
            f"- **Werkzeug Success Rate:** {werkzeug_success}/{len(werkzeug_results)} "
            f"({100*werkzeug_success/len(werkzeug_results):.1f}%)"
        )
    
    # Best policies
    lines.extend([
        "",
        "### Recommended Policies",
        ""
    ])
    
    # Find best tradeoff for Flask
    scored_flask = []
    for r in flask_results:
        if r.success:
            precision = calculate_precision_score(r)
            cost = calculate_cost_score(r)
            tradeoff = precision - cost
            scored_flask.append((r, tradeoff))
    
    scored_flask.sort(key=lambda x: x[1], reverse=True)
    
    if scored_flask:
        best_policy = scored_flask[0][0]
        lines.extend([
            f"**Best Overall (Flask):** `{best_policy.policy}`",
            f"- Time: {format_time(best_policy.total_time)}",
            f"- Memory: {format_memory(best_policy.peak_memory)}",
            f"- Avg Points-To Size: {best_policy.avg_points_to_size:.2f}",
            f"- Unknowns: {best_policy.total_unknowns}",
            ""
        ])
    
    # Fastest policy
    fastest = min((r for r in flask_results if r.success), 
                  key=lambda r: r.total_time, default=None)
    if fastest:
        lines.extend([
            f"**Fastest:** `{fastest.policy}` ({format_time(fastest.total_time)})",
            ""
        ])
    
    # Most precise policy
    most_precise = min((r for r in flask_results if r.success and r.avg_points_to_size > 0), 
                       key=lambda r: r.avg_points_to_size, default=None)
    if most_precise:
        lines.extend([
            f"**Most Precise:** `{most_precise.policy}` (Avg PTS: {most_precise.avg_points_to_size:.2f})",
            ""
        ])
    
    return "\n".join(lines)


def generate_recommendations(flask_results: List[AnalysisMetrics],
                             werkzeug_results: Optional[List[AnalysisMetrics]] = None) -> str:
    """Generate recommendations section.
    
    Args:
        flask_results: Flask benchmark results
        werkzeug_results: Werkzeug benchmark results (optional)
    
    Returns:
        Markdown recommendations string
    """
    lines = [
        "## Recommendations",
        "",
        "### Default Policy",
        ""
    ]
    
    # Analyze results to make recommendation
    scored_results = []
    for r in flask_results:
        if r.success:
            precision = calculate_precision_score(r)
            cost = calculate_cost_score(r)
            tradeoff = precision - cost
            scored_results.append((r, precision, cost, tradeoff))
    
    scored_results.sort(key=lambda x: x[3], reverse=True)
    
    if scored_results:
        best = scored_results[0][0]
        lines.extend([
            f"Based on the validation results, we recommend **`{best.policy}`** as the default policy.",
            "",
            "**Rationale:**",
            f"- Good balance between precision and performance",
            f"- Analysis time: {format_time(best.total_time)}",
            f"- Memory usage: {format_memory(best.peak_memory)}",
            f"- Average points-to set size: {best.avg_points_to_size:.2f}",
            f"- Reasonable unknown count: {best.total_unknowns}",
            "",
            "### Policy Selection Guide",
            "",
            "**For Quick Analysis (< 10s):**",
        ])
        
        # Find fast policies
        fast_policies = [(r.policy, format_time(r.total_time)) 
                        for r in flask_results if r.success and r.total_time < 10]
        fast_policies.sort(key=lambda x: x[1])
        for policy, time in fast_policies[:3]:
            lines.append(f"- `{policy}` ({time})")
        
        lines.extend([
            "",
            "**For High Precision:**",
        ])
        
        # Find precise policies
        precise_policies = [(r.policy, r.avg_points_to_size, format_time(r.total_time))
                           for r in flask_results if r.success and r.avg_points_to_size > 0]
        precise_policies.sort(key=lambda x: x[1])
        for policy, pts, time in precise_policies[:3]:
            lines.append(f"- `{policy}` (Avg PTS: {pts:.2f}, Time: {time})")
        
        lines.extend([
            "",
            "**For Memory-Constrained Environments:**",
        ])
        
        # Find memory-efficient policies
        memory_policies = [(r.policy, format_memory(r.peak_memory), format_time(r.total_time))
                          for r in flask_results if r.success]
        memory_policies.sort(key=lambda x: x[1])
        for policy, mem, time in memory_policies[:3]:
            lines.append(f"- `{policy}` ({mem}, {time})")
    
    return "\n".join(lines)


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Generate comparison tables from benchmark results"
    )
    parser.add_argument(
        "--flask",
        type=str,
        help="Path to Flask results JSON"
    )
    parser.add_argument(
        "--werkzeug",
        type=str,
        help="Path to Werkzeug results JSON"
    )
    parser.add_argument(
        "--output",
        type=str,
        default="docs/kcfa/PHASE6_VALIDATION_RESULTS.md",
        help="Output file path"
    )
    
    args = parser.parse_args()
    
    if not args.flask and not args.werkzeug:
        print("Error: Must provide at least one of --flask or --werkzeug")
        return 1
    
    # Load results
    flask_results = None
    werkzeug_results = None
    
    if args.flask:
        print(f"Loading Flask results from {args.flask}...")
        flask_results = load_results(args.flask)
        print(f"  Loaded {len(flask_results)} results")
    
    if args.werkzeug:
        print(f"Loading Werkzeug results from {args.werkzeug}...")
        werkzeug_results = load_results(args.werkzeug)
        print(f"  Loaded {len(werkzeug_results)} results")
    
    # Generate report
    print(f"\nGenerating report...")
    
    output_lines = []
    
    # Summary
    if flask_results:
        output_lines.append(generate_summary(flask_results, werkzeug_results))
        output_lines.append("")
    
    # Tables for Flask
    if flask_results:
        output_lines.append("## Flask Results")
        output_lines.append("")
        output_lines.append(generate_performance_table(flask_results, "flask"))
        output_lines.append("")
        output_lines.append(generate_precision_table(flask_results, "flask"))
        output_lines.append("")
        output_lines.append(generate_tradeoff_table(flask_results, "flask"))
        output_lines.append("")
        output_lines.append(generate_unknown_breakdown(flask_results, "flask"))
        output_lines.append("")
    
    # Tables for Werkzeug
    if werkzeug_results:
        output_lines.append("## Werkzeug Results")
        output_lines.append("")
        output_lines.append(generate_performance_table(werkzeug_results, "werkzeug"))
        output_lines.append("")
        output_lines.append(generate_precision_table(werkzeug_results, "werkzeug"))
        output_lines.append("")
        output_lines.append(generate_tradeoff_table(werkzeug_results, "werkzeug"))
        output_lines.append("")
        output_lines.append(generate_unknown_breakdown(werkzeug_results, "werkzeug"))
        output_lines.append("")
    
    # Recommendations
    if flask_results:
        output_lines.append(generate_recommendations(flask_results, werkzeug_results))
        output_lines.append("")
    
    # Write output
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    
    with open(output_path, 'w') as f:
        f.write("\n".join(output_lines))
    
    print(f"Report saved to: {output_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

