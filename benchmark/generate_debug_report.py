#!/usr/bin/env python3
"""Generate comprehensive debug report from pointer analysis debug output.

This script parses debug monitoring output and generates detailed analysis reports
including call graph coverage, points-to distribution, and bottleneck identification.

Usage:
    python benchmark/generate_debug_report.py debug_output/flask_3_param_20251117_*/
    python benchmark/generate_debug_report.py debug_output/*.json
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Dict, List, Any
from collections import defaultdict
import re


def load_debug_data(debug_file: Path) -> Dict[str, Any]:
    """Load debug data from JSON file."""
    with open(debug_file, 'r') as f:
        return json.load(f)


def analyze_call_coverage(debug_data: Dict[str, Any]) -> Dict[str, Any]:
    """Analyze call graph coverage."""
    stats = debug_data.get('statistics', {})
    call_stats = stats.get('call_constraints', {})
    
    total_processed = call_stats.get('total_processed', 0)
    successful = call_stats.get('successful', 0)
    empty_callee = call_stats.get('empty_callee', 0)
    non_callable = call_stats.get('non_callable', 0)
    
    coverage_rate = (successful / total_processed * 100) if total_processed > 0 else 0.0
    
    return {
        'total_call_constraints': total_processed,
        'successful_resolutions': successful,
        'failed_empty_callee': empty_callee,
        'failed_non_callable': non_callable,
        'coverage_rate': coverage_rate,
        'call_types': {
            'function_calls': call_stats.get('function_calls', 0),
            'method_calls': call_stats.get('method_calls', 0),
            'class_instantiation': call_stats.get('class_instantiation', 0),
            'builtin_calls': call_stats.get('builtin_calls', 0),
            'bound_method_calls': call_stats.get('bound_method_calls', 0)
        }
    }


def analyze_points_to_distribution(debug_data: Dict[str, Any]) -> Dict[str, Any]:
    """Analyze points-to set distribution."""
    stats = debug_data.get('statistics', {})
    pts_stats = stats.get('points_to', {})
    
    size_dist = pts_stats.get('size_distribution', {})
    
    return {
        'total_variables': pts_stats.get('total_variables', 0),
        'empty_count': pts_stats.get('empty_variables', 0),
        'singleton_count': pts_stats.get('singleton_variables', 0),
        'large_count': pts_stats.get('large_variables', 0),
        'avg_size': pts_stats.get('avg_size', 0.0),
        'max_size': pts_stats.get('max_size', 0),
        'distribution': size_dist
    }


def analyze_object_flow(debug_data: Dict[str, Any]) -> Dict[str, Any]:
    """Analyze object creation and flow."""
    stats = debug_data.get('statistics', {})
    obj_stats = stats.get('object_flow', {})
    
    objects_by_kind = obj_stats.get('objects_by_kind', {})
    total_objects = sum(objects_by_kind.values())
    
    return {
        'total_objects_created': obj_stats.get('objects_created', 0),
        'total_objects_propagated': obj_stats.get('objects_propagated', 0),
        'objects_by_kind': objects_by_kind,
        'kind_distribution': {
            kind: (count / total_objects * 100) if total_objects > 0 else 0.0
            for kind, count in objects_by_kind.items()
        }
    }


def analyze_pfg_effectiveness(debug_data: Dict[str, Any]) -> Dict[str, Any]:
    """Analyze PFG effectiveness."""
    stats = debug_data.get('statistics', {})
    pfg_stats = stats.get('pfg', {})
    
    total_edges = pfg_stats.get('total_edges', 0)
    activated_edges = pfg_stats.get('activated_edges', 0)
    activation_rate = (activated_edges / total_edges * 100) if total_edges > 0 else 0.0
    
    return {
        'total_edges': total_edges,
        'activated_edges': activated_edges,
        'dead_edges': pfg_stats.get('dead_edges', 0),
        'activation_rate': activation_rate,
        'total_object_flow': pfg_stats.get('total_object_flow', 0),
        'avg_activations_per_edge': (
            pfg_stats.get('total_activations', 0) / activated_edges
            if activated_edges > 0 else 0.0
        )
    }


def identify_bottlenecks(debug_data: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Identify analysis bottlenecks."""
    bottlenecks = []
    
    stats = debug_data.get('statistics', {})
    
    # Check for high empty callee rate
    call_stats = stats.get('call_constraints', {})
    empty_callee = call_stats.get('empty_callee', 0)
    total_processed = call_stats.get('total_processed', 0)
    
    if total_processed > 0 and (empty_callee / total_processed) > 0.5:
        bottlenecks.append({
            'type': 'Empty Callee Points-to Sets',
            'severity': 'HIGH',
            'description': f'{empty_callee}/{total_processed} call constraints have empty callee ({empty_callee/total_processed*100:.1f}%)',
            'recommendation': 'Check function object allocation and propagation'
        })
    
    # Check for low PFG activation rate
    pfg_stats = stats.get('pfg', {})
    total_edges = pfg_stats.get('total_edges', 0)
    activated = pfg_stats.get('activated_edges', 0)
    
    if total_edges > 0 and (activated / total_edges) < 0.5:
        bottlenecks.append({
            'type': 'Low PFG Activation Rate',
            'severity': 'MEDIUM',
            'description': f'Only {activated}/{total_edges} PFG edges activated ({activated/total_edges*100:.1f}%)',
            'recommendation': 'Review constraint generation and object flow'
        })
    
    # Check for many empty points-to sets
    pts_stats = stats.get('points_to', {})
    total_vars = pts_stats.get('total_variables', 0)
    empty_vars = pts_stats.get('empty_variables', 0)
    
    if total_vars > 0 and (empty_vars / total_vars) > 0.3:
        bottlenecks.append({
            'type': 'High Empty Variable Rate',
            'severity': 'MEDIUM',
            'description': f'{empty_vars}/{total_vars} variables have empty points-to sets ({empty_vars/total_vars*100:.1f}%)',
            'recommendation': 'Check allocation constraints and object creation'
        })
    
    return bottlenecks


