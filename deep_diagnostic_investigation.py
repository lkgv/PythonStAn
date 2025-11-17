#!/usr/bin/env python3
"""
DEEP DIAGNOSTIC INVESTIGATION

Thoroughly investigate why call edge coverage is only 16% when 40-60% is expected.
This script instruments every stage of the call discovery pipeline.
"""

import os
import sys
import json
import time
from pathlib import Path
from typing import Dict, List, Set, Any, Tuple
from collections import defaultdict, Counter
from dataclasses import dataclass, asdict

# Add project root
PROJECT_ROOT = Path(__file__).parent
sys.path.insert(0, str(PROJECT_ROOT))

from pythonstan.world.pipeline import Pipeline
from pythonstan.analysis.pointer.kcfa2 import KCFA2PointerAnalysis, KCFAConfig
from pythonstan.ir.ir_statements import IRCall


@dataclass
class CallPipelineStats:
    """Track calls through the entire pipeline."""
    module_name: str
    
    # Stage 1: IR Instructions
    total_ircall_instructions: int = 0
    ircall_by_type: Dict[str, int] = None  # direct, method, indirect
    
    # Stage 2: Events Generated
    call_events_generated: int = 0
    call_events_by_type: Dict[str, int] = None
    
    # Stage 3: Call Resolution
    calls_attempted: int = 0
    calls_resolved: int = 0
    calls_unresolved: int = 0
    unresolved_reasons: Dict[str, int] = None
    
    # Stage 4: Edge Creation
    edges_created: int = 0
    edges_by_type: Dict[str, int] = None
    
    # Per-function breakdown
    functions_with_ircalls: int = 0
    functions_with_events: int = 0
    functions_with_edges: int = 0
    
    # Detailed tracking
    ircall_targets: List[str] = None
    unresolved_callees: List[str] = None
    
    def __post_init__(self):
        if self.ircall_by_type is None:
            self.ircall_by_type = {}
        if self.call_events_by_type is None:
            self.call_events_by_type = {}
        if self.unresolved_reasons is None:
            self.unresolved_reasons = {}
        if self.edges_by_type is None:
            self.edges_by_type = {}
        if self.ircall_targets is None:
            self.ircall_targets = []
        if self.unresolved_callees is None:
            self.unresolved_callees = []


def count_ircall_instructions(ir_module) -> Tuple[int, Dict[str, int], List[str]]:
    """Count IRCall instructions in a module."""
    count = 0
    by_type = defaultdict(int)
    targets = []
    
    # Get all functions from module
    if hasattr(ir_module, 'scope_manager'):
        scope_manager = ir_module.scope_manager
        if hasattr(scope_manager, 'get_subscopes'):
            all_scopes = scope_manager.get_subscopes()
            
            for scope in all_scopes:
                # Get IR for this function
                for fmt in ['cfg', 'block cfg', 'three address']:
                    func_ir = scope_manager.get_ir(scope, fmt)
                    if func_ir is not None:
                        # Count IRCalls in this IR
                        if hasattr(func_ir, 'blocks'):
                            for block in func_ir.blocks:
                                for instr in block.instructions:
                                    if isinstance(instr, IRCall):
                                        count += 1
                                        func_name = instr.get_func_name()
                                        targets.append(func_name)
                                        
                                        # Classify call type
                                        if hasattr(instr, 'receiver') and instr.receiver:
                                            by_type['method'] += 1
                                        elif func_name and not func_name.startswith('$'):
                                            by_type['direct'] += 1
                                        else:
                                            by_type['indirect'] += 1
                        break
    
    return count, dict(by_type), targets


