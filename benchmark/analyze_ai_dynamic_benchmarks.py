#!/usr/bin/env python3
"""
AI dynamic feature benchmark runner.

This script:
1. Discovers samples under benchmark/ai/*/sample_*.py with matching sample_*.json
2. Runs KCFA (baseline), KCFA+AI intrinsics (optional), and AI-Driver
3. Extracts predictions and computes TP/FP/FN metrics
4. Outputs JSON + Markdown reports

Notes:
- AI-Driver uses KCFA results as its points-to query source.
- Variable predictions for AI-Driver default to KCFA variables unless disabled.
- Field predictions currently use KCFA as baseline since AI module-level analysis is incomplete.
- The AI-Driver demonstrates infrastructure readiness; further AI development needed for differentiation.
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
from pythonstan.analysis import AnalysisConfig
from pythonstan.analysis.pointer.ai.analysis import AIAnalysisDriver


# =============================================================================
# Normalization helpers
# =============================================================================

# Standard Python builtins that should be filtered out
PYTHON_BUILTINS = {
    "abs", "all", "any", "ascii", "bin", "bool", "bytearray", "bytes",
    "callable", "chr", "classmethod", "compile", "complex", "delattr",
    "dict", "dir", "divmod", "enumerate", "eval", "exec", "filter",
    "float", "format", "frozenset", "getattr", "globals", "hasattr",
    "hash", "help", "hex", "id", "input", "int", "isinstance",
    "issubclass", "iter", "len", "list", "locals", "map", "max",
    "memoryview", "min", "next", "object", "oct", "open", "ord", "pow",
    "print", "property", "range", "repr", "reversed", "round", "set",
    "setattr", "slice", "sorted", "staticmethod", "str", "sum", "super",
    "tuple", "type", "vars", "zip", "__import__", "__name__", "__doc__",
    "__package__", "__loader__", "__spec__", "__builtins__", "__file__",
    "__cached__",
}


def normalize_qualname(qualname: str, entry_module_qualname: str) -> str:
    """Rewrite entry module qualnames to __main__ for stable comparison."""
    if qualname == entry_module_qualname:
        return "__main__"
    if qualname.startswith(entry_module_qualname + "."):
        suffix = qualname[len(entry_module_qualname) + 1:]
        # Convert nested function syntax: parent.child -> parent.<locals>.child
        # but only for the last segment if it looks like a nested function
        parts = suffix.split(".")
        if len(parts) >= 2:
            # Check if the second-to-last part looks like a function (lowercase start)
            # and last part looks like a nested function (lowercase start)
            if parts[-2] and parts[-2][0].islower() and parts[-1] and parts[-1][0].islower():
                parts.insert(-1, "<locals>")
                suffix = ".".join(parts)
        return f"__main__.{suffix}"
    return qualname


def extract_call_expr(call_site_str: str) -> str:
    """Extract call expression from KCFA call_site string."""
    parts = call_site_str.split(":")
    if len(parts) >= 4:
        stmt = ":".join(parts[3:])
        if " = " in stmt:
            stmt = stmt.split(" = ", 1)[1]
        return stmt.strip()
    return call_site_str


def _stable_alloc_site_id(obj: Any, entry_module_qualname: str) -> str:
    """Build a stable ID from an object's alloc_site."""
    alloc_site = getattr(obj, "alloc_site", None)
    if alloc_site is None:
        return "unknown"
    stmt = getattr(alloc_site, "stmt", None)
    if stmt is None:
        return "unknown"
    # Use statement string representation and normalize it
    stmt_str = str(stmt)
    # For IR statements, try to get line/col info
    if hasattr(stmt, "get_ast"):
        ast_node = stmt.get_ast()
        if ast_node is not None:
            line = getattr(ast_node, "lineno", 0)
            col = getattr(ast_node, "col_offset", 0)
            stmt_str = f"L{line}C{col}"
    return stmt_str


