#!/usr/bin/env python3
"""Run the real KCFA -> abstract interpretation pipeline (offline).

JSON --ai-options accepts every AI Config field, including solver_options.
A completed legacy summary is solver-relative, not a Python soundness certificate.
"""
import argparse
import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pythonstan.world.pipeline import Pipeline
from pythonstan.analysis.pointer.ai.config import Config as AIConfig
from pythonstan.analysis.pointer.kcfa.config import Config as PTAConfig

QUICK_SNIPPETS = {
    "simple": "def identity(x):\n    return x\n\ndef main():\n    return identity('route')\n\nmain()\n",
    "conditional": "def main(flag):\n    if flag is True:\n        return 'route'\n    return 'method'\n\nmain(True)\n",
    "loop": "def main(flag):\n    while flag is True:\n        flag = False\n    return flag\n\nmain(True)\n",
    "objects": "class Route:\n    pass\n\ndef main():\n    obj = Route()\n    setattr(obj, 'method', 'get')\n    return getattr(obj, 'method')\n\nmain()\n",
    "containers": "def main():\n    routes = ('get', 'post')\n    return routes[0]\n\nmain()\n",
}


def run_analysis(path, args):
    if not path.is_file():
        raise FileNotFoundError(f"Source file not found: {path}")
    ai_options = json.loads(args.ai_options.read_text()) if args.ai_options else {}
    if type(ai_options) is not dict:
        raise ValueError("--ai-options requires a JSON object")
    # Explicit CLI controls override the JSON file; defaults do not.
    if args.max_iterations is not None:
        ai_options["max_iterations"] = args.max_iterations
    if args.function:
        ai_options["plan_scopes"] = [args.function]
    if args.entry:
        ai_options["entry_scope"] = args.entry
    pta_options = json.loads(args.pta_options.read_text()) if args.pta_options else {}
    if type(pta_options) is not dict:
        raise ValueError("--pta-options requires a JSON object")
    if args.context_policy is not None:
        pta_options["context_policy"] = args.context_policy
    elif args.k is not None:
        pta_options["context_policy"] = f"{args.k}-cfa"
    if args.no_ai_intrinsics:
        pta_options["enable_ai_intrinsics"] = False
    pta_options = PTAConfig.from_dict(pta_options).to_dict()
    if args.entry:
        pta_options["entry_points"] = [args.entry]
    ai_options["pta_analysis_name"] = "kcfa"
    ai_options = AIConfig.from_dict(ai_options).to_dict()
    config = {
        "filename": str(path), "project_path": str(path.parent),
        "lazy_ir_construction": True, "library_paths": [],
        "analysis": [
            {"name": "kcfa", "id": "PointerAnalysis", "prev_analysis": ["closure"], "options": pta_options},
            {"name": "ai", "id": "AIAnalysisDriver", "prev_analysis": ["kcfa"], "options": ai_options},
        ],
    }
    captured = io.StringIO()
    with contextlib.redirect_stdout(captured):
        pipeline = Pipeline(config=config)
        # Resolve default selection against actual IR names; no target code is executed.
        if not ai_options.get("plan_scopes") and not ai_options.get("entry_scope"):
            names = sorted(scope.get_qualname() for scope in pipeline.get_world().scope_manager.get_scopes())
            driver = pipeline.analysis_manager.get_analyzer("ai")
            driver.ai_config = AIConfig.from_dict({**ai_options, "plan_scopes": names})
        pipeline.run()
        result = pipeline.analysis_manager.get_results("ai")
    if args.verbose:
        print(captured.getvalue(), file=sys.stderr, end="")
    summaries = {}
    for name, summary in result.summaries.items():
        summaries[name] = {
            "is_complete": summary.is_complete, "incomplete_reasons": list(summary.incomplete_reasons),
            "return_objects": sorted(str(obj) for obj in summary.ret),
            "write_count": len(summary.writes), "read_count": len(summary.reads),
            "call_count": len(summary.calls), "unknown_effect_count": len(summary.unknown_effects),
            "may_raise": sorted(summary.may_raise),
            "analyzed_contexts": len(result.context_summaries.get(name, ())),
        }
    complete = bool(summaries) and not result.plan.missing and all(s["is_complete"] for s in summaries.values())
    payload = {"status": "complete" if complete else "incomplete", "certified": False,
            "claim": "solver-relative AI summaries over existing KCFA facts; no whole-Python soundness claim",
            "missing_scopes": result.plan.missing, "summaries": summaries,
            "effective_config": result.config.to_dict(), "effective_pta_config": pta_options,
            "import_scope": "entry module only; imports are residual"}
    manager = pipeline.get_world().scope_manager
    scopes = sorted(manager.get_scopes(), key=lambda scope: scope.get_qualname())
    if args.dump_ir:
        payload["ir"] = {scope.get_qualname(): [str(stmt) for stmt in manager.get_ir(scope, "ir") or ()]
                         for scope in scopes}
    if args.dump_cfg:
        graphs = {}
        for scope in scopes:
            cfg = manager.get_ir(scope, "block cfg")
            if cfg is None:
                continue
            graphs[scope.get_qualname()] = {
                "entry": cfg.entry_blk.idx,
                "blocks": [{"id": block.idx, "statements": [str(stmt) for stmt in block.stmts]}
                           for block in sorted(cfg.blks, key=lambda block: block.idx)],
                "edges": [{"source": edge.get_src().idx, "target": edge.get_tgt().idx,
                           "kind": type(edge).__name__}
                          for block in sorted(cfg.blks, key=lambda block: block.idx)
                          for edge in cfg.out_edges_of(block)],
            }
        payload["cfg"] = graphs
    return payload


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    inputs = parser.add_mutually_exclusive_group(required=True)
    inputs.add_argument("--file", type=Path)
    inputs.add_argument("--snippet", choices=tuple(QUICK_SNIPPETS))
    parser.add_argument("--ai-options", type=Path, help="JSON AI Config including solver_options")
    parser.add_argument("--pta-options", type=Path, help="JSON KCFA Config; explicit CLI controls override matching fields")
    parser.add_argument("--dump-ir", action="store_true", help="Include production IR statements in the JSON result")
    parser.add_argument("--dump-cfg", action="store_true", help="Include production block CFGs in the JSON result")
    contexts = parser.add_mutually_exclusive_group()
    contexts.add_argument("--context-policy")
    contexts.add_argument("--k", type=int, choices=(0, 1, 2, 3))
    parser.add_argument("--max-iterations", type=int)
    parser.add_argument("--no-ai-intrinsics", action="store_true")
    parser.add_argument("--function", help="exact qualified AI scope name")
    parser.add_argument("--entry", help="entry point for KCFA and exact qualified AI scope")
    parser.add_argument("--output", "--dump-ai-state", type=Path)
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.snippet:
            with tempfile.TemporaryDirectory(prefix="pythonstan-ai-") as directory:
                path = Path(directory) / "snippet.py"
                path.write_text(QUICK_SNIPPETS[args.snippet])
                payload = run_analysis(path, args)
        else:
            payload = run_analysis(args.file.resolve(), args)
        code = 0 if payload["status"] == "complete" else 2
    except (ValueError, OSError, KeyError, RuntimeError, SyntaxError) as exc:
        payload = {"status": "input_or_analysis_error", "certified": False, "diagnostic": str(exc)}
        code = 2
    rendered = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.write_text(rendered)
    else:
        print(rendered, end="")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