def generate_markdown_report(
    debug_file: Path,
    debug_data: Dict[str, Any],
    output_file: Path
):
    """Generate comprehensive markdown report."""
    
    call_coverage = analyze_call_coverage(debug_data)
    pts_distribution = analyze_points_to_distribution(debug_data)
    obj_flow = analyze_object_flow(debug_data)
    pfg_effectiveness = analyze_pfg_effectiveness(debug_data)
    bottlenecks = identify_bottlenecks(debug_data)
    
    with open(output_file, 'w') as f:
        f.write("# Pointer Analysis Debug Report\n\n")
        f.write(f"**Input File:** `{debug_file.name}`\n")
        f.write(f"**Generated:** {debug_data.get('metadata', {}).get('timestamp', 'Unknown')}\n\n")
        
        # Metadata
        metadata = debug_data.get('metadata', {})
        f.write("## Analysis Metadata\n\n")
        f.write(f"- Total Iterations: {metadata.get('total_iterations', 0)}\n")
        f.write(f"- Log Interval: {metadata.get('log_interval', 0)}\n")
        f.write(f"- Events Tracked: {debug_data.get('events_count', 0)}\n\n")
        
        # Call Coverage
        f.write("## Call Graph Coverage\n\n")
        f.write(f"- **Total Call Constraints:** {call_coverage['total_call_constraints']}\n")
        f.write(f"- **Successfully Resolved:** {call_coverage['successful_resolutions']}\n")
        f.write(f"- **Coverage Rate:** {call_coverage['coverage_rate']:.1f}%\n")
        f.write(f"- **Failed (Empty Callee):** {call_coverage['failed_empty_callee']}\n")
        f.write(f"- **Failed (Non-callable):** {call_coverage['failed_non_callable']}\n\n")
        
        f.write("### Call Types\n\n")
        for call_type, count in call_coverage['call_types'].items():
            f.write(f"- {call_type.replace('_', ' ').title()}: {count}\n")
        f.write("\n")
        
        # Points-to Distribution
        f.write("## Points-To Set Distribution\n\n")
        f.write(f"- **Total Variables:** {pts_distribution['total_variables']}\n")
        f.write(f"- **Empty Sets:** {pts_distribution['empty_count']}\n")
        f.write(f"- **Singleton Sets:** {pts_distribution['singleton_count']}\n")
        f.write(f"- **Large Sets (>10):** {pts_distribution['large_count']}\n")
        f.write(f"- **Average Size:** {pts_distribution['avg_size']:.2f}\n")
        f.write(f"- **Max Size:** {pts_distribution['max_size']}\n\n")
        
        f.write("### Size Distribution\n\n")
        f.write("| Size Range | Count |\n")
        f.write("|------------|-------|\n")
        for size_range, count in sorted(pts_distribution['distribution'].items()):
            f.write(f"| {size_range} | {count} |\n")
        f.write("\n")
        
        # Object Flow
        f.write("## Object Creation and Flow\n\n")
        f.write(f"- **Objects Created:** {obj_flow['total_objects_created']}\n")
        f.write(f"- **Objects Propagated:** {obj_flow['total_objects_propagated']}\n\n")
        
        f.write("### Objects by Kind\n\n")
        f.write("| Kind | Count | Percentage |\n")
        f.write("|------|-------|------------|\n")
        for kind, count in sorted(obj_flow['objects_by_kind'].items(), key=lambda x: x[1], reverse=True):
            pct = obj_flow['kind_distribution'].get(kind, 0.0)
            f.write(f"| {kind} | {count} | {pct:.1f}% |\n")
        f.write("\n")
        
        # PFG Effectiveness
        f.write("## Pointer Flow Graph Effectiveness\n\n")
        f.write(f"- **Total Edges:** {pfg_effectiveness['total_edges']}\n")
        f.write(f"- **Activated Edges:** {pfg_effectiveness['activated_edges']}\n")
        f.write(f"- **Dead Edges:** {pfg_effectiveness['dead_edges']}\n")
        f.write(f"- **Activation Rate:** {pfg_effectiveness['activation_rate']:.1f}%\n")
        f.write(f"- **Total Object Flow:** {pfg_effectiveness['total_object_flow']}\n")
        f.write(f"- **Avg Activations per Edge:** {pfg_effectiveness['avg_activations_per_edge']:.2f}\n\n")
        
        # Bottlenecks
        if bottlenecks:
            f.write("## Identified Bottlenecks\n\n")
            for i, bottleneck in enumerate(bottlenecks, 1):
                f.write(f"### {i}. {bottleneck['type']} [{bottleneck['severity']}]\n\n")
                f.write(f"**Description:** {bottleneck['description']}\n\n")
                f.write(f"**Recommendation:** {bottleneck['recommendation']}\n\n")
        
        # Recommendations
        f.write("## Recommendations\n\n")
        if call_coverage['coverage_rate'] < 50:
            f.write("1. **Improve Call Resolution:** Call coverage is low. Focus on:\n")
            f.write("   - Ensuring function objects are allocated\n")
            f.write("   - Verifying function object propagation through copy constraints\n")
            f.write("   - Checking that callee variables are not empty\n\n")
        
        if pfg_effectiveness['activation_rate'] < 70:
            f.write("2. **Optimize PFG:** Many edges are never activated. Consider:\n")
            f.write("   - Reviewing constraint generation logic\n")
            f.write("   - Checking for unreachable code paths\n")
            f.write("   - Verifying object allocation locations\n\n")
        
        if pts_distribution['empty_count'] > pts_distribution['total_variables'] * 0.3:
            f.write("3. **Reduce Empty Points-to Sets:** Many variables have empty sets. Consider:\n")
            f.write("   - Checking allocation constraint generation\n")
            f.write("   - Verifying copy constraint correctness\n")
            f.write("   - Reviewing variable initialization\n\n")


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Generate debug report from pointer analysis output"
    )
    parser.add_argument(
        "debug_files",
        nargs="+",
        help="Debug JSON files or directories to analyze"
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default="benchmark/debug_reports",
        help="Output directory for reports"
    )
    
    args = parser.parse_args()
    
    # Ensure output directory exists
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # Process each debug file
    for debug_path_str in args.debug_files:
        debug_path = Path(debug_path_str)
        
        # Handle directories
        if debug_path.is_dir():
            debug_files = list(debug_path.glob("*_debug.json"))
        elif debug_path.suffix == '.json':
            debug_files = [debug_path]
        else:
            debug_files = list(Path('.').glob(debug_path_str))
        
        for debug_file in debug_files:
            if not debug_file.exists():
                print(f"Warning: File not found: {debug_file}")
                continue
            
            print(f"Processing: {debug_file}")
            
            try:
                # Load debug data
                debug_data = load_debug_data(debug_file)
                
                # Generate report
                output_file = output_dir / f"{debug_file.stem}_report.md"
                generate_markdown_report(debug_file, debug_data, output_file)
                
                print(f"  Report generated: {output_file}")
                
            except Exception as e:
                print(f"  Error processing {debug_file}: {e}")
                import traceback
                traceback.print_exc()
    
    print(f"\nAll reports saved to: {output_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