def normalize_object_id(obj: Any, entry_module_qualname: str) -> str:
    """Normalize abstract object to stable ID string."""
    from pythonstan.analysis.pointer.kcfa.object import (
        InstanceObject, ClassObject, FunctionObject, MethodObject,
        BuiltinFunctionObject, BuiltinClassObject, BuiltinInstanceObject,
        BuiltinMethodObject, ModuleObject, ConstantObject,
        ListObject, DictObject, TupleObject, SetObject,
    )

    if isinstance(obj, InstanceObject):
        class_qualname = obj.class_obj.ir.get_qualname() if hasattr(obj.class_obj, "ir") else str(obj.class_obj)
        return f"instance:{normalize_qualname(class_qualname, entry_module_qualname)}"
    if isinstance(obj, MethodObject):
        method_qualname = obj.ir.get_qualname() if hasattr(obj, "ir") else str(obj)
        return f"method:{normalize_qualname(method_qualname, entry_module_qualname)}"
    if isinstance(obj, FunctionObject):
        func_qualname = obj.ir.get_qualname() if hasattr(obj, "ir") else str(obj)
        return f"function:{normalize_qualname(func_qualname, entry_module_qualname)}"
    if isinstance(obj, ClassObject):
        class_qualname = obj.ir.get_qualname() if hasattr(obj, "ir") else str(obj)
        return f"class:{normalize_qualname(class_qualname, entry_module_qualname)}"
    if isinstance(obj, BuiltinFunctionObject):
        return f"builtin:{obj.function_name}"
    if isinstance(obj, BuiltinClassObject):
        return f"builtin_class:{obj.builtin_name}"
    if isinstance(obj, BuiltinInstanceObject):
        return f"builtin_instance:{obj.builtin_type}"
    if isinstance(obj, BuiltinMethodObject):
        return f"builtin_method:{obj.method_name}"
    if isinstance(obj, ModuleObject):
        mod_qualname = obj.ir.get_qualname() if hasattr(obj, "ir") else str(obj)
        return f"module:{normalize_qualname(mod_qualname, entry_module_qualname)}"
    if isinstance(obj, ConstantObject):
        return f"constant:{type(obj.value).__name__}:{repr(obj.value)[:50]}"
    if isinstance(obj, ListObject):
        alloc_str = _stable_alloc_site_id(obj, entry_module_qualname)
        return f"list:{alloc_str}"
    if isinstance(obj, DictObject):
        alloc_str = _stable_alloc_site_id(obj, entry_module_qualname)
        return f"dict:{alloc_str}"
    if isinstance(obj, TupleObject):
        alloc_str = _stable_alloc_site_id(obj, entry_module_qualname)
        return f"tuple:{alloc_str}"
    if isinstance(obj, SetObject):
        alloc_str = _stable_alloc_site_id(obj, entry_module_qualname)
        return f"set:{alloc_str}"
    return f"unknown:{type(obj).__name__}"


def normalize_callee_name(obj: Any, entry_module_qualname: str) -> str:
    if hasattr(obj, "ir") and hasattr(obj.ir, "get_qualname"):
        return normalize_qualname(obj.ir.get_qualname(), entry_module_qualname)
    if hasattr(obj, "get_qualname"):
        return normalize_qualname(obj.get_qualname(), entry_module_qualname)
    if hasattr(obj, "name"):
        return normalize_qualname(obj.name, entry_module_qualname)
    return str(obj)


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

            if hasattr(callsite, "content"):
                call_site_str = str(callsite.content)
            else:
                call_site_str = str(callsite)
            call_expr = extract_call_expr(call_site_str)

            if hasattr(callee, "name"):
                callee_name = normalize_qualname(callee.name, entry_module_qualname)
            elif hasattr(callee, "stmt") and hasattr(callee.stmt, "get_qualname"):
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
                # Skip temporary variables and builtins
                if var_name.startswith("$") or var_name in PYTHON_BUILTINS:
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


def _collect_callsite_map(scope_ir) -> Dict[str, str]:
    """Build callsite_id -> call_expr map from block cfg."""
    from pythonstan.ir.ir_statements import IRCall

    callsite_map: Dict[str, str] = {}
    scope_qualname = scope_ir.get_qualname()
    cfg = World().scope_manager.get_ir(scope_ir, "block cfg")
    if cfg is None:
        return callsite_map

    blocks = list(getattr(cfg, "blks", []) or [])
    entry_blk = getattr(cfg, "entry_blk", None)
    if entry_blk is not None and entry_blk not in blocks:
        blocks.append(entry_blk)

    for block in blocks:
        stmts = list(getattr(block, "stmts", []) or [])
        for idx, stmt in enumerate(stmts):
            if not isinstance(stmt, IRCall):
                continue
            callsite_id = f"{scope_qualname}:blk{block.idx}:stmt{idx}"
            call_expr = str(stmt)
            if " = " in call_expr:
                call_expr = call_expr.split(" = ", 1)[1].strip()
            callsite_map[callsite_id] = call_expr

    return callsite_map


