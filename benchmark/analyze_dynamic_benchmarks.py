#!/usr/bin/env python3
"""
Dynamic feature benchmark runner for KCFA vs KCFA+AI vs AI-only comparison.

This script:
1. Discovers samples under benchmark/ai/*/sample_*.py with matching sample_*.json
2. Runs three analysis methods per sample:
   - KCFA: pointer analysis with enable_ai_intrinsics=False
   - KCFA+AI: pointer analysis with enable_ai_intrinsics=True
   - AI-only: AI analysis without KCFA (if available)
3. Extracts predictions and normalizes them
4. Computes TP/FP/FN metrics for calls, variables, and fields
5. Outputs JSON + Markdown reports
"""

import argparse
import json
import os
import re
import sys
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, FrozenSet, List, Optional, Set, Tuple

# Add project root to path
PROJECT_ROOT = Path(__file__).parent.parent.absolute()
sys.path.insert(0, str(PROJECT_ROOT))

from pythonstan.world.pipeline import Pipeline
from pythonstan.world import World


# =============================================================================
# Normalization helpers
# =============================================================================

def normalize_qualname(qualname: str, entry_module_qualname: str) -> str:
    """Rewrite entry module qualnames to __main__ for stable comparison."""
    if qualname == entry_module_qualname:
        return "__main__"
    if qualname.startswith(entry_module_qualname + "."):
        suffix = qualname[len(entry_module_qualname) + 1:]
        return f"__main__.{suffix}"
    return qualname


_LOCALS_SEGMENT_RE = re.compile(r"\.<locals>\.")


def normalize_benchmark_qualname(qualname: str) -> str:
    """Normalize benchmark qualnames to align with KCFA naming."""
    return _LOCALS_SEGMENT_RE.sub(".", qualname)


def normalize_benchmark_object_id(obj_id: str) -> str:
    """Normalize object IDs that embed qualnames in expectations/predictions."""
    if ":" not in obj_id:
        return normalize_benchmark_qualname(obj_id)
    prefix, rest = obj_id.split(":", 1)
    if prefix in {"function", "method", "class", "module", "instance"}:
        return f"{prefix}:{normalize_benchmark_qualname(rest)}"
    return obj_id


def extract_call_expr(call_site_str: str) -> str:
    """Extract call expression from KCFA call_site string.
    
    Format: "scope:line:col:stmt#idx" -> extract stmt part
    Example: "__main__:5:4:r = f()#0" -> "f()"
    """
    # Split by colon but be careful about assignment expressions
    parts = call_site_str.split(":")
    if len(parts) >= 4:
        # Rejoin everything after line:col as the statement
        stmt = ":".join(parts[3:])
        # If it's an assignment, extract the RHS call
        if " = " in stmt:
            stmt = stmt.split(" = ", 1)[1]
        stmt = stmt.strip()
        return re.sub(r"#\d+\s*$", "", stmt)
    return re.sub(r"#\d+\s*$", "", call_site_str.strip())


def normalize_object_id(obj: Any, entry_module_qualname: str) -> str:
    """Normalize abstract object to stable ID string."""
    from pythonstan.analysis.pointer.kcfa.object import (
        InstanceObject, ClassObject, FunctionObject, MethodObject,
        BuiltinFunctionObject, BuiltinClassObject, BuiltinInstanceObject,
        BuiltinMethodObject, ModuleObject, ConstantObject,
        ListObject, DictObject, TupleObject, SetObject,
    )
    
    if isinstance(obj, InstanceObject):
        class_qualname = obj.class_obj.ir.get_qualname() if hasattr(obj.class_obj, 'ir') else str(obj.class_obj)
        return f"instance:{normalize_qualname(class_qualname, entry_module_qualname)}"
    elif isinstance(obj, MethodObject):
        method_qualname = obj.ir.get_qualname() if hasattr(obj, 'ir') else str(obj)
        return f"method:{normalize_qualname(method_qualname, entry_module_qualname)}"
    elif isinstance(obj, FunctionObject):
        func_qualname = obj.ir.get_qualname() if hasattr(obj, 'ir') else str(obj)
        return f"function:{normalize_qualname(func_qualname, entry_module_qualname)}"
    elif isinstance(obj, ClassObject):
        class_qualname = obj.ir.get_qualname() if hasattr(obj, 'ir') else str(obj)
        return f"class:{normalize_qualname(class_qualname, entry_module_qualname)}"
    elif isinstance(obj, BuiltinFunctionObject):
        return f"builtin:{obj.function_name}"
    elif isinstance(obj, BuiltinClassObject):
        return f"builtin_class:{obj.builtin_name}"
    elif isinstance(obj, BuiltinInstanceObject):
        return f"builtin_instance:{obj.builtin_type}"
    elif isinstance(obj, BuiltinMethodObject):
        return f"builtin_method:{obj.method_name}"
    elif isinstance(obj, ModuleObject):
        mod_qualname = obj.ir.get_qualname() if hasattr(obj, 'ir') else str(obj)
        return f"module:{normalize_qualname(mod_qualname, entry_module_qualname)}"
    elif isinstance(obj, ConstantObject):
        return f"constant:{type(obj.value).__name__}:{repr(obj.value)[:50]}"
    elif isinstance(obj, ListObject):
        return f"list:{id(obj.alloc_site)}"
    elif isinstance(obj, DictObject):
        return f"dict:{id(obj.alloc_site)}"
    elif isinstance(obj, TupleObject):
        return f"tuple:{id(obj.alloc_site)}"
    elif isinstance(obj, SetObject):
        return f"set:{id(obj.alloc_site)}"
    else:
        return f"unknown:{type(obj).__name__}"


