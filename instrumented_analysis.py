#!/usr/bin/env python3
"""
Instrumented Analysis - Add diagnostic counters to understand call edge discovery.

This patches the existing analysis code to add counters at each stage.
"""

import os
import sys
from pathlib import Path

# Add project root
PROJECT_ROOT = Path(__file__).parent
sys.path.insert(0, str(PROJECT_ROOT))

# Monkey-patch to add diagnostic counters
original_iter_function_events = None
original_process_call = None

call_stats = {
    'ircall_instructions': 0,
    'call_events_generated': 0,
    'calls_attempted': 0,
    'calls_resolved': 0,
    'calls_unresolved': 0,
    'unresolved_targets': []
}


def instrumented_iter_function_events(func_ir):
    """Wrapper around iter_function_events to count IRCall instructions and CallEvents."""
    from pythonstan.ir.ir_statements import IRCall
    from pythonstan.analysis.pointer.kcfa2.ir_adapter import CallEvent
    
    # Count IRCall instructions
    if hasattr(func_ir, 'blocks'):
        for block in func_ir.blocks:
            for instr in block.instructions:
                if isinstance(instr, IRCall):
                    call_stats['ircall_instructions'] += 1
    
    # Call original and count events
    for event in original_iter_function_events(func_ir):
        if event.get('kind') == 'call':
            call_stats['call_events_generated'] += 1
        yield event


def instrumented_process_call(self, call):
    """Wrapper around _process_call to count resolution attempts."""
    call_stats['calls_attempted'] += 1
    
    # Track resolution
    if call.call_type == "direct":
        resolved_callee = self._resolve_function_name(call.callee)
        if resolved_callee and resolved_callee in self._functions:
            call_stats['calls_resolved'] += 1
        else:
            call_stats['calls_unresolved'] += 1
            if call.callee not in call_stats['unresolved_targets']:
                call_stats['unresolved_targets'].append(call.callee)
    
    # Call original
    return original_process_call(self, call)


def install_instrumentation():
    """Install diagnostic instrumentation."""
    global original_iter_function_events, original_process_call
    
    # Patch ir_adapter.iter_function_events
    from pythonstan.analysis.pointer.kcfa2 import ir_adapter
    original_iter_function_events = ir_adapter.iter_function_events
    ir_adapter.iter_function_events = instrumented_iter_function_events
    
    # Patch KCFA2PointerAnalysis._process_call
    from pythonstan.analysis.pointer.kcfa2.analysis import KCFA2PointerAnalysis
    original_process_call = KCFA2PointerAnalysis._process_call
    KCFA2PointerAnalysis._process_call = instrumented_process_call
    
    print("✅ Instrumentation installed")


def reset_stats():
    """Reset statistics."""
    global call_stats
    call_stats = {
        'ircall_instructions': 0,
        'call_events_generated': 0,
        'calls_attempted': 0,
        'calls_resolved': 0,
        'calls_unresolved': 0,
        'unresolved_targets': []
    }


def print_stats(prefix=""):
    """Print current statistics."""
    print(f"\n{prefix}CALL DISCOVERY STATISTICS:")
    print(f"  IRCall instructions:    {call_stats['ircall_instructions']}")
    print(f"  Call events generated:  {call_stats['call_events_generated']}")
    print(f"  Calls attempted:        {call_stats['calls_attempted']}")
    print(f"  Calls resolved:         {call_stats['calls_resolved']}")
    print(f"  Calls unresolved:       {call_stats['calls_unresolved']}")
    
    if call_stats['ircall_instructions'] > 0:
        event_rate = (call_stats['call_events_generated'] / call_stats['ircall_instructions']) * 100
        print(f"  IRCall → Event rate:    {event_rate:.1f}%")
    
    if call_stats['calls_attempted'] > 0:
        resolve_rate = (call_stats['calls_resolved'] / call_stats['calls_attempted']) * 100
        print(f"  Call resolution rate:   {resolve_rate:.1f}%")
    
    if call_stats['unresolved_targets']:
        print(f"  Sample unresolved targets: {call_stats['unresolved_targets'][:10]}")


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="Run instrumented analysis")
    parser.add_argument('project', choices=['flask', 'werkzeug', 'both'], help='Project to analyze')
    parser.add_argument('--k', type=int, default=2, help='Context sensitivity (k)')
    parser.add_argument('--include-deps', action='store_true', help='Include dependencies')
    parser.add_argument('--verbose', action='store_true', help='Verbose output')
    
    args = parser.parse_args()
    
    # Install instrumentation
    install_instrumentation()
    
    # Import and run analyze_real_world
    from benchmark import analyze_real_world
    
    # Run analysis
    analyzer = analyze_real_world.ProjectAnalyzer(
        k=args.k,
        include_dependencies=args.include_deps,
        verbose=args.verbose
    )
    
    if args.project == 'both':
        projects = ['flask', 'werkzeug']
    else:
        projects = [args.project]
    
    for project in projects:
        print(f"\n{'='*80}")
        print(f"Analyzing {project.upper()}")
        print(f"{'='*80}")
        
        reset_stats()
        results = analyzer.analyze_project(project)
        
        print_stats(f"\n{project.upper()} ")
        
        # Print regular results too
        print(f"\n{project.upper()} REGULAR RESULTS:")
        print(f"  Modules analyzed: {len(results)}")
        print(f"  Success rate: {sum(1 for r in results if r.success)}/{len(results)}")