def extract_ai_predictions(
    pipeline: Pipeline,
    ai_result: Any,
    entry_module_qualname: str,
    analysis_name: str,
    kcfa_vars: bool = True,
    kcfa_fields: bool = True,
) -> AnalysisPredictions:
    """Extract predictions from AI-Driver results."""
    from pythonstan.analysis.pointer.kcfa.heap_model import FieldKind

    preds = AnalysisPredictions()

    try:
        if ai_result is None:
            preds.error = "No AI analysis result found"
            return preds

        # Start with KCFA baseline for variables (AI may not cover all module-level vars)
        if kcfa_vars:
            kcfa_preds = extract_kcfa_predictions(pipeline, analysis_name, entry_module_qualname)
            preds.variables = dict(kcfa_preds.variables)  # Copy so we can update
            preds.calls = dict(kcfa_preds.calls)  # Copy so we can update
        else:
            preds.variables = defaultdict(set)
            preds.calls = defaultdict(set)

        # Use KCFA fields as baseline (AI module-level analysis is limited)
        if kcfa_fields:
            if not kcfa_vars:
                kcfa_preds = extract_kcfa_predictions(pipeline, analysis_name, entry_module_qualname)
            preds.fields = dict(kcfa_preds.fields)
        else:
            preds.fields = defaultdict(lambda: defaultdict(set))

        # Build per-scope callsite maps
        callsite_maps: Dict[str, Dict[str, str]] = {}
        for scope_qualname in ai_result.summaries.keys():
            scope_ir = World().scope_manager.get_module(scope_qualname)
            if scope_ir is None:
                continue
            callsite_maps[scope_qualname] = _collect_callsite_map(scope_ir)

        # Extract AI-discovered calls from summaries
        for scope_qualname, summary in ai_result.summaries.items():
            call_map = callsite_maps.get(scope_qualname, {})
            for callsite_id, callee_addrs in summary.calls:
                call_expr = call_map.get(callsite_id, callsite_id)
                for callee_obj in callee_addrs:
                    callee_name = normalize_callee_name(callee_obj, entry_module_qualname)
                    preds.calls[call_expr].add(callee_name)

        # Extract AI return values (contribute to variable predictions)
        for scope_qualname, summary in ai_result.summaries.items():
            if not summary.ret:
                continue
            # Map return values to the calling context's target variable
            # This is approximate since we don't track exact call sites here
            # For benchmarks, we'll rely on the AI summaries being precise

        # Extract AI field writes
        for scope_qualname, summary in ai_result.summaries.items():
            for write in summary.writes:
                field_key = write.field_key
                if field_key.kind != FieldKind.ATTRIBUTE or not field_key.name:
                    continue
                attr_name = field_key.name
                for obj in write.obj_addrs:
                    base_id = normalize_object_id(obj, entry_module_qualname)
                    for val in write.value_addrs:
                        obj_id = normalize_object_id(val, entry_module_qualname)
                        preds.fields[base_id][attr_name].add(obj_id)

    except Exception as e:
        preds.error = str(e)

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
    max_iterations: int,
) -> Tuple[Optional[Pipeline], str]:
    """Run KCFA pointer analysis on a sample file."""
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
                    "max_iterations": max_iterations,
                },
            }
        ],
    }

    pipeline = Pipeline(config=config)
    pipeline.run()

    entry_mod = pipeline.get_world().get_entry_module()
    entry_module_qualname = entry_mod.get_qualname() if entry_mod else ""

    return pipeline, entry_module_qualname


def collect_plan_scopes(entry_module) -> List[str]:
    """Collect explicit qualnames for entry module + all subscopes."""
    scope_manager = World().scope_manager
    seen = set()
    ordered: List[str] = []
    queue = [entry_module]

    while queue:
        scope = queue.pop(0)
        if scope in seen:
            continue
        seen.add(scope)
        ordered.append(scope.get_qualname())
        for sub in scope_manager.get_subscopes(scope):
            queue.append(sub)

    return ordered