# =============================================================================
# Result extraction
# =============================================================================

@dataclass
class AnalysisPredictions:
    """Extracted predictions from an analysis run."""
    variables: Dict[str, Set[str]] = field(default_factory=lambda: defaultdict(set))
    fields: Dict[str, Dict[str, Set[str]]] = field(default_factory=lambda: defaultdict(lambda: defaultdict(set)))
    calls: Dict[str, Set[str]] = field(default_factory=lambda: defaultdict(set))
    skipped: bool = False
    error: Optional[str] = None


def extract_kcfa_predictions(
    pipeline: Pipeline,
    analysis_name: str,
    entry_module_qualname: str,
) -> AnalysisPredictions:
    """Extract predictions from KCFA analysis results."""
    from pythonstan.analysis.pointer.kcfa.variable import Variable, FieldAccess
    from pythonstan.analysis.pointer.kcfa.heap_model import FieldKind
    from pythonstan.analysis.pointer.kcfa.context import Ctx
    
    preds = AnalysisPredictions()
    
    try:
        result = pipeline.analysis_manager.get_results(analysis_name)
        if result is None:
            preds.error = "No analysis result found"
            return preds
        
        query = result.query()
        state = query._state
        
        # Extract call edges
        call_graph = query.call_graph()
        for edge in call_graph.get_edges():
            callsite = edge.get_callsite()
            callee = edge.get_callee()
            
            # Extract call expression from callsite
            if hasattr(callsite, 'content'):
                call_site_str = str(callsite.content)
            else:
                call_site_str = str(callsite)
            call_expr = extract_call_expr(call_site_str)
            
            # Get callee qualname
            if hasattr(callee, 'name'):
                callee_name = normalize_qualname(callee.name, entry_module_qualname)
            elif hasattr(callee, 'stmt') and hasattr(callee.stmt, 'get_qualname'):
                callee_name = normalize_qualname(callee.stmt.get_qualname(), entry_module_qualname)
            else:
                callee_name = str(callee)
            
            preds.calls[call_expr].add(callee_name)
        
        # Extract variable and field points-to
        for ctx_var, pts in state._env.items():
            if not isinstance(ctx_var, Ctx):
                continue
            
            content = ctx_var.content
            
            if isinstance(content, Variable):
                var_name = content.name
                # Skip internal temporaries
                if var_name.startswith("$"):
                    continue
                for obj in pts:
                    obj_id = normalize_object_id(obj, entry_module_qualname)
                    preds.variables[var_name].add(obj_id)
            
            elif isinstance(content, FieldAccess):
                field_obj = content.field
                if field_obj.kind == FieldKind.ATTRIBUTE and field_obj.name:
                    base_obj = content.obj
                    base_id = normalize_object_id(base_obj, entry_module_qualname)
                    attr_name = field_obj.name
                    for obj in pts:
                        obj_id = normalize_object_id(obj, entry_module_qualname)
                        preds.fields[base_id][attr_name].add(obj_id)
        
    except Exception as e:
        preds.error = str(e)
    
    return preds


