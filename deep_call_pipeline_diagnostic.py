#!/usr/bin/env python3
"""
Deep Call Pipeline Diagnostic Tool

Instruments the entire call discovery pipeline:
  AST → IR → Events → Resolution → Edges

Tracks exactly where calls are lost and why.
"""

import ast
import sys
import json
from pathlib import Path
from typing import Dict, List, Set, Any, Optional, Tuple
from dataclasses import dataclass, asdict, field
from collections import defaultdict, Counter
from datetime import datetime

# Add project root to path
PROJECT_ROOT = Path(__file__).parent
sys.path.insert(0, str(PROJECT_ROOT))

from pythonstan.world.pipeline import Pipeline
from pythonstan.analysis.pointer.kcfa2 import KCFA2PointerAnalysis, KCFAConfig
from pythonstan.ir import IRCall, IRFunc, IRClass


@dataclass
class PipelineFunnelMetrics:
    """Metrics tracking calls through the entire pipeline."""
    # Stage 1: AST (baseline)
    ast_call_nodes: int = 0
    
    # Stage 2: IR conversion
    ir_call_instructions: int = 0
    ir_conversion_rate: float = 0.0
    
    # Stage 3: Event extraction
    call_events_generated: int = 0
    event_extraction_rate: float = 0.0
    
    # Stage 4: Call resolution
    resolution_attempts: int = 0
    resolved_calls: int = 0
    unresolved_calls: int = 0
    resolution_success_rate: float = 0.0
    
    # Stage 5: Edge creation
    call_edges_created: int = 0
    edge_creation_rate: float = 0.0
    
    # Overall pipeline efficiency
    overall_conversion_rate: float = 0.0  # (edges / ast_calls) * 100
    
    # Bottleneck identification
    bottleneck_stage: str = "unknown"
    primary_loss_stage: str = "unknown"
    primary_loss_amount: int = 0
    primary_loss_percentage: float = 0.0
    
    # Unresolved callees tracking
    unresolved_callees: Dict[str, int] = field(default_factory=dict)
    unresolved_reasons: Dict[str, int] = field(default_factory=dict)


@dataclass
class IRInstrumentationData:
    """Data collected from IR instrumentation."""
    ir_calls_by_module: Dict[str, int] = field(default_factory=dict)
    ir_calls_by_type: Dict[str, int] = field(default_factory=dict)  # direct, method, indirect
    total_ir_calls: int = 0


@dataclass
class EventInstrumentationData:
    """Data collected from event extraction instrumentation."""
    call_events_by_module: Dict[str, int] = field(default_factory=dict)
    call_events_by_type: Dict[str, int] = field(default_factory=dict)
    total_call_events: int = 0


@dataclass
class ResolutionInstrumentationData:
    """Data collected from call resolution instrumentation."""
    resolution_attempts: int = 0
    successful_resolutions: int = 0
    failed_resolutions: int = 0
    unresolved_callees: Counter = field(default_factory=Counter)
    failure_reasons: Counter = field(default_factory=Counter)


class InstrumentedAnalysis(KCFA2PointerAnalysis):
    """Instrumented version of KCFA2PointerAnalysis that tracks call processing."""
    
    def __init__(self, config: KCFAConfig):
        super().__init__(config)
        self.instrumentation = ResolutionInstrumentationData()
        self._tracked_call_sites = set()
        self._resolution_log = []
    
    def _process_call_event(self, event, ctx):
        """Instrumented version of call event processing."""
        # Track resolution attempt
        self.instrumentation.resolution_attempts += 1
        
        # Try to resolve
        try:
            # Call parent implementation
            result = super()._process_call_event(event, ctx)
            
            # If resolution succeeded, track it
            if result:
                self.instrumentation.successful_resolutions += 1
            else:
                self.instrumentation.failed_resolutions += 1
                # Track unresolved callee
                callee_name = self._extract_callee_name(event)
                if callee_name:
                    self.instrumentation.unresolved_callees[callee_name] += 1
                    self.instrumentation.failure_reasons["callee_not_in_function_table"] += 1
            
            return result
            
        except Exception as e:
            self.instrumentation.failed_resolutions += 1
            self.instrumentation.failure_reasons[type(e).__name__] += 1
            raise
    
    def _extract_callee_name(self, event) -> Optional[str]:
        """Extract callee name from call event."""
        if hasattr(event, 'callee'):
            callee = event.callee
            if hasattr(callee, 'name'):
                return callee.name
            elif isinstance(callee, str):
                return callee
        return None


