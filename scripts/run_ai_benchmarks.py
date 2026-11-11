#!/usr/bin/env python3
"""Run real KCFA -> abstract interpretation benchmarks with bounded workers.

Completion is relative to the implemented transfers; no Python soundness
certificate or precision claim is inferred from elapsed time or empty effects.
"""
import argparse
import ast
from collections import Counter
import contextlib
import io
import json
import multiprocessing
from pathlib import Path
import statistics
import sys
import time
import tracemalloc

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pythonstan.analysis.pointer.ai.config import Config as AIConfig
from pythonstan.analysis.pointer.kcfa.config import Config as PTAConfig
from pythonstan.world.pipeline import Pipeline

PROJECT_ROOT = Path(__file__).resolve().parents[1]
BENCHMARK_DIR = PROJECT_ROOT / "benchmark"


def _validated_options(pointer_config, ai_config):
    pointer = dict(pointer_config)
    ai = dict(ai_config)
    if pointer.pop("skip_pointer", False):
        raise ValueError("--skip-pointer is unsupported: independent AI requires KCFA predecessor results")
    k, obj_depth = pointer.pop("k", None), pointer.pop("obj_depth", None)
    if k is not None and obj_depth is not None:
        raise ValueError("select either call sensitivity (--k) or object sensitivity (--obj-depth)")
    if k is not None or obj_depth is not None:
        if "context_policy" in pointer:
            raise ValueError("context_policy conflicts with k/obj_depth")
        pointer["context_policy"] = f"{k}-cfa" if k is not None else f"{obj_depth}-obj"
    field = pointer.pop("field_sensitivity", None)
    container = pointer.pop("containers_mode", None)
    if field is not None and field not in {"attr", "elem", "value", "full"}:
        raise ValueError("unsupported field_sensitivity")
    if container is not None and container not in {"precise", "summary"}:
        raise ValueError("containers_mode must be precise or summary")
    index = None if field is None else field in {"attr", "full"}
    container_index = None if container is None else container == "precise"
    if index is not None and container_index is not None and index != container_index:
        raise ValueError("field_sensitivity and containers_mode request conflicting index sensitivity")
    requested = index if index is not None else container_index
    if requested is not None:
        if "index_sensitive" in pointer and pointer["index_sensitive"] != requested:
            raise ValueError("conflicting index sensitivity controls")
        pointer["index_sensitive"] = requested
    pointer.setdefault("log_level", "ERROR")
    pointer.setdefault("max_iterations", 50000)
    timeout = ai.pop("timeout", 300)
    if type(timeout) not in {int, float} or not 0 < timeout < float("inf"):
        raise ValueError("timeout must be a finite positive number of seconds")
    widening = ai.pop("widening", False)
    if type(widening) is not bool:
        raise ValueError("widening must be Boolean")
    ai_verbose = ai.pop("verbose", None)
    if ai_verbose is not None and type(ai_verbose) is not bool:
        raise ValueError("verbose must be Boolean")
    if widening:
        solver = dict(ai.get("solver_options", {}))
        if any(solver.get(name, 0) != 0 for name in ("widen_delay", "loop_unroll")):
            raise ValueError("--widening requests immediate widening and conflicts with the configured delay")
        solver["widen_delay"] = 0
        solver["loop_unroll"] = 0
        ai["solver_options"] = solver
    if ai.get("pta_analysis_name", "pointer_analysis") != "pointer_analysis":
        raise ValueError("benchmark AI predecessor must be pointer_analysis")
    ai["pta_analysis_name"] = "pointer_analysis"
    ai.setdefault("log_level", "ERROR")
    return PTAConfig.from_dict(pointer).to_dict(), AIConfig.from_dict(ai).to_dict(), timeout