def run_ai_only_analysis(sample_path: Path, entry_module_qualname: str) -> AnalysisPredictions:
    """Run AI-only analysis without KCFA.
    
    This executes AI's fixpoint solver over the entry module CFG
    without invoking KCFA's constraint solver.
    """
    preds = AnalysisPredictions()
    preds.skipped = True
    preds.error = "AI-only mode not yet fully implemented - call edge tracking unavailable"
    
    # TODO: Implement AI-only execution path when ready
    # For now, we mark it as skipped but include the method slot
    # so the framework is ready for when AI-only is implemented.
    
    return preds


# =============================================================================
# Pipeline runner
# =============================================================================

def get_library_paths() -> List[str]:
    """Get Python library paths for the current environment."""
    import site
    library_paths = []
    library_paths.append(os.path.dirname(os.__file__))
    for path in site.getsitepackages():
        library_paths.append(path)
    user_site = site.getusersitepackages()
    if user_site:
        library_paths.append(user_site)
    return library_paths


def run_kcfa_analysis(
    sample_path: Path,
    enable_ai_intrinsics: bool,
) -> Tuple[Optional[Pipeline], str]:
    """Run KCFA pointer analysis on a sample file.
    
    Args:
        sample_path: Path to the sample .py file
        enable_ai_intrinsics: Whether to enable AI intrinsics
    
    Returns:
        Tuple of (pipeline, entry_module_qualname)
    """
    # Reset World singleton for fresh analysis
    World._instance = None
    
    config = {
        "filename": str(sample_path),
        "project_path": str(PROJECT_ROOT),
        "library_paths": get_library_paths(),
        "lazy_ir_construction": True,
        "analysis": [
            {
                "name": "pointer_analysis",
                "id": "PointerAnalysis",
                "description": "k-CFA pointer analysis",
                "prev_analysis": ["cfg"],
                "options": {
                    "type": "pointer analysis",
                    "context_policy": "2-cfa",
                    "enable_ai_intrinsics": enable_ai_intrinsics,
                    "verbose": False,
                    "max_iterations": 5000,
                }
            }
        ]
    }
    
    pipeline = Pipeline(config=config)
    pipeline.run()
    
    # Get entry module qualname
    entry_mod = pipeline.get_world().get_entry_module()
    entry_module_qualname = entry_mod.get_qualname() if entry_mod else ""
    
    return pipeline, entry_module_qualname


# =============================================================================
# Metrics computation
# =============================================================================

@dataclass
class Metrics:
    """Precision/recall metrics for a single category."""
    tp: int = 0
    fp: int = 0
    fn: int = 0
    
    @property
    def precision(self) -> float:
        if self.tp + self.fp == 0:
            return 1.0
        return self.tp / (self.tp + self.fp)
    
    @property
    def recall(self) -> float:
        if self.tp + self.fn == 0:
            return 1.0
        return self.tp / (self.tp + self.fn)
    
    @property
    def fdr(self) -> float:
        """False discovery rate = FP / (TP + FP)"""
        if self.tp + self.fp == 0:
            return 0.0
        return self.fp / (self.tp + self.fp)
    
    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        if p + r == 0:
            return 0.0
        return 2 * p * r / (p + r)


def compute_metrics(
    predicted: AnalysisPredictions,
    expected: Dict[str, Any],
) -> Dict[str, Metrics]:
    """Compute TP/FP/FN metrics for variables, fields, and calls."""
    metrics = {
        "variables": Metrics(),
        "fields": Metrics(),
        "calls": Metrics(),
    }
    
    if predicted.skipped:
        return metrics
    
    # Variables: items are (var_name, obj_id)
    expected_vars = expected.get("variables", {})
    pred_var_items = set()
    exp_var_items = set()
    
    for var_name, obj_ids in predicted.variables.items():
        for obj_id in obj_ids:
            pred_var_items.add((var_name, normalize_benchmark_object_id(obj_id)))
    
    for var_name, obj_ids in expected_vars.items():
        for obj_id in obj_ids:
            exp_var_items.add((var_name, normalize_benchmark_object_id(obj_id)))
    
    metrics["variables"].tp = len(pred_var_items & exp_var_items)
    metrics["variables"].fp = len(pred_var_items - exp_var_items)
    metrics["variables"].fn = len(exp_var_items - pred_var_items)
    
    # Fields: items are (base_obj_id, attr_name, obj_id)
    expected_fields = expected.get("fields", {})
    pred_field_items = set()
    exp_field_items = set()
    
    for base_id, attrs in predicted.fields.items():
        base_id = normalize_benchmark_object_id(base_id)
        for attr_name, obj_ids in attrs.items():
            for obj_id in obj_ids:
                pred_field_items.add((base_id, attr_name, normalize_benchmark_object_id(obj_id)))
    
    for base_id, attrs in expected_fields.items():
        base_id = normalize_benchmark_object_id(base_id)
        for attr_name, obj_ids in attrs.items():
            for obj_id in obj_ids:
                exp_field_items.add((base_id, attr_name, normalize_benchmark_object_id(obj_id)))
    
    metrics["fields"].tp = len(pred_field_items & exp_field_items)
    metrics["fields"].fp = len(pred_field_items - exp_field_items)
    metrics["fields"].fn = len(exp_field_items - pred_field_items)
    
    # Calls: items are (call_expr, callee_qualname)
    expected_calls = expected.get("calls", {})
    pred_call_items = set()
    exp_call_items = set()
    
    for call_expr, callees in predicted.calls.items():
        for callee in callees:
            pred_call_items.add((call_expr, normalize_benchmark_qualname(callee)))
    
    for call_expr, callees in expected_calls.items():
        for callee in callees:
            exp_call_items.add((call_expr, normalize_benchmark_qualname(callee)))
    
    metrics["calls"].tp = len(pred_call_items & exp_call_items)
    metrics["calls"].fp = len(pred_call_items - exp_call_items)
    metrics["calls"].fn = len(exp_call_items - pred_call_items)
    
    return metrics