def count_ast_calls(module_path: Path) -> int:
    """Count total Call nodes in AST."""
    try:
        source = module_path.read_text(encoding='utf-8')
        tree = ast.parse(source)
        
        call_count = 0
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                call_count += 1
        
        return call_count
    except Exception as e:
        print(f"  WARNING: Failed to parse {module_path.name}: {e}")
        return 0


def count_ir_calls(ir_module) -> Tuple[int, Dict[str, int]]:
    """Count IRCall instructions in IR representation."""
    total_calls = 0
    calls_by_type = defaultdict(int)
    
    # Get all functions in module (including methods)
    functions = []
    if hasattr(ir_module, 'functions'):
        functions.extend(ir_module.functions)
    
    # Also check for classes with methods
    if hasattr(ir_module, 'classes'):
        for cls in ir_module.classes:
            if hasattr(cls, 'methods'):
                functions.extend(cls.methods)
    
    # Count IRCall instructions in each function
    for func in functions:
        if hasattr(func, '_cfg'):
            cfg = func._cfg
            # Walk CFG blocks
            if hasattr(cfg, 'blocks'):
                for block in cfg.blocks:
                    if hasattr(block, 'instructions'):
                        for instr in block.instructions:
                            if isinstance(instr, IRCall):
                                total_calls += 1
                                # Categorize call type
                                if hasattr(instr, 'is_method_call') and instr.is_method_call:
                                    calls_by_type['method'] += 1
                                elif hasattr(instr, 'is_indirect') and instr.is_indirect:
                                    calls_by_type['indirect'] += 1
                                else:
                                    calls_by_type['direct'] += 1
        elif hasattr(func, 'body'):
            # Walk function body (if no CFG yet)
            for stmt in func.body:
                if isinstance(stmt, IRCall):
                    total_calls += 1
                    calls_by_type['direct'] += 1
    
    return total_calls, dict(calls_by_type)


def analyze_module_pipeline(module_path: Path, project_path: Path, 
                            config: KCFAConfig, 
                            verbose: bool = False) -> Tuple[Dict[str, int], Optional[InstrumentedAnalysis]]:
    """Analyze a single module and track calls through the pipeline."""
    
    metrics = {
        'ast_calls': 0,
        'ir_calls': 0,
        'call_events': 0,
        'resolved_calls': 0,
        'call_edges': 0
    }
    
    if verbose:
        print(f"\n{'='*70}")
        print(f"Pipeline Analysis: {module_path.name}")
        print(f"{'='*70}")
    
    # Stage 1: AST Call Count
    ast_calls = count_ast_calls(module_path)
    metrics['ast_calls'] = ast_calls
    if verbose:
        print(f"[AST] Found {ast_calls} Call nodes")
    
    try:
        # Stage 2: IR Construction
        pipeline_config = {
            "filename": str(module_path),
            "project_path": str(project_path),
            "analysis": [],
            "lazy_ir_construction": True
        }
        
        pipeline = Pipeline(config=pipeline_config)
        pipeline.run()
        
        world = pipeline.get_world()
        ir_module = world.entry_module
        scope_manager = world.scope_manager
        
        # Count IR calls
        ir_calls, ir_call_types = count_ir_calls(ir_module)
        metrics['ir_calls'] = ir_calls
        if verbose:
            print(f"[IR] Found {ir_calls} IRCall instructions")
            if ir_call_types:
                print(f"     Types: {ir_call_types}")
        
        # Stage 3: Extract functions for analysis
        from pythonstan.ir import IRFunc, IRClass
        functions = []
        if hasattr(scope_manager, 'get_subscopes'):
            subscopes = scope_manager.get_subscopes(ir_module)
            module_funcs = [s for s in subscopes if isinstance(s, IRFunc)]
            classes = [s for s in subscopes if isinstance(s, IRClass)]
            
            # Extract methods from classes
            class_methods = []
            for cls in classes:
                cls_subscopes = scope_manager.get_subscopes(cls)
                methods = [s for s in cls_subscopes if isinstance(s, IRFunc)]
                class_methods.extend(methods)
            
            functions = module_funcs + class_methods
            
            # Attach IR representations
            for func in functions:
                for fmt in ['cfg', 'block cfg', 'three address', 'ir']:
                    func_ir = scope_manager.get_ir(func, fmt)
                    if func_ir is not None:
                        func._cfg = func_ir
                        func._ir_format = fmt
                        break
        
        if verbose:
            print(f"[Functions] Found {len(functions)} functions to analyze")
        
        # Stage 4: Run instrumented pointer analysis
        analysis = InstrumentedAnalysis(config)
        
        if functions:
            analysis.plan(functions)
        
        # Also process module-level and classes
        if hasattr(analysis, 'plan_module'):
            analysis.plan_module(ir_module)
        
        analysis.initialize()
        analysis.run()
        
        # Stage 5: Extract metrics from analysis
        # Note: CallEvents are internal to the analysis, hard to count directly
        # We estimate from resolution attempts
        metrics['call_events'] = analysis.instrumentation.resolution_attempts
        metrics['resolved_calls'] = analysis.instrumentation.successful_resolutions
        
        # Count call edges from call graph
        if hasattr(analysis, '_call_graph'):
            cg = analysis._call_graph
            if hasattr(cg, '_cs_call_graph'):
                edge_count = sum(len(targets) for targets in cg._cs_call_graph.values())
                metrics['call_edges'] = edge_count
        
        if verbose:
            print(f"[Events] Generated ~{metrics['call_events']} CallEvents (resolution attempts)")
            print(f"[Resolution] Resolved {metrics['resolved_calls']} calls")
            print(f"[Edges] Created {metrics['call_edges']} call edges")
            print(f"\nPipeline Efficiency:")
            if ast_calls > 0:
                print(f"  AST→IR:         {100*ir_calls/ast_calls:.1f}%")
                print(f"  IR→Events:      {100*metrics['call_events']/ir_calls if ir_calls > 0 else 0:.1f}%")
                print(f"  Events→Resolved: {100*metrics['resolved_calls']/metrics['call_events'] if metrics['call_events'] > 0 else 0:.1f}%")
                print(f"  Resolved→Edges:  {100*metrics['call_edges']/metrics['resolved_calls'] if metrics['resolved_calls'] > 0 else 0:.1f}%")
                print(f"  Overall:        {100*metrics['call_edges']/ast_calls:.1f}%")
        
        return metrics, analysis
        
    except Exception as e:
        if verbose:
            print(f"  ERROR: {e}")
        return metrics, None