def create_benchmark_config(benchmark_file, pointer_config, ai_config):
    """Pipeline supplies default transforms exactly once; add only PTA and AI."""
    pointer, ai, _ = _validated_options(pointer_config, ai_config)
    path = Path(benchmark_file).resolve()
    return {
        "filename": str(path), "project_path": str(path.parent),
        "library_paths": [], "import_level": 0, "mock_libs": False,
        "time_count": False,
        "analysis": [
            {"name": "pointer_analysis", "id": "PointerAnalysis", "prev_analysis": ["closure"], "options": pointer},
            {"name": "ai_analysis", "id": "AIAnalysisDriver", "prev_analysis": ["pointer_analysis"], "options": ai},
        ],
    }


def extract_metrics(world, analysis_manager, pointer_config, ai_config, start_time, end_time):
    """Read the production object APIs; missing/incompatible results fail visibly."""
    pointer_result = analysis_manager.get_results("pointer_analysis")
    query = pointer_result.query()
    stats = query.get_statistics()
    residuals = query.get_residual_effects()
    ai_result = analysis_manager.get_results("ai_analysis")
    summaries = {}
    for name, summary in ai_result.summaries.items():
        summaries[name] = {
            "is_complete": summary.is_complete,
            "incomplete_reasons": list(summary.incomplete_reasons),
            "return_objects": sorted(str(obj) for obj in summary.ret),
            "write_count": len(summary.writes), "read_count": len(summary.reads),
            "call_count": len(summary.calls), "unknown_effect_count": len(summary.unknown_effects),
            "unknown_effects": [str(effect) for effect in summary.unknown_effects],
            "may_raise": sorted(summary.may_raise),
            "analyzed_contexts": len(ai_result.context_summaries.get(name, ())),
        }
    transfer_complete = bool(summaries) and not ai_result.plan.missing and all(s["is_complete"] for s in summaries.values())
    ai_complete = transfer_complete and not any(s["unknown_effect_count"] for s in summaries.values())
    pta_complete = type(stats.get("converged")) in {bool, int} and stats["converged"] == 1
    scope_names = sorted(scope.get_qualname() for scope in world.scope_manager.get_scopes())
    registered = set(world.scope_manager.get_module_graph().get_modules())
    unprocessed = sorted(module.get_qualname() for module in registered
                         if world.scope_manager.get_ir(module, "cfg") is None)
    syntactic_imports = sorted(ast.unparse(node) for node in ast.walk(world.get_entry_module().stmt)
                              if isinstance(node, (ast.Import, ast.ImportFrom))
                              and not (isinstance(node, ast.ImportFrom) and node.module == "__future__"))
    return {
        "timing": {"total_time": end_time-start_time, "analysis_time": end_time-start_time},
        "scopes": {"total_scopes": len(scope_names), "scope_names": scope_names},
        "configuration": {"pointer": pointer_config, "ai": ai_result.config.to_dict()},
        "pointer_analysis": {"available": True, "converged": pta_complete,
            "statistics": stats, "total_call_graph_edges": len(query.call_graph().edges),
            "residual_count": len(residuals), "residuals": residuals},
        "ai_analysis": {"available": True, "complete": ai_complete,
            "transfer_complete": transfer_complete,
            "scope_count": len(summaries), "missing_scopes": list(ai_result.plan.missing),
            "summaries": summaries,
            "iteration_metrics_available": False,
            "iteration_metrics_reason": "AIAnalysisResult does not expose per-scope solver iteration counts"},
        "import_coverage": {"policy": "entry module only; imported behavior remains a boundary",
                            "registered_modules": len(registered), "unprocessed_modules": unprocessed,
                            "unprocessed_import_statements": syntactic_imports},
        "complete": pta_complete and ai_complete and not residuals and not unprocessed and not syntactic_imports,
    }