def run_ai_driver_analysis(
    pipeline: Pipeline,
    analysis_name: str,
    plan_scopes: List[str],
    ai_budget: Dict[str, Any],
) -> Any:
    entry_mod = pipeline.get_world().get_entry_module()
    if entry_mod is None:
        return None

    options = {
        "type": "ai analysis",
        "pta_analysis_name": analysis_name,
        "plan_scopes": plan_scopes,
        **ai_budget,
    }
    ai_cfg = AnalysisConfig(
        name="ai_analysis",
        id="AIAnalysisDriver",
        description="AI analysis driver",
        prev_analysis=[analysis_name],
        options=options,
    )
    driver = AIAnalysisDriver(ai_cfg)
    prev_results = {analysis_name: pipeline.analysis_manager.get_results(analysis_name)}
    return driver.analyze(entry_mod, prev_results)


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
    metrics = {
        "variables": Metrics(),
        "fields": Metrics(),
        "calls": Metrics(),
    }

    if predicted.skipped:
        return metrics

    expected_vars = expected.get("variables", {})
    pred_var_items = set()
    exp_var_items = set()

    for var_name, obj_ids in predicted.variables.items():
        for obj_id in obj_ids:
            pred_var_items.add((var_name, obj_id))

    for var_name, obj_ids in expected_vars.items():
        for obj_id in obj_ids:
            exp_var_items.add((var_name, obj_id))

    metrics["variables"].tp = len(pred_var_items & exp_var_items)
    metrics["variables"].fp = len(pred_var_items - exp_var_items)
    metrics["variables"].fn = len(exp_var_items - pred_var_items)

    expected_fields = expected.get("fields", {})
    pred_field_items = set()
    exp_field_items = set()

    for base_id, attrs in predicted.fields.items():
        for attr_name, obj_ids in attrs.items():
            for obj_id in obj_ids:
                pred_field_items.add((base_id, attr_name, obj_id))

    for base_id, attrs in expected_fields.items():
        for attr_name, obj_ids in attrs.items():
            for obj_id in obj_ids:
                exp_field_items.add((base_id, attr_name, obj_id))

    metrics["fields"].tp = len(pred_field_items & exp_field_items)
    metrics["fields"].fp = len(pred_field_items - exp_field_items)
    metrics["fields"].fn = len(exp_field_items - pred_field_items)

    expected_calls = expected.get("calls", {})
    pred_call_items = set()
    exp_call_items = set()

    for call_expr, callees in predicted.calls.items():
        for callee in callees:
            pred_call_items.add((call_expr, callee))

    for call_expr, callees in expected_calls.items():
        for callee in callees:
            exp_call_items.add((call_expr, callee))

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
    samples = []
    ai_dir = benchmark_dir / "ai"

    if not ai_dir.exists():
        return samples

    for feature_dir in sorted(ai_dir.iterdir()):
        if not feature_dir.is_dir():
            continue
        feature = feature_dir.name
        for py_file in sorted(feature_dir.glob("sample_*.py")):
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

def generate_markdown_report(results: List[Dict[str, Any]], output_path: Path) -> None:
    lines = [
        "# AI Dynamic Benchmark Results",
        "",
        f"Generated: {time.strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        "## Overall Summary",
        "",
        "| Method | Calls P | Calls R | Calls F1 | Vars P | Vars R | Fields P | Fields R |",
        "|--------|---------|---------|----------|--------|--------|----------|----------|",
    ]

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

    for method in ["KCFA", "KCFA+AI", "AI-Driver"]:
        if method not in method_totals:
            continue
        totals = method_totals[method]
        lines.append(
            f"| {method} | "
            f"{totals['calls'].precision:.2%} | {totals['calls'].recall:.2%} | {totals['calls'].f1:.2%} | "
            f"{totals['variables'].precision:.2%} | {totals['variables'].recall:.2%} | "
            f"{totals['fields'].precision:.2%} | {totals['fields'].recall:.2%} |"
        )

    lines.extend(["", "## Per-Feature Breakdown", ""])
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

    with open(output_path, "w") as f:
        f.write("\n".join(lines))
    print(f"Markdown report written to {output_path}")


def generate_json_report(results: List[Dict[str, Any]], output_path: Path) -> None:
    report = {
        "generated": time.strftime("%Y-%m-%d %H:%M:%S"),
        "samples": results,
        "summary": {},
    }

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
# Sample runner
# =============================================================================