# =============================================================================
# Sample discovery
# =============================================================================

@dataclass
class Sample:
    """A discovered benchmark sample."""
    py_path: Path
    json_path: Path
    feature: str
    sample_id: int
    
    @property
    def name(self) -> str:
        return f"{self.feature}/sample_{self.sample_id}"


def discover_samples(benchmark_dir: Path) -> List[Sample]:
    """Discover all samples under benchmark/ai/*/sample_*.py with matching JSON."""
    samples = []
    ai_dir = benchmark_dir / "ai"
    
    if not ai_dir.exists():
        return samples
    
    for feature_dir in sorted(ai_dir.iterdir()):
        if not feature_dir.is_dir():
            continue
        
        feature = feature_dir.name
        
        for py_file in sorted(feature_dir.glob("sample_*.py")):
            # Extract sample ID from filename
            match = re.match(r"sample_(\d+)\.py", py_file.name)
            if not match:
                continue
            
            sample_id = int(match.group(1))
            json_file = feature_dir / f"sample_{sample_id}.json"
            
            if json_file.exists():
                samples.append(Sample(
                    py_path=py_file,
                    json_path=json_file,
                    feature=feature,
                    sample_id=sample_id,
                ))
            else:
                print(f"Warning: No JSON file for {py_file}")
    
    return samples


# =============================================================================
# Report generation
# =============================================================================