def _benchmark_worker(connection, config):
    start = time.perf_counter()
    captured = io.StringIO()
    tracemalloc.start()
    try:
        with contextlib.redirect_stdout(captured), contextlib.redirect_stderr(captured):
            pipeline = Pipeline(config=config)
            driver = pipeline.analysis_manager.get_analyzer("ai_analysis")
            ai_options = driver.ai_config.to_dict()
            if not ai_options.get("plan_scopes") and not ai_options.get("entry_scope"):
                scopes = pipeline.get_world().scope_manager.get_scopes()
                names = sorted(scope.get_qualname() for scope in scopes
                               if pipeline.get_world().scope_manager.get_ir(scope, "cfg") is not None)
                driver.ai_config = AIConfig.from_dict({**ai_options, "plan_scopes": names})
            pipeline.run()
            metrics = extract_metrics(pipeline.get_world(), pipeline.analysis_manager,
                config["analysis"][0]["options"], driver.ai_config.to_dict(), start, time.perf_counter())
        payload = {"status": "completed" if metrics["complete"] else "incomplete",
                   "success": metrics["complete"], "analysis_executed": True, "results_measured": True,
                   "metrics": metrics, "error": None}
    except Exception as exc:
        payload = {"status": "analysis_error", "success": False, "analysis_executed": None, "results_measured": False,
                   "metrics": {}, "error": f"{type(exc).__name__}: {exc}"}
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    payload["resources"] = {"worker_wall_seconds": time.perf_counter()-start,
                            "peak_python_bytes": peak, "memory_limit_enforced": False}
    if payload["results_measured"]:
        reasons = sorted({reason for summary in metrics["ai_analysis"]["summaries"].values()
                          for reason in summary["incomplete_reasons"]})
        limits = {"max_iterations", "max_block_visits", "max_recursion_depth"}
        payload["resources"]["ai_budget_reasons"] = [reason for reason in reasons if reason in limits]
        payload["resources"]["pta_iteration_limit_reached"] = bool(
            metrics["pointer_analysis"]["statistics"].get("iteration_limit_reached", False))
    payload["diagnostics"] = captured.getvalue()
    try:
        connection.send(payload)
    finally:
        connection.close()


def run_single_benchmark(benchmark_file, pointer_config, ai_config, verbose=False):
    """Execute one isolated worker; a timeout never becomes a successful row."""
    path = Path(benchmark_file)
    base = {"benchmark": path.name, "benchmark_path": str(path.resolve()), "certified": False,
            "claim": "solver-relative transfer coverage only; no whole-Python soundness or precision claim"}
    try:
        _, _, timeout = _validated_options(pointer_config, ai_config)
        config = create_benchmark_config(path, pointer_config, ai_config)
        if not path.is_file():
            raise ValueError(f"Benchmark file not found: {path}")
    except (ValueError, OSError) as exc:
        return {**base, "status": "configuration_error", "success": False,
                "analysis_executed": False, "results_measured": False, "error": str(exc), "metrics": {}}
    context = multiprocessing.get_context("spawn")
    receiver, sender = context.Pipe(duplex=False)
    worker = context.Process(target=_benchmark_worker, args=(sender, config))
    started = time.perf_counter()
    worker.start()
    sender.close()
    try:
        if receiver.poll(timeout):
            try:
                payload = receiver.recv()
            except EOFError:
                payload = {"status": "worker_error", "success": False, "analysis_executed": None, "results_measured": False,
                           "metrics": {}, "error": "Worker exited without a result"}
        else:
            payload = {"status": "timeout", "success": False, "analysis_executed": None, "results_measured": False,
                       "metrics": {}, "error": f"Worker exceeded {timeout:g} seconds; results unmeasured"}
    finally:
        receiver.close()
        worker.join(timeout=0.2)
        if worker.is_alive():
            worker.terminate()
            worker.join(timeout=2)
        if worker.is_alive():
            worker.kill()
            worker.join()
    payload.setdefault("resources", {})
    payload["resources"].update({"wall_seconds": time.perf_counter()-started,
                                 "timeout_seconds": timeout, "worker_exit_code": worker.exitcode})
    if verbose and payload.get("diagnostics"):
        print(payload["diagnostics"], file=sys.stderr, end="")
    return {**base, **payload}


def generate_config_combinations(base_pointer_config, base_ai_config):
    combinations = [{"name": "base", "pointer": dict(base_pointer_config), "ai": dict(base_ai_config)}]
    for k in (1, 3):
        pointer = {key: value for key, value in base_pointer_config.items()
                   if key not in {"k", "obj_depth", "context_policy"}}
        pointer["k"] = k
        combinations.append({"name": f"k_{k}", "pointer": pointer, "ai": dict(base_ai_config)})
    if not base_ai_config.get("widening", False):
        combinations.append({"name": "widening", "pointer": dict(base_pointer_config),
                             "ai": {**base_ai_config, "widening": True}})
    return combinations