def analyze_module_deep(module_path: Path, project_path: Path, verbose: bool = False) -> CallPipelineStats:
    """Deep analysis of a single module tracking call pipeline."""
    stats = CallPipelineStats(module_name=str(module_path.name))
    
    try:
        # Stage 1: Build IR and count IRCall instructions
        if verbose:
            print(f"\n{'='*60}")
            print(f"Module: {module_path.name}")
            print(f"{'='*60}")
        
        # Get library paths
        import site
        library_paths = [os.path.dirname(os.__file__)]
        library_paths.extend(site.getsitepackages())
        
        pipeline_config = {
            "filename": str(module_path),
            "project_path": str(project_path),
            "library_paths": library_paths,
            "analysis": [],
            "lazy_ir_construction": True
        }
        
        pipeline = Pipeline(config=pipeline_config)
        pipeline.run()
        
        # Get IR module and scope manager
        world = pipeline.get_world()
        ir_module = world.entry_module
        scope_manager = world.scope_manager
        
        # Count IRCall instructions
        ircall_count, ircall_by_type, ircall_targets = count_ircall_instructions(ir_module)
        stats.total_ircall_instructions = ircall_count
        stats.ircall_by_type = ircall_by_type
        stats.ircall_targets = ircall_targets[:50]  # Sample
        
        if verbose:
            print(f"\n[STAGE 1: IR Instructions]")
            print(f"  Total IRCall instructions: {ircall_count}")
            print(f"  By type: {ircall_by_type}")
            if ircall_targets:
                print(f"  Sample targets: {ircall_targets[:10]}")
        
        # Stage 2: Run analysis and count events/edges
        functions_with_ir = []
        
        if hasattr(scope_manager, 'get_subscopes'):
            all_scopes = scope_manager.get_subscopes()
            
            for scope in all_scopes:
                for fmt in ['cfg', 'block cfg']:
                    func_ir = scope_manager.get_ir(scope, fmt)
                    if func_ir is not None:
                        scope._cfg = func_ir
                        scope._ir_format = fmt
                        functions_with_ir.append(scope)
                        break
        
        # Create and run analysis
        config = KCFAConfig(k=2, verbose=False)
        analysis = KCFA2PointerAnalysis(config)
        
        if functions_with_ir:
            analysis.plan(functions_with_ir)
        
        analysis.initialize()
        analysis.run()
        
        # Stage 3: Extract call graph statistics
        if hasattr(analysis, '_call_graph'):
            cg = analysis._call_graph
            
            # Count edges
            if hasattr(cg, '_edges'):
                stats.edges_created = len(cg._edges)
                
                # Count edge types
                edge_types = defaultdict(int)
                for edge in cg._edges:
                    if hasattr(edge, 'call_type'):
                        edge_types[edge.call_type] += 1
                    else:
                        edge_types['unknown'] += 1
                stats.edges_by_type = dict(edge_types)
            
            # Get call graph stats
            cg_stats = cg.get_statistics()
            if verbose:
                print(f"\n[STAGE 3: Call Graph]")
                print(f"  Edges created: {stats.edges_created}")
                print(f"  By type: {stats.edges_by_type}")
                print(f"  CG stats: {cg_stats}")
        
        # Stage 4: Per-function analysis
        if hasattr(analysis, '_functions'):
            total_funcs = len(analysis._functions)
            
            # Count functions with calls
            funcs_with_calls = 0
            if hasattr(analysis, '_call_graph'):
                for func_name in analysis._functions.keys():
                    # Check if function has outgoing edges
                    if hasattr(cg, 'get_callees'):
                        callees = cg.get_callees(func_name)
                        if callees:
                            funcs_with_calls += 1
            
            stats.functions_with_edges = funcs_with_calls
            
            if verbose:
                print(f"\n[STAGE 4: Function Statistics]")
                print(f"  Total functions: {total_funcs}")
                print(f"  Functions with outgoing calls: {funcs_with_calls}")
                print(f"  Coverage: {funcs_with_calls/total_funcs*100:.1f}%")
        
        # Compute conversion rates
        if verbose:
            print(f"\n[PIPELINE CONVERSION RATES]")
            if ircall_count > 0:
                edge_rate = (stats.edges_created / ircall_count) * 100
                print(f"  IRCall → Edges: {stats.edges_created}/{ircall_count} ({edge_rate:.1f}%)")
            else:
                print(f"  IRCall → Edges: 0/0 (N/A)")
    
    except Exception as e:
        if verbose:
            print(f"ERROR analyzing {module_path.name}: {e}")
            import traceback
            traceback.print_exc()
    
    return stats