def generate_markdown_report(
    results: List[Dict[str, Any]],
    output_path: Path,
) -> None:
    """Generate Markdown report with comparison tables."""
    lines = [
        "# Dynamic Benchmark Results",
        "",
        f"Generated: {time.strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        "## Overall Summary",
        "",
        "| Method | Calls P | Calls R | Calls F1 | Vars P | Vars R | Fields P | Fields R |",
        "|--------|---------|---------|----------|--------|--------|----------|----------|",
    ]
    
    # Aggregate metrics by method
    method_totals = defaultdict(lambda: {"calls": Metrics(), "variables": Metrics(), "fields": Metrics()})
    
    for result in results:
        for method, method_result in result.get("methods", {}).items():
            if method_result.get("skipped"):
                continue
            metrics = method_result.get("metrics", {})
            for category in ["calls", "variables", "fields"]:
                cat_metrics = metrics.get(category, {})
                method_totals[method][category].tp += cat_metrics.get("tp", 0)
                method_totals[method][category].fp += cat_metrics.get("fp", 0)
                method_totals[method][category].fn += cat_metrics.get("fn", 0)
    
    for method in ["KCFA", "KCFA+AI", "AI-only"]:
        if method not in method_totals:
            continue
        totals = method_totals[method]
        lines.append(
            f"| {method} | "
            f"{totals['calls'].precision:.2%} | {totals['calls'].recall:.2%} | {totals['calls'].f1:.2%} | "
            f"{totals['variables'].precision:.2%} | {totals['variables'].recall:.2%} | "
            f"{totals['fields'].precision:.2%} | {totals['fields'].recall:.2%} |"
        )
    
    # Per-feature breakdown
    lines.extend([
        "",
        "## Per-Feature Breakdown",
        "",
    ])
    
    # Group results by feature
    by_feature = defaultdict(list)
    for result in results:
        by_feature[result["feature"]].append(result)
    
    for feature, feature_results in sorted(by_feature.items()):
        lines.extend([
            f"### {feature}",
            "",
            "| Sample | Method | Calls P/R/F1 | Vars P/R | Fields P/R | Notes |",
            "|--------|--------|--------------|----------|------------|-------|",
        ])
        
        for result in feature_results:
            sample_name = f"sample_{result['sample_id']}"
            for method, method_result in result.get("methods", {}).items():
                if method_result.get("skipped"):
                    notes = method_result.get("error", "skipped")[:30]
                    lines.append(f"| {sample_name} | {method} | - | - | - | {notes} |")
                else:
                    metrics = method_result.get("metrics", {})
                    calls = metrics.get("calls", {})
                    vars_m = metrics.get("variables", {})
                    fields = metrics.get("fields", {})
                    
                    calls_str = f"{calls.get('precision', 0):.0%}/{calls.get('recall', 0):.0%}/{calls.get('f1', 0):.0%}"
                    vars_str = f"{vars_m.get('precision', 0):.0%}/{vars_m.get('recall', 0):.0%}"
                    fields_str = f"{fields.get('precision', 0):.0%}/{fields.get('recall', 0):.0%}"
                    notes = method_result.get("error", "")[:30] if method_result.get("error") else ""
                    
                    lines.append(f"| {sample_name} | {method} | {calls_str} | {vars_str} | {fields_str} | {notes} |")
        
        lines.append("")
    
    # Per-sample details
    lines.extend([
        "## Per-Sample Details",
        "",
    ])
    
    for result in results:
        lines.extend([
            f"### {result['feature']}/sample_{result['sample_id']}",
            "",
            f"**Description**: {result.get('description', 'N/A')}",
            "",
        ])
        
        for method, method_result in result.get("methods", {}).items():
            lines.append(f"**{method}**:")
            if method_result.get("skipped"):
                lines.append(f"  - Skipped: {method_result.get('error', 'unknown reason')}")
            elif method_result.get("error"):
                lines.append(f"  - Error: {method_result.get('error')}")
            else:
                metrics = method_result.get("metrics", {})
                for cat in ["calls", "variables", "fields"]:
                    m = metrics.get(cat, {})
                    lines.append(f"  - {cat}: TP={m.get('tp', 0)}, FP={m.get('fp', 0)}, FN={m.get('fn', 0)}")
            lines.append("")
    
    with open(output_path, "w") as f:
        f.write("\n".join(lines))
    
    print(f"Markdown report written to {output_path}")


def generate_json_report(
    results: List[Dict[str, Any]],
    output_path: Path,
) -> None:
    """Generate JSON report with all results."""
    report = {
        "generated": time.strftime('%Y-%m-%d %H:%M:%S'),
        "samples": results,
        "summary": {},
    }
    
    # Aggregate metrics
    method_totals = defaultdict(lambda: {"calls": {"tp": 0, "fp": 0, "fn": 0}, 
                                          "variables": {"tp": 0, "fp": 0, "fn": 0},
                                          "fields": {"tp": 0, "fp": 0, "fn": 0}})
    
    for result in results:
        for method, method_result in result.get("methods", {}).items():
            if method_result.get("skipped"):
                continue
            metrics = method_result.get("metrics", {})
            for category in ["calls", "variables", "fields"]:
                cat_metrics = metrics.get(category, {})
                method_totals[method][category]["tp"] += cat_metrics.get("tp", 0)
                method_totals[method][category]["fp"] += cat_metrics.get("fp", 0)
                method_totals[method][category]["fn"] += cat_metrics.get("fn", 0)
    
    report["summary"] = dict(method_totals)
    
    with open(output_path, "w") as f:
        json.dump(report, f, indent=2)
    
    print(f"JSON report written to {output_path}")


# =============================================================================
# Main runner
# =============================================================================