def analyze_project_pipeline(project_path: Path, project_name: str,
                             config: KCFAConfig,
                             max_modules: Optional[int] = None,
                             verbose: bool = False) -> PipelineFunnelMetrics:
    """Analyze entire project and aggregate pipeline metrics."""
    
    print(f"\n{'='*70}")
    print(f"Deep Pipeline Analysis: {project_name}")
    print(f"{'='*70}\n")
    
    # Find all Python modules
    modules = []
    for path in project_path.rglob("*.py"):
        if not any(part.startswith('.') or part == '__pycache__' for part in path.parts):
            modules.append(path)
    
    if max_modules:
        modules = modules[:max_modules]
    
    print(f"Analyzing {len(modules)} modules...\n")
    
    # Aggregate metrics
    total_metrics = PipelineFunnelMetrics()
    all_instrumentation_data = []
    
    for i, module_path in enumerate(modules, 1):
        rel_path = module_path.relative_to(project_path)
        print(f"[{i}/{len(modules)}] {rel_path}...", end=' ', flush=True)
        
        metrics, analysis = analyze_module_pipeline(module_path, project_path, config, verbose=False)
        
        # Aggregate
        total_metrics.ast_call_nodes += metrics['ast_calls']
        total_metrics.ir_call_instructions += metrics['ir_calls']
        total_metrics.call_events_generated += metrics['call_events']
        total_metrics.resolved_calls += metrics['resolved_calls']
        total_metrics.call_edges_created += metrics['call_edges']
        
        if analysis:
            all_instrumentation_data.append(analysis.instrumentation)
            print(f"✓ ({metrics['ast_calls']} AST → {metrics['call_edges']} edges)")
        else:
            print(f"✗ (failed)")
    
    # Compute rates
    if total_metrics.ast_call_nodes > 0:
        total_metrics.ir_conversion_rate = 100.0 * total_metrics.ir_call_instructions / total_metrics.ast_call_nodes
    
    if total_metrics.ir_call_instructions > 0:
        total_metrics.event_extraction_rate = 100.0 * total_metrics.call_events_generated / total_metrics.ir_call_instructions
    
    if total_metrics.call_events_generated > 0:
        total_metrics.resolution_success_rate = 100.0 * total_metrics.resolved_calls / total_metrics.call_events_generated
    
    if total_metrics.resolved_calls > 0:
        total_metrics.edge_creation_rate = 100.0 * total_metrics.call_edges_created / total_metrics.resolved_calls
    
    if total_metrics.ast_call_nodes > 0:
        total_metrics.overall_conversion_rate = 100.0 * total_metrics.call_edges_created / total_metrics.ast_call_nodes
    
    # Identify bottleneck (stage with largest loss)
    losses = {
        'ir_generation': total_metrics.ast_call_nodes - total_metrics.ir_call_instructions,
        'event_extraction': total_metrics.ir_call_instructions - total_metrics.call_events_generated,
        'call_resolution': total_metrics.call_events_generated - total_metrics.resolved_calls,
        'edge_creation': total_metrics.resolved_calls - total_metrics.call_edges_created
    }
    
    total_metrics.bottleneck_stage = max(losses, key=losses.get)
    total_metrics.primary_loss_stage = total_metrics.bottleneck_stage
    total_metrics.primary_loss_amount = losses[total_metrics.bottleneck_stage]
    if total_metrics.ast_call_nodes > 0:
        total_metrics.primary_loss_percentage = 100.0 * total_metrics.primary_loss_amount / total_metrics.ast_call_nodes
    
    # Aggregate unresolved callees
    all_unresolved = Counter()
    all_reasons = Counter()
    for instr_data in all_instrumentation_data:
        all_unresolved.update(instr_data.unresolved_callees)
        all_reasons.update(instr_data.failure_reasons)
    
    total_metrics.unresolved_callees = dict(all_unresolved.most_common(50))
    total_metrics.unresolved_reasons = dict(all_reasons)
    
    return total_metrics