def analyze_project(project_name: str, max_modules: int = 10, verbose: bool = True):
    """Analyze a project with deep call pipeline tracking."""
    print(f"\n{'='*80}")
    print(f"DEEP DIAGNOSTIC INVESTIGATION: {project_name.upper()}")
    print(f"{'='*80}")
    
    # Find project
    benchmark_dir = PROJECT_ROOT / "benchmark" / "projects"
    project_dir = benchmark_dir / project_name
    
    if not project_dir.exists():
        print(f"ERROR: Project {project_name} not found at {project_dir}")
        return
    
    # Find Python modules (exclude tests, docs, examples)
    modules = list(project_dir.glob("**/*.py"))
    modules = [m for m in modules 
               if '__pycache__' not in str(m) 
               and 'test' not in str(m).lower()
               and 'doc' not in str(m).lower()
               and 'example' not in str(m).lower()]
    
    # Prioritize src/ directory if it exists
    src_modules = [m for m in modules if '/src/' in str(m)]
    if src_modules:
        modules = src_modules + [m for m in modules if m not in src_modules]
    
    print(f"\nFound {len(modules)} modules")
    print(f"Analyzing first {max_modules} modules...\n")
    
    # Analyze modules
    all_stats = []
    for i, module_path in enumerate(modules[:max_modules]):
        print(f"\n[{i+1}/{max_modules}] ", end='')
        stats = analyze_module_deep(module_path, project_dir, verbose=verbose)
        all_stats.append(stats)
    
    # Aggregate statistics
    print(f"\n\n{'='*80}")
    print(f"AGGREGATE STATISTICS")
    print(f"{'='*80}")
    
    total_ircalls = sum(s.total_ircall_instructions for s in all_stats)
    total_edges = sum(s.edges_created for s in all_stats)
    total_modules = len(all_stats)
    
    print(f"\nModules analyzed: {total_modules}")
    print(f"Total IRCall instructions: {total_ircalls}")
    print(f"Total call edges created: {total_edges}")
    
    if total_ircalls > 0:
        conversion_rate = (total_edges / total_ircalls) * 100
        print(f"Overall conversion rate: {conversion_rate:.1f}%")
    else:
        print(f"Overall conversion rate: N/A (no IRCalls found)")
    
    # Distribution analysis
    modules_with_ircalls = sum(1 for s in all_stats if s.total_ircall_instructions > 0)
    modules_with_edges = sum(1 for s in all_stats if s.edges_created > 0)
    
    print(f"\nModules with IRCall instructions: {modules_with_ircalls}/{total_modules}")
    print(f"Modules with call edges: {modules_with_edges}/{total_modules}")
    
    # Detailed breakdown
    print(f"\n{'='*80}")
    print(f"PER-MODULE BREAKDOWN")
    print(f"{'='*80}")
    print(f"{'Module':<30} {'IRCalls':>10} {'Edges':>10} {'Rate':>10}")
    print(f"{'-'*80}")
    
    for stats in all_stats:
        if stats.total_ircall_instructions > 0:
            rate = (stats.edges_created / stats.total_ircall_instructions) * 100
            print(f"{stats.module_name:<30} {stats.total_ircall_instructions:>10} {stats.edges_created:>10} {rate:>9.1f}%")
    
    # Show modules with ZERO conversion
    print(f"\n{'='*80}")
    print(f"MODULES WITH ZERO CALL EDGES (despite having IRCalls)")
    print(f"{'='*80}")
    
    zero_conversion = [s for s in all_stats if s.total_ircall_instructions > 0 and s.edges_created == 0]
    if zero_conversion:
        for stats in zero_conversion:
            print(f"\n{stats.module_name}:")
            print(f"  IRCalls: {stats.total_ircall_instructions}")
            print(f"  By type: {stats.ircall_by_type}")
            print(f"  Sample targets: {stats.ircall_targets[:10]}")
    else:
        print("None (all modules with IRCalls have at least 1 edge)")
    
    # Save detailed report
    report_path = PROJECT_ROOT / "benchmark" / "reports" / f"deep_diagnostic_{project_name}.json"
    report_path.parent.mkdir(exist_ok=True)
    
    report = {
        "project": project_name,
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "summary": {
            "modules_analyzed": total_modules,
            "total_ircall_instructions": total_ircalls,
            "total_call_edges": total_edges,
            "conversion_rate": f"{(total_edges/total_ircalls)*100:.1f}%" if total_ircalls > 0 else "N/A",
            "modules_with_ircalls": modules_with_ircalls,
            "modules_with_edges": modules_with_edges,
        },
        "per_module_stats": [asdict(s) for s in all_stats]
    }
    
    with open(report_path, 'w') as f:
        json.dump(report, f, indent=2)
    
    print(f"\n\nDetailed report saved to: {report_path}")


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="Deep diagnostic investigation of call edge discovery")
    parser.add_argument('project', choices=['flask', 'werkzeug'], help='Project to analyze')
    parser.add_argument('--max-modules', type=int, default=10, help='Max modules to analyze')
    parser.add_argument('--verbose', action='store_true', help='Verbose output')
    
    args = parser.parse_args()
    
    analyze_project(args.project, max_modules=args.max_modules, verbose=args.verbose)

