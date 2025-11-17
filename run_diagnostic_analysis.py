#!/usr/bin/env python3
"""Run pointer analysis with diagnostic instrumentation.

This script wraps the existing benchmark script to add comprehensive debugging
without modifying the core codebase.
"""

import sys
import subprocess
import json
import time
from pathlib import Path

# Add project to path
sys.path.insert(0, str(Path(__file__).parent))

from debug_tools.statistical_analyzer import StatisticalAnalyzer
from debug_tools.visualizer import PFGVisualizer


def parse_log_file(log_file: Path) -> dict:
    """Parse analysis log file to extract metrics.
    
    Args:
        log_file: Path to log file
    
    Returns:
        Dictionary with extracted metrics
    """
    metrics = {
        'iterations': [],
        'call_edges': [],
        'pfg_edges': [],
        'objects': [],
        'worklist_sizes': []
    }
    
    if not log_file.exists():
        return metrics
    
    with open(log_file, 'r') as f:
        for line in f:
            # Parse iteration lines
            if 'Iteration' in line and 'worklist size' in line:
                try:
                    parts = line.split()
                    iteration = int(parts[parts.index('Iteration') + 1].rstrip(','))
                    worklist_idx = parts.index('size') + 1
                    worklist_size = int(parts[worklist_idx].rstrip(','))
                    
                    if 'objs:' in parts:
                        objs_idx = parts.index('objs:') + 1
                        objs = int(parts[objs_idx].rstrip(','))
                        metrics['objects'].append((iteration, objs))
                    
                    if 'call_edges:' in parts:
                        edges_idx = parts.index('call_edges:') + 1
                        edges = int(parts[edges_idx].rstrip(','))
                        metrics['call_edges'].append((iteration, edges))
                    
                    if 'plain_call_edges:' in parts:
                        plain_idx = parts.index('plain_call_edges:') + 1
                        plain_edges = int(parts[plain_idx])
                        
                    metrics['iterations'].append(iteration)
                    metrics['worklist_sizes'].append((iteration, worklist_size))
                except (ValueError, IndexError):
                    continue
    
    return metrics