def run_sample(
    sample: Sample,
    verbose: bool,
    include_kcfa_ai: bool,
    ai_budget: Dict[str, Any],
    max_kcfa_iterations: int,
    kcfa_vars_for_ai: bool,
) -> Dict[str, Any]:
    result = {
        "feature": sample.feature,
        "sample_id": sample.sample_id,
        "py_path": str(sample.py_path),
        "json_path": str(sample.json_path),
        "methods": {},
    }

    with open(sample.json_path) as f:
        expected_data = json.load(f)
    result["description"] = expected_data.get("meta", {}).get("description", "")
    expected = expected_data.get("expect", {})

    if verbose:
        print(f"  Running KCFA on {sample.name}...")
    try:
        pipeline, entry_qualname = run_kcfa_analysis(
            sample.py_path,
            enable_ai_intrinsics=False,
            max_iterations=max_kcfa_iterations,
        )
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
        pipeline = None
        entry_qualname = ""

    if include_kcfa_ai:
        if verbose:
            print(f"  Running KCFA+AI on {sample.name}...")
        try:
            pipeline_ai, entry_qualname_ai = run_kcfa_analysis(
                sample.py_path,
                enable_ai_intrinsics=True,
                max_iterations=max_kcfa_iterations,
            )
            preds = extract_kcfa_predictions(pipeline_ai, "pointer_analysis", entry_qualname_ai)
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

    if verbose:
        print(f"  Running AI-Driver on {sample.name}...")
    try:
        # AI-Driver should use KCFA+AI as its baseline PTA (with intrinsics enabled)
        # Re-run KCFA with AI intrinsics to serve as the PTA query source
        pipeline_for_ai, entry_qualname_for_ai = run_kcfa_analysis(
            sample.py_path,
            enable_ai_intrinsics=True,
            max_iterations=max_kcfa_iterations,
        )
        if pipeline_for_ai is None:
            raise RuntimeError("KCFA+AI pipeline unavailable for AI-Driver")
        entry_mod = pipeline_for_ai.get_world().get_entry_module()
        plan_scopes = collect_plan_scopes(entry_mod)
        ai_result = run_ai_driver_analysis(
            pipeline_for_ai,
            analysis_name="pointer_analysis",
            plan_scopes=plan_scopes,
            ai_budget=ai_budget,
        )
        preds = extract_ai_predictions(
            pipeline_for_ai,
            ai_result,
            entry_qualname_for_ai,
            analysis_name="pointer_analysis",
            kcfa_vars=kcfa_vars_for_ai,
            kcfa_fields=True,  # Use KCFA fields as baseline
        )
        metrics = compute_metrics(preds, expected)
        result["methods"]["AI-Driver"] = {
            "skipped": preds.skipped,
            "error": preds.error,
            "metrics": {cat: {"tp": m.tp, "fp": m.fp, "fn": m.fn,
                              "precision": m.precision, "recall": m.recall, "f1": m.f1, "fdr": m.fdr}
                        for cat, m in metrics.items()},
        }
    except Exception as e:
        result["methods"]["AI-Driver"] = {"skipped": True, "error": str(e)}

    return result


def aggregate_method_metrics(results: List[Dict[str, Any]], method: str) -> Dict[str, Metrics]:
    totals = {"calls": Metrics(), "variables": Metrics(), "fields": Metrics()}
    for result in results:
        method_result = result.get("methods", {}).get(method)
        if not method_result or method_result.get("skipped"):
            continue
        metrics = method_result.get("metrics", {})
        for category in ["calls", "variables", "fields"]:
            cat_metrics = metrics.get(category, {})
            totals[category].tp += cat_metrics.get("tp", 0)
            totals[category].fp += cat_metrics.get("fp", 0)
            totals[category].fn += cat_metrics.get("fn", 0)
    return totals


def check_thresholds(results: List[Dict[str, Any]], method: str,
                     min_calls_f1: float, min_vars_f1: float, min_fields_f1: float) -> bool:
    totals = aggregate_method_metrics(results, method)
    calls_ok = totals["calls"].f1 >= min_calls_f1
    vars_ok = totals["variables"].f1 >= min_vars_f1
    fields_ok = totals["fields"].f1 >= min_fields_f1
    return calls_ok and vars_ok and fields_ok


# =============================================================================
# Main runner
# =============================================================================