def print_summary(results, output_file=None):
    statuses = dict(Counter(row["status"] for row in results))
    measured = [row for row in results if row.get("results_measured")]
    times = [row["metrics"]["timing"]["total_time"] for row in measured]
    complete = sum(row["success"] for row in results)
    summary = {"overview": {"total_benchmarks": len(results), "successful": complete,
               "failed": len(results)-complete, "statuses": statuses,
               "success_rate": 100*complete/len(results) if results else 0},
               "timing": {"measured_rows": len(times), "total_time": sum(times),
                          "average_time": statistics.mean(times) if times else None},
               "certified": False,
               "success_policy": "both analyses completed, no reported mandatory residuals or unprocessed imports; solver-relative only",
               "detailed_results": results}
    print(json.dumps({"overview": summary["overview"], "timing": summary["timing"]}, indent=2))
    if output_file:
        path = Path(output_file)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(summary, indent=2)+"\n")
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--benchmark", help="Specific benchmark file, absolute or relative to benchmark/")
    parser.add_argument("--exclude", nargs="+")
    contexts = parser.add_mutually_exclusive_group()
    contexts.add_argument("--k", type=int, choices=(0, 1, 2, 3), help="Call context depth (default 2)")
    contexts.add_argument("--obj-depth", type=int, choices=(1, 2, 3), help="Object context policy instead of call contexts")
    parser.add_argument("--field-sensitivity", choices=("attr", "elem", "value", "full"),
                        help="attr/full retain finite container indices; elem/value join indices")
    parser.add_argument("--containers-mode", choices=("precise", "summary"), help="Alias for precise/merged container indices")
    parser.add_argument("--skip-pointer", action="store_true", help="Rejected: independent AI requires KCFA facts")
    parser.add_argument("--timeout", type=float, default=300, help="Hard per-worker wall timeout, seconds")
    parser.add_argument("--widening", action="store_true", help="Use immediate AI widening (widen_delay=0)")
    parser.add_argument("--max-iterations", type=int, default=100, help="AI fixpoint iteration budget")
    parser.add_argument("--pta-max-iterations", type=int, default=50000, help="KCFA worklist budget")
    parser.add_argument("--config-sweep", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--output", type=Path, help="Save all rows, including incomplete/errors, as JSON")
    parser.add_argument("--parallel", type=int, help="Only 1 is supported; each benchmark has its own bounded worker")
    args = parser.parse_args(argv)
    if args.parallel not in (None, 1):
        parser.error("parallel execution is unsupported; use --parallel 1")
    pointer = {"k": args.k, "obj_depth": args.obj_depth, "field_sensitivity": args.field_sensitivity,
               "containers_mode": args.containers_mode, "skip_pointer": args.skip_pointer,
               "verbose": args.verbose, "max_iterations": args.pta_max_iterations}
    ai = {"timeout": args.timeout, "widening": args.widening, "max_iterations": args.max_iterations}
    try:
        _validated_options(pointer, ai)
    except ValueError as exc:
        parser.error(str(exc))
    if args.benchmark:
        path = Path(args.benchmark)
        files = [path if path.is_absolute() else BENCHMARK_DIR/path]
    else:
        excluded = set(args.exclude or ())
        files = sorted(path for path in BENCHMARK_DIR.glob("*.py") if path.name not in excluded)
    if not files:
        parser.error("No benchmark files found")
    configs = generate_config_combinations(pointer, ai) if args.config_sweep else [{"name": "single", "pointer": pointer, "ai": ai}]
    results = []
    for config in configs:
        for path in files:
            row = run_single_benchmark(path, config["pointer"], config["ai"], args.verbose)
            row["config_name"] = config["name"]
            results.append(row)
    print_summary(results, args.output)
    return 0 if all(row["success"] for row in results) else 2


if __name__ == "__main__":
    raise SystemExit(main())