def print_pipeline_report(metrics: PipelineFunnelMetrics, project_name: str):
    """Print human-readable pipeline report."""
    print(f"\n{'='*70}")
    print(f"PIPELINE FUNNEL REPORT: {project_name}")
    print(f"{'='*70}\n")
    
    # Funnel visualization
    print("Call Discovery Pipeline:")
    print(f"  AST Call Nodes:        {metrics.ast_call_nodes:>6}  (100.0%)")
    print(f"    ↓ IR Generation")
    print(f"  IRCall Instructions:   {metrics.ir_call_instructions:>6}  ({metrics.ir_conversion_rate:>5.1f}%)")
    print(f"    ↓ Event Extraction")
    print(f"  CallEvent Objects:     {metrics.call_events_generated:>6}  ({metrics.event_extraction_rate:>5.1f}%)")
    print(f"    ↓ Call Resolution")
    print(f"  Resolved Calls:        {metrics.resolved_calls:>6}  ({metrics.resolution_success_rate:>5.1f}%)")
    print(f"    ↓ Edge Creation")
    print(f"  Call Edges:            {metrics.call_edges_created:>6}  ({metrics.edge_creation_rate:>5.1f}%)")
    print(f"\n  OVERALL CONVERSION:    {metrics.overall_conversion_rate:.1f}%")
    print(f"  TOTAL LOSS:            {metrics.ast_call_nodes - metrics.call_edges_created:>6}  ({100-metrics.overall_conversion_rate:.1f}%)")
    
    print(f"\n{'='*70}")
    print("BOTTLENECK ANALYSIS")
    print(f"{'='*70}\n")
    print(f"Primary Bottleneck:     {metrics.primary_loss_stage}")
    print(f"Loss at this stage:     {metrics.primary_loss_amount} calls ({metrics.primary_loss_percentage:.1f}%)")
    
    if metrics.unresolved_callees:
        print(f"\nTop 20 Unresolved Callees:")
        for i, (callee, count) in enumerate(list(metrics.unresolved_callees.items())[:20], 1):
            print(f"  {i:2}. {callee:<30} ({count} calls)")
    
    if metrics.unresolved_reasons:
        print(f"\nUnresolved Reasons:")
        for reason, count in metrics.unresolved_reasons.items():
            print(f"  - {reason}: {count}")
    
    print()


def main():
    import argparse
    
    parser = argparse.ArgumentParser(
        description="Deep pipeline diagnostic for call edge discovery"
    )
    parser.add_argument("project_path", type=Path, help="Path to project source directory")
    parser.add_argument("project_name", help="Project name for reporting")
    parser.add_argument("--k", type=int, default=2, help="k-CFA depth (default: 2)")
    parser.add_argument("--max-modules", type=int, help="Max modules to analyze")
    parser.add_argument("--verbose", action="store_true", help="Verbose output")
    parser.add_argument("--output", type=Path, help="Output JSON file")
    
    args = parser.parse_args()
    
    config = KCFAConfig(
        k=args.k,
        obj_depth=2,
        field_sensitivity_mode="attr-name",
        build_class_hierarchy=True,
        use_mro=True,
        verbose=args.verbose
    )
    
    metrics = analyze_project_pipeline(
        args.project_path,
        args.project_name,
        config,
        max_modules=args.max_modules,
        verbose=args.verbose
    )
    
    print_pipeline_report(metrics, args.project_name)
    
    if args.output:
        output_data = {
            'project_name': args.project_name,
            'timestamp': datetime.now().isoformat(),
            'metrics': asdict(metrics)
        }
        args.output.write_text(json.dumps(output_data, indent=2))
        print(f"\nResults saved to: {args.output}")


if __name__ == "__main__":
    main()