def main() -> int:
    parser = argparse.ArgumentParser(description="Run AI dynamic feature benchmarks")
    parser.add_argument("--feature", "-f", help="Run only samples from this feature")
    parser.add_argument("--sample", "-s", type=int, help="Run only this sample ID")
    parser.add_argument("--max-samples", type=int, help="Limit number of samples")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose output")
    parser.add_argument("--output-dir", "-o", default="benchmark/reports/ai_dynamic",
                        help="Output directory for reports")
    parser.add_argument("--list-samples", action="store_true", help="List samples and exit")
    parser.add_argument("--dry-run", action="store_true", help="Discover samples but do not run analyses")
    parser.add_argument("--include-kcfa-ai", action="store_true",
                        help="Also run KCFA with AI intrinsics enabled")
    parser.add_argument("--kcfa-max-iterations", type=int, default=5000,
                        help="Max iterations for KCFA solver")
    parser.add_argument("--kcfa-vars-for-ai", action="store_true",
                        help="Use KCFA variable predictions for AI-Driver (default on)")
    parser.add_argument("--no-kcfa-vars-for-ai", dest="kcfa_vars_for_ai", action="store_false",
                        help="Disable KCFA variable fallback for AI-Driver")
    parser.set_defaults(kcfa_vars_for_ai=True)

    # AI budget knobs
    parser.add_argument("--ai-max-iterations", type=int, default=200)
    parser.add_argument("--ai-max-stmt-visits", type=int, default=50)
    parser.add_argument("--ai-max-recursion-depth", type=int, default=4)
    parser.add_argument("--ai-max-addr-set-size", type=int, default=50)
    parser.add_argument("--ai-max-field-keys", type=int, default=20)
    parser.add_argument("--ai-max-const-strings", type=int, default=10)
    parser.add_argument("--ai-enable-narrowing", action="store_true", default=True)
    parser.add_argument("--ai-narrowing-iterations", type=int, default=2)
    parser.add_argument("--ai-enable-value-partition", action="store_true", default=True)
    parser.add_argument("--ai-max-value-partitions", type=int, default=4)
    parser.add_argument("--ai-value-partition-str-cap", type=int, default=4)

    # Thresholds (optional)
    parser.add_argument("--enforce-thresholds", action="store_true",
                        help="Fail if AI-Driver metrics fall below thresholds")
    parser.add_argument("--min-calls-f1", type=float, default=0.0)
    parser.add_argument("--min-vars-f1", type=float, default=0.0)
    parser.add_argument("--min-fields-f1", type=float, default=0.0)

    args = parser.parse_args()

    benchmark_dir = PROJECT_ROOT / "benchmark"
    output_dir = PROJECT_ROOT / args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    samples = discover_samples(benchmark_dir)
    if args.feature:
        samples = [s for s in samples if s.feature == args.feature]
    if args.sample:
        samples = [s for s in samples if s.sample_id == args.sample]
    if args.max_samples:
        samples = samples[:args.max_samples]

    if args.list_samples:
        for s in samples:
            print(s.name)
        return 0

    if not samples:
        print("No samples found!")
        return 1

    if args.dry_run:
        print(f"Discovered {len(samples)} samples (dry-run)")
        return 0

    ai_budget = {
        "max_iterations": args.ai_max_iterations,
        "max_stmt_visits": args.ai_max_stmt_visits,
        "max_recursion_depth": args.ai_max_recursion_depth,
        "max_addr_set_size": args.ai_max_addr_set_size,
        "max_field_keys": args.ai_max_field_keys,
        "max_const_strings": args.ai_max_const_strings,
        "enable_narrowing": args.ai_enable_narrowing,
        "narrowing_iterations": args.ai_narrowing_iterations,
        "enable_value_partition": args.ai_enable_value_partition,
        "max_value_partitions": args.ai_max_value_partitions,
        "value_partition_str_cap": args.ai_value_partition_str_cap,
    }

    results = []
    for sample in samples:
        print(f"Processing {sample.name}...")
        try:
            result = run_sample(
                sample,
                verbose=args.verbose,
                include_kcfa_ai=args.include_kcfa_ai,
                ai_budget=ai_budget,
                max_kcfa_iterations=args.kcfa_max_iterations,
                kcfa_vars_for_ai=args.kcfa_vars_for_ai,
            )
            results.append(result)
        except Exception as e:
            print(f"  Error: {e}")
            results.append({
                "feature": sample.feature,
                "sample_id": sample.sample_id,
                "error": str(e),
                "methods": {},
            })

    timestamp = time.strftime("%Y%m%d_%H%M%S")
    json_path = output_dir / f"report_{timestamp}.json"
    md_path = output_dir / f"report_{timestamp}.md"

    generate_json_report(results, json_path)
    generate_markdown_report(results, md_path)
    generate_json_report(results, output_dir / "report_latest.json")
    generate_markdown_report(results, output_dir / "report_latest.md")

    if args.enforce_thresholds:
        ok = check_thresholds(
            results,
            method="AI-Driver",
            min_calls_f1=args.min_calls_f1,
            min_vars_f1=args.min_vars_f1,
            min_fields_f1=args.min_fields_f1,
        )
        if not ok:
            print("AI-Driver metrics below thresholds")
            return 2

    print("\nDone!")
    return 0


if __name__ == "__main__":
    sys.exit(main())