def run_sample(sample: Sample, verbose: bool = False) -> Dict[str, Any]:
    """Run all analysis methods on a single sample."""
    result = {
        "feature": sample.feature,
        "sample_id": sample.sample_id,
        "py_path": str(sample.py_path),
        "json_path": str(sample.json_path),
        "methods": {},
    }
    
    # Load expected results
    with open(sample.json_path) as f:
        expected_data = json.load(f)
    
    result["description"] = expected_data.get("meta", {}).get("description", "")
    expected = expected_data.get("expect", {})
    
    # Run KCFA (without AI intrinsics)
    if verbose:
        print(f"  Running KCFA on {sample.name}...")
    
    try:
        pipeline, entry_qualname = run_kcfa_analysis(sample.py_path, enable_ai_intrinsics=False)
        preds = extract_kcfa_predictions(pipeline, "pointer_analysis", entry_qualname)
        metrics = compute_metrics(preds, expected)
        
        result["methods"]["KCFA"] = {
            "skipped": preds.skipped,
            "error": preds.error,
            "metrics": {cat: {"tp": m.tp, "fp": m.fp, "fn": m.fn,
                              "precision": m.precision, "recall": m.recall, "f1": m.f1, "fdr": m.fdr}
                        for cat, m in metrics.items()},
        }
    except Exception as e:
        result["methods"]["KCFA"] = {"skipped": True, "error": str(e)}
    
    # Run KCFA+AI (with AI intrinsics)
    if verbose:
        print(f"  Running KCFA+AI on {sample.name}...")
    
    try:
        pipeline, entry_qualname = run_kcfa_analysis(sample.py_path, enable_ai_intrinsics=True)
        preds = extract_kcfa_predictions(pipeline, "pointer_analysis", entry_qualname)
        metrics = compute_metrics(preds, expected)
        
        result["methods"]["KCFA+AI"] = {
            "skipped": preds.skipped,
            "error": preds.error,
            "metrics": {cat: {"tp": m.tp, "fp": m.fp, "fn": m.fn,
                              "precision": m.precision, "recall": m.recall, "f1": m.f1, "fdr": m.fdr}
                        for cat, m in metrics.items()},
        }
    except Exception as e:
        result["methods"]["KCFA+AI"] = {"skipped": True, "error": str(e)}
    
    # Run AI-only
    if verbose:
        print(f"  Running AI-only on {sample.name}...")
    
    try:
        preds = run_ai_only_analysis(sample.py_path, entry_qualname if 'entry_qualname' in dir() else "")
        if preds.skipped:
            result["methods"]["AI-only"] = {"skipped": True, "error": preds.error}
        else:
            metrics = compute_metrics(preds, expected)
            result["methods"]["AI-only"] = {
                "skipped": False,
                "error": preds.error,
                "metrics": {cat: {"tp": m.tp, "fp": m.fp, "fn": m.fn,
                                  "precision": m.precision, "recall": m.recall, "f1": m.f1, "fdr": m.fdr}
                            for cat, m in metrics.items()},
            }
    except Exception as e:
        result["methods"]["AI-only"] = {"skipped": True, "error": str(e)}
    
    return result


def main():
    parser = argparse.ArgumentParser(description="Run dynamic feature benchmarks")
    parser.add_argument("--feature", "-f", help="Run only samples from this feature")
    parser.add_argument("--sample", "-s", type=int, help="Run only this sample ID")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose output")
    parser.add_argument("--output-dir", "-o", default="benchmark/reports/dynamic",
                        help="Output directory for reports")
    args = parser.parse_args()
    
    benchmark_dir = PROJECT_ROOT / "benchmark"
    output_dir = PROJECT_ROOT / args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # Discover samples
    samples = discover_samples(benchmark_dir)
    
    if args.feature:
        samples = [s for s in samples if s.feature == args.feature]
    if args.sample:
        samples = [s for s in samples if s.sample_id == args.sample]
    
    if not samples:
        print("No samples found!")
        return 1
    
    print(f"Found {len(samples)} samples")
    
    # Run all samples
    results = []
    for sample in samples:
        print(f"Processing {sample.name}...")
        try:
            result = run_sample(sample, verbose=args.verbose)
            results.append(result)
        except Exception as e:
            print(f"  Error: {e}")
            results.append({
                "feature": sample.feature,
                "sample_id": sample.sample_id,
                "error": str(e),
                "methods": {},
            })
    
    # Generate reports
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    json_path = output_dir / f"report_{timestamp}.json"
    md_path = output_dir / f"report_{timestamp}.md"
    
    generate_json_report(results, json_path)
    generate_markdown_report(results, md_path)
    
    # Also write latest versions
    generate_json_report(results, output_dir / "report_latest.json")
    generate_markdown_report(results, output_dir / "report_latest.md")
    
    print("\nDone!")
    return 0


if __name__ == "__main__":
    sys.exit(main())