def analyze_metrics(metrics: dict) -> dict:
    """Analyze parsed metrics to identify issues.
    
    Args:
        metrics: Parsed metrics dictionary
    
    Returns:
        Analysis results
    """
    analysis = {
        'total_iterations': len(metrics['iterations']),
        'final_call_edges': 0,
        'max_call_edges': 0,
        'call_edge_growth': [],
        'issues': [],
        'recommendations': []
    }
    
    if not metrics['call_edges']:
        analysis['issues'].append({
            'severity': 'critical',
            'description': 'No call edge data found in logs',
            'suggestion': 'Check if analysis is running and logging is enabled'
        })
        return analysis
    
    # Analyze call edge growth
    call_edges = metrics['call_edges']
    analysis['final_call_edges'] = call_edges[-1][1] if call_edges else 0
    analysis['max_call_edges'] = max(e[1] for e in call_edges) if call_edges else 0
    
    # Check growth pattern
    if len(call_edges) > 10:
        early_avg = sum(e[1] for e in call_edges[:len(call_edges)//3]) / (len(call_edges)//3)
        late_avg = sum(e[1] for e in call_edges[-len(call_edges)//3:]) / (len(call_edges)//3)
        
        analysis['call_edge_growth'] = {
            'early_avg': early_avg,
            'late_avg': late_avg,
            'growth_rate': (late_avg - early_avg) / max(early_avg, 1)
        }
        
        if late_avg < early_avg * 1.5:
            analysis['issues'].append({
                'severity': 'high',
                'description': 'Slow call edge discovery - edges not growing significantly',
                'suggestion': 'Check if CallConstraints are being created and applied'
            })
    
    # Check final count
    if analysis['final_call_edges'] < 100:
        analysis['issues'].append({
            'severity': 'critical',
            'description': f'Very low final call edge count: {analysis["final_call_edges"]}',
            'suggestion': 'Review object allocation and constraint generation'
        })
    
    # Analyze worklist behavior
    if metrics['worklist_sizes']:
        avg_worklist = sum(w[1] for w in metrics['worklist_sizes']) / len(metrics['worklist_sizes'])
        max_worklist = max(w[1] for w in metrics['worklist_sizes'])
        
        analysis['worklist_stats'] = {
            'avg': avg_worklist,
            'max': max_worklist
        }
        
        if avg_worklist < 10:
            analysis['issues'].append({
                'severity': 'medium',
                'description': 'Low average worklist size - may indicate insufficient propagation',
                'suggestion': 'Check if PFG edges are being created properly'
            })
    
    # Generate recommendations
    if analysis['issues']:
        analysis['recommendations'].append("=== CRITICAL ACTIONS ===")
        for issue in analysis['issues']:
            if issue['severity'] == 'critical':
                analysis['recommendations'].append(f"- {issue['description']}")
                analysis['recommendations'].append(f"  → {issue['suggestion']}")
    
    return analysis


def run_with_diagnostics(project: str = "flask", timeout: int = 300):
    """Run analysis with diagnostics.
    
    Args:
        project: Project name (flask or werkzeug)
        timeout: Timeout in seconds
    """
    print("\n" + "="*80)
    print(f"DIAGNOSTIC POINTER ANALYSIS - {project.upper()}")
    print("="*80 + "\n")
    
    # Create output directory
    output_dir = Path("debug_output")
    output_dir.mkdir(exist_ok=True, parents=True)
    
    # Run benchmark with logging
    log_file = output_dir / f"{project}_analysis.log"
    cmd = [
        "timeout", str(timeout),
        "python", "benchmark/analyze_kcfa_policies.py",
        project
    ]
    
    print(f"Running: {' '.join(cmd)}")
    print(f"Logging to: {log_file}")
    print("\nAnalysis in progress...\n")
    
    start_time = time.time()
    
    try:
        with open(log_file, 'w') as f:
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                universal_newlines=True,
                bufsize=1
            )
            
            # Stream output
            for line in process.stdout:
                print(line, end='')
                f.write(line)
                f.flush()
            
            process.wait()
            returncode = process.returncode
    
    except subprocess.TimeoutExpired:
        print(f"\n⚠️  Analysis timed out after {timeout} seconds")
        returncode = 124
    except KeyboardInterrupt:
        print("\n⚠️  Analysis interrupted by user")
        returncode = 130
    except Exception as e:
        print(f"\n❌ Error running analysis: {e}")
        returncode = 1
    
    elapsed = time.time() - start_time
    
    print("\n" + "="*80)
    print("ANALYSIS COMPLETE")
    print("="*80)
    print(f"Elapsed time: {elapsed:.1f}s")
    print(f"Exit code: {returncode}")
    
    # Parse logs
    print("\n📊 Parsing analysis logs...")
    metrics = parse_log_file(log_file)
    
    if metrics['iterations']:
        print(f"  Total iterations: {len(metrics['iterations'])}")
        print(f"  Call edges tracked: {len(metrics['call_edges'])} data points")
        print(f"  Objects tracked: {len(metrics['objects'])} data points")
        
        # Analyze
        print("\n🔍 Running diagnostic analysis...")
        analysis = analyze_metrics(metrics)
        
        # Save analysis
        analysis_file = output_dir / f"{project}_diagnostic_analysis.json"
        with open(analysis_file, 'w') as f:
            json.dump(analysis, f, indent=2)
        
        print(f"\n📈 ANALYSIS RESULTS:")
        print(f"  Total iterations: {analysis['total_iterations']}")
        print(f"  Final call edges: {analysis['final_call_edges']}")
        print(f"  Max call edges: {analysis['max_call_edges']}")
        
        if 'call_edge_growth' in analysis:
            growth = analysis['call_edge_growth']
            print(f"  Call edge growth:")
            print(f"    Early avg: {growth['early_avg']:.1f}")
            print(f"    Late avg: {growth['late_avg']:.1f}")
            print(f"    Growth rate: {growth['growth_rate']*100:.1f}%")
        
        if 'worklist_stats' in analysis:
            wl = analysis['worklist_stats']
            print(f"  Worklist stats:")
            print(f"    Average size: {wl['avg']:.1f}")
            print(f"    Max size: {wl['max']}")
        
        # Print issues
        if analysis['issues']:
            print(f"\n⚠️  ISSUES FOUND ({len(analysis['issues'])}):")
            for issue in analysis['issues']:
                severity_icon = {'critical': '🔴', 'high': '🟠', 'medium': '🟡'}.get(issue['severity'], '⚪')
                print(f"\n  {severity_icon} [{issue['severity'].upper()}] {issue['description']}")
                print(f"     → {issue['suggestion']}")
        
        # Print recommendations
        if analysis['recommendations']:
            print("\n💡 RECOMMENDATIONS:")
            for rec in analysis['recommendations']:
                print(f"  {rec}")
        
        print(f"\n📁 Results saved to:")
        print(f"  - {log_file}")
        print(f"  - {analysis_file}")
        
        # Run statistical analyzer if we have enough data
        if analysis['final_call_edges'] > 0:
            print("\n🔬 Running advanced statistical analysis...")
            try:
                # Create synthetic instrumentation data from metrics
                instr_data = {
                    'summary': {
                        'iterations': analysis['total_iterations'],
                        'call_edges': analysis['final_call_edges'],
                    },
                    'call_edges': [
                        {'callsite': f'call_{i}', 'callee': f'func_{i}', 'iteration': iter_num}
                        for i, (iter_num, _) in enumerate(metrics['call_edges'])
                    ],
                    'constraints': {
                        'total_applied': analysis['total_iterations'],
                        'by_type': {}
                    },
                    'pfg': {
                        'edges': [],
                        'by_kind': {}
                    },
                    'object_flows': {},
                    'field_inheritances': []
                }
                
                analyzer = StatisticalAnalyzer(output_dir=str(output_dir))
                stat_results = analyzer.run_full_analysis(instr_data)
                analyzer.save_analysis(stat_results, filename=f"{project}_statistical_analysis.json")
                
                print("  ✓ Statistical analysis complete")
                print(f"  - {output_dir}/{project}_statistical_analysis.json")
                print(f"  - {output_dir}/analysis_report.txt")
            except Exception as e:
                print(f"  ⚠️  Statistical analysis failed: {e}")
    
    else:
        print("\n❌ No metrics found in log file")
        print("   The analysis may have failed early or produced no output")
    
    print("\n" + "="*80)
    
    return returncode


def main():
    """Main entry point."""
    import argparse
    
    parser = argparse.ArgumentParser(
        description="Run pointer analysis with diagnostic logging"
    )
    parser.add_argument(
        "project",
        nargs="?",
        default="flask",
        choices=["flask", "werkzeug"],
        help="Project to analyze (default: flask)"
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=300,
        help="Timeout in seconds (default: 300)"
    )
    
    args = parser.parse_args()
    
    return run_with_diagnostics(args.project, args.timeout)


if __name__ == "__main__":
    sys.exit(main())

